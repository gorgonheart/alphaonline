"""TDD + independent oracle tests for Phase 3 (P3-1): cross-strategy read-only Pivot sharing in Step 6."""
from types import SimpleNamespace
import os
import time
import numpy as np
import pandas as pd
import pytest


def _build_synthetic_market(minute=25, periods=60):
    index = pd.date_range(f'2026-01-01 00:{minute:02}', periods=periods, freq='h', tz='UTC')
    syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    market = {}
    for kind, base_offset in (('spot', 10.0), ('swap', 10.5)):
        rng = np.arange(periods, dtype=np.float64)
        open_df = pd.DataFrame({s: base_offset + i * 2.0 + rng * 0.05 for i, s in enumerate(syms)}, index=index)
        close_df = open_df + 0.03
        vwap_df = open_df + 0.01
        fr_df = pd.DataFrame({s: np.full(periods, 0.0001 * (i + 1)) for i, s in enumerate(syms)}, index=index)
        market[kind] = {'open': open_df, 'close': close_df, 'vwap1m': vwap_df, 'funding_rate': fr_df}
    units = {
        'spot': pd.DataFrame({'symbol': syms, 'lot_size': [0.01, 0.01, 0.1]}),
        'swap': pd.DataFrame({'symbol': syms, 'lot_size': [0.01, 0.01, 0.1]}),
    }
    return index, market, units


def _write_bmac_files(root, offset, market, units):
    offset_dir = root / 'bmac' / offset
    offset_dir.mkdir(parents=True, exist_ok=True)
    exg_dir = root / 'exginfo'
    exg_dir.mkdir(parents=True, exist_ok=True)
    for kind in ('spot', 'swap'):
        pd.to_pickle(market[kind], offset_dir / f'market_pivot_{kind}_2026.pkl')
        units[kind].to_pickle(exg_dir / f'exginfo_{kind}.pkl')
    return root / 'bmac', exg_dir


def _make_conf(folder, name, with_timing=False, lookback=3):
    from core.model.rebalance_mode import RebalanceMode
    from core.model.timing_signal import TimingSignal

    folder.mkdir(parents=True, exist_ok=True)
    timing = None
    if with_timing:
        timing = TimingSignal.__new__(TimingSignal)
        timing.name = 'mock_timing'
        timing.impl_flags = {'dynamic_leverage': True}
        timing.get_dynamic_leverage = lambda e: pd.Series(np.where(np.arange(len(e)) % 4 == 0, 0.5, 1.0))
    return SimpleNamespace(
        name=name,
        timing=timing,
        strategy_list=[SimpleNamespace(name='sub0')],
        get_fullname=lambda **kw: name,
        get_result_folder=lambda: folder,
        get_snapshot_folder=lambda: folder,
        incremental_lookback_hours=lookback,
        checkpoint_hours=25,
        initial_usdt=10000.0,
        is_day_period=False,
        leverage=1.0,
        margin_rate=0.05,
        spot_c_rate=0.002,
        swap_c_rate=0.0006,
        spot_min_order_limit=20.0,
        swap_min_order_limit=20.0,
        rebalance_mode=RebalanceMode.init(None),
    )


