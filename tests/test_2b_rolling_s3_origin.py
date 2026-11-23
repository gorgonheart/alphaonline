from types import SimpleNamespace
import pandas as pd
import pyarrow as pa
import pytest
from tools.robust40 import server
from tools.robust40.cli.server_robust40 import apply_profile


def test_resolve_initialization_s3_origin_keeps_configured_when_history_sufficient():
    configured = pd.Timestamp("2025-05-20 00:25:00")
    earliest = pd.Timestamp("2025-03-08 00:25:00")
    end_bar = pd.Timestamp("2026-11-23 00:25:00")
    # 2025-03-08 + 1200h (min_kline_num=480, n=720) = 2025-04-27 < 2025-05-20 -> stays 2025-05-20
    assert server.resolve_initialization_s3_origin(configured, earliest, 720, 480, end_bar) == configured


def test_resolve_initialization_s3_origin_advances_to_utc_minute_grid_and_enforces_365d():
    configured = pd.Timestamp("2025-05-20 00:25:00")
    earliest = pd.Timestamp("2025-06-01 05:25:00")
    end_bar = pd.Timestamp("2027-01-01 00:25:00")
    # 2025-06-01 05:25 + 1200h = 2025-07-21 05:25 -> ceil to 00:25 grid = 2025-07-22 00:25
    resolved = server.resolve_initialization_s3_origin(configured, earliest, 720, 480, end_bar)
    assert resolved == pd.Timestamp("2025-07-22 00:25:00")
    # Exact grid hit (no extra day added): 2025-06-01 00:25 + 1200h = 2025-07-21 00:25
    exact = server.resolve_initialization_s3_origin(configured, pd.Timestamp("2025-06-01 00:25:00"), 720, 480, end_bar)
    assert exact == pd.Timestamp("2025-07-21 00:25:00")
    # Less than 365 days remaining -> ValueError
    with pytest.raises(ValueError, match="365"):
        server.resolve_initialization_s3_origin(configured, earliest, 720, 480, pd.Timestamp("2026-06-01 00:25:00"))


def test_scan_arrow_earliest_bar_handles_ms_and_ns_units_without_pandas(tmp_path):
    """Review Focus #2: PyArrow-only scan works across ms and ns UTC timestamps and skips short listings."""
    arrow_path = tmp_path / "swap_dict_1.arrow"
    # Batch 0: 10 rows (qualifies), ms precision; Batch 1: 3 rows (too short, ignored even though earlier)
    t_long = pa.array(pd.date_range("2025-06-01 00:25", periods=10, freq="h", tz="UTC"), type=pa.timestamp("ms", tz="UTC"))
    t_short = pa.array(pd.date_range("2024-01-01 00:25", periods=3, freq="h", tz="UTC"), type=pa.timestamp("ms", tz="UTC"))
    schema = pa.schema([("candle_begin_time", pa.timestamp("ms", tz="UTC"))], metadata={b"symbols": b'["BTC","NEW"]'})
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(pa.RecordBatch.from_arrays([t_long], schema=schema))
            w.write_batch(pa.RecordBatch.from_arrays([t_short], schema=schema))
    assert server.scan_arrow_earliest_bar(arrow_path, minimum_history_bars=5) == pd.Timestamp("2025-06-01 00:25:00")


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("tz", [None, "UTC"])
def test_scan_arrow_earliest_bar_all_units_and_tz(tmp_path, unit, tz):
    """Review Focus #2: verify s/ms/us/ns and naive/tz-aware timestamp columns."""
    arrow_path = tmp_path / f"swap_dict_{unit}_{tz or 'naive'}.arrow"
    ts_type = pa.timestamp(unit, tz=tz)
    t_later = pa.array(pd.date_range("2025-07-01 00:25", periods=6, freq="h", tz=tz), type=ts_type)
    t_earlier = pa.array(pd.date_range("2025-06-01 00:25", periods=6, freq="h", tz=tz), type=ts_type)
    schema = pa.schema([("candle_begin_time", ts_type)])
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(pa.RecordBatch.from_arrays([t_later], schema=schema))
            w.write_batch(pa.RecordBatch.from_arrays([t_earlier], schema=schema))
    assert server.scan_arrow_earliest_bar(arrow_path, minimum_history_bars=5) == pd.Timestamp("2025-06-01 00:25:00")


def test_scan_arrow_earliest_bar_raises_when_no_symbol_qualifies(tmp_path):
    arrow_path = tmp_path / "swap_dict_short.arrow"
    ts_type = pa.timestamp("ns", tz="UTC")
    t_short = pa.array(pd.date_range("2025-06-01 00:25", periods=3, freq="h", tz="UTC"), type=ts_type)
    schema = pa.schema([("candle_begin_time", ts_type)])
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(pa.RecordBatch.from_arrays([t_short], schema=schema))
    with pytest.raises(ValueError, match="Full raw history is not ready for initialization"):
        server.scan_arrow_earliest_bar(arrow_path, minimum_history_bars=5)


