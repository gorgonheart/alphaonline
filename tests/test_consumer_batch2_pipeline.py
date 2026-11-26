"""Tests for consumer batch 2 optimizations (Phase B: Stages 5, 6, 7)."""
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_synth_arrow(path: Path, symbols: list[str], times: pd.DatetimeIndex, extra_cols: dict | None = None,
                       trim_date: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    batches = []
    schema = None
    for sym in symbols:
        data = {
            'candle_begin_time': times.tz_localize(None),
            'first_candle_time': np.full(len(times), times[0].tz_localize(None)),
            'open': np.linspace(100.0, 105.0, len(times)),
            'high': np.linspace(101.0, 106.0, len(times)),
            'low': np.linspace(99.0, 104.0, len(times)),
            'close': np.linspace(100.5, 105.5, len(times)),
            'volume': np.full(len(times), 1000.0),
            'quote_volume': np.full(len(times), 100000.0),
            'trade_num': np.full(len(times), 100, dtype=np.int64),
            'taker_buy_base_asset_volume': np.full(len(times), 500.0),
            'taker_buy_quote_asset_volume': np.full(len(times), 50000.0),
            'avg_price_1m': np.linspace(100.2, 105.2, len(times)),
            'funding_rate': np.full(len(times), 0.0001),
            '是否交易': np.full(len(times), 1, dtype=np.int8),
            'symbol_spot': np.full(len(times), sym if 'spot' in path.name else sym),
            'symbol_swap': np.full(len(times), sym),
            'is_spot': np.full(len(times), 1 if 'spot' in path.name else 0, dtype=np.int8),
            'funding_fee': np.full(len(times), 0.0),
        }
        if extra_cols:
            for k, v in extra_cols.items():
                data[k] = v
        df = pd.DataFrame(data)
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if schema is None:
            meta = {b'symbols': json.dumps(symbols).encode('utf-8')}
            if trim_date is not None:
                meta[b'history_trim_date'] = trim_date.encode('utf-8')
            schema = tbl.schema.with_metadata(meta)
        batches.extend(tbl.to_batches())
    with pa.OSFile(str(path), 'wb') as sink:
        with ipc.new_file(sink, schema) as writer:
            for b in batches:
                writer.write_batch(b)


def _write_synth_pivots(folder: Path, kind: str, symbols: list[str], times: pd.DatetimeIndex):
    idx = times.tz_localize(None)
    pivot = {
        field: pd.DataFrame({s: np.linspace(100.0, 101.0, len(idx)) for s in symbols}, index=idx)
        for field in ('open', 'close', 'vwap1m', 'funding_rate')
    }
    pd.to_pickle(pivot, folder / f'market_pivot_{kind}_2026.pkl')


def test_stage5_streaming_spot_first_finishes_before_swap_ready_and_matches_oracle(tmp_path, monkeypatch):
    from tools.robust40 import handoff
    import config

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=10), end, freq='1h')
    start = times[0]
    syms = ['BTCUSDT', 'ETHUSDT']

    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', syms, times, extra_cols={'Bias_5': np.ones(len(times))})
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', syms, times, extra_cols={'Bias_5': np.ones(len(times))})
    _write_synth_pivots(folder, 'spot', syms, times)
    _write_synth_pivots(folder, 'swap', syms, times)

    # First write only spot .ready files
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    spot_done = threading.Event()
    orig_read_arrow_tail = handoff.read_arrow_tail

    def _spy_read_arrow_tail(root, kind, *args, **kwargs):
        res = orig_read_arrow_tail(root, kind, *args, **kwargs)
        if kind == 'spot':
            spot_done.set()
        return res

    monkeypatch.setattr(handoff, 'read_arrow_tail', _spy_read_arrow_tail)

    def _release_swap():
        assert spot_done.wait(timeout=2.0), 'spot read must finish before swap.ready arrives'
        time.sleep(0.05)
        (folder / f'swap_dict_{stamp}.ready').write_text('')
        (folder / f'market_pivot_swap_{stamp}.ready').write_text('')

    t = threading.Thread(target=_release_swap)
    t.start()
    status, streamed, trims, stats = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, start, {'Bias_5'}, deadline=time.time() + 5.0, poll_interval=0.02
    )
    t.join()

    assert status == 'ok'
    assert stats['spot_early_lead_s'] > 0
    assert trims == (None, None)

    monkeypatch.setattr(config, 'bmac_data_path', bmac)
    oracle = handoff.read_inputs(run_time, 25, end, start=start)
    assert set(streamed.keys()) == set(oracle.keys())
    for k in oracle:
        pd.testing.assert_frame_equal(streamed[k], oracle[k], check_exact=True, obj=k)


def test_stage5_wait_ready_releases_account_lock_and_excludes_wait_from_total_s(tmp_path, monkeypatch):
    from tools.robust40 import handoff, server, telemetry

    dest = tmp_path / 'data' / 'snapshot' / 'acct_s'
    dest.mkdir(parents=True)

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')
    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    lock_acquired_during_wait = {}

    def _delayed_swap_with_lock_probe():
        time.sleep(0.15)
        with server.account_lock(dest):
            lock_acquired_during_wait['ok'] = True
        (folder / f'swap_dict_{stamp}.ready').write_text('')
        (folder / f'market_pivot_swap_{stamp}.ready').write_text('')

    t = threading.Thread(target=_delayed_swap_with_lock_probe)
    t.start()
    status, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], set(), deadline=time.time() + 5.0, poll_interval=0.02
    )
    t.join()
    assert status == 'ok'
    assert lock_acquired_during_wait.get('ok') is True

    emitted = []
    monkeypatch.setattr(telemetry, '_write', emitted.append)
    with telemetry.round_log('acct', stamp):
        time.sleep(0.25)
        telemetry.reset_timer(wait_ready_s=0.35, prewarm_s=0.1)
    end_payload = json.loads(emitted[-1].split('MODEL_REPLAY ', 1)[1])
    assert end_payload['wait_ready_s'] == pytest.approx(0.35)
    assert end_payload['total_s'] < 0.15