def _assert_state_exact(actual, expected):
    from core.simulator import Simulator

    if isinstance(actual, dict):
        assert actual.keys() == expected.keys()
        for key in actual:
            _assert_state_exact(actual[key], expected[key])
    elif isinstance(actual, (tuple, list)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for a, b in zip(actual, expected):
            _assert_state_exact(a, b)
    elif isinstance(actual, pd.Index):
        pd.testing.assert_index_equal(actual, expected, exact=True)
    elif isinstance(actual, pd.Series):
        pd.testing.assert_series_equal(actual, expected, check_exact=True)
    elif isinstance(actual, Simulator):
        assert type(actual) is type(expected)
        _assert_state_exact(vars(actual), vars(expected))
    else:
        np.testing.assert_equal(actual, expected)


def test_p3_step6_worker_reuses_pivot_and_matches_uncached_oracle(tmp_path, monkeypatch):
    """Multiple strategies (with/without timing & symbol expansion) match uncached oracle bit-for-bit
    while reading market_pivot only once per worker process and never mutating cached DataFrames."""
    import config
    from core import backtest, equity
    from core.utils import datatools

    index, market, units = _build_synthetic_market(minute=25, periods=60)
    bmac_dir, exg_dir = _write_bmac_files(tmp_path, '25m', market, units)
    monkeypatch.setattr(config, 'bmac_data_path', bmac_dir)
    monkeypatch.setattr(config, 'bmac_exginfo_path', exg_dir)
    monkeypatch.setattr(datatools, 'bmac_data_path', bmac_dir)
    monkeypatch.setattr(datatools, 'bmac_exginfo_path', exg_dir)
    cache_dir = tmp_path / 'local_cache'
    cache_dir.mkdir()
    monkeypatch.setattr(datatools, 'get_folder_path', lambda *a, **kw: cache_dir)
    monkeypatch.setattr(backtest, 'save_performance_df_csv', lambda *a, **kw: None)

    # Reset caches
    monkeypatch.setattr(backtest, '_STEP6_FORK_MARKET', None)
    monkeypatch.setattr(backtest, '_STEP6_WORKER_MARKET', None)

    # Snapshot originals to verify zero mutation
    orig_market = {
        k: {f: df.copy(deep=True) for f, df in d.items()}
        for k, d in market.items()
    }
    orig_units = {k: df.copy(deep=True) for k, df in units.items()}

    # Build 3 strategies: S1 (spot+swap subset), S2 (different subset + extra delisted symbol), S3 (with retiming)
    naive_idx = index.tz_localize(None)
    specs = [
        ('S1', False,
         pd.DataFrame({'BTCUSDT': 0.25, 'ETHUSDT': 0.0}, index=naive_idx),
         pd.DataFrame({'BTCUSDT': 0.25, 'SOLUSDT': -0.2}, index=naive_idx)),
        ('S2', False,
         pd.DataFrame({'ETHUSDT': 0.2, 'DELISTUSDT': 0.1}, index=naive_idx),
         pd.DataFrame({'SOLUSDT': -0.3, 'DELISTUSDT': 0.0}, index=naive_idx)),
        ('S3', True,
         pd.DataFrame({'BTCUSDT': 0.15, 'SOLUSDT': 0.15}, index=naive_idx),
         pd.DataFrame({'ETHUSDT': -0.25, 'SOLUSDT': 0.1}, index=naive_idx)),
    ]

    oracle_confs = [_make_conf(tmp_path / f'oracle_{n}', n, with_timing=t) for n, t, _, _ in specs]
    worker_confs = [_make_conf(tmp_path / f'worker_{n}', n, with_timing=t) for n, t, _, _ in specs]

    # Run 2 continuation rounds (stop=58, stop=60) so merge_state + snapshot_queue both execute!
    for stop in (58, 60):
        run_time = index[stop - 1] + pd.Timedelta(hours=1)
        for (_, _, sp, sw), oc, wc in zip(specs, oracle_confs, worker_confs):
            sp.iloc[:stop].to_pickle(oc.get_result_folder() / 'df_spot_ratio.pkl')
            sw.iloc[:stop].to_pickle(oc.get_result_folder() / 'df_swap_ratio.pkl')
            sp.iloc[:stop].to_pickle(wc.get_result_folder() / 'df_spot_ratio.pkl')
            sw.iloc[:stop].to_pickle(wc.get_result_folder() / 'df_swap_ratio.pkl')

        # 1. Uncached oracle
        oracle_results = []
        for (_, with_timing, _, _), oc in zip(specs, oracle_confs):
            df0 = equity.calc_equity(oc, run_time, state_name='state0')
            if with_timing:
                df0 = backtest.simu_timing(oc, run_time, account_df=df0)
            oracle_results.append(df0)

        # 2. Cached _step6_worker with spy on read_bmac_market_pivot
        reads = []
        real_read_pivot = datatools.read_bmac_market_pivot

        def spy_read_pivot(hour_offset, symbol_type):
            reads.append((hour_offset, symbol_type))
            return real_read_pivot(hour_offset, symbol_type)

        monkeypatch.setattr(datatools, 'read_bmac_market_pivot', spy_read_pivot)
        monkeypatch.setattr(equity, 'read_bmac_market_pivot', spy_read_pivot)

        worker_results = [backtest._step6_worker(wc, run_time) for wc in worker_confs]

        # On first round, pivot is read ONCE (spot + swap = 2 calls) for all 3 strategies!
        # On second round (same file sig), 0 reads!
        expected_reads = [('25m', 'spot'), ('25m', 'swap')] if stop == 58 else []
        assert reads == expected_reads, f'stop={stop}: expected {expected_reads}, got {reads}'

        for (name, with_timing, _, _), exp_df, act_df, oc, wc in zip(
            specs, oracle_results, worker_results, oracle_confs, worker_confs
        ):
            pd.testing.assert_frame_equal(act_df, exp_df, check_exact=True)
            state_files = ('lots_state0.pkl', 'lots_state1.pkl') if with_timing else ('lots_state0.pkl',)
            for fn in state_files:
                _assert_state_exact(
                    pd.read_pickle(wc.get_snapshot_folder() / fn),
                    pd.read_pickle(oc.get_snapshot_folder() / fn),
                )
            for fn in ('account_state0.pkl', 'ratio_spot.pkl', 'ratio_swap.pkl'):
                pd.testing.assert_frame_equal(
                    pd.read_pickle(wc.get_snapshot_folder() / fn),
                    pd.read_pickle(oc.get_snapshot_folder() / fn),
                    check_exact=True,
                )

    # Verify cached DataFrames were NEVER mutated in place
    cached_market = backtest._STEP6_WORKER_MARKET[1]
    for kind in ('spot', 'swap'):
        for field, orig_df in orig_market[kind].items():
            pd.testing.assert_frame_equal(cached_market['pivots'][kind][field], orig_df, check_exact=True)
        pd.testing.assert_frame_equal(cached_market['units'][kind], orig_units[kind], check_exact=True)


def test_p3_worker_cache_invalidates_when_pivot_file_updates(tmp_path, monkeypatch):
    """Updating a market_pivot pkl (mtime/size) must invalidate the worker cache immediately."""
    import config
    from core import backtest
    from core.utils import datatools

    index, market, units = _build_synthetic_market(minute=25, periods=20)
    bmac_dir, exg_dir = _write_bmac_files(tmp_path, '25m', market, units)
    monkeypatch.setattr(config, 'bmac_data_path', bmac_dir)
    monkeypatch.setattr(config, 'bmac_exginfo_path', exg_dir)
    monkeypatch.setattr(datatools, 'bmac_data_path', bmac_dir)
    monkeypatch.setattr(datatools, 'bmac_exginfo_path', exg_dir)
    cache_dir = tmp_path / 'local_cache'
    cache_dir.mkdir()
    monkeypatch.setattr(datatools, 'get_folder_path', lambda *a, **kw: cache_dir)
    monkeypatch.setattr(backtest, '_STEP6_FORK_MARKET', None)
    monkeypatch.setattr(backtest, '_STEP6_WORKER_MARKET', None)

    m1 = backtest._get_worker_step6_market_inputs('25m')
    m2 = backtest._get_worker_step6_market_inputs('25m')
    assert m1 is m2

    # Mutate spot pivot file on disk with a new column + bumped mtime
    updated_spot = {k: df.assign(NEWUSDT=99.0) for k, df in market['spot'].items()}
    spot_path = bmac_dir / '25m' / 'market_pivot_spot_2026.pkl'
    pd.to_pickle(updated_spot, spot_path)
    st = spot_path.stat()
    os.utime(spot_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    m3 = backtest._get_worker_step6_market_inputs('25m')
    assert m3 is not m1
    assert 'NEWUSDT' in m3['pivots']['spot']['close'].columns


def test_p3_fork_shared_market_respected_only_for_matching_offset(monkeypatch):
    """_STEP6_FORK_MARKET is used only when hour_offset matches; mismatched offset reloads."""
    from core import backtest

    sentinel = {'pivots': {'spot': {}, 'swap': {}}, 'units': {'spot': None, 'swap': None}}
    reloaded = {'pivots': {'spot': {'close': 1}, 'swap': {}}, 'units': {'spot': None, 'swap': None}}
    monkeypatch.setattr(backtest, '_STEP6_FORK_MARKET', ('25m', sentinel))
    monkeypatch.setattr(backtest, '_STEP6_WORKER_MARKET', None)
    monkeypatch.setattr(backtest, '_bmac_market_file_sig', lambda offset: ())
    monkeypatch.setattr(backtest, '_load_step6_market_inputs', lambda offset: reloaded)

    assert backtest._get_worker_step6_market_inputs('25m') is sentinel
    assert backtest._get_worker_step6_market_inputs('35m') is reloaded
