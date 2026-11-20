"""TDD + independent Python oracle tests for Phase 4 optimizations (WP1, WP2, WP3)."""
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl
import pytest


def _make_ohlcv(n=400, seed=101, with_nans=False):
    rng = np.random.default_rng(seed)
    base = 100.0 + rng.normal(0, 1.2, n).cumsum()
    base = np.maximum(base, 5.0)
    open_p = base.copy()
    close_p = np.maximum(base + rng.normal(0, 0.6, n), 1.0)
    high_p = np.maximum(open_p, close_p) + np.abs(rng.normal(0, 0.8, n))
    low_p = np.maximum(np.minimum(open_p, close_p) - np.abs(rng.normal(0, 0.8, n)), 0.5)
    vol = np.abs(rng.normal(1000.0, 300.0, n)) + 1.0
    qv = vol * close_p
    tbqv = qv * rng.uniform(0.2, 0.8, n)
    if with_nans and n > 30:
        high_p[4] = np.nan
        low_p[9] = np.nan
        close_p[15] = np.nan
        vol[20] = 0.0
    ts = pd.date_range('2026-01-01 00:25:00', periods=n, freq='1h')
    return pd.DataFrame({
        'candle_begin_time': ts,
        'symbol': 'BTC-USDT',
        'symbol_spot': 'BTCUSDT',
        'symbol_swap': 'BTCUSDT',
        'open': open_p,
        'high': high_p,
        'low': low_p,
        'close': close_p,
        'volume': vol,
        'quote_volume': qv,
        'taker_buy_quote_asset_volume': tbqv,
        'btc_close': 50000.0 + rng.normal(0, 100.0, n).cumsum(),
    })


def test_wp1_to_naive_ns_avoids_pd_to_datetime_on_typed_series(monkeypatch):
    """_to_naive_ns must not invoke slow pd.to_datetime when Series is already datetime64 or DatetimeTZDtype."""
    import core.data_bridge as db

    s_naive = pd.Series(pd.date_range('2026-01-01 00:25:00', periods=60, freq='1h'))
    s_utc = pd.Series(pd.date_range('2026-01-01 00:25:00', periods=60, freq='1h', tz='UTC'))
    s_sh = pd.Series(pd.date_range('2026-01-01 08:25:00', periods=60, freq='1h', tz='Asia/Shanghai'))
    s_us = s_naive.astype('datetime64[us]')
    s_str = s_naive.dt.strftime('%Y-%m-%d %H:%M:%S')

    def _oracle(s):
        dt = pd.to_datetime(s)
        if dt.dt.tz is not None:
            dt = dt.dt.tz_localize(None)
        return dt.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)

    expected = [_oracle(s) for s in (s_naive, s_utc, s_sh, s_us)]
    for s in (s_naive, s_utc, s_sh, s_us, s_str):
        np.testing.assert_array_equal(db._to_naive_ns(s), _oracle(s))

    # Now forbid pd.to_datetime for already-typed datetime series
    def _forbid_to_datetime(*args, **kwargs):
        raise AssertionError('pd.to_datetime should not be called on already-typed datetime Series')

    monkeypatch.setattr(db.pd, 'to_datetime', _forbid_to_datetime)
    for s, exp in zip((s_naive, s_utc, s_sh, s_us), expected):
        np.testing.assert_array_equal(db._to_naive_ns(s), exp)