def test_stage5_timeout_returns_76_and_missing_columns_returns_75(tmp_path, monkeypatch):
    from tools.robust40 import handoff, server
    from tools.robust40.cli import server_robust40
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')

    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    status, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], set(), deadline=time.time() + 0.12, poll_interval=0.02
    )
    assert status == 'timeout'

    # Now write swap ready, but require a missing column -> missing_arrow
    (folder / f'swap_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_swap_{stamp}.ready').write_text('')
    status2, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], {'MISSING_COL_1'}, deadline=time.time() + 1.0, poll_interval=0.02
    )
    assert status2 == 'missing_arrow'

    # Coordinator.handle_result on exit 76 returns False without raising
    c = server.Coordinator(ROOT)
    cmd = [sys.executable, str(ROOT / 'tools/robust40/cli/server_robust40.py'), 'trade', '--account', 'a.py', '--stamp', str(stamp)]
    assert c.handle_result(tmp_path / 'a.py', run_time, bmac, cmd, {}, WAIT_FOR_DATA_EXIT) is False
    assert hasattr(c, 'launch_prewarmed_trade')
    assert REBUILD_REQUIRED_EXIT == 75
    assert WAIT_FOR_DATA_EXIT == 76


def test_stage5_wait_ready_end_to_end_timeout_sig_change_and_trim_mismatch(tmp_path, monkeypatch):
    import config
    from tools.robust40 import handoff, server
    from tools.robust40.cli import server_robust40
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT

    acct = tmp_path / 'acct_m.py'
    acct.write_text("account_config={'hour_offset':'25m'}\nstrategy_name='s'\n", encoding='utf-8')
    values = {'account': {'hour_offset': '25m'}, 'strategy_name': 's', 'strategy_pool': [], 'precompute_factor_configs': {}}
    dest = server.location(tmp_path, acct, values)
    (dest / 'model').mkdir(parents=True)
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')

    warmup_meta = {'account': 'acct_m', 'settings': values, 'cutoff': end, 'origin': '2026-01-01 00:25:00',
                   'checkpoint_version': 1, 'complete': True, 'sections': [], 'history_trim_date': None}
    pd.to_pickle(warmup_meta, dest / 'warmup.pkl')
    pd.to_pickle({'raw/spot/BTCUSDT': pd.DataFrame({'close': [1.0]}, index=[times[0]])}, dest / 'model/revision_inputs.pkl')

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times, trim_date='2025-01-01')
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times, trim_date='2025-01-02')
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)

    monkeypatch.setattr(server_robust40, 'ROOT', tmp_path)
    monkeypatch.setattr('tools.robust40.initialization_config.DATA_READY_WAIT_SECONDS', 0.1)
    monkeypatch.setattr(config, 'bmac_data_path', bmac)
    monkeypatch.setattr(server, 'settings', lambda a: (values, {'minute': 25}))

    # 1. Timeout -> exit 76 and warmup.pkl stays complete=True
    rc = server_robust40.trade(acct, stamp, wait_ready=True)
    assert rc == WAIT_FOR_DATA_EXIT
    assert pd.read_pickle(dest / 'warmup.pkl')['complete'] is True

    # 2. Ready with mismatched trim dates -> ValueError
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')
    (folder / f'swap_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_swap_{stamp}.ready').write_text('')
    monkeypatch.setattr('tools.robust40.initialization_config.DATA_READY_WAIT_SECONDS', 2.0)
    with pytest.raises(ValueError, match='history_trim_date mismatch'):
        server_robust40.trade(acct, stamp, wait_ready=True)

    # 3. Warmup signature changes during wait -> discards stale preloaded inputs and falls back to _trade
    called = {}
    orig_wait = handoff.wait_and_read_inputs_streaming

    def _mutate_warmup_during_wait(*args, **kwargs):
        res = orig_wait(*args, **kwargs)
        pd.to_pickle(dict(warmup_meta, marker='updated_by_installer'), dest / 'warmup.pkl')
        return res

    monkeypatch.setattr(handoff, 'wait_and_read_inputs_streaming', _mutate_warmup_during_wait)
    monkeypatch.setattr(server_robust40, '_trade', lambda *a, **kw: (called.setdefault('kw', kw), REBUILD_REQUIRED_EXIT)[1])
    rc2 = server_robust40.trade(acct, stamp, wait_ready=True)
    assert rc2 == REBUILD_REQUIRED_EXIT
    assert 'preloaded_current' not in called['kw']


