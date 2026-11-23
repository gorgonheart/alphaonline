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


# ==============================================================================
# P5-A1: 3-way parallel read_inputs & multi-unit zero-copy read_arrow_tail
# ==============================================================================

def _write_typed_arrow(path, times_arr, extra_cols, symbols):
    import json
    import pyarrow as pa

    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {'candle_begin_time': times_arr, **extra_cols}
    table = pa.table(arrays)
    meta = dict(table.schema.metadata or {})
    meta[b'symbols'] = json.dumps(symbols).encode('utf-8')
    table = table.replace_schema_metadata(meta)
    with pa.OSFile(str(path), 'wb') as fout:
        with pa.ipc.new_file(fout, table.schema) as writer:
            for _ in symbols:
                writer.write_table(table)


@pytest.mark.parametrize('unit,tz', [
    ('ns', 'UTC'),
    ('ms', 'UTC'),
    ('us', None),
    ('s', 'Asia/Hong_Kong'),
])
@pytest.mark.parametrize('sub_ns_offset', [0, 500])
def test_p5_a1_read_arrow_tail_multi_unit_and_tz_matches_reference(tmp_path, monkeypatch, unit, tz, sub_ns_offset):
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path
    from tools.robust40 import handoff as h

    run = pd.Timestamp('2026-10-31 08:25', tz='Asia/Hong_Kong').to_pydatetime()
    base_end = pd.Timestamp('2026-10-30 23:25')
    idx_ns = pd.date_range(base_end - pd.Timedelta(hours=220), periods=222, freq='h', tz='UTC')
    unit_div = {'ns': 1, 'us': 10**3, 'ms': 10**6, 's': 10**9}[unit]
    ticks = (idx_ns.asi8 // unit_div).astype(np.int64)
    ts_arr = pa.array(ticks, type=pa.timestamp(unit, tz=tz))
    close_arr = pa.array(np.arange(len(ticks), dtype=np.float64))

    path = arrow_snapshot_path(tmp_path, 'swap', run)
    _write_typed_arrow(path, ts_arr, {'close': close_arr}, ['BTC-USDT'])

    end = base_end + pd.Timedelta(nanoseconds=sub_ns_offset)
    start = (base_end - pd.Timedelta(hours=40)) + pd.Timedelta(nanoseconds=sub_ns_offset)

    # Independent mathematical oracle: exact [start, end] interval (avoids pandas 2.2 coarse-unit
    # DatetimeIndex.searchsorted sub-unit truncation quirk) and last-10 rows <= end for tail mode.
    ref_idx = h.utc_times(ts_arr.to_numpy())
    in_window = (ref_idx >= start) & (ref_idx <= end)
    ref_indexed = pd.DataFrame(
        {'close': np.arange(len(ticks), dtype=np.float64)[in_window], 'funding_fee': np.nan},
        index=ref_idx[in_window].copy(deep=True).rename('candle_begin_time'),
    )
    le_end = np.flatnonzero(ref_idx <= end)
    r_ref = int(le_end[-1]) + 1 if len(le_end) else 0
    l_tail = max(0, r_ref - 10)
    ref_tail = pd.DataFrame({
        'candle_begin_time': ts_arr.slice(l_tail, r_ref - l_tail).to_pandas(date_as_object=False),
        'close': np.arange(len(ticks), dtype=np.float64)[l_tail:r_ref],
    })

    # Fast path must NOT invoke utc_times on candle_begin_time when schema is a non-null Arrow TimestampType
    def _forbid_utc_times(*args, **kwargs):
        raise AssertionError('read_arrow_tail fast path should not call utc_times on candle_begin_time')

    monkeypatch.setattr(h, 'utc_times', _forbid_utc_times)
    actual_indexed = h.read_arrow_tail(tmp_path, 'swap', run, end, start=start, indexed=True)['BTC-USDT']
    pd.testing.assert_frame_equal(actual_indexed, ref_indexed, check_exact=True)

    actual_tail = h.read_arrow_tail(tmp_path, 'swap', run, end, start=start, indexed=False, tail=10)['BTC-USDT']
    pd.testing.assert_frame_equal(actual_tail.reset_index(drop=True), ref_tail.reset_index(drop=True), check_exact=True)


def test_p5_a1_read_arrow_tail_null_timestamp_falls_back_safely(tmp_path):
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path
    from tools.robust40 import handoff as h

    run = pd.Timestamp('2026-10-31 08:25', tz='Asia/Hong_Kong').to_pydatetime()
    end = pd.Timestamp('2026-10-30 23:25')
    # 1. Non-timestamp (string) column triggers fallback to utc_times cleanly
    path = arrow_snapshot_path(tmp_path, 'spot', run)
    str_times = pa.array([str(end - pd.Timedelta(hours=1)), str(end)], type=pa.string())
    _write_typed_arrow(path, str_times, {'close': pa.array([1.0, 2.0])}, ['ETH-USDT'])

    out = h.read_arrow_tail(tmp_path, 'spot', run, end, start=end - pd.Timedelta(hours=2), indexed=True)
    assert list(out['ETH-USDT']['close']) == [1.0, 2.0]

    # 2. Nullable timestamp[ms, UTC] column with a null entry falls back safely without ArrowInvalid
    ms_vals = [None, (end - pd.Timedelta(hours=1)).value // 10**6, end.value // 10**6]
    null_ts = pa.array(ms_vals, type=pa.timestamp('ms', tz='UTC'))
    _write_typed_arrow(path, null_ts, {'close': pa.array([30.0, 10.0, 20.0])}, ['ETH-USDT'])
    out_null = h.read_arrow_tail(tmp_path, 'spot', run, end, start=end - pd.Timedelta(hours=2), indexed=True)
    assert 'ETH-USDT' in out_null and 10.0 in out_null['ETH-USDT']['close'].values and 20.0 in out_null['ETH-USDT']['close'].values


def test_p5_a1_read_inputs_parallel_three_way_preserves_telemetry_and_errors(tmp_path, monkeypatch):
    import json
    import threading
    import config
    from core.utils.arrow_reader import arrow_snapshot_path
    from tools.robust40 import handoff as h, telemetry

    run = pd.Timestamp('2026-10-31 08:25', tz='Asia/Hong_Kong').to_pydatetime()
    end = pd.Timestamp('2026-10-30 23:25')
    start = end - pd.Timedelta(hours=10)
    idx = pd.date_range(start, end, freq='h')

    # Create dummy arrow files so path.exists() checks pass if needed
    for kind in ('spot', 'swap'):
        p = arrow_snapshot_path(tmp_path, kind, run)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')
    monkeypatch.setattr(config, 'bmac_data_path', tmp_path)

    # 1. All 3 branches execute concurrently via Barrier(3) and emit all 3 telemetry phase fields
    barrier = threading.Barrier(3, timeout=2.0)

    def fake_arrow(root, kind, rt, e, s, indexed=False):
        barrier.wait()
        return {'BTC-USDT': pd.DataFrame({'close': np.ones(len(idx))}, index=idx)}

    def fake_pivot(root, minute, e, s):
        barrier.wait()
        return {'pivot/spot/close': pd.DataFrame({'BTC-USDT': np.ones(len(idx))}, index=idx)}

    monkeypatch.setattr(h, 'read_arrow_tail', fake_arrow)
    monkeypatch.setattr(h, 'read_pivot_inputs', fake_pivot)

    events = []
    monkeypatch.setattr(telemetry, '_write', lambda msg: events.append(json.loads(msg.split(' ', 1)[1])))
    with telemetry.round_log('acct_a1', int(run.timestamp())):
        out = h.read_inputs(run, 25, end, start=start)

    assert set(out.keys()) == {'raw/spot/BTCUSDT', 'raw/swap/BTCUSDT', 'pivot/spot/close'}
    end_event = events[-1]
    for field in ('revision_arrow_spot_s', 'revision_arrow_swap_s', 'revision_pivot_s'):
        assert field in end_event and isinstance(end_event[field], float) and end_event[field] >= 0.0

    # 2. Failure in pivot branch propagates original exception and sets failed_phase='revision_pivot_s'
    def ok_arrow(root, kind, rt, e, s, indexed=False):
        return {'BTC-USDT': pd.DataFrame({'close': np.ones(len(idx))}, index=idx)}

    def fail_pivot(root, minute, e, s):
        raise RuntimeError('pivot disk error')

    monkeypatch.setattr(h, 'read_arrow_tail', ok_arrow)
    monkeypatch.setattr(h, 'read_pivot_inputs', fail_pivot)
    events.clear()
    with pytest.raises(RuntimeError, match='pivot disk error'):
        with telemetry.round_log('acct_a1', int(run.timestamp())):
            h.read_inputs(run, 25, end, start=start)
    assert events[-1].get('failed_phase') == 'revision_pivot_s'

    # 3. Empty spot Arrow raises ValueError WITHOUT setting failed_phase='revision_arrow_spot_s'
    def empty_spot_arrow(root, kind, rt, e, s, indexed=False):
        return {} if kind == 'spot' else {'BTC-USDT': pd.DataFrame({'close': np.ones(len(idx))}, index=idx)}

    def ok_pivot(root, minute, e, s):
        return {'pivot/spot/close': pd.DataFrame({'BTC-USDT': np.ones(len(idx))}, index=idx)}

    monkeypatch.setattr(h, 'read_arrow_tail', empty_spot_arrow)
    monkeypatch.setattr(h, 'read_pivot_inputs', ok_pivot)
    events.clear()
    with pytest.raises(ValueError, match='Missing same-round spot Arrow'):
        with telemetry.round_log('acct_a1', int(run.timestamp())):
            h.read_inputs(run, 25, end, start=start)
    assert events[-1].get('failed_phase') != 'revision_arrow_spot_s'


# ==============================================================================
# P5-C1: 3-thread signal_block sections & live _polars_threads=budget
# ==============================================================================

def _make_section_blocks_frame():
    import polars as pl

    rng = np.random.default_rng(2026)
    ts = pd.date_range('2026-01-01 00:20:00', periods=40, freq='1h')
    rows = []
    for t in ts:
        for i in range(6):
            rows.append({
                'candle_begin_time': t,
                'symbol': f'SYM{i}USDT',
                'is_spot': 0 if i < 4 else 1,
                'symbol_spot': f'SYM{i}USDT',
                'symbol_swap': f'SYM{i}USDT',
                '是否交易': 1,
                'VolumeMaxBias_17': float(rng.uniform(0.05, 1.0)),
                'Cci过滤无现货删数据_1000': float(rng.normal(0, 100)),
                'Cci过滤无现货删数据_1440': float(rng.normal(0, 100)),
                '当前回撤_53': -abs(float(rng.normal(0, 0.15))),
                '当前回撤_109': -abs(float(rng.normal(0, 0.15))),
                '当前回撤_168': -abs(float(rng.normal(0, 0.25))),
            })
    return pl.DataFrame(rows)


def test_p5_c1_parallel_block_sections_match_serial_and_preserve_order(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace
    import config
    import polars as pl
    from core import select_coin as sc
    from core.utils.factor_data import FactorBlocks

    full_pl = _make_section_blocks_frame()
    s3_mod = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    topk_mod = importlib.import_module('sections.TopKTo2K')
    fix_mod = importlib.import_module('sections.固定回撤阈值触发币数占比开仓门槛')
    dyn_mod = importlib.import_module('sections.动态回撤阈值')

    # Interleaved legacy (signal_polars-only) family to verify mixed declaration order preservation
    legacy_mod = SimpleNamespace(
        signal_polars=lambda lf, p, col: lf.with_columns((pl.col('当前回撤_53') * p).alias(col)),
        get_factor_list=lambda p: [('当前回撤', 53)],
        is_cross=True,
    )
    hub_map = {
        'VolumeMaxBias_进出场阈值分离官方': s3_mod,
        'TopKTo2K': topk_mod,
        'LegacyMix': legacy_mod,
        '固定回撤阈值触发币数占比开仓门槛': fix_mod,
        '动态回撤阈值': dyn_mod,
    }
    monkeypatch.setattr(sc, 'KLINE_COLS', ['candle_begin_time', 'symbol', 'is_spot', 'symbol_spot', 'symbol_swap', '是否交易'])
    monkeypatch.setattr(sc.FactorHub, 'get_by_name', lambda name: hub_map[name])

    section_params = {
        'VolumeMaxBias_进出场阈值分离官方': [(17, 0.25, 0.6, '2026-01-01 18:20')],
        'TopKTo2K': [(3, 'Cci过滤无现货删数据', 1000), (5, 'Cci过滤无现货删数据', 1440)],
        'LegacyMix': [2],
        '固定回撤阈值触发币数占比开仓门槛': [(168, -0.20, 0.04)],
        '动态回撤阈值': [53, 109],
    }
    factor_cols = [f'{n}_{p}' for n, ps in section_params.items() for p in ps]

    def _run_with_gate(tag, gate_val):
        store = FactorBlocks(tmp_path / f'blocks_{tag}', hours=15, minute=20)
        store.append(full_pl, 0)
        snap = tmp_path / f'snap_{tag}'
        conf = SimpleNamespace(
            section_state=True,
            checkpoint_hours=10,
            incremental_lookback_hours=0,
            get_snapshot_folder=lambda: snap,
            section_params_dict=section_params,
            factor_col_name_list=factor_cols,
        )
        monkeypatch.setattr(config, 'compute_gate', gate_val, raising=False)
        sc.calc_cross_sections(conf, store=store)
        return store, snap

    store_ser, snap_ser = _run_with_gate('ser', lambda: None)
    store_par, snap_par = _run_with_gate('par', None)
    try:
        assert store_par.section_columns == store_ser.section_columns == factor_cols
        for col in factor_cols:
            s_vals = store_ser.read_history([col])[col].to_numpy()
            p_vals = store_par.read_history([col])[col].to_numpy()
            np.testing.assert_allclose(p_vals, s_vals, rtol=0.0, atol=1e-15, equal_nan=True)
        # Verify S3 persisted checkpoint matches identically
        ser_pkls = sorted((snap_ser / 'sections').glob('*.pkl'))
        par_pkls = sorted((snap_par / 'sections').glob('*.pkl'))
        assert [p.name for p in par_pkls] == [p.name for p in ser_pkls] and len(ser_pkls) == 1
        st_ser = pd.read_pickle(ser_pkls[0])
        st_par = pd.read_pickle(par_pkls[0])
        assert list(st_par['checkpoints']) == list(st_ser['checkpoints'])
        pd.testing.assert_frame_equal(st_par['output'], st_ser['output'], check_exact=True)
    finally:
        store_ser.close()
        store_par.close()


def test_p5_c1_block_families_execute_concurrently_when_gate_is_none_and_serially_when_gated(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    import config
    import polars as pl
    from core import select_coin as sc
    from core.utils.factor_data import FactorBlocks

    frame = pl.DataFrame({
        'candle_begin_time': pd.date_range('2026-01-01', periods=8, freq='h'),
        'symbol': ['A'] * 8,
        'f_1': np.arange(8, dtype=float),
    })
    monkeypatch.setattr(sc, 'KLINE_COLS', ['candle_begin_time', 'symbol'])

    # 1. With compute_gate=None, 2 block families MUST overlap concurrently via Barrier(2)
    barrier = threading.Barrier(2, timeout=2.0)

    def _make_barrier_factor(mult):
        def _sig(block, params, col, state=None):
            if state is None:
                barrier.wait()
            return block.with_columns((pl.col('f_1') * mult).alias(col)), {'seen': True}

        return SimpleNamespace(
            signal_block=_sig,
            get_factor_list=lambda p: [('f', 1)],
            get_state_input_columns=lambda p: ['candle_begin_time', 'symbol', 'f_1'],
        )

    hub = {'B1': _make_barrier_factor(1.0), 'B2': _make_barrier_factor(2.0)}
    monkeypatch.setattr(sc.FactorHub, 'get_by_name', lambda name: hub[name])

    store1 = FactorBlocks(tmp_path / 'b_par', hours=4, minute=0)
    store1.append(frame, 0)
    conf = SimpleNamespace(
        section_state=False,
        section_params_dict={'B1': [1], 'B2': [1]},
        factor_col_name_list=['B1_1', 'B2_1'],
    )
    monkeypatch.setattr(config, 'compute_gate', None, raising=False)
    try:
        sc.calc_cross_sections(conf, store=store1)
        assert store1.section_columns == ['B1_1', 'B2_1']
    finally:
        store1.close()

    # 2. With compute_gate set (initialization mode), families MUST run serially on main thread
    main_tid = threading.get_ident()
    seen_tids = []

    def _make_tid_factor(mult):
        def _sig(block, params, col, state=None):
            seen_tids.append(threading.get_ident())
            return block.with_columns((pl.col('f_1') * mult).alias(col)), None

        return SimpleNamespace(
            signal_block=_sig,
            get_factor_list=lambda p: [('f', 1)],
            get_state_input_columns=lambda p: ['candle_begin_time', 'symbol', 'f_1'],
        )

    hub_ser = {'B1': _make_tid_factor(1.0), 'B2': _make_tid_factor(2.0)}
    monkeypatch.setattr(sc.FactorHub, 'get_by_name', lambda name: hub_ser[name])
    store2 = FactorBlocks(tmp_path / 'b_ser', hours=4, minute=0)
    store2.append(frame, 0)
    monkeypatch.setattr(config, 'compute_gate', lambda: None, raising=False)
    try:
        sc.calc_cross_sections(conf, store=store2)
        assert seen_tids and all(tid == main_tid for tid in seen_tids)
    finally:
        store2.close()


def test_p5_c1_parallel_section_failure_leaves_zero_partial_store_or_state(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import config
    import polars as pl
    from core import select_coin as sc
    from core.utils.factor_data import FactorBlocks

    frame = pl.DataFrame({
        'candle_begin_time': pd.date_range('2026-01-01', periods=6, freq='h'),
        'symbol': ['A'] * 6,
        'f_1': np.arange(6, dtype=float),
    })
    monkeypatch.setattr(sc, 'KLINE_COLS', ['candle_begin_time', 'symbol'])

    ok_persisted = SimpleNamespace(
        signal_block=lambda b, p, col, st=None, **kw: (b.with_columns(pl.lit(1.0).alias(col)), {'cutoff': b['candle_begin_time'].max()}),
        signal_with_state=lambda *a, **kw: None,
        get_factor_list=lambda p: [('f', 1)],
        get_state_input_columns=lambda p: ['candle_begin_time', 'symbol', 'f_1'],
    )

    def _fail_block(b, p, col, st=None, **kw):
        raise RuntimeError('boom in sibling section')

    bad_family = SimpleNamespace(
        signal_block=_fail_block,
        get_factor_list=lambda p: [('f', 1)],
        get_state_input_columns=lambda p: ['candle_begin_time', 'symbol', 'f_1'],
    )
    hub = {'PersistedOk': ok_persisted, 'BadBlock': bad_family}
    monkeypatch.setattr(sc.FactorHub, 'get_by_name', lambda name: hub[name])

    snap = tmp_path / 'snap_atomic'
    snap.mkdir()
    store = FactorBlocks(tmp_path / 'b_atomic', hours=3, minute=0)
    store.append(frame, 0)
    conf = SimpleNamespace(
        section_state=True,
        checkpoint_hours=0,
        incremental_lookback_hours=0,
        get_snapshot_folder=lambda: snap,
        section_params_dict={'PersistedOk': [1], 'BadBlock': [1]},
        factor_col_name_list=['PersistedOk_1', 'BadBlock_1'],
    )
    monkeypatch.setattr(config, 'compute_gate', None, raising=False)
    try:
        with pytest.raises(RuntimeError, match='boom in sibling section'):
            sc.calc_cross_sections(conf, store=store)
        assert store.section_columns == []
        assert list(snap.rglob('*')) == []
    finally:
        store.close()


@pytest.mark.parametrize('gate_val,expected_section_threads', [
    (None, 4),
    (lambda: None, 2),
])
def test_p5_c1_prepare_factor_selection_polars_threads_respects_compute_gate(
    tmp_path, monkeypatch, gate_val, expected_section_threads
):
    from types import SimpleNamespace
    import config
    from core import backtest
    from test_streaming_selection import inputs, strategy

    frame = inputs(tmp_path / 'source')
    conf = SimpleNamespace(name='test', input_mode='arrow_strict', max_hold_period='3h', lookback_days=0,
                           incremental_lookback_hours=7)
    child = SimpleNamespace(strategy_list=[strategy()], unified_time='2017-01-01', get_result_folder=lambda: tmp_path)
    factory = SimpleNamespace(config_list=[child], generate_all_factor_config=lambda: conf)
    budgets = []

    monkeypatch.setattr(config, 'job_num', 4)
    monkeypatch.setattr(config, 'compute_gate', gate_val, raising=False)
    monkeypatch.setattr(config, 'selection_block_hours', 6, raising=False)
    monkeypatch.setattr(backtest, 'apply_bstocks_black_list', lambda c: None)
    monkeypatch.setattr(backtest, 'get_snapshot_time', lambda _: None)

    def run_stage(fn, *a, _polars_threads=1, **kw):
        budgets.append(_polars_threads)
        return fn(*a, **kw)

    monkeypatch.setattr(backtest, '_run_block_stage', run_stage, raising=False)
    monkeypatch.setattr(backtest, 'calc_factors', lambda c, **kw: kw['store'].append(frame, 0))
    monkeypatch.setattr(backtest, 'calc_cross_sections', lambda c, **kw: ({}, []))
    store, _ = backtest.prepare_factor_selection(factory, pd.Timestamp('2025-01-02 07:55'), directory=tmp_path / 'shared')
    try:
        assert budgets == [1, expected_section_threads, 4]
    finally:
        store.close()