def test_wp1_coin_cap_cache_reuses_and_invalidates_on_mtime_or_size(tmp_path, monkeypatch):
    """load_coin_cap caches deduplicated/sorted extra_df per (path, mtime_ns, size) across spot/swap calls."""
    import core.data_bridge as db

    candle = _make_ohlcv(120, seed=12)
    cap_dir = tmp_path / 'coin_cap'
    cap_dir.mkdir()
    cap_path = cap_dir / 'BTCUSDT.pkl'

    extra1 = pd.DataFrame({
        'candle_begin_time': pd.date_range('2025-12-25 00:00:00', periods=15, freq='1D'),
        'usd_price': np.linspace(95.0, 105.0, 15),
        'circulating_supply': np.linspace(1e7, 1.2e7, 15),
    })
    extra1.to_pickle(cap_path)

    if hasattr(db, '_CAP_CACHE'):
        db._CAP_CACHE.clear()

    reads = []
    real_read_pickle = pd.read_pickle

    def spy_read_pickle(p, *a, **kw):
        reads.append(str(p))
        return real_read_pickle(p, *a, **kw)

    monkeypatch.setattr(db.pd, 'read_pickle', spy_read_pickle)

    out_spot = db.load_coin_cap(str(cap_dir), candle, ['circulating_supply'], 'BTCUSDT')
    out_swap = db.load_coin_cap(str(cap_dir), candle, ['circulating_supply'], 'BTCUSDT')
    assert len(reads) == 1, f'Expected 1 disk read for dual spot/swap symbol, got {len(reads)}'
    pd.testing.assert_frame_equal(out_spot, out_swap, check_exact=True)

    # Update file on disk and bump mtime -> must invalidate cache
    extra2 = extra1.assign(circulating_supply=extra1['circulating_supply'] * 2.0)
    extra2.to_pickle(cap_path)
    st = cap_path.stat()
    os.utime(cap_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    out_updated = db.load_coin_cap(str(cap_dir), candle, ['circulating_supply'], 'BTCUSDT')
    assert len(reads) == 2
    np.testing.assert_allclose(
        out_updated['circulating_supply'].to_numpy(),
        out_spot['circulating_supply'].to_numpy() * 2.0,
        rtol=1e-15,
        atol=0.0,
    )


def test_wp1_corr_btc_exact_parity_including_constant_nonzero_windows():
    """CorrBTC and rolling_corr_pandas_exact must match pandas rolling().corr() bit-for-bit (0.0 diff & 0 NaN diff)."""
    import factors.CorrBTC as cb_mod
    from core.utils.fast_rolling import rolling_corr_pandas_exact

    df = _make_ohlcv(300, seed=33, with_nans=True)
    # Inject constant growth rate (so pct_change is constant nonzero 0.01 for 80 bars -> Welford boundary!)
    df.loc[80:160, 'close'] = 100.0 * (1.01 ** np.arange(81))

    ind1 = df['close'].pct_change()
    ind2 = df['btc_close'].pct_change().fillna(0)
    params = [20, 60, 144]

    fast_dict = rolling_corr_pandas_exact(ind1, ind2, params)
    mod_dict = cb_mod.signal_multi_params(df, params)

    for p in params:
        expected = ind1.rolling(p, min_periods=1).corr(ind2).to_numpy()
        got_fast = fast_dict[p].to_numpy()
        got_mod = mod_dict[p].to_numpy()

        np.testing.assert_array_equal(np.isnan(got_fast), np.isnan(expected))
        np.testing.assert_array_equal(got_fast[~np.isnan(got_fast)], expected[~np.isnan(expected)])
        np.testing.assert_array_equal(np.isnan(got_mod), np.isnan(expected))
        np.testing.assert_array_equal(got_mod[~np.isnan(got_mod)], expected[~np.isnan(expected)])


def test_wp1_atr_selectcoin_drawdown_and_factor_shift0_contracts(monkeypatch):
    """Atr, SelectCoin, 当前回撤 match legacy signal() bit-for-bit; calc_factor_vals skips Series.shift(0)."""
    import factors.Atr as atr_mod
    import factors.SelectCoin as sc_mod
    dd_mod = importlib.import_module('factors.当前回撤')
    import core.factor as cf

    df = _make_ohlcv(250, seed=44, with_nans=True)

    # 1. Atr parity
    atr_multi = atr_mod.signal_multi_params(df, [14, 168])
    for p in (14, 168):
        exp_df = atr_mod.signal(df.copy(deep=True), p, f'Atr_{p}')
        np.testing.assert_allclose(
            atr_multi[p].to_numpy(),
            exp_df[f'Atr_{p}'].to_numpy(),
            rtol=0.0,
            atol=1e-15,
            equal_nan=True,
        )

    # 2. SelectCoin parity (single-symbol fast path + multi-symbol fallback path)
    params_sc = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
    sc_single = sc_mod.signal_multi_params(df, params_sc)
    for p in params_sc:
        exp_df = sc_mod.signal(df.copy(deep=True), p, f'SelectCoin_{p}')
        np.testing.assert_array_equal(sc_single[p].to_numpy(), exp_df[f'SelectCoin_{p}'].to_numpy())

    df_mixed = df.copy()
    df_mixed.loc[100:, 'symbol'] = 'ETH-USDT'
    sc_mixed = sc_mod.signal_multi_params(df_mixed, params_sc)
    for p in params_sc:
        exp_df = sc_mod.signal(df_mixed.copy(deep=True), p, f'SelectCoin_{p}')
        np.testing.assert_array_equal(sc_mixed[p].to_numpy(), exp_df[f'SelectCoin_{p}'].to_numpy())

    # 3. 当前回撤 parity (normal coin, LUNA coin, and mixed-symbol frame)
    for sym_mode in ('normal', 'luna', 'mixed'):
        d_test = df.copy()
        if sym_mode == 'luna':
            d_test['symbol'] = 'LUNA-USDT'
        elif sym_mode == 'mixed':
            d_test.loc[50:100, 'symbol'] = 'LUNAUSDT'
        dd_multi = dd_mod.signal_multi_params(d_test, [39, 93])
        for p in (39, 93):
            exp_df = dd_mod.signal(d_test.copy(deep=True), p, f'当前回撤_{p}')
            np.testing.assert_allclose(
                dd_multi[str(p)].to_numpy(),
                exp_df[f'当前回撤_{p}'].to_numpy(),
                rtol=0.0,
                atol=1e-15,
                equal_nan=True,
            )

    # 4. core.factor.calc_factor_vals skips Series.shift when shift == 0
    shift_calls = []
    orig_shift = pd.Series.shift

    def spy_shift(self, periods=1, *a, **kw):
        if periods == 0:
            shift_calls.append(periods)
        return orig_shift(self, periods, *a, **kw)

    monkeypatch.setattr(pd.Series, 'shift', spy_shift)
    res0 = cf.calc_factor_vals(df.copy(), 'Atr', [14, 168], shift=0)
    assert shift_calls == [], f'Expected 0 calls to Series.shift(0), got {len(shift_calls)}'
    np.testing.assert_allclose(res0['Atr_14'], atr_multi[14].to_numpy(), rtol=0.0, atol=0.0, equal_nan=True)


def test_wp2_topk_minimal_input_columns_and_multi_param_single_block_read(tmp_path, monkeypatch):
    """TopKTo2K declares get_state_input_columns and _calc_section_blocks reads each block once per family."""
    from core import select_coin as sc
    from core.utils.factor_data import FactorBlocks
    import sections.TopKTo2K as topk_mod
    dd_dyn_mod = importlib.import_module('sections.动态回撤阈值')

    # 1. TopKTo2K must declare minimal get_state_input_columns
    assert hasattr(topk_mod, 'get_state_input_columns')
    cols = topk_mod.get_state_input_columns((168, 'Cci过滤无现货删数据', 1000))
    assert set(cols) == {'candle_begin_time', 'symbol', 'is_spot', 'Cci过滤无现货删数据_1000'}

    # 2. Build FactorBlocks with 2 blocks and run _calc_section_blocks with 3 TopKTo2K params + 2 动态回撤阈值 params
    ts = pd.date_range('2026-01-01 00:00:00', periods=20, freq='1h')
    n_syms = 6
    rows = []
    rng = np.random.default_rng(77)
    for t in ts:
        for i in range(n_syms):
            rows.append({
                'candle_begin_time': t,
                'symbol': f'SYM{i}USDT',
                'is_spot': 0 if i < 4 else 1,
                'close': 100.0 + i,
                'next_close': 100.5 + i,
                'symbol_spot': f'SYM{i}USDT',
                'symbol_swap': f'SYM{i}USDT',
                '是否交易': 1,
                'Cci过滤无现货删数据_1000': float(rng.normal(0, 100)),
                'Cci过滤无现货删数据_1440': float(rng.normal(0, 100)),
                'Cci过滤无现货删数据_1800': float(rng.normal(0, 100)),
                '当前回撤_53': -abs(float(rng.normal(0, 0.1))),
                '当前回撤_109': -abs(float(rng.normal(0, 0.1))),
            })
    full_pl = pl.DataFrame(rows)
    store = FactorBlocks(tmp_path / 'blocks', hours=10, minute=0)
    store.append(full_pl, 0)

    topk_params = [
        (3, 'Cci过滤无现货删数据', 1000),
        (3, 'Cci过滤无现货删数据', 1440),
        (5, 'Cci过滤无现货删数据', 1800),
    ]
    dd_params = [53, 109]
    conf = SimpleNamespace(
        section_state=True,
        checkpoint_hours=0,
        incremental_lookback_hours=0,
        get_snapshot_folder=lambda: tmp_path / 'snap',
        section_params_dict={
            'TopKTo2K': topk_params,
            '动态回撤阈值': dd_params,
        },
        factor_col_name_list=[
            *(f'TopKTo2K_{p}' for p in topk_params),
            *(f'动态回撤阈值_{p}' for p in dd_params),
        ],
    )

    block_data_reads = []
    orig_read_block = store.read_block

    def spy_read_block(key, columns, *, order=None):
        if any(c not in ('_row', 'candle_begin_time') for c in columns):
            block_data_reads.append((key, tuple(columns)))
        return orig_read_block(key, columns, order=order)

    monkeypatch.setattr(store, 'read_block', spy_read_block)
    sc.calc_cross_sections(conf, store=store)

    # n_blocks x 2 families merged data block reads (instead of n_blocks x 5 params reads)
    n_blocks = len(store.block_keys())
    assert n_blocks >= 2
    assert len(block_data_reads) == n_blocks * 2, (
        f'Expected {n_blocks * 2} merged block reads across {n_blocks} blocks x 2 families, '
        f'got {len(block_data_reads)}: {block_data_reads}'
    )
    # Also verify TopKTo2K reads did not pull unused columns like close / next_close / 是否交易
    for _key, read_cols in block_data_reads:
        assert 'close' not in read_cols
        assert 'next_close' not in read_cols
        assert '是否交易' not in read_cols

    # Verify exact numerical parity against single-frame signal_polars oracle
    for p in topk_params:
        col_name = f'TopKTo2K_{p}'
        exp = topk_mod.signal_polars(full_pl.lazy(), p, col_name).collect()[col_name].to_numpy()
        act = store.read_history([col_name])[col_name].to_numpy()
        np.testing.assert_array_equal(act, exp)

    for p in dd_params:
        col_name = f'动态回撤阈值_{p}'
        exp = dd_dyn_mod.signal_polars(full_pl.lazy(), p, col_name).collect()[col_name].to_numpy()
        act = store.read_history([col_name])[col_name].to_numpy()
        np.testing.assert_allclose(act, exp, rtol=0.0, atol=1e-15, equal_nan=True)

    store.close()


def test_wp2_volumemaxbias_s3_block_continuation_avoids_full_multiindex_on_past_blocks(monkeypatch):
    """VolumeMaxBias_进出场阈值分离官方.signal_block must not build pandas MultiIndex on historical blocks
    and must match full oracle bit-for-bit on continuation."""
    from test_section_resume_state import candles, oracle
    section = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')

    data = candles()
    params = (17, 0.25, 0.6, '2025-01-02 00:20')
    col = 'chosen'
    cutoff = data.candle_begin_time.max() - pd.Timedelta(hours=2)

    # Initial state up to cutoff
    _, state = section.signal_with_checkpoints(
        data[data.candle_begin_time <= cutoff].copy(), params, col, hours=25
    )
    expected = oracle(data, params)

    frame = pl.from_pandas(data).with_row_index('_row')
    parts = frame.with_columns(
        (pl.col('candle_begin_time').dt.epoch('s') // 3600 // 19).alias('block')
    ).partition_by('block', maintain_order=True)
    assert len(parts) >= 3

    # For purely historical blocks (max(candle_begin_time) <= cutoff), pd.MultiIndex.from_frame must NOT be called
    orig_from_frame = pd.MultiIndex.from_frame
    past_multiindex_calls = []

    def spy_from_frame(df_arg, *a, **kw):
        past_multiindex_calls.append(len(df_arg))
        return orig_from_frame(df_arg, *a, **kw)

    monkeypatch.setattr(pd.MultiIndex, 'from_frame', spy_from_frame)

    outputs = []
    end = data.candle_begin_time.max()
    for part in parts:
        block_df = part.drop('block')
        is_pure_past = block_df['candle_begin_time'].max() <= cutoff
        before_calls = len(past_multiindex_calls)
        result, state = section.signal_block(
            block_df, params, col, state,
            checkpoint_start=end - pd.Timedelta(hours=24),
            checkpoint_hours=25,
        )
        if is_pure_past:
            assert len(past_multiindex_calls) == before_calls, (
                f'Historical block should use Polars join instead of pd.MultiIndex.from_frame, got calls {past_multiindex_calls}'
            )
        outputs.append(result.select('_row', col))

    actual = pl.concat(outputs).sort('_row')[col].to_numpy()
    np.testing.assert_array_equal(actual, expected.to_numpy())
    assert len(state['checkpoints']) == 25
    assert state['cutoff'] == end


# =============================================================================
# WP3 (P4-2): Cross-stage Pivot/State Reuse & merge_context Fast Path
# =============================================================================

def test_wp3_step6_market_handoff_reuses_pivots_and_releases_on_exit(monkeypatch):
    """step6_market_handoff passes Step 6 pivots to MODEL once if file sig matches and clears on exit."""
    from core import backtest

    assert hasattr(backtest, 'step6_market_handoff')
    assert hasattr(backtest, 'consume_round_market_pivots')

    sentinel_pivots = {'spot': {'close': pd.DataFrame({'A': [1.0]})}, 'swap': {'close': pd.DataFrame({'A': [1.0]})}}
    sig = [('spot', 100, 50)]
    monkeypatch.setattr(backtest, '_bmac_market_file_sig', lambda offset: sig)

    # Outside handoff context, publishing does nothing and consume returns None
    backtest._publish_round_market_pivots('25m', sig, sentinel_pivots)
    assert backtest.consume_round_market_pivots('25m') is None

    # Inside handoff context: matching offset & sig returns sentinel ONCE (one-shot)
    with backtest.step6_market_handoff():
        backtest._publish_round_market_pivots('25m', sig, sentinel_pivots)
        assert backtest.consume_round_market_pivots('35m') is None  # wrong offset preserves slot
        got = backtest.consume_round_market_pivots('25m')
        assert got is sentinel_pivots
        assert backtest.consume_round_market_pivots('25m') is None  # one-shot consumed

    # Signature change invalidates handoff
    with backtest.step6_market_handoff():
        backtest._publish_round_market_pivots('25m', [('spot', 99, 50)], sentinel_pivots)
        assert backtest.consume_round_market_pivots('25m') is None

    # Exception inside handoff still clears slot
    with pytest.raises(RuntimeError):
        with backtest.step6_market_handoff():
            backtest._publish_round_market_pivots('25m', sig, sentinel_pivots)
            raise RuntimeError('boom')
    assert backtest.consume_round_market_pivots('25m') is None


def _make_synthetic_context(index, columns, seed=10, close_cols=None, scale=1.0):
    from scipy import sparse
    rng = np.random.default_rng(seed)
    n = len(index)
    m = len(columns)
    matrices = []
    for k in range(14):
        dense = np.zeros((n, m), dtype=np.float64)
        for j in range(m):
            dense[:, j] = ((k + j + 1) * 0.01) * (1.0 + 0.001 * np.arange(n))
        matrices.append(sparse.csr_matrix(dense))
    leverage = pd.DataFrame(
        np.ones((n, 14), dtype=np.float64),
        index=index,
        columns=range(14),
    )
    equities = []
    for k in range(14):
        base = 100.0 * scale * np.exp(0.0005 * np.arange(n) * (k % 3 + 1))
        equities.append(pd.DataFrame({
            'candle_begin_time': index,
            'open': base,
            'high': base * 1.01,
            'low': base * 0.99,
            'close': base * 1.002,
            'factor_a': np.arange(n, dtype=np.float64) * 0.01,
        }))
    spot_cols = [c for c in columns if c.startswith('spot:')]
    swap_cols = [c for c in columns if c.startswith('swap:')]
    sim = {
        'init_capital': 10000.0,
        'leverages': np.ones(n, dtype=np.float64),
        'spot_c_rate': 0.002,
        'swap_c_rate': 0.0006,
        'spot_min_order_limit': 20.0,
        'swap_min_order_limit': 20.0,
        'min_margin_rate': 0.05,
        'require_rebalance': np.ones(n, dtype=np.int8),
        'capital_flow_arr': np.zeros(n, dtype=np.float64),
        'capital_flow_mode': -1,
        'spot_lot_sizes': np.full(len(spot_cols), 0.01, dtype=np.float64),
        'swap_lot_sizes': np.full(len(swap_cols), 0.01, dtype=np.float64),
    }
    for kind, kc in (('spot', spot_cols), ('swap', swap_cols)):
        base_mat = np.tile(np.arange(1, len(kc) + 1, dtype=np.float64), (n, 1)) * 10.0 + np.arange(n)[:, None] * 0.05
        sim[f'{kind}_open_p'] = np.ascontiguousarray(base_mat)
        sim[f'{kind}_close_p'] = np.ascontiguousarray(base_mat + 0.03)
        sim[f'{kind}_vwap1m_p'] = np.ascontiguousarray(base_mat + 0.01)
        if kind == 'swap':
            fr = np.full((n, len(kc)), 0.0001, dtype=np.float64)
            fr[0, 0] = np.nan
            sim['funding_rates'] = np.ascontiguousarray(fr)
    c_cols = list(columns) if close_cols is None else list(close_cols)
    close_data = {c: 10.0 + (i + 1) + np.arange(n, dtype=np.float64) * 0.02 for i, c in enumerate(c_cols)}
    close_df = pd.DataFrame(close_data, index=index)
    close_df.iloc[0, 0] = np.nan
    return {
        'index': index,
        'minute': 25,
        'columns': list(columns),
        'matrices': matrices,
        'leverage': leverage,
        'equities': equities,
        'simulation': sim,
        'close': close_df,
    }


def _legacy_merge_context_oracle(old, new, minimum_overlap=168, warmup=1000):
    """Independent reference implementation matching legacy DataFrame reindex/combine_first path."""
    from scipy import sparse
    from positions.ModelRobust40Strategy import _same
    last = old['index'][-1]
    overlap = old['index'].intersection(new['index'][warmup:])[-minimum_overlap:]
    addition = new['index'][new['index'] > last]
    columns = sorted(set(old['columns']) | set(new['columns']))
    oi = old['index'].get_indexer(overlap)
    ni = new['index'].get_indexer(overlap)
    ai = new['index'].get_indexer(addition)

    def exp_mat(mat, old_cols):
        coo = mat.tocoo()
        dest = pd.Index(columns).get_indexer(old_cols)
        return sparse.csr_matrix((coo.data, (coo.row, dest[coo.col])), shape=(mat.shape[0], len(columns)))

    matrices = []
    for a, b in zip(old['matrices'], new['matrices']):
        a = exp_mat(a, old['columns'])
        b = exp_mat(b, new['columns'])
        matrices.append(sparse.vstack([a, b[ai]], format='csr'))
    equities = []
    for a, b in zip(old['equities'], new['equities']):
        a = a.set_index('candle_begin_time')
        b = b.set_index('candle_begin_time').copy()
        scale = a.loc[overlap[-1], 'close'] / b.loc[overlap[-1], 'close']
        b.loc[:, ['open', 'high', 'low', 'close']] *= scale
        equities.append(pd.concat([a, b.loc[addition, a.columns]]).rename_axis('candle_begin_time').reset_index())
    simulation = dict(old['simulation'])
    for key in ('leverages', 'require_rebalance', 'capital_flow_arr'):
        simulation[key] = np.concatenate([simulation[key], new['simulation'][key][ai]])
    for kind in ('spot', 'swap'):
        cols = [c for c in columns if c.startswith(kind + ':')]
        oc = [c for c in old['columns'] if c.startswith(kind + ':')]
        nc = [c for c in new['columns'] if c.startswith(kind + ':')]
        old_sizes = pd.Series(old['simulation'][kind + '_lot_sizes'], index=oc)
        sizes = pd.Series(new['simulation'][kind + '_lot_sizes'], index=nc)
        simulation[kind + '_lot_sizes'] = old_sizes.reindex(cols).fillna(sizes).to_numpy()
        keys = [kind + '_' + k + '_p' for k in ('open', 'close', 'vwap1m')]
        if kind == 'swap':
            keys.append('funding_rates')
        added = [c for c in cols if c not in oc]
        shared_idx = old['index'].intersection(new['index']) if added else pd.DatetimeIndex([])
        for key in keys:
            a = pd.DataFrame(old['simulation'][key], index=old['index'], columns=oc).reindex(columns=cols)
            b = pd.DataFrame(new['simulation'][key], index=new['index'], columns=nc).reindex(columns=cols)
            if added and len(shared_idx):
                a.loc[shared_idx, added] = b.loc[shared_idx, added]
            if key == 'funding_rates':
                a = a.fillna(0)
                b = b.fillna(0)
            simulation[key] = np.ascontiguousarray(pd.concat([a, b.loc[addition]]).to_numpy())
    price_columns = list(dict.fromkeys(columns + list(old['close'].columns) + list(new['close'].columns)))
    close = old['close'].reindex(columns=price_columns).combine_first(new['close'].reindex(columns=price_columns))
    close = close.loc[:new['index'][-1]]
    return dict(
        old,
        index=old['index'].append(addition),
        columns=columns,
        matrices=matrices,
        leverage=pd.concat([old['leverage'], new['leverage'].loc[addition]]),
        equities=equities,
        simulation=simulation,
        close=close,
    )


def test_wp3_merge_context_same_columns_fast_path_matches_general_oracle():
    """merge_context must short-circuit _expand_matrix & NumPy simulation/close paths on identical columns
    while matching the general DataFrame oracle bit-for-bit on both same-column and expanded-column cases."""
    from positions import ModelRobust40Strategy as mrs

    cols = ['spot:BTCUSDT', 'spot:ETHUSDT', 'swap:BTCUSDT', 'swap:ETHUSDT', 'swap:SOLUSDT']
    idx_old = pd.date_range('2026-01-01 00:25:00', periods=120, freq='h')
    idx_new = pd.date_range('2026-01-01 00:25:00', periods=124, freq='h')

    old = _make_synthetic_context(idx_old, cols)
    new = _make_synthetic_context(idx_new, cols, scale=1.5)
    # Put a NaN in old['close'] row 0 col 0 that new['close'] fills with non-NaN to verify combine_first parity!
    new['close'].iloc[0, 0] = 123.45

    # 1. _expand_matrix must return the exact same CSR matrix object when columns are unchanged
    m0 = old['matrices'][0]
    assert mrs._expand_matrix(m0, cols, cols) is m0

    # 2. Same-columns merge_context must match legacy oracle bit-for-bit
    exp = _legacy_merge_context_oracle(old, new, minimum_overlap=16, warmup=20)
    act = mrs.merge_context(old, new, minimum_overlap=16, warmup=20)

    pd.testing.assert_index_equal(act['index'], exp['index'])
    assert act['columns'] == exp['columns']
    for am, em in zip(act['matrices'], exp['matrices']):
        np.testing.assert_array_equal(am.toarray(), em.toarray())
    for ae, ee in zip(act['equities'], exp['equities']):
        pd.testing.assert_frame_equal(ae, ee, check_exact=False, rtol=1e-12, atol=1e-12)
    for k, v in exp['simulation'].items():
        if isinstance(v, np.ndarray):
            np.testing.assert_array_equal(act['simulation'][k], v)
        else:
            assert act['simulation'][k] == v
    pd.testing.assert_frame_equal(act['close'], exp['close'], check_exact=True)

    # 3. Same-columns overlap revision must still raise ValueError with detailed index/column info
    bad_new = _make_synthetic_context(idx_new, cols, scale=1.5)
    bad_new['simulation']['swap_close_p'][115, 1] += 1.0
    with pytest.raises(ValueError, match=r'Historical revision in swap_close_p: index=.*column=swap:ETHUSDT'):
        mrs.merge_context(old, bad_new, minimum_overlap=16, warmup=20)

    # 4. Expanded-columns case (new listing added) must also match oracle bit-for-bit
    cols_expanded = ['spot:BTCUSDT', 'spot:ETHUSDT', 'swap:BTCUSDT', 'swap:ETHUSDT', 'swap:NEWUSDT', 'swap:SOLUSDT']
    new_exp = _make_synthetic_context(idx_new, cols_expanded, scale=1.5)
    # Match existing column values on overlap; newly listed coin has 0 position on historical bars
    new_only_idx = cols_expanded.index('swap:NEWUSDT')
    for m_idx in range(14):
        a_dense = old['matrices'][m_idx].toarray()
        b_dense = new_exp['matrices'][m_idx].toarray()
        for j, c in enumerate(cols):
            nj = cols_expanded.index(c)
            b_dense[:len(idx_old), nj] = a_dense[:, j]
        b_dense[:len(idx_old), new_only_idx] = 0.0
        from scipy import sparse
        new_exp['matrices'][m_idx] = sparse.csr_matrix(b_dense)
    for kind, kc_old, kc_new in (
        ('spot', [c for c in cols if c.startswith('spot:')], [c for c in cols_expanded if c.startswith('spot:')]),
        ('swap', [c for c in cols if c.startswith('swap:')], [c for c in cols_expanded if c.startswith('swap:')]),
    ):
        keys = [f'{kind}_{k}_p' for k in ('open', 'close', 'vwap1m')] + (['funding_rates'] if kind == 'swap' else [])
        for key in keys:
            for j, c in enumerate(kc_old):
                nj = kc_new.index(c)
                new_exp['simulation'][key][:len(idx_old), nj] = old['simulation'][key][:, j]
    for c in cols:
        new_exp['close'].loc[idx_old, c] = old['close'].loc[idx_old, c]
    exp2 = _legacy_merge_context_oracle(old, new_exp, minimum_overlap=16, warmup=20)
    act2 = mrs.merge_context(old, new_exp, minimum_overlap=16, warmup=20)
    assert act2['columns'] == exp2['columns']
    for k, v in exp2['simulation'].items():
        if isinstance(v, np.ndarray):
            np.testing.assert_array_equal(act2['simulation'][k], v)
    pd.testing.assert_frame_equal(act2['close'], exp2['close'], check_exact=True)


def test_wp3_finish_live_reuses_in_memory_bundle_on_replay_and_clears_reference(tmp_path, monkeypatch):
    """finish_live on replay rounds uses strategy._robust40_bundle instead of re-reading model/state.pkl
    and always deletes the cached bundle reference afterward."""
    from tools.robust40 import server, warmup, telemetry

    snap = tmp_path / 'snap'
    (snap / 'model').mkdir(parents=True)
    (snap / 'sections').mkdir(parents=True)
    end_ts = pd.Timestamp('2026-01-10 12:25:00')
    pd.to_pickle({'account': 'acct1', 'settings': {'k': 1}, 'origin': '2025-01-01'}, snap / 'warmup.pkl')
    pd.to_pickle({'cutoff': end_ts}, snap / 'sections/s3.pkl')

    in_mem_bundle = {'sentinel': 'in-memory-bundle'}
    validated_bundles = []

    def fake_validate(path, bundle):
        validated_bundles.append(bundle)
        return end_ts

    orig_read_pickle = pd.read_pickle
    state_reads = []

    def spy_read_pickle(p, *a, **kw):
        if str(p).endswith('state.pkl'):
            state_reads.append(str(p))
        return orig_read_pickle(p, *a, **kw)

    monkeypatch.setattr(warmup, 'validate_snapshot', fake_validate)
    monkeypatch.setattr(server.pd, 'read_pickle', spy_read_pickle)
    monkeypatch.setattr(server, 'complete', lambda *a, **kw: None)
    monkeypatch.setattr(telemetry, 'commit', lambda: None)

    strategy = SimpleNamespace(
        params={'runtime': {
            'server_init': True,
            'replaying': True,
            'state_path': str(snap / 'model/state.pkl'),
        }},
        _robust40_result={'parent': pd.DataFrame({'w': [1.0]}, index=pd.DatetimeIndex([end_ts]))},
        _robust40_bundle=in_mem_bundle,
    )

    server.finish_live(strategy)

    assert state_reads == [], f'Expected 0 disk reads of state.pkl, got {state_reads}'
    assert validated_bundles == [in_mem_bundle]
    assert not hasattr(strategy, '_robust40_bundle')