def test_apply_profile_accepts_forward_grid_origin_and_rejects_earlier_or_misaligned(tmp_path):
    pool = [{"filter_list": [("VolumeMaxBias_进出场阈值分离官方", (720, 0.1, 0.9, "2025-05-20"), "val:==1")]}]
    def make_cfg():
        return SimpleNamespace(strategy_pool=pool, incremental_lookback_hours=0, data_config={},
                               account_config={"hour_offset": "25m"}, strategy_config={"params": {"runtime": {}}})
    # Forward on 00:25 grid -> accepted and injected into strategy_pool
    cfg = make_cfg()
    apply_profile(cfg, "2025-07-22 00:25:00", tmp_path, True)
    assert cfg.strategy_pool[0]["filter_list"][0][1][3] == "20250722T002500"
    # Earlier than configured -> rejected
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-04-26 00:25:00", tmp_path, True)
    # Wrong hour or minute -> rejected
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-07-22 01:25:00", tmp_path, True)
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-07-22 00:40:00", tmp_path, True)
    # NaT / invalid -> rejected
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "NaT", tmp_path, True)


def test_initialize_wires_rolling_s3_origin_into_worker_payload(tmp_path, monkeypatch, capsys):
    import json
    import subprocess
    import config
    import core.model.backtest_config as backtests
    import core.utils.exchange_hub as exchanges
    from tools.robust40 import arrow_demand, market_inputs as warm
    from tools.robust40.cli import server_robust40 as worker
    from tools.robust40.initialization_config import INITIALIZATION

    account = tmp_path / "acct_roll.py"
    account.write_text("")
    values = {
        "min_kline_num": 480,
        "strategy_pool": [
            {"filter_list": [("VolumeMaxBias_进出场阈值分离官方", (720, 0.1, 0.9, "2025-05-20"), "val:==1")]}
        ],
    }
    monkeypatch.setattr(server, "settings", lambda p: (values, {"minute": 25}))
    monkeypatch.setitem(INITIALIZATION, "minimum_history_bars", 10)
    stamp = int(pd.Timestamp("2027-01-01 01:25:00", tz="UTC").timestamp())
    monkeypatch.setattr(warm, "latest_ready", lambda *a: (stamp, 1))

    def fake_freeze(source, exg, destination, minute, st, ready, *, full_arrow=False):
        assert full_arrow is True
        folder = destination / f"{minute}m"
        folder.mkdir(parents=True)
        (destination / "exginfo").mkdir()
        ts_type = pa.timestamp("ms", tz="UTC")
        arr = pa.array(pd.date_range("2025-06-01 05:25", periods=20, freq="h", tz="UTC"), type=ts_type)
        schema = pa.schema([("candle_begin_time", ts_type)])
        with pa.OSFile(str(folder / f"swap_dict_{st}.arrow"), "wb") as sink:
            with pa.ipc.new_file(sink, schema) as w:
                w.write_batch(pa.RecordBatch.from_arrays([arr], schema=schema))

    monkeypatch.setattr(warm, "freeze_inputs", fake_freeze)
    monkeypatch.setattr(
        backtests,
        "MultiEquityBacktestConfig",
        lambda: SimpleNamespace(factory=SimpleNamespace(generate_all_factor_config=lambda: SimpleNamespace(factor_params_dict={}))),
    )
    monkeypatch.setattr(warm, "prepare_public_market", lambda: SimpleNamespace(market_info={"spot": {"symbol_list": []}, "swap": {"symbol_list": []}}))
    monkeypatch.setattr(exchanges, "get_bstocks_coins", lambda: set())
    monkeypatch.setattr(arrow_demand, "set_demand", lambda *a, **k: None)
    monkeypatch.setattr(arrow_demand, "release_demand", lambda *a, **k: None)
    monkeypatch.setattr(config, "realtime_data_path", "/srv/alpha/data")
    captured = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: captured.append(json.loads(k["env"]["ROBUST40_SERVER_WORKER"])))

    workspace = tmp_path / "ws"
    worker._initialize(account, workspace, stamp, tmp_path / "snapshot/acct_roll")
    assert len(captured) == 1
    assert captured[0]["origin"] == "2025-07-22 00:25:00"
    out = capsys.readouterr().out
    assert "滚动推进 S3 origin" in out
    assert "2025-07-22 00:25:00" in out