def test_stage6_model_state_preload_hits_cache_and_invalidates_on_mtime_change(tmp_path, monkeypatch):
    from tools import local_risk_model as lrm
    from core.backtest import step6_market_handoff

    state_path = tmp_path / 'model' / 'state.pkl'
    state_path.parent.mkdir(parents=True)
    expected = {'v': 1, 'arr': [1, 2, 3]}
    pd.to_pickle(expected, state_path)

    # 1. Preload + consume hits cache without second pd.read_pickle
    lrm.start_model_state_preload(state_path)
    lrm._PRELOAD_SLOT['thread'].join(timeout=2.0)
    orig_read_pickle = pd.read_pickle
    monkeypatch.setattr(pd, 'read_pickle', lambda *a, **k: (_ for _ in ()).throw(AssertionError('should hit preloaded state')))
    got = lrm.consume_preloaded_model_state(state_path)
    assert got == expected
    assert lrm.consume_preloaded_model_state(state_path) is None
    monkeypatch.setattr(pd, 'read_pickle', orig_read_pickle)

    # 2. Mutating state file after preload invalidates cache
    lrm.start_model_state_preload(state_path)
    time.sleep(0.05)
    pd.to_pickle({'v': 2, 'arr': [1, 2, 3, 4, 5]}, state_path)
    assert lrm.consume_preloaded_model_state(state_path) is None

    # 3. step6_market_handoff clears preloaded state on exception exit
    with pytest.raises(RuntimeError, match='boom'):
        with step6_market_handoff(model_state_preload_path=state_path):
            lrm.start_model_state_preload(state_path)
            raise RuntimeError('boom')
    assert lrm.consume_preloaded_model_state(state_path) is None


def test_stage6_equity_fast_counts_and_ratio_snapshot_match_oracle_exact(tmp_path):
    from core import equity as eq_mod

    idx = pd.date_range('2026-01-01', periods=5, freq='1h', tz='UTC')
    df_spot = pd.DataFrame({
        'BTCUSDT': [0.5, 0.0, np.nan, -0.2, 0.3],
        'ETHUSDT': [0.0, np.nan, 0.1, 0.2, 0.0],
    }, index=idx)
    df_swap = pd.DataFrame({
        'BTCUSDT': [-0.5, 0.0, 0.2, np.nan, -0.1],
        'SOLUSDT': [0.1, -0.1, 0.0, 0.0, np.nan],
    }, index=idx)

    # Oracle counts via legacy pandas masking .count(axis=1)
    expected_long = df_spot[df_spot > 0].count(axis=1) + df_swap[df_swap > 0].count(axis=1)
    expected_short = df_spot[df_spot < 0].count(axis=1) + df_swap[df_swap < 0].count(axis=1)

    # Run calc_equity on synthetic inputs
    ratio_dir = tmp_path / 'ratios'
    ratio_dir.mkdir()
    df_spot.to_pickle(ratio_dir / 'df_spot_ratio.pkl')
    df_swap.to_pickle(ratio_dir / 'df_swap_ratio.pkl')

    snap_dir = tmp_path / 'snap'
    snap_dir.mkdir()

    class _FakeConf:
        name = 's_eq'
        initial_usdt = 10000.0
        margin_rate = 0.05
        spot_c_rate = 0.001
        swap_c_rate = 0.0006
        spot_min_order_limit = 10.0
        swap_min_order_limit = 5.0
        incremental_lookback_hours = 0
        checkpoint_hours = 0
        is_day_period = False
        leverage = 1.0
        from core.model.rebalance_mode import RebalanceMode
        rebalance_mode = RebalanceMode.init({'mode': 'RebAlways', 'params': {}})

        def get_snapshot_folder(self):
            return snap_dir

    px_idx = idx
    spot_px = pd.DataFrame({'BTCUSDT': [100.0]*5, 'ETHUSDT': [50.0]*5}, index=px_idx)
    swap_px = pd.DataFrame({'BTCUSDT': [100.0]*5, 'SOLUSDT': [20.0]*5}, index=px_idx)
    market_inputs = {
        'pivots': {
            'spot': {'open': spot_px, 'close': spot_px, 'vwap1m': spot_px, 'funding_rate': spot_px * 0.0},
            'swap': {'open': swap_px, 'close': swap_px, 'vwap1m': swap_px, 'funding_rate': swap_px * 0.0},
        },
        'units': {
            'spot': pd.DataFrame({'symbol': ['BTCUSDT', 'ETHUSDT'], 'lot_size': [0.001, 0.001]}),
            'swap': pd.DataFrame({'symbol': ['BTCUSDT', 'SOLUSDT'], 'lot_size': [0.001, 0.001]}),
        },
    }
    res_df = eq_mod.calc_equity(
        _FakeConf(), datetime(2026, 1, 1, 5, 0), account_name='acct', ratio_folder=ratio_dir, market_inputs=market_inputs, state_name='state0'
    )
    pd.testing.assert_series_equal(res_df['symbol_long_num'], expected_long.reset_index(drop=True), check_names=False)
    pd.testing.assert_series_equal(res_df['symbol_short_num'], expected_short.reset_index(drop=True), check_names=False)

    # Also check create_ratio_snapshot parity with explicit 3rd reindex
    r_old = pd.DataFrame({'ETHUSDT': [0.1, 0.2]}, index=idx[:2])
    r_old.to_pickle(snap_dir / 'ratio_spot.pkl')
    r_new = pd.DataFrame({'BTCUSDT': [0.3, 0.4, 0.5]}, index=idx[2:])
    out_snap = eq_mod.create_ratio_snapshot(_FakeConf(), r_new, 'spot')
    final_cols = sorted(set(r_old.columns) | set(r_new.columns))
    oracle_snap = pd.concat([r_old.reindex(columns=final_cols, fill_value=0), r_new.reindex(columns=final_cols, fill_value=0)], sort=False).reindex(columns=final_cols, fill_value=0)
    pd.testing.assert_frame_equal(out_snap, oracle_snap, check_exact=True)


