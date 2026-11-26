"""TDD tests for 2026-11-26 post-comparison consumer optimizations (Items 2 & 5).

Covers:
1. Item 2: _trade_wait_ready / _trade release old_inputs, current_inputs, and inputs
   from parent stack frames and release PyArrow unused pool pages BEFORE entering
   account_exec.main() (preventing COW swap-out spikes during FACTOR_BLOCKS).
2. Item 5a: BinanceClient._parse_bstocks_coins tolerates partial dirty rows (e.g. name=None)
   while still rejecting completely malformed payloads, plus 12h cross-process disk cache.
3. Item 5b: BinanceClient.get_swap_leverage_bracket 6h cross-process disk cache and
   adjust_leverage_by_target_notional lazy swap_account skip when notionals fit within
   tier-1 notionalCap.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import weakref

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Network forbidden in offline unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _write_min_arrow(path: Path, symbols: list[str], times: pd.DatetimeIndex):
    path.parent.mkdir(parents=True, exist_ok=True)
    batches = []
    schema = None
    for sym in symbols:
        df = pd.DataFrame({
            "candle_begin_time": times.tz_localize(None),
            "first_candle_time": np.full(len(times), times[0].tz_localize(None)),
            "open": np.linspace(100.0, 104.0, len(times)),
            "high": np.linspace(101.0, 105.0, len(times)),
            "low": np.linspace(99.0, 103.0, len(times)),
            "close": np.linspace(100.5, 104.5, len(times)),
            "volume": np.full(len(times), 1000.0),
            "quote_volume": np.full(len(times), 100000.0),
            "trade_num": np.full(len(times), 50, dtype=np.int64),
            "taker_buy_base_asset_volume": np.full(len(times), 500.0),
            "taker_buy_quote_asset_volume": np.full(len(times), 50000.0),
            "avg_price_1m": np.linspace(100.2, 104.2, len(times)),
            "funding_rate": np.full(len(times), 0.0001),
            "是否交易": np.full(len(times), 1, dtype=np.int8),
            "symbol_spot": np.full(len(times), sym),
            "symbol_swap": np.full(len(times), sym),
            "is_spot": np.full(len(times), 1 if "spot" in path.name else 0, dtype=np.int8),
            "funding_fee": np.full(len(times), 0.0),
        })
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if schema is None:
            schema = tbl.schema.with_metadata({b"symbols": json.dumps(symbols).encode("utf-8")})
        batches.extend(tbl.to_batches())
    with pa.OSFile(str(path), "wb") as sink:
        with ipc.new_file(sink, schema) as writer:
            for b in batches:
                writer.write_batch(b)


def _write_min_pivots(folder: Path, kind: str, symbols: list[str], times: pd.DatetimeIndex):
    idx = times.tz_localize(None)
    pivot = {
        field: pd.DataFrame({s: np.linspace(100.0, 101.0, len(idx)) for s in symbols}, index=idx)
        for field in ("open", "close", "vwap1m", "funding_rate")
    }
    pd.to_pickle(pivot, folder / f"market_pivot_{kind}_2026.pkl")


# ============================================================================
# Item 2: Release revision input dicts & PyArrow pool BEFORE account_exec.main
# ============================================================================


def test_wait_ready_releases_revision_input_dicts_before_account_exec_main(tmp_path, monkeypatch):
    import config
    import core.account_exec as account_exec
    from tools.robust40 import handoff, server
    from tools.robust40.cli import server_robust40

    acct = tmp_path / "acct_mem.py"
    acct.write_text("account_config={'hour_offset':'25m'}\nstrategy_name='s'\n", encoding="utf-8")
    values = {
        "account": {"hour_offset": "25m"},
        "strategy_name": "s",
        "strategy_pool": [],
        "precompute_factor_configs": {},
    }
    dest = server.location(tmp_path, acct, values)
    (dest / "model").mkdir(parents=True)

    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit="s") - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq="1h")

    warmup_meta = {
        "account": "acct_mem",
        "settings": values,
        "cutoff": end - pd.Timedelta(hours=1),
        "origin": "2026-01-01 00:25:00",
        "checkpoint_version": 1,
        "complete": True,
        "sections": [],
        "history_trim_date": None,
    }
    pd.to_pickle(warmup_meta, dest / "warmup.pkl")

    old_df = pd.DataFrame({"close": np.linspace(100.0, 103.0, 4)}, index=times[:-1])
    pd.to_pickle({"raw/spot/BTCUSDT": old_df}, dest / "model/revision_inputs.pkl")
    del old_df

    bmac = tmp_path / "preprocess_1h_resample"
    folder = bmac / "25m"
    _write_min_arrow(folder / f"spot_dict_{stamp}.arrow", ["BTCUSDT"], times)
    _write_min_arrow(folder / f"swap_dict_{stamp}.arrow", ["BTCUSDT"], times)
    _write_min_pivots(folder, "spot", ["BTCUSDT"], times)
    _write_min_pivots(folder, "swap", ["BTCUSDT"], times)
    for prefix in ("spot_dict_", "market_pivot_spot_", "swap_dict_", "market_pivot_swap_"):
        (folder / f"{prefix}{stamp}.ready").write_text("")

    monkeypatch.setattr(server_robust40, "ROOT", tmp_path)
    monkeypatch.setattr(config, "bmac_data_path", bmac)
    monkeypatch.setattr(config, "strategy_config", {"params": {"runtime": {}}})
    monkeypatch.setattr(server, "settings", lambda a: (values, {"minute": 25}))
    monkeypatch.setattr(server, "check_dependencies", lambda v: None)
    monkeypatch.setattr(server, "inspect", lambda *a, **k: ("ready", "ok"))

    refs = {}
    orig_read_pickle = pd.read_pickle

    def _spy_read_pickle(p, *a, **kw):
        obj = orig_read_pickle(p, *a, **kw)
        if Path(p).name == "revision_inputs.pkl" and isinstance(obj, dict) and "raw/spot/BTCUSDT" in obj:
            refs["old_df"] = weakref.ref(obj["raw/spot/BTCUSDT"])
        return obj

    orig_stream = handoff.wait_and_read_inputs_streaming

    def _spy_stream(*a, **kw):
        st, cur, trims, stats = orig_stream(*a, **kw)
        if isinstance(cur, dict) and "raw/spot/BTCUSDT" in cur:
            refs["cur_df"] = weakref.ref(cur["raw/spot/BTCUSDT"])
        return st, cur, trims, stats

    monkeypatch.setattr(pd, "read_pickle", _spy_read_pickle)
    monkeypatch.setattr(handoff, "wait_and_read_inputs_streaming", _spy_stream)
    monkeypatch.setattr(handoff, "prepare", lambda dest, values, run, minute, end, preloaded_old=None, preloaded_current=None: (None, preloaded_current))

    pool_released = []
    real_pool = pa.default_memory_pool()
    monkeypatch.setattr(
        pa,
        "default_memory_pool",
        lambda: SimpleNamespace(release_unused=lambda: (pool_released.append(True), real_pool.release_unused())[1]),
    )

    observed_in_main = {}

    def _fake_main():
        observed_in_main["old_alive"] = refs["old_df"]() is not None
        observed_in_main["cur_alive"] = refs["cur_df"]() is not None
        observed_in_main["pending_exists"] = (dest / "model/revision_inputs.pending.pkl").exists()
        observed_in_main["pool_released"] = len(pool_released) > 0
        return run_time

    monkeypatch.setattr(account_exec, "main", _fake_main)

    rc = server_robust40.trade(acct, stamp, wait_ready=True)
    assert rc in (0, None)
    assert observed_in_main == {
        "old_alive": False,
        "cur_alive": False,
        "pending_exists": True,
        "pool_released": True,
    }


# ============================================================================
# Item 5a: bStocks dirty-row tolerance + 12h cross-process disk cache
# ============================================================================


def test_parse_bstocks_skips_dirty_rows_but_rejects_all_invalid_list():
    from core.binance.base_client import BinanceClient

    # Dirty rows (name=None, empty coin) alongside valid rows -> skips dirty rows, keeps valid bStocks
    mixed = [
        {"coin": "AAPL", "name": "Apple (bStocks)"},
        {"coin": "TSLA", "name": "Tesla（bStocks）"},
        {"coin": "BTC", "name": "Bitcoin"},
        {"coin": "BROKEN1", "name": None},
        {"coin": "", "name": "EmptyCoin"},
        {"coin": "   ", "name": None},
    ]
    assert BinanceClient._parse_bstocks_coins(mixed) == {"AAPL", "TSLA"}

    # Empty list remains valid empty set
    assert BinanceClient._parse_bstocks_coins([]) == set()

    # Non-list or non-empty list with zero valid rows must still raise ValueError
    with pytest.raises(ValueError, match="不是列表"):
        BinanceClient._parse_bstocks_coins(None)
    with pytest.raises(ValueError, match="字段无效"):
        BinanceClient._parse_bstocks_coins([{"unexpected": "value"}])
    with pytest.raises(ValueError, match="字段无效"):
        BinanceClient._parse_bstocks_coins([{"coin": "ONLY_BAD", "name": None}])
    with pytest.raises(ValueError, match="字段无效"):
        BinanceClient._parse_bstocks_coins(["not-a-dict"])


def test_bstocks_disk_cache_persists_across_fresh_process_state(tmp_path, monkeypatch):
    import core.binance.base_client as base
    from core.binance.base_client import BinanceClient

    now = [1_700_000_000]
    monkeypatch.setattr(base.time, "time", lambda: now[0])
    monkeypatch.setattr(base, "retry_wrapper", lambda fn, **kw: fn())
    monkeypatch.setattr(base, "send_wechat_work_msg", lambda *a: None)
    monkeypatch.setattr(BinanceClient, "bstocks_coins", set())
    monkeypatch.setattr(BinanceClient, "_bstocks_last_update", 0)

    cache_file = tmp_path / "bstocks_cache.json"

    def _make_client(cap_fn, tok_fn):
        cli = BinanceClient.__new__(BinanceClient)
        cli.api_key = "offline-key"
        cli.filter_bstocks = True
        cli._disk_cache_enabled = True
        cli._bstocks_cache_file = cache_file
        cli.exchange = SimpleNamespace(
            sapi_get_capital_config_getall=cap_fn,
            sapi_get_equity_market_tokenized_assets=tok_fn,
        )
        return cli

    cap_mock = Mock(return_value=[{"coin": "NVDA", "name": "Nvidia (bStocks)"}, {"coin": "BAD", "name": None}])
    tok_mock = Mock(return_value=[{"assetCode": "SPY"}])
    cli1 = _make_client(cap_mock, tok_mock)
    assert cli1.fetch_bstocks_coins(expire_seconds=3600 * 12) == {"NVDA", "SPY"}
    assert cache_file.exists()
    assert cap_mock.call_count == 1 and tok_mock.call_count == 1

    # Simulate next-hour child process: reset in-memory class cache, advance 1 hour (< 12h TTL)
    monkeypatch.setattr(BinanceClient, "bstocks_coins", set())
    monkeypatch.setattr(BinanceClient, "_bstocks_last_update", 0)
    now[0] += 3600

    cap_mock2 = Mock(side_effect=AssertionError("Should hit disk cache without API call"))
    tok_mock2 = Mock(side_effect=AssertionError("Should hit disk cache without API call"))
    cli2 = _make_client(cap_mock2, tok_mock2)
    assert cli2.fetch_bstocks_coins(expire_seconds=3600 * 12) == {"NVDA", "SPY"}

    # Advance past 12h TTL -> refreshes from API; if API fails, preserves stale disk coins
    monkeypatch.setattr(BinanceClient, "bstocks_coins", set())
    monkeypatch.setattr(BinanceClient, "_bstocks_last_update", 0)
    now[0] += 3600 * 13
    fail_cap = Mock(side_effect=RuntimeError("network down"))
    fail_tok = Mock(side_effect=RuntimeError("network down"))
    cli3 = _make_client(fail_cap, fail_tok)
    assert cli3.fetch_bstocks_coins(expire_seconds=3600 * 12) == {"NVDA", "SPY"}


# ============================================================================
# Item 5b: Leverage bracket disk cache & lazy swap_account skip
# ============================================================================


def test_leverage_bracket_disk_cache_and_lazy_swap_account_skip(tmp_path, monkeypatch):
    import core.binance.base_client as base
    from core.binance.portfolio_margin_client import PortfolioMarginClient

    now = [1_700_000_000]
    monkeypatch.setattr(base.time, "time", lambda: now[0])
    monkeypatch.setattr(base, "retry_wrapper", lambda fn, params=None, **kw: fn(params) if params is not None else fn())
    monkeypatch.setattr(base, "send_wechat_work_msg", lambda *a: None)

    bracket_file = tmp_path / "leverage_brackets_pm.json"
    bracket_calls = []
    acct_calls = []
    lev_calls = []

    brackets_payload = [
        {
            "symbol": "BTCUSDT",
            "brackets": [
                {"initialLeverage": 5, "notionalCap": 10000},
                {"initialLeverage": 2, "notionalCap": 50000},
                {"initialLeverage": 1, "notionalCap": 200000},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "brackets": [
                {"initialLeverage": 10, "notionalCap": 20000},
                {"initialLeverage": 3, "notionalCap": 100000},
            ],
        },
    ]

    class _FakeExg:
        def papiGetUmLeverageBracket(self, params=None):
            bracket_calls.append(1)
            return brackets_payload

        def papi_post_um_leverage(self, params=None):
            lev_calls.append((params["symbol"], params["leverage"]))
            return {"leverage": params["leverage"]}

    def _make_pm_client():
        cli = PortfolioMarginClient.__new__(PortfolioMarginClient)
        cli._leverage_bracket_cache = None
        cli.swap_account = None
        cli._disk_cache_enabled = True
        cli._bracket_cache_file = bracket_file
        cli.exchange = _FakeExg()
        cli.wechat_webhook_url = ""

        def _get_swap_account(require_update=False):
            acct_calls.append(require_update)
            return {
                "positions": [
                    {"symbol": "BTCUSDT", "leverage": "3", "notional": "500", "maxNotional": "50000"},
                    {"symbol": "ETHUSDT", "leverage": "3", "notional": "500", "maxNotional": "100000"},
                ]
            }

        cli.get_swap_account = _get_swap_account
        return cli

    # Round 1: small notionals (500 <= 0.9 * 10000 = 9000 for BTC tier-1 5x; 500 <= 18000 for ETH tier-1 10x)
    # Fetches brackets once & caches to disk, and SKIPS get_swap_account completely!
    c1 = _make_pm_client()
    c1.adjust_leverage_by_target_notional({"BTCUSDT": 500.0, "ETHUSDT": 500.0}, max_leverage=3)
    assert len(bracket_calls) == 1
    assert bracket_file.exists()
    assert acct_calls == []
    assert lev_calls == []

    # Round 2 (next hour child process): hits bracket disk cache (0 bracket API calls) AND skips get_swap_account!
    now[0] += 3600
    c2 = _make_pm_client()
    c2.adjust_leverage_by_target_notional({"BTCUSDT": 800.0, "ETHUSDT": 1000.0}, max_leverage=3)
    assert len(bracket_calls) == 1
    assert acct_calls == []
    assert lev_calls == []

    # Round 3: large notional on BTCUSDT (12000 > 0.9 * 10000) -> queries get_swap_account and steps down 3x -> 2x
    c3 = _make_pm_client()
    c3.adjust_leverage_by_target_notional({"BTCUSDT": 12000.0, "ETHUSDT": 1000.0}, max_leverage=3)
    assert len(bracket_calls) == 1  # Still served from disk cache!
    assert len(acct_calls) == 1
    assert lev_calls == [("BTCUSDT", 2)]
