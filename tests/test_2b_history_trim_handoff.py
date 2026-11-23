from types import SimpleNamespace
import pandas as pd
import pyarrow as pa
import pytest
from core.utils.arrow_reader import read_arrow_history_trim_date
from tools.robust40 import server
from tools.robust40.cli import server_robust40 as cli
from tools.robust40.replay import REBUILD_REQUIRED_EXIT


def _write_arrow(path, trim_date=None):
    meta = {b"symbols": b'["BTC"]', b"full_history": b"1"}
    if trim_date is not None:
        meta[b"history_trim_date"] = trim_date.encode("utf-8")
    schema = pa.schema([("candle_begin_time", pa.timestamp("ns", tz="UTC"))], metadata=meta)
    batch = pa.RecordBatch.from_arrays([pa.array(pd.to_datetime(["2026-11-23 00:25"], utc=True))], schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(batch)


def test_read_arrow_history_trim_date_missing_and_present(tmp_path):
    p1 = tmp_path / "a.arrow"
    p2 = tmp_path / "b.arrow"
    _write_arrow(p1, None)
    _write_arrow(p2, "2025-06-01")
    assert read_arrow_history_trim_date(p1) is None
    assert read_arrow_history_trim_date(p2) == "2025-06-01"


def test_check_upstream_trim_date_handles_none_advance_backward_and_mismatch(tmp_path):
    """Review Focus #4: mismatch raises ValueError; backward date does not rebuild; advance triggers rebuild."""
    stamp = int(pd.Timestamp("2026-11-23 01:25", tz="UTC").timestamp())
    folder = tmp_path / "25m"
    # Both None -> no rebuild (Day-1 upgrade)
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", None)
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", None)
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None) == (False, None, None)

    # Spot/Swap mismatch -> ValueError (skip round, never corrupt snapshot)
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", "2025-06-01")
    with pytest.raises(ValueError, match="history_trim_date"):
        cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None)

    # Both advanced None -> '2025-06-01' -> rebuild required
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", "2025-06-01")
    need, old_t, new_t = cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None)
    assert (need, old_t, new_t) == (True, None, "2025-06-01")

    # Equal -> no rebuild
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim="2025-06-01")[0] is False

    # Backward ('2025-05-01' < '2025-06-01') -> no rebuild
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", "2025-05-01")
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", "2025-05-01")
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim="2025-06-01") == (
        False,
        "2025-06-01",
        "2025-05-01",
    )


def test_check_upstream_trim_date_missing_files_returns_false(tmp_path):
    stamp = int(pd.Timestamp("2026-11-23 01:25", tz="UTC").timestamp())
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim="2025-06-01") == (
        False,
        "2025-06-01",
        None,
    )


def test_server_complete_persists_history_trim_date(tmp_path):
    snap = tmp_path / "snap"
    (snap / "sections").mkdir(parents=True)
    (snap / "sections" / "s3.pkl").write_bytes(b"ok")
    server.complete(
        snap,
        "acct",
        {},
        pd.Timestamp("2026-11-23 00:25"),
        "2025-05-20 00:25:00",
        history_trim_date="2025-06-01",
    )
    meta = pd.read_pickle(snap / "warmup.pkl")
    assert meta["history_trim_date"] == "2025-06-01"


def test_server_finish_live_preserves_history_trim_date(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    (snap / "sections").mkdir(parents=True)
    (snap / "model").mkdir(parents=True)
    (snap / "sections" / "s3.pkl").write_bytes(b"ok")
    server.complete(
        snap,
        "acct",
        {},
        pd.Timestamp("2026-11-23 00:25"),
        "2025-05-20 00:25:00",
        history_trim_date="2025-06-01",
    )
    from tools.robust40 import telemetry

    monkeypatch.setattr(telemetry, "commit", lambda: None)
    strategy = SimpleNamespace(
        params={"runtime": {"server_init": True, "state_path": str(snap / "model" / "state.pkl")}},
        _robust40_result={"parent": pd.DataFrame({"a": [1.0]}, index=pd.to_datetime(["2026-11-23 01:25"]))},
    )
    server.finish_live(strategy)
    meta = pd.read_pickle(snap / "warmup.pkl")
    assert meta["history_trim_date"] == "2025-06-01"
    assert meta["cutoff"] == pd.Timestamp("2026-11-23 01:25")


def test_trade_triggers_exit_75_on_trim_advance_and_skips_on_mismatch(tmp_path, monkeypatch):
    import sys
    import types
    import config
    from tools.robust40 import handoff, telemetry

    stamp = int(pd.Timestamp("2026-11-23 01:25", tz="UTC").timestamp())
    folder = tmp_path / "market" / "25m"
    snap = tmp_path / "snap"
    (snap / "sections").mkdir(parents=True)
    (snap / "sections" / "s3.pkl").write_bytes(b"ok")
    # Legacy snapshot without history_trim_date key
    server.save_meta(
        snap,
        dict(
            account="acct",
            settings={},
            cutoff=pd.Timestamp("2026-11-23 00:25"),
            origin="2025-05-20 00:25:00",
            checkpoint_version=1,
            complete=True,
            sections=["sections/s3.pkl"],
        ),
    )

    monkeypatch.setattr(config, "bmac_data_path", tmp_path / "market", raising=False)
    monkeypatch.setattr(config, "utc_offset", 0, raising=False)
    monkeypatch.setattr(server, "settings", lambda account: ({}, {"minute": 25}))
    monkeypatch.setattr(server, "location", lambda root, account, values: snap)
    monkeypatch.setattr(server, "check_dependencies", lambda values: None)
    monkeypatch.setattr(server, "resume_capacity", lambda values: 240)
    monkeypatch.setattr(server, "inspect", lambda *a, **kw: ("ready", ""))
    dummy_exec = types.ModuleType("core.account_exec")
    dummy_exec.main = lambda: None
    monkeypatch.setitem(sys.modules, "core.account_exec", dummy_exec)

    handoff_called = []
    monkeypatch.setattr(handoff, "prepare", lambda *a, **kw: handoff_called.append(a))
    recorded = []
    monkeypatch.setattr(telemetry, "record", lambda **kw: recorded.append(kw))

    # Case 1: spot/swap mismatch -> ValueError, warmup.pkl remains complete=True
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", "2025-06-01")
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", None)
    with pytest.raises(ValueError, match="history_trim_date"):
        cli._trade(tmp_path / "acct.py", stamp)
    assert pd.read_pickle(snap / "warmup.pkl")["complete"] is True
    assert not handoff_called
    assert not recorded

    # Case 2: both advanced None -> '2025-06-01' -> server.begin + telemetry + exit 75
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", "2025-06-01")
    rc = cli._trade(tmp_path / "acct.py", stamp)
    assert rc == REBUILD_REQUIRED_EXIT
    assert pd.read_pickle(snap / "warmup.pkl")["complete"] is False
    assert not handoff_called
    assert recorded == [
        {
            "mode": "rebuild_required",
            "status": "rebuild_required",
            "recovery_reason": "upstream history_trim_date changed: None -> 2025-06-01",
        }
    ]