def test_stage6_parallel_process_equities_and_async_disk_writer_parity_and_error_propagation(tmp_path):
    from core.utils.async_disk import AsyncDiskWriter
    from core.model.backtest_config import MultiEquityBacktestConfig, BacktestConfig

    times = pd.date_range('2026-01-01 00:00:00+00:00', periods=10, freq='1h')

    def _build_me(sub_root: Path):
        sub_root.mkdir(parents=True, exist_ok=True)
        confs = []
        for i in range(6):
            r_dir = sub_root / f'S{i+1}' / 'result'
            s_dir = sub_root / f'S{i+1}' / 'snap'
            r_dir.mkdir(parents=True)
            s_dir.mkdir(parents=True)
            eq_df = pd.DataFrame({
                'candle_begin_time': times,
                'equity': 10000.0 + np.arange(10) * (i + 1) * 10.0,
            })
            eq_df.to_csv(r_dir / '资金曲线.csv', encoding='utf-8-sig')
            pd.DataFrame({'BTCUSDT': np.full(10, 0.1 * (i + 1))}, index=times).to_pickle(s_dir / 'ratio_spot.pkl')
            pd.DataFrame({'BTCUSDT': np.full(10, -0.1 * (i + 1))}, index=times).to_pickle(s_dir / 'ratio_swap.pkl')
            c = SimpleNamespace(
                name=f'S{i+1}',
                unified_time='2017-01-01',
                get_final_equity_path=lambda p=r_dir / '资金曲线.csv': p,
                get_snapshot_folder=lambda p=s_dir: p,
                get_result_folder=lambda p=r_dir: p,
            )
            confs.append(c)

        me = object.__new__(MultiEquityBacktestConfig)
        me.strategy = SimpleNamespace(hold_period='1H', factor_list=[], factor_columns=[])
        me.factory = SimpleNamespace(config_list=confs, result_folder=sub_root / 'iter_res')
        me.factory.result_folder.mkdir(parents=True, exist_ok=True)
        me.equity_dfs = []
        me.ratio_dfs = []
        me.start_time = None
        me.end_time = None
        return me

    me_sync = _build_me(tmp_path / 'sync')
    me_async = _build_me(tmp_path / 'async')

    from core.model import backtest_config as bc_mod
    orig_get_fp = bc_mod.get_file_path
    def _mock_get_fp(*parts, **kw):
        if Path(parts[0]).is_absolute():
            out = Path(parts[0]).joinpath(*parts[1:])
            out.parent.mkdir(parents=True, exist_ok=True)
            return out
        return orig_get_fp(*parts, **kw)

    try:
        bc_mod.get_file_path = _mock_get_fp
        run_t = datetime(2026, 1, 1, 18, 0)
        me_sync.process_equities(run_t, hour_offset='0m')
        with AsyncDiskWriter() as writer:
            me_async.process_equities(run_t, hour_offset='0m', async_writer=writer)
    finally:
        bc_mod.get_file_path = orig_get_fp

    for a, b in zip(me_sync.equity_dfs, me_async.equity_dfs):
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    for (sp_a, sw_a), (sp_b, sw_b) in zip(me_sync.ratio_dfs, me_async.ratio_dfs):
        pd.testing.assert_frame_equal(sp_a, sp_b, check_exact=True)
        pd.testing.assert_frame_equal(sw_a, sw_b, check_exact=True)
    for i in range(6):
        p_sync = pd.read_pickle(tmp_path / 'sync' / 'iter_res' / f'S{i+1}' / 'equity_df.pkl')
        p_async = pd.read_pickle(tmp_path / 'async' / 'iter_res' / f'S{i+1}' / 'equity_df.pkl')
        pd.testing.assert_frame_equal(p_sync, p_async, check_exact=True)

    # Error propagation in AsyncDiskWriter.flush()
    w = AsyncDiskWriter()
    w.submit(lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        w.flush()
    w.close()


def test_stage6_run_backtest_multi_starts_preload_only_after_pool_submits(tmp_path, monkeypatch):
    from core import backtest as bt
    from tools import local_risk_model as lrm

    state_path = tmp_path / 'model' / 'state.pkl'
    state_path.parent.mkdir(parents=True)
    pd.to_pickle({'ok': True}, state_path)

    events = []

    class _FakeFuture:
        def result(self):
            return pd.DataFrame({'candle_begin_time': [pd.Timestamp('2026-01-01', tz='UTC')]})

    class _FakePool:
        def __init__(self, *a, **k):
            events.append('pool_enter')

        def __enter__(self):
            return self

        def __exit__(self, *a):
            events.append('pool_exit')

        def submit(self, fn, *a, **k):
            events.append('submit')
            return _FakeFuture()

    monkeypatch.setattr(bt, 'ProcessPoolExecutor', _FakePool)
    monkeypatch.setattr(bt, 'as_completed', lambda fs: list(fs))
    monkeypatch.setattr(bt, 'apply_bstocks_black_list', lambda *a: None)
    monkeypatch.setattr(bt, 'mem_save_exe', lambda *a, **k: None)
    monkeypatch.setattr(bt, 'get_snapshot_time', lambda *a: '2026-01-01')
    monkeypatch.setattr(bt, 'set_snapshot_time', lambda *a: None)
    monkeypatch.setattr(bt, '_bmac_market_file_sig', lambda *a: None)
    monkeypatch.setattr(lrm, 'start_model_state_preload', lambda p: events.append(('preload', Path(p))))

    import config
    monkeypatch.setattr(config, 'shared_factor_pipeline', False, raising=False)
    monkeypatch.setattr(config, 'backtest_job_num', 2, raising=False)

    conf = SimpleNamespace(name='S1', strategy_list=[1], is_day_period=False, save=lambda: None, get_fullname=lambda: 'S1')
    factory = SimpleNamespace(
        result_folder=tmp_path / 'iter',
        config_list=[conf],
        generate_all_factor_config=lambda: conf,
    )
    with bt.step6_market_handoff(model_state_preload_path=state_path):
        bt.run_backtest_multi(factory, datetime(2026, 1, 1, 12, 25))

    # Preload must occur strictly AFTER all Step 6 worker submits (post-fork), BEFORE pool exit
    submits = [i for i, e in enumerate(events) if e == 'submit']
    preloads = [i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == 'preload']
    assert submits and preloads
    assert preloads[0] > max(submits)


def test_stage7_deferred_atomic_save_overlaps_compute_and_commits_before_warmup_complete(tmp_path, monkeypatch):
    from tools import local_risk_model as lrm
    from tools.robust40 import server

    snap_dir = tmp_path / 'data' / 'snapshot' / 'acct_s'
    (snap_dir / 'model').mkdir(parents=True)
    (snap_dir / 'sections').mkdir(parents=True)
    state_path = snap_dir / 'model' / 'state.pkl'
    pd.to_pickle({'v': 'old'}, state_path)
    pd.to_pickle({'old': 1}, snap_dir / 'model' / 'revision_inputs.pkl')
    pd.to_pickle({'old': 2}, snap_dir / 'model' / 'revision_inputs.pending.pkl')
    pd.to_pickle({'cutoff': pd.Timestamp('2026-01-01 00:25:00')}, snap_dir / 'sections' / 's1.pkl')
    pd.to_pickle({
        'account': 'acct', 'settings': {}, 'cutoff': pd.Timestamp('2026-01-01 00:25:00'),
        'origin': '2026-01-01 00:25:00', 'checkpoint_version': 1, 'complete': False,
        'sections': ['sections/s1.pkl'], 'history_trim_date': None,
    }, snap_dir / 'warmup.pkl')

    pickle_started = threading.Event()
    allow_pickle_finish = threading.Event()
    orig_to_pickle = pd.to_pickle

    def _blocking_to_pickle(obj, path, *a, **k):
        if Path(path).parent == state_path.parent and '.tmp' in Path(path).name:
            pickle_started.set()
            assert allow_pickle_finish.wait(timeout=3.0)
        return orig_to_pickle(obj, path, *a, **k)

    monkeypatch.setattr(pd, 'to_pickle', _blocking_to_pickle)

    lock_cm = lrm.lock(state_path)
    lock_cm.__enter__()
    pending = lrm.DeferredAtomicSave(state_path, {'v': 'new'}, lock_cm=lock_cm)

    # Background thread is writing .tmp while main thread "computes" concurrently
    assert pickle_started.wait(timeout=2.0)
    assert pd.read_pickle(state_path) == {'v': 'old'}
    with pytest.raises(FileExistsError):
        with lrm.lock(state_path):
            pass

    allow_pickle_finish.set()

    dummy_strategy = SimpleNamespace(
        params={'runtime': {'server_init': True, 'state_path': str(state_path), 'replaying': False}},
        _robust40_result={'parent': pd.DataFrame({'x': [1.0]}, index=[pd.Timestamp('2026-01-01 01:25:00')])},
        _robust40_pending_save=pending,
    )
    server.finish_live(dummy_strategy)

    assert pd.read_pickle(state_path) == {'v': 'new'}
    assert pd.read_pickle(snap_dir / 'warmup.pkl')['complete'] is True
    assert list(state_path.parent.glob('*.tmp')) == []
    # Lock released after commit
    with lrm.lock(state_path):
        pass


def test_stage7_deferred_save_abort_and_write_error_preserve_old_state_and_release_lock(tmp_path, monkeypatch):
    from tools import local_risk_model as lrm
    from tools.robust40 import server
    from core import real_trading as rt

    snap_dir = tmp_path / 'data' / 'snapshot' / 'acct_s'
    (snap_dir / 'model').mkdir(parents=True)
    state_path = snap_dir / 'model' / 'state.pkl'
    pd.to_pickle({'v': 'old'}, state_path)
    pd.to_pickle({'old': 1}, snap_dir / 'model' / 'revision_inputs.pkl')
    pd.to_pickle({'new_pending': 2}, snap_dir / 'model' / 'revision_inputs.pending.pkl')
    pd.to_pickle({
        'account': 'acct', 'settings': {}, 'cutoff': pd.Timestamp('2026-01-01 00:25:00'),
        'origin': '2026-01-01 00:25:00', 'checkpoint_version': 1, 'complete': False,
        'sections': [], 'history_trim_date': None,
    }, snap_dir / 'warmup.pkl')

    # Scenario A: downstream calc_ratios/validate_targets raises -> calc_by_account finally calls abort()
    lock_a = lrm.lock(state_path)
    lock_a.__enter__()
    pending_a = lrm.DeferredAtomicSave(state_path, {'v': 'should_abort'}, lock_cm=lock_a)

    acct = SimpleNamespace(name='acct', hour_offset='25m', hour_offset_minute=25, bn=SimpleNamespace(fetch_market_info=lambda *a: None))
    strategy = SimpleNamespace(params={'runtime': {}}, core_funcs={}, _robust40_pending_save=pending_a)
    me_conf = SimpleNamespace(
        factory=SimpleNamespace(config_list=[]),
        strategy=strategy,
        backtest_strategies=lambda *a: None,
        process_equities=lambda *a, **k: None,
        calc_ratios=lambda *a, **k: (_ for _ in ()).throw(ValueError('validate_targets failed')),
    )
    monkeypatch.setattr(rt, 'refresh_diff_time', lambda: None)
    monkeypatch.setattr(rt, 'snapshot_bmac_exginfo', lambda: None)
    monkeypatch.setattr(rt, 'save_position_snapshot', lambda *a: None)
    monkeypatch.setattr(rt, 'get_folder_path', lambda *p, **k: tmp_path.joinpath(*p))
    monkeypatch.setattr(rt, 'lookback_save_interval_hours', 0, raising=False)

    with pytest.raises(ValueError, match='validate_targets failed'):
        rt.calc_by_account(acct, me_conf, datetime(2026, 1, 1, 12, 25))

    assert pd.read_pickle(state_path) == {'v': 'old'}
    assert list(state_path.parent.glob('*.tmp')) == []
    with lrm.lock(state_path):
        pass

    # Scenario B: background pd.to_pickle raises OSError -> finish_live fails before touching revision_inputs or warmup.pkl
    orig_to_pickle = pd.to_pickle
    monkeypatch.setattr(pd, 'to_pickle', lambda obj, path, *a, **k: (_ for _ in ()).throw(OSError('io error')) if '.tmp' in str(path) else orig_to_pickle(obj, path, *a, **k))

    lock_b = lrm.lock(state_path)
    lock_b.__enter__()
    pending_b = lrm.DeferredAtomicSave(state_path, {'v': 'broken'}, lock_cm=lock_b)
    dummy_strategy = SimpleNamespace(
        params={'runtime': {'server_init': True, 'state_path': str(state_path), 'replaying': False}},
        _robust40_result={'parent': pd.DataFrame({'x': [1.0]}, index=[pd.Timestamp('2026-01-01 01:25:00')])},
        _robust40_pending_save=pending_b,
    )
    with pytest.raises(OSError, match='io error'):
        server.finish_live(dummy_strategy)

    assert pd.read_pickle(state_path) == {'v': 'old'}
    assert (snap_dir / 'model' / 'revision_inputs.pending.pkl').exists()
    assert pd.read_pickle(snap_dir / 'model' / 'revision_inputs.pkl') == {'old': 1}
    assert pd.read_pickle(snap_dir / 'warmup.pkl')['complete'] is False
    assert list(state_path.parent.glob('*.tmp')) == []
    with lrm.lock(state_path):
        pass


def test_warm_numba_cli_exits_zero():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/robust40/cli/server_robust40.py'), 'warm-numba'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'warm-numba' in result.stdout


def test_warm_numba_kernels_uses_int8_require_rebalance(monkeypatch):
    from tools.robust40.cli import server_robust40
    import positions.ModelRobust40Strategy as mrs

    seen_dtypes = []
    orig_sim = mrs.start_simulation

    def _spy_sim(*args, **kwargs):
        seen_dtypes.append(args[20].dtype)
        return orig_sim(*args, **kwargs)

    monkeypatch.setattr(mrs, 'start_simulation', _spy_sim)
    server_robust40.warm_numba_kernels()
    assert seen_dtypes and all(dt == np.int8 for dt in seen_dtypes)


def test_run_by_account_flushes_async_writer_before_save_final_select_results_and_simulate_calc_equity(tmp_path, monkeypatch):
    from core import real_trading as rt

    wrote_flag = {'csv': False, 'ratio': False}
    slow_started = threading.Event()
    result_dir = tmp_path / 'factory'
    result_dir.mkdir()

    def _fake_calc_ratios(async_writer=None):
        def _slow_csv():
            slow_started.set()
            time.sleep(0.05)
            wrote_flag['csv'] = True
        if async_writer is not None:
            async_writer.submit(_slow_csv)
        else:
            _slow_csv()
        return pd.DataFrame()

    idx = pd.to_datetime(['2026-01-01 12:25:00'])
    ratio = pd.DataFrame({'BTCUSDT': [0.1]}, index=idx)
    me_conf = SimpleNamespace(
        factory=SimpleNamespace(config_list=[], result_folder=result_dir),
        strategy=SimpleNamespace(params={}, core_funcs={}),
        backtest_strategies=lambda *a: None,
        process_equities=lambda *a, **k: None,
        calc_ratios=_fake_calc_ratios,
        agg_pos_ratio=lambda r: (ratio, ratio),
        apply_position_limits=lambda s, w: (s, w),
    )
    acct = SimpleNamespace(
        name='acct',
        hour_offset='25m',
        hour_offset_minute=25,
        leverage=1.0,
        spot_equity=1000.0,
        swap_equity=0.0,
        bn=SimpleNamespace(
            fetch_market_info=lambda *a: None,
            cancel_all_swap_orders=lambda: None,
            cancel_all_spot_orders=lambda: None,
            collect_asset=lambda: None,
            adjust_base_margin=lambda: None,
        ),
        calc_order_amount=lambda df: pd.DataFrame(),
        proceed_spot_order=lambda *a, **k: ([], pd.DataFrame()),
        proceed_swap_order=lambda *a, **k: ([], pd.DataFrame()),
    )

    monkeypatch.setattr(rt, 'refresh_diff_time', lambda: None)
    monkeypatch.setattr(rt, 'snapshot_bmac_exginfo', lambda: None)
    monkeypatch.setattr(rt, 'save_position_snapshot', lambda *a: None)
    monkeypatch.setattr(rt, 'get_folder_path', lambda *p, **k: tmp_path.joinpath(*p))
    monkeypatch.setattr(rt, 'runtime_folder', tmp_path / 'rt', raising=False)
    (tmp_path / 'rt').mkdir(exist_ok=True)
    monkeypatch.setattr(rt, 'lookback_save_interval_hours', 0, raising=False)
    monkeypatch.setattr(rt, 'calc_target_position', lambda *a, **k: pd.DataFrame({'目标持仓量': [0.0]}))
    monkeypatch.setattr(rt, 'save_position_results', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'save_symbol_order', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'save_order_history', lambda *a, **k: None)

    # Mode 1: dry_run -> save_final_select_results must observe csv write finished
    observed_csv = []
    monkeypatch.setattr(rt, 'is_debug', False, raising=False)
    monkeypatch.setattr(rt, 'is_simulate', 'dry_run', raising=False)
    monkeypatch.setattr(rt, 'save_final_select_results', lambda *a, **k: observed_csv.append(wrote_flag['csv']))
    rt.run_by_account(acct, me_conf, datetime(2026, 1, 1, 12, 25))
    assert observed_csv == [True]

    # Mode 2: debug + simulate -> calc_equity must observe queued ratio pickle write finished
    observed_ratio = []
    orig_to_pickle = pd.DataFrame.to_pickle
    def _slow_to_pickle(self, path, *a, **k):
        if Path(path).name in ('df_spot_ratio.pkl', 'df_swap_ratio.pkl'):
            time.sleep(0.05)
            wrote_flag['ratio'] = True
        return orig_to_pickle(self, path, *a, **k)

    monkeypatch.setattr(pd.DataFrame, 'to_pickle', _slow_to_pickle)
    monkeypatch.setattr(rt, 'is_debug', True, raising=False)
    monkeypatch.setattr(rt, 'is_simulate', 'simulate', raising=False)
    monkeypatch.setattr(rt, 'calc_equity', lambda *a, **k: observed_ratio.append(wrote_flag['ratio']) or pd.DataFrame())
    monkeypatch.setattr(rt, 'save_performance_df_csv', lambda *a, **k: None)
    rt.run_by_account(acct, me_conf, datetime(2026, 1, 1, 12, 25))
    assert observed_ratio == [True]


def test_stage5_wait_ready_telemetry_anchors_at_ready_without_double_counting(tmp_path, monkeypatch):
    import config
    from tools.robust40 import handoff, server, telemetry
    from tools.robust40.cli import server_robust40

    acct = tmp_path / 'acct_tel.py'
    acct.write_text("account_config={'hour_offset':'25m'}\nstrategy_name='s'\n", encoding='utf-8')
    values = {'account': {'hour_offset': '25m'}, 'strategy_name': 's', 'strategy_pool': [], 'precompute_factor_configs': {}}
    dest = server.location(tmp_path, acct, values)
    (dest / 'model').mkdir(parents=True)
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')

    warmup_meta = {
        'account': 'acct_tel', 'settings': values, 'cutoff': end, 'origin': '2026-01-01 00:25:00',
        'checkpoint_version': 1, 'complete': True, 'sections': [], 'history_trim_date': None,
    }
    pd.to_pickle(warmup_meta, dest / 'warmup.pkl')
    pd.to_pickle({'raw/spot/BTCUSDT': pd.DataFrame({'close': [1.0]}, index=[times[0]])}, dest / 'model/revision_inputs.pkl')

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    spot_started = threading.Event()
    orig_read_arrow_tail = handoff.read_arrow_tail

    def _slow_swap_read(root, kind, *args, **kwargs):
        if kind == 'spot':
            spot_started.set()
        if kind == 'swap':
            time.sleep(0.12)
        return orig_read_arrow_tail(root, kind, *args, **kwargs)

    monkeypatch.setattr(handoff, 'read_arrow_tail', _slow_swap_read)
    monkeypatch.setattr(server_robust40, 'ROOT', tmp_path)
    monkeypatch.setattr('tools.robust40.initialization_config.DATA_READY_WAIT_SECONDS', time.time() - stamp + 5.0)
    monkeypatch.setattr(config, 'bmac_data_path', bmac)
    monkeypatch.setattr(config, 'strategy_config', {'params': {'runtime': {}}}, raising=False)
    monkeypatch.setattr(server, 'settings', lambda a: (values, {'minute': 25}))
    monkeypatch.setattr(server, 'inspect', lambda *a, **k: ('ready', 'ok'))
    monkeypatch.setattr(server, 'check_dependencies', lambda *a, **k: None)
    monkeypatch.setattr(server, 'resume_capacity', lambda *a, **k: 100)
    monkeypatch.setattr(handoff, 'first_revision', lambda *a, **k: None)
    import core.account_exec as ae
    monkeypatch.setattr(ae, 'main', lambda: run_time)

    emitted = []
    monkeypatch.setattr(telemetry, '_write', emitted.append)

    def _release_swap_later():
        assert spot_started.wait(timeout=3.0)
        time.sleep(0.16)
        (folder / f'swap_dict_{stamp}.ready').write_text('')
        (folder / f'market_pivot_swap_{stamp}.ready').write_text('')

    t = threading.Thread(target=_release_swap_later)
    t.start()
    rc = server_robust40.trade(acct, stamp, wait_ready=True)
    t.join()
    assert rc in (0, None)

    end_payload = json.loads(emitted[-1].split('MODEL_REPLAY ', 1)[1])
    # Must NOT double-count swap arrow read (~0.12s, definitely < 0.22s; doubled would be >= 0.24s)
    assert 0.10 <= end_payload['revision_arrow_swap_s'] < 0.22
    # total_s must anchor at swap_ready_at (so it includes the 0.12s post-ready swap read, while excluding the 0.16s wait)
    assert end_payload['wait_ready_s'] >= 0.14
    assert 0.11 <= end_payload['input_read_s'] < 0.23
    assert end_payload['total_s'] >= end_payload['input_read_s']
    assert end_payload['total_s'] < end_payload['wait_ready_s'] + end_payload['input_read_s'] - 0.05
    assert end_payload['prepare_s'] >= end_payload['input_read_s']
    assert end_payload['prewarm_s'] < 0.50


def test_stage5_wait_ready_corrupt_or_empty_pickle_falls_back_to_trade_exit75(tmp_path, monkeypatch):
    import config
    from tools.robust40 import server
    from tools.robust40.cli import server_robust40
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT

    acct = tmp_path / 'acct_bad.py'
    acct.write_text("account_config={'hour_offset':'25m'}\nstrategy_name='s'\n", encoding='utf-8')
    values = {'account': {'hour_offset': '25m'}, 'strategy_name': 's', 'strategy_pool': [], 'precompute_factor_configs': {}}
    dest = server.location(tmp_path, acct, values)
    (dest / 'model').mkdir(parents=True)
    stamp = int(datetime(2026, 1, 1, 12, 25).timestamp())

    monkeypatch.setattr(server_robust40, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'bmac_data_path', tmp_path / 'bmac')
    monkeypatch.setattr(server, 'settings', lambda a: (values, {'minute': 25}))
    monkeypatch.setattr(server, 'check_dependencies', lambda *a, **k: None)
    monkeypatch.setattr(server, 'resume_capacity', lambda *a, **k: 100)
    monkeypatch.setattr(server, 'inspect', lambda *a, **k: ('rebuild', 'corrupt revision_inputs'))

    # Case 1: corrupt bytes in revision_inputs.pkl
    pd.to_pickle({'complete': True}, dest / 'warmup.pkl')
    (dest / 'model/revision_inputs.pkl').write_bytes(b'corrupt-pickle-bytes')
    assert server_robust40.trade(acct, stamp, wait_ready=True) == REBUILD_REQUIRED_EXIT

    # Case 2: empty dict in revision_inputs.pkl (min() ValueError)
    pd.to_pickle({}, dest / 'model/revision_inputs.pkl')
    assert server_robust40.trade(acct, stamp, wait_ready=True) == REBUILD_REQUIRED_EXIT


def test_stage6_factor_hub_skips_repeated_setlocale_under_parallel_equities(tmp_path, monkeypatch):
    import locale
    from core.utils.factor_hub import FactorHub
    from core.model import backtest_config as bc_mod
    from core.model.backtest_config import MultiEquityBacktestConfig

    FactorHub.get_by_name('Bias')
    setlocale_calls = []
    orig_setlocale = locale.setlocale
    monkeypatch.setattr(locale, 'setlocale', lambda *a, **k: setlocale_calls.append(a) or orig_setlocale(*a, **k))

    times = pd.date_range('2026-01-01 00:00:00+00:00', periods=12, freq='1h')
    confs = []
    for i in range(4):
        r_dir = tmp_path / f'S{i+1}' / 'result'
        s_dir = tmp_path / f'S{i+1}' / 'snap'
        r_dir.mkdir(parents=True)
        s_dir.mkdir(parents=True)
        pd.DataFrame({'candle_begin_time': times, 'equity': 10000.0 + np.arange(12) * 5.0}).to_csv(
            r_dir / '资金曲线.csv', encoding='utf-8-sig'
        )
        pd.DataFrame({'BTCUSDT': np.full(12, 0.1)}, index=times).to_pickle(s_dir / 'ratio_spot.pkl')
        pd.DataFrame({'BTCUSDT': np.full(12, -0.1)}, index=times).to_pickle(s_dir / 'ratio_swap.pkl')
        confs.append(SimpleNamespace(
            name=f'S{i+1}',
            unified_time='2017-01-01',
            get_final_equity_path=lambda p=r_dir / '资金曲线.csv': p,
            get_snapshot_folder=lambda p=s_dir: p,
            get_result_folder=lambda p=r_dir: p,
        ))

    me = object.__new__(MultiEquityBacktestConfig)
    me.strategy = SimpleNamespace(
        hold_period='1H',
        factor_list=[SimpleNamespace(name='Bias', param=3)],
        factor_columns=['Bias_3'],
    )
    me.factory = SimpleNamespace(config_list=confs, result_folder=tmp_path / 'iter')
    me.factory.result_folder.mkdir(parents=True, exist_ok=True)
    me.equity_dfs = []
    me.ratio_dfs = []
    me.start_time = None
    me.end_time = None

    orig_get_fp = bc_mod.get_file_path
    monkeypatch.setattr(
        bc_mod, 'get_file_path',
        lambda *parts, **kw: (Path(parts[0]).joinpath(*parts[1:]).parent.mkdir(parents=True, exist_ok=True) or Path(parts[0]).joinpath(*parts[1:]))
        if Path(parts[0]).is_absolute() else orig_get_fp(*parts, **kw),
    )
    me.process_equities(datetime(2026, 1, 1, 20, 0), hour_offset='0m')
    assert setlocale_calls == []





