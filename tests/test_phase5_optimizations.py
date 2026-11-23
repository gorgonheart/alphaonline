"""TDD + independent Python oracle tests for Phase 5 (Sub-project 2A) optimizations.

Covers:
- P5-D1: agg_pos_ratio column-contiguous NumPy accumulation
- P5-B2: parallel restore_child in restore_snapshot
- P5-A1: 3-way parallel read_inputs & multi-unit zero-copy read_arrow_tail
- P5-C1: 3-thread signal_block sections & live _polars_threads budget
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _oracle_agg_pos_ratio(ratio_dfs, pos_ratio):
    """Independent reference replicating legacy MultiEquityConfig.agg_pos_ratio."""
    df_spot_ratio_sum = pd.DataFrame()
    df_swap_ratio_sum = pd.DataFrame()
    for idx, (df_spot_ratio, df_swap_ratio) in enumerate(ratio_dfs):
        group_ratio = pos_ratio[idx]
        spot_ratio = df_spot_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)
        swap_ratio = df_swap_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)
        df_spot_ratio_sum = df_spot_ratio_sum.add(spot_ratio, fill_value=0)
        df_swap_ratio_sum = df_swap_ratio_sum.add(swap_ratio, fill_value=0)
    return df_spot_ratio_sum, df_swap_ratio_sum


def _make_me_conf(ratio_dfs):
    from core.model.backtest_config import MultiEquityBacktestConfig

    me = object.__new__(MultiEquityBacktestConfig)
    me.ratio_dfs = ratio_dfs
    return me


def test_p5_d1_agg_pos_ratio_matches_oracle_across_shapes_and_edges():
    rng = np.random.default_rng(20261123)
    full_idx = pd.date_range('2026-01-01 00:25:00', periods=40, freq='1h', tz='UTC')
    # Subset and intentionally non-monotonic order in pos_ratio.index
    sub_idx = full_idx[[35, 10, 0, 22, 15, 39, 3]]
    all_cols = [f'SYM{i:02d}-USDT' for i in range(25)]

    # 1. 14 strategies with sparse/overlapping columns
    pos_ratio = pd.DataFrame(rng.uniform(0.0, 0.15, (len(sub_idx), 14)), index=sub_idx, columns=range(14))
    ratio_dfs = []
    for k in range(14):
        c_spot = list(rng.choice(all_cols, size=10, replace=False))
        c_swap = list(rng.choice(all_cols, size=12, replace=False))
        sp = pd.DataFrame(rng.uniform(0.0, 0.2, (len(full_idx), len(c_spot))), index=full_idx, columns=c_spot)
        sw = pd.DataFrame(rng.uniform(-0.2, 0.2, (len(full_idx), len(c_swap))), index=full_idx, columns=c_swap)
        ratio_dfs.append((sp, sw))

    exp_spot, exp_swap = _oracle_agg_pos_ratio(ratio_dfs, pos_ratio)
    act_spot, act_swap = _make_me_conf(ratio_dfs).agg_pos_ratio(pos_ratio)
    pd.testing.assert_frame_equal(act_spot, exp_spot, check_exact=False, rtol=0.0, atol=1e-15)
    pd.testing.assert_frame_equal(act_swap, exp_swap, check_exact=False, rtol=0.0, atol=1e-15)
    assert list(act_spot.columns) == list(exp_spot.columns)
    assert list(act_swap.columns) == list(exp_swap.columns)

    # 2. Single strategy with unsorted columns must preserve exact column order
    single_pos = pd.DataFrame({0: [0.5, 1.0]}, index=full_idx[:2])
    single_sp = pd.DataFrame({'Z-USDT': [1, 2], 'A-USDT': [3, 4], 'M-USDT': [5, 6]}, index=full_idx[:2])
    single_sw = pd.DataFrame({'B-USDT': [-1.0, 0.0]}, index=full_idx[:2])
    exp_s1, exp_w1 = _oracle_agg_pos_ratio([(single_sp, single_sw)], single_pos)
    act_s1, act_w1 = _make_me_conf([(single_sp, single_sw)]).agg_pos_ratio(single_pos)
    pd.testing.assert_frame_equal(act_s1, exp_s1, check_exact=True)
    pd.testing.assert_frame_equal(act_w1, exp_w1, check_exact=True)

    # 3. Empty ratio_dfs
    empty_s, empty_w = _make_me_conf([]).agg_pos_ratio(single_pos)
    assert empty_s.empty and empty_w.empty

    # 4. Pure swap (0-column spot DataFrames)
    pure_swap_dfs = [
        (pd.DataFrame(index=full_idx[:3]), pd.DataFrame({'BTC-USDT': [0.1, 0.2, 0.3]}, index=full_idx[:3])),
        (pd.DataFrame(index=full_idx[:3]), pd.DataFrame({'ETH-USDT': [-0.1, 0.0, 0.2]}, index=full_idx[:3])),
    ]
    pure_pos = pd.DataFrame({0: [0.4, 0.5, 0.6], 1: [0.6, 0.5, 0.4]}, index=full_idx[:3])
    exp_ps, exp_pw = _oracle_agg_pos_ratio(pure_swap_dfs, pure_pos)
    act_ps, act_pw = _make_me_conf(pure_swap_dfs).agg_pos_ratio(pure_pos)
    pd.testing.assert_frame_equal(act_ps, exp_ps, check_exact=True)
    pd.testing.assert_frame_equal(act_pw, exp_pw, check_exact=True)


def test_p5_d1_agg_pos_ratio_nan_and_missing_index_semantics():
    idx = pd.date_range('2026-02-01 00:25:00', periods=4, freq='1h', tz='UTC')
    # Strategy 0 has NaN at rows 0,1 in A and finite at rows 2,3; col B only in Strategy 0
    s0 = pd.DataFrame({'A': [np.nan, np.nan, 1.0, 0.5], 'B': [2.0, 3.0, 4.0, 5.0]}, index=idx)
    # Strategy 1 lacks col A completely (tests NaN + missing(fill_value=0) stays NaN)
    s1 = pd.DataFrame({'C': [0.2, 0.3, 0.4, 0.5]}, index=idx)
    # Strategy 2 supplies finite 0.5 at row 0 (overrides NaN via fill_value=0) and NaN at row 1
    s2 = pd.DataFrame({'A': [0.5, np.nan, 0.25, 0.0]}, index=idx)
    ratio_dfs = [(s0, s0.copy()), (s1, s1.copy()), (s2, s2.copy())]
    # pos_ratio[0] has NaN at row 2 (B becomes NaN; A still receives Strategy 2 finite 0.25*w)
    pos_ratio = pd.DataFrame(
        {0: [1.0, 1.0, np.nan, 0.5], 1: [0.5, 0.5, 0.5, 0.5], 2: [2.0, 2.0, 2.0, 2.0]},
        index=idx,
    )

    exp_spot, exp_swap = _oracle_agg_pos_ratio(ratio_dfs, pos_ratio)
    act_spot, act_swap = _make_me_conf(ratio_dfs).agg_pos_ratio(pos_ratio)
    pd.testing.assert_frame_equal(act_spot, exp_spot, check_exact=True)
    pd.testing.assert_frame_equal(act_swap, exp_swap, check_exact=True)

    # Strategy 0 finite (takes fast path), Strategy 1 introduces NaN on overlapping col A + new col D
    f0 = pd.DataFrame({'A': [1.0, 2.0, 3.0, 4.0]}, index=idx)
    f1 = pd.DataFrame({'A': [np.nan, 0.5, np.nan, 1.0], 'D': [np.nan, 1.0, np.nan, 2.0]}, index=idx)
    f_pos = pd.DataFrame({0: [0.5, 0.5, 0.5, 0.5], 1: [1.0, 1.0, 1.0, 1.0]}, index=idx)
    exp_fs, exp_fw = _oracle_agg_pos_ratio([(f0, f0.copy()), (f1, f1.copy())], f_pos)
    act_fs, act_fw = _make_me_conf([(f0, f0.copy()), (f1, f1.copy())]).agg_pos_ratio(f_pos)
    pd.testing.assert_frame_equal(act_fs, exp_fs, check_exact=True)
    pd.testing.assert_frame_equal(act_fw, exp_fw, check_exact=True)

    # Missing timestamp in pos_ratio.index must still raise KeyError
    bad_idx = idx.append(pd.DatetimeIndex([idx[-1] + pd.Timedelta(hours=1)]))
    bad_pos = pd.DataFrame({0: np.ones(len(bad_idx)), 1: np.ones(len(bad_idx)), 2: np.ones(len(bad_idx))}, index=bad_idx)
    with pytest.raises(KeyError):
        _make_me_conf(ratio_dfs).agg_pos_ratio(bad_pos)


def test_p5_d1_agg_pos_ratio_avoids_dataframe_add(monkeypatch):
    idx = pd.date_range('2026-03-01 00:25:00', periods=8, freq='1h', tz='UTC')
    sp0 = pd.DataFrame({'BTC-USDT': np.linspace(0.1, 0.8, 8), 'ETH-USDT': np.linspace(0.2, 0.9, 8)}, index=idx)
    sp1 = pd.DataFrame({'ETH-USDT': np.linspace(0.05, 0.4, 8), 'SOL-USDT': np.linspace(0.1, 0.2, 8)}, index=idx)
    ratio_dfs = [(sp0, sp0 * -1.0), (sp1, sp1 * -1.0)]
    pos_ratio = pd.DataFrame({0: np.full(8, 0.6), 1: np.full(8, 0.4)}, index=idx)

    exp_spot, exp_swap = _oracle_agg_pos_ratio(ratio_dfs, pos_ratio)

    def _forbid_add(self, *args, **kwargs):
        raise AssertionError('agg_pos_ratio must use preallocated NumPy accumulation')

    monkeypatch.setattr(pd.DataFrame, 'add', _forbid_add)
    act_spot, act_swap = _make_me_conf(ratio_dfs).agg_pos_ratio(pos_ratio)
    pd.testing.assert_frame_equal(act_spot, exp_spot, check_exact=True)
    pd.testing.assert_frame_equal(act_swap, exp_swap, check_exact=True)


# ==============================================================================
# P5-B2: parallel restore_child in restore_snapshot
# ==============================================================================

def _build_restore_snapshot_fixture(root, n_children=4):
    """Build a self-contained snapshot that passes real restore_model & validate_snapshot."""
    from scipy import sparse
    from core.simulator import Simulator
    from tools.robust40 import server

    times = pd.date_range('2026-10-01 00:40:00', periods=30, freq='h')
    utc_times = times.tz_localize('UTC')
    pool = [dict(name=f'child{i}', re_timing=(True if i == 1 else None)) for i in range(n_children)]

    cols = ['spot:S', 'swap:U']
    mat = sparse.csr_matrix(np.tile(np.array([[0.1, -0.1]]), (len(times), 1)))
    base_state = ((10000.0, np.zeros(1), np.zeros(1)), (10000.0, np.zeros(1), np.zeros(1)))
    layout = (('spot', ('S',), (1.0,)), ('swap', ('U',), (1.0,)))

    def _make_account_df():
        df = pd.DataFrame({'equity': np.linspace(10000.0, 10100.0, len(times))}, index=times)
        df.attrs['checkpoints'] = {t: base_state for t in times[-25:]}
        df.attrs['simulation_state'] = base_state
        df.attrs['checkpoint_layout'] = layout
        return df

    context = dict(
        index=times,
        minute=40,
        columns=cols,
        leverage=pd.Series(1.0, index=times),
        equities=[pd.DataFrame({'candle_begin_time': times, 'equity': 10000.0}) for _ in range(n_children)],
        matrices=[mat.copy() for _ in range(n_children)],
        close=pd.DataFrame({'spot:S': 100.0, 'swap:U': 100.0}, index=times),
        simulation=dict(
            spot_open_p=np.full((len(times), 1), 100.0),
            spot_close_p=np.full((len(times), 1), 100.0),
            swap_open_p=np.full((len(times), 1), 100.0),
            swap_close_p=np.full((len(times), 1), 100.0),
            spot_lot_sizes=np.array([1.0]),
            swap_lot_sizes=np.array([1.0]),
            funding_rates=np.zeros((len(times), 1)),
            leverages=np.ones(len(times)),
            require_rebalance=np.ones(len(times), dtype=bool),
            capital_flow_arr=np.zeros(len(times)),
        ),
    )
    result = dict(
        virtual_accounts=[_make_account_df()],
        account=_make_account_df(),
        parent=pd.DataFrame(1.0 / n_children, index=times, columns=range(n_children)),
        base=pd.DataFrame(1.0 / n_children, index=times, columns=range(n_children)),
        delta=pd.DataFrame(0.0, index=times, columns=['U']),
        views=[pd.DataFrame(1.0 / n_children, index=times, columns=range(n_children))],
        targets=dict(
            spot=pd.DataFrame(0.1, index=times, columns=['S']),
            swap=pd.DataFrame(-0.1, index=times, columns=['U']),
        ),
        weekly_mix=pd.DataFrame({'w': [1.0]}, index=pd.DatetimeIndex([times[0]])),
        predictions=pd.DataFrame({'time': pd.Series(dtype='datetime64[ns]'), 'forward24': pd.Series(dtype=float)}),
        train_windows=pd.DataFrame({'quarter': pd.Series(dtype='datetime64[ns]')}),
        models={},
    )
    bundle = dict(context=context, result=result, minute=40, account_identity='acct_b2', strategy_pool=pool)

    (root / 'model').mkdir(parents=True)
    pd.to_pickle(bundle, root / 'model/state.pkl')
    for i, spec in enumerate(pool):
        child = root / f'S{i + 1}-{spec["name"]}'
        child.mkdir()
        pd.DataFrame({'candle_begin_time': utc_times, 'val': np.arange(len(times)) + i}).to_pickle(child / '选币结果.pkl')
        pd.DataFrame({'S': np.full(len(times), 0.1)}, index=utc_times).to_pickle(child / 'ratio_spot.pkl')
        pd.DataFrame({'U': np.full(len(times), -0.1)}, index=utc_times).to_pickle(child / 'ratio_swap.pkl')
        for suffix in (('state0', 'state1') if spec['re_timing'] else ('state0',)):
            st = {
                'spot': dict(columns=pd.Index(['S']), sim=Simulator(10000.0, np.array([1.0]), 0.001, np.zeros(1, dtype=np.int64), 5.0)),
                'swap': dict(columns=pd.Index(['U']), sim=Simulator(10000.0, np.array([1.0]), 0.0006, np.zeros(1, dtype=np.int64), 5.0)),
            }
            st.update(
                simulate_to=utc_times[-1],
                snapshot_queue=[
                    dict(time=t, spot=st['spot']['sim'].snapshot(), swap=st['swap']['sim'].snapshot())
                    for t in utc_times[-25:]
                ],
            )
            pd.to_pickle(st, child / f'lots_{suffix}.pkl')
            pd.DataFrame({'candle_begin_time': utc_times, 'equity': 10000.0 + i}).to_pickle(child / f'account_{suffix}.pkl')

    (root / 'sections').mkdir()
    points = {t: dict(cutoff=t, held=[], ranks=[], params=(17, 0.1, 0.9, '2026-01-01'), initialized=True, version=1) for t in times[-25:]}
    section = dict(points[times[-1]], checkpoints=points, output=pd.DataFrame({'candle_begin_time': times, 'chosen': 1}))
    pd.to_pickle(section, root / 'sections/s3.pkl')
    (root / 'snapshot_time.txt').write_text(str(utc_times[-1]))
    server.complete(root, 'acct_b2', {'strategy_pool': pool}, times[-1], '2026-01-01 00:40')
    return times, {'strategy_pool': pool}


def test_p5_b2_restore_snapshot_runs_children_concurrently_and_matches_serial(tmp_path, monkeypatch):
    import shutil
    import threading
    from tools.robust40 import handoff as h

    serial_snap = tmp_path / 'serial'
    parallel_snap = tmp_path / 'parallel'
    times, values = _build_restore_snapshot_fixture(serial_snap, n_children=4)
    shutil.copytree(serial_snap, parallel_snap)
    revised = times[-3]

    # 1. Run true serial reference (1-by-1 restore_child) on serial_snap
    orig_restore_child = h.restore_child
    expected_time = times[-4]
    for child in sorted({p.parent for p in serial_snap.glob('S*/lots_state*.pkl')}):
        orig_restore_child(child, expected_time)

    # 2. Prove parallel_snap overlaps at least 2 children concurrently via Barrier
    barrier = threading.Barrier(2, timeout=2.0)
    entered = 0
    lock = threading.Lock()

    def spy_restore_child(child, t):
        nonlocal entered
        with lock:
            entered += 1
            should_wait = entered <= 2
        if should_wait:
            barrier.wait()
        return orig_restore_child(child, t)

    monkeypatch.setattr(h, 'restore_child', spy_restore_child)
    actual_time = h.restore_snapshot(parallel_snap, revised, values)
    assert actual_time == expected_time

    # 3. Verify every child artifact matches serial reference identically
    for i, spec in enumerate(values['strategy_pool']):
        child_rel = f'S{i + 1}-{spec["name"]}'
        for s_path in sorted((serial_snap / child_rel).glob('*.pkl')):
            p_path = parallel_snap / child_rel / s_path.name
            s_obj = pd.read_pickle(s_path)
            p_obj = pd.read_pickle(p_path)
            if isinstance(s_obj, pd.DataFrame):
                pd.testing.assert_frame_equal(p_obj, s_obj, check_exact=True)
            else:
                assert p_obj['simulate_to'] == s_obj['simulate_to']
                assert [x['time'] for x in p_obj['snapshot_queue']] == [x['time'] for x in s_obj['snapshot_queue']]


def test_p5_b2_restore_snapshot_propagates_child_error_after_draining_workers(tmp_path, monkeypatch):
    import threading
    import time
    from tools.robust40 import handoff as h

    snap = tmp_path / 'err_snap'
    times, values = _build_restore_snapshot_fixture(snap, n_children=4)
    orig_restore_child = h.restore_child

    sibling_started = threading.Event()
    sibling_exited = threading.Event()

    def faulty_restore_child(child, t):
        if child.name.startswith('S1-'):
            sibling_started.wait(timeout=2.0)
            raise RuntimeError('corrupted child S1')
        if child.name.startswith('S2-'):
            sibling_started.set()
            try:
                time.sleep(0.08)
                return orig_restore_child(child, t)
            finally:
                sibling_exited.set()
        return orig_restore_child(child, t)

    monkeypatch.setattr(h, 'restore_child', faulty_restore_child)
    with pytest.raises(RuntimeError, match='corrupted child S1'):
        h.restore_snapshot(snap, times[-3], values)
    # All concurrent worker threads must have exited before restore_snapshot propagated the error
    assert sibling_exited.is_set()
