import importlib
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n=600, seed=123, with_nans=False, with_anomalies=False):
    rng = np.random.default_rng(seed)
    base = 100.0 + rng.normal(0, 1.5, n).cumsum()
    base = np.maximum(base, 5.0)
    open_p = base.copy()
    close_p = np.maximum(base + rng.normal(0, 0.8, n), 1.0)
    high_p = np.maximum(open_p, close_p) + np.abs(rng.normal(0, 1.0, n))
    low_p = np.maximum(np.minimum(open_p, close_p) - np.abs(rng.normal(0, 1.0, n)), 0.5)
    vol = np.abs(rng.normal(1000.0, 400.0, n)) + 1.0
    qv = vol * close_p
    tbqv = qv * rng.uniform(0.1, 0.9, n)
    if with_anomalies and n > 300:
        # Inject two strong anomalies separated by > 168h and one within 168h
        high_p[120] = low_p[120] * 50.0
        high_p[150] = low_p[150] * 60.0
        high_p[380] = low_p[380] * 45.0
    if with_nans and n > 20:
        high_p[5] = np.nan
        low_p[10] = np.nan
        open_p[12] = 0.0  # produces inf in (high-low)/open
        qv[7] = 0.0
        tbqv[7] = 0.0
    ts = pd.date_range('2025-06-01 00:25:00', periods=n, freq='1h')
    return pd.DataFrame({
        'candle_begin_time': ts,
        'symbol': 'TEST-USDT',
        'symbol_spot': 'TEST-USDT',
        'symbol_swap': 'TEST-USDT',
        'open': open_p,
        'high': high_p,
        'low': low_p,
        'close': close_p,
        'volume': vol,
        'quote_volume': qv,
        'taker_buy_quote_asset_volume': tbqv,
        'btc_close': 50000.0 + rng.normal(0, 120.0, n).cumsum(),
    })


def test_fast_rolling_rank_pct_matches_pandas_oracle():
    from core.utils.fast_rolling import rolling_rank_pct

    rng = np.random.default_rng(7)
    cases = [
        np.array([], dtype=np.float64),
        np.array([42.0], dtype=np.float64),
        np.array([5.0] * 50, dtype=np.float64),
        np.array([np.nan, 2.0, 2.0, 1.0, np.nan, 2.0, 3.0, np.inf, -np.inf, 2.0, 2.0]),
        rng.choice([1.0, 2.0, 2.0, 3.0, np.nan, np.inf], size=300),
        rng.normal(0, 1, size=1200),
    ]
    for arr in cases:
        s = pd.Series(arr, dtype=np.float64)
        for n in (1, 4, 37, 240):
            expected = s.rolling(n, min_periods=1).rank(ascending=True, pct=True).to_numpy()
            actual = rolling_rank_pct(arr, n)
            np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
            np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-15, equal_nan=True)


def test_fast_rolling_argmax_first_matches_pandas_oracle():
    from core.utils.fast_rolling import rolling_argmax_first

    rng = np.random.default_rng(11)
    cases = [
        np.array([], dtype=np.float64),
        np.array([3.0], dtype=np.float64),
        np.array([1.0, 5.0, 5.0, 2.0, np.nan, 4.0, 3.0, np.nan, 1.0, 6.0]),
        np.arange(100, dtype=np.float64),
        np.arange(100, 0, -1, dtype=np.float64),
        rng.choice([1.0, 2.0, 3.0, 3.0, 4.0], size=400),
        rng.normal(100, 5, size=900),
    ]
    for arr in cases:
        s = pd.Series(arr, dtype=np.float64)
        for n in (1, 3, 25, 400):
            expected = s.rolling(n, min_periods=1).apply(lambda x: x.argmax(), raw=True).to_numpy()
            actual = rolling_argmax_first(arr, n)
            np.testing.assert_array_equal(actual, expected)


def test_zfmeanq_langmax23_biasq_biasv1_match_pandas_oracles():
    import factors.ZfMeanQ as zf_mod
    import factors.BiasQ as bq_mod
    import factors.BiasV1 as bv1_mod
    lang_mod = importlib.import_module('factors.浪Max23')

    for with_nans in (False, True):
        df = _make_ohlcv(500, seed=19 if not with_nans else 23, with_nans=with_nans)
        snap = df.copy(deep=True)

        # ZfMeanQ oracle
        zf_out = zf_mod.signal_multi_params(df, [72, 240, 576])
        amp = (df['high'] - df['low']) / df['open']
        for p in (72, 240, 576):
            exp = amp.rolling(p, min_periods=1).mean().rolling(p, min_periods=1).rank(ascending=True, pct=True)
            np.testing.assert_allclose(zf_out[p].to_numpy(), exp.to_numpy(), rtol=0.0, atol=1e-14, equal_nan=True)

        # 浪Max23 oracle
        lm_out = lang_mod.signal_multi_params(df, [398])
        zfm = amp.rolling(398, min_periods=1).mean().rolling(398, min_periods=1).rank(ascending=True, pct=True)
        vm = df['volume'].rolling(398, min_periods=1).mean().rolling(398, min_periods=1).rank(ascending=True, pct=True)
        np.testing.assert_allclose(lm_out['398'].to_numpy(), np.maximum(zfm, vm).to_numpy(), rtol=0.0, atol=1e-14, equal_nan=True)

        # BiasQ oracle
        bq_out = bq_mod.signal_multi_params(df, [420])
        bias = df['close'] / df['close'].rolling(420, min_periods=1).mean() - 1
        exp_bq = bias.rolling(420, min_periods=1).rank(ascending=True, pct=True)
        np.testing.assert_allclose(bq_out['420'].to_numpy(), exp_bq.to_numpy(), rtol=0.0, atol=1e-14, equal_nan=True)

        # BiasV1 oracle
        bv1_out = bv1_mod.signal_multi_params(df, [120, 2760])
        prices = df.loc[df['close'].notna(), ['close', 'high', 'low']].reset_index(drop=True)
        tp = (prices['close'] + prices['high'] + prices['low']) / 3
        for p in (120, 2760):
            add_index = np.where(prices.index >= p, prices.index - p + 1, 0)
            hm = prices['high'].rolling(p, min_periods=1).apply(lambda x: x.argmax(), raw=True) + add_index
            dist = prices.index - hm + 1
            exp_bv1 = (tp / tp.rolling(p, min_periods=1).mean()) / dist
            np.testing.assert_allclose(bv1_out[p].to_numpy(), exp_bv1.to_numpy(), rtol=0.0, atol=1e-14, equal_nan=True)

        pd.testing.assert_frame_equal(df, snap)


def test_factor_111_isolated_matches_legacy_oracle_without_mutation():
    f111_iso = importlib.import_module('factors.111_输入隔离')
    f111_leg = importlib.import_module('factors.111')

    for n_rows, with_nans in ((0, False), (1, False), (2, False), (400, False), (400, True)):
        df = _make_ohlcv(n_rows, seed=31, with_nans=with_nans)
        if n_rows >= 400:
            # Regression: nonzero quote_volume followed by 20 zero-volume bars (CRWDUSDT case)
            df.loc[50:55, 'quote_volume'] = 1e8
            df.loc[56:80, 'quote_volume'] = 0.0
            df.loc[56:80, 'taker_buy_quote_asset_volume'] = 0.0
        snap = df.copy(deep=True)
        expected = f111_leg.signal_multi_params(df.copy(deep=True), [13, 26, 36])
        actual = f111_iso.signal_multi_params(df, [13, 26, 36])

        pd.testing.assert_frame_equal(df, snap)
        assert list(actual.keys()) == list(expected.keys())
        for k in expected:
            np.testing.assert_array_equal(np.isnan(actual[k].to_numpy()), np.isnan(expected[k].to_numpy()))
            np.testing.assert_allclose(actual[k].to_numpy(), expected[k].to_numpy(), rtol=1e-14, atol=1e-14, equal_nan=True)


def _cci_causal_168_oracle(df, param_list):
    high = df['high'].astype(float).reset_index(drop=True).copy()
    low = df['low'].astype(float).reset_index(drop=True).copy()
    close = df['close'].astype(float).reset_index(drop=True).copy()
    tp = (high + low + close) / 3.0
    if len(df):
        high.iloc[0] = low.iloc[0] = tp.iloc[0] = np.nan
    rolling_low = low.rolling(2).min()
    pctchange = (high.rolling(2).max() - rolling_low) / rolling_low
    times = None
    if 'candle_begin_time' in df:
        times = pd.to_datetime(df['candle_begin_time']).reset_index(drop=True)
    ret = {}
    for param in param_list:
        n = int(param)
        pctma = pctchange.rolling(n, min_periods=1).mean()
        pctstd = pctchange.rolling(n, min_periods=1).std()
        anomaly = ((pctchange - pctma).div(pctstd).abs() > 25) & (pctchange.abs() > 0.2)
        factor = pd.Series(np.nan, index=pd.RangeIndex(len(df)), dtype=float)
        start = 0
        for end in np.append(np.flatnonzero(anomaly.to_numpy()), len(df)):
            segment = tp.iloc[start:end]
            if len(segment):
                ma = segment.rolling(n, min_periods=1).mean()
                md = (segment - ma).abs().rolling(n, min_periods=1).mean() * 0.015
                factor.iloc[start:end] = ((segment - ma) / md.replace(0, np.nan)).to_numpy()
            start = end + 1
        if times is not None:
            last_anomaly = times.where(anomaly).ffill()
            blocked = (times - last_anomaly) <= pd.Timedelta(hours=168)
        else:
            blocked = anomaly.rolling(169, min_periods=1).max().astype(bool)
        factor.loc[blocked] = np.nan
        ret[str(param)] = factor
    return ret


def test_cci_causal_168_matches_oracle_including_anomalies_and_gaps():
    cci_mod = importlib.import_module('factors.Cci异常隔离168_因果版')

    # Case 1: With multiple anomalies + timestamp gap
    df = _make_ohlcv(700, seed=41, with_anomalies=True)
    # Introduce a 50-hour timestamp gap after bar 200
    ts = df['candle_begin_time'].copy()
    ts.iloc[200:] = ts.iloc[200:] + pd.Timedelta(hours=50)
    df['candle_begin_time'] = ts

    for test_df in (df, df.drop(columns=['candle_begin_time']), _make_ohlcv(0, seed=1), _make_ohlcv(5, seed=2)):
        snap = test_df.copy(deep=True)
        expected = _cci_causal_168_oracle(test_df, [240, 816])
        actual = cci_mod.signal_multi_params(test_df, [240, 816])
        pd.testing.assert_frame_equal(test_df, snap)
        for k in expected:
            np.testing.assert_array_equal(np.isnan(actual[k].to_numpy()), np.isnan(expected[k].to_numpy()))
            np.testing.assert_allclose(actual[k].to_numpy(), expected[k].to_numpy(), rtol=1e-11, atol=1e-11, equal_nan=True)

    # Error validation preserved
    bad = df.copy()
    bad.loc[5, 'candle_begin_time'] = bad.loc[4, 'candle_begin_time']
    with pytest.raises(ValueError, match='unique'):
        cci_mod.signal_multi_params(bad, [240])
    with pytest.raises(ValueError, match='positive'):
        cci_mod.signal_multi_params(df, [0])


def test_data_bridge_coin_cap_and_btc_match_reference(tmp_path):
    from core.data_bridge import load_coin_cap, load_coin_btc

    candle = _make_ohlcv(200, seed=55)
    candle['close'] = candle['close'] * 1000.0  # 1000x scaled coin like 1000SATS

    cap_dir = tmp_path / 'cap'
    cap_dir.mkdir()
    extra_ts = pd.date_range('2025-05-30 00:00:00', periods=30, freq='1D')
    extra = pd.DataFrame({
        'candle_begin_time': extra_ts,
        'usd_price': np.linspace(90.0, 110.0, 30),
        'circulating_supply': np.linspace(1e9, 1.1e9, 30),
        'unused_col': np.arange(30, dtype=float),
    })
    # Add duplicate timestamp to verify keep='last'
    extra = pd.concat([extra, extra.iloc[[5]].assign(circulating_supply=2e9)], ignore_index=True)
    extra.to_pickle(cap_dir / 'TEST-USDT.pkl')

    # Reference pandas oracle for load_coin_cap
    ref_extra = extra.drop_duplicates(subset=['candle_begin_time'], keep='last').sort_values('candle_begin_time').copy()
    ref_extra['candle_begin_time'] += pd.Timedelta('23H')
    ref_m = pd.merge_asof(candle[['candle_begin_time', 'close']], ref_extra, on='candle_begin_time', direction='backward')
    ref_times = (ref_m['close'] / ref_m['usd_price']).map(lambda x: 10 ** np.log10(x).round(0))
    ref_mode = ref_times.mode().iloc[0]
    ref_m['circulating_supply'] = (ref_m['circulating_supply'] / ref_mode).ffill()
    expected_cap = ref_m[['circulating_supply']]

    actual_cap = load_coin_cap(str(cap_dir), candle, ['circulating_supply'], 'TEST-USDT')
    pd.testing.assert_frame_equal(actual_cap.reset_index(drop=True), expected_cap.reset_index(drop=True))

    # BTC test + mtime/size cache invalidation
    btc_dir = tmp_path / 'btc' / '25m'
    btc_dir.mkdir(parents=True)
    btc_path = btc_dir / 'BTCUSDT.pkl'
    btc_df1 = pd.DataFrame({
        'candle_begin_time': candle['candle_begin_time'][::2],
        'close': np.linspace(40000.0, 41000.0, len(candle) // 2),
    })
    btc_df1.to_pickle(btc_path)
    out1 = load_coin_btc(str(tmp_path / 'btc'), candle, ['btc_close'], 'TEST-USDT')
    ref_btc = pd.merge(candle[['candle_begin_time']], btc_df1.rename(columns={'close': 'btc_close'}),
                       on='candle_begin_time', how='left')
    ref_btc['btc_close'] = ref_btc['btc_close'].ffill()
    pd.testing.assert_frame_equal(out1.reset_index(drop=True), ref_btc[['btc_close']].reset_index(drop=True))

    # Overwrite BTC file with different content/size and ensure cache invalidates
    btc_df2 = pd.DataFrame({
        'candle_begin_time': candle['candle_begin_time'],
        'close': np.linspace(60000.0, 62000.0, len(candle)),
    })
    btc_df2.to_pickle(btc_path)
    out2 = load_coin_btc(str(tmp_path / 'btc'), candle, ['btc_close'], 'TEST-USDT')
    np.testing.assert_allclose(out2['btc_close'].to_numpy(), btc_df2['close'].to_numpy())


def test_corr_btc_matches_pandas_oracle():
    import factors.CorrBTC as cb_mod

    for with_nans in (False, True):
        df = _make_ohlcv(500, seed=77 if not with_nans else 79, with_nans=with_nans)
        ind1 = df['close'].pct_change()
        ind2 = df['btc_close'].pct_change().fillna(0)
        actual = cb_mod.signal_multi_params(df, [60, 344, 892])
        for p in (60, 344, 892):
            expected = ind1.rolling(p, min_periods=1).corr(ind2).to_numpy()
            got = actual[p].to_numpy()
            np.testing.assert_array_equal(np.isnan(got), np.isnan(expected))
            np.testing.assert_allclose(got, expected, rtol=1e-11, atol=1e-11, equal_nan=True)


def test_direct_polars_factor_build_matches_pandas_roundtrip_exactly(monkeypatch):
    import polars as pl
    import core.select_coin as sc

    monkeypatch.setattr(sc, 'get_snapshot_time', lambda name: None)

    for unsorted, min_kline in ((False, 24), (True, 24), (False, 500)):
        df = _make_ohlcv(120, seed=91)
        df['is_spot'] = np.int64(0)
        df['是否交易'] = np.int64(1)
        df['first_candle_time'] = df['candle_begin_time'].iloc[0]
        if unsorted:
            df = df.iloc[::-1].reset_index(drop=True)

        conf = type('Conf', (), {
            'name': 'test-direct-pl',
            'min_kline_num': min_kline,
            'has_section_factor': True,
            'precompute_factor_configs': {},
            'input_mode': 'arrow_strict',
            'factor_params_dict': {'ZfMeanQ': [12], 'BiasV1': [20]},
        })()
        cols = ['ZfMeanQ_12', 'BiasV1_20']

        expected_pdf = sc.calc_factors_by_candle(df, conf, cols)
        expected_pl = pl.from_pandas(expected_pdf) if len(expected_pdf) else pl.DataFrame()
        actual_pl = sc.calc_factors_by_candle_polars(df, conf, cols)

        assert actual_pl.columns == expected_pl.columns
        assert actual_pl.dtypes == expected_pl.dtypes
        assert actual_pl.equals(expected_pl, null_equal=True)


def test_newcoin_and_hours_since_spot_swap_match_oracles():
    newcoin_mod = importlib.import_module('factors.新币落九天用的')
    hours_mod = importlib.import_module('factors.HoursSinceSpotAndSwap')

    df = _make_ohlcv(100, seed=99)
    df.loc[:10, 'symbol_spot'] = ''
    df.loc[50:55, 'symbol_swap'] = ''

    # Oracle for 新币落九天用的
    ts = pd.to_datetime(pd.Series(df['candle_begin_time']))
    exp_new = ((ts - ts.iloc[0]).dt.total_seconds() / 3600).mask(((df['symbol_swap'] == '') | (df['symbol_spot'] == '')).fillna(False))
    got_new = newcoin_mod.signal_multi_params(df, [1])[1]
    np.testing.assert_allclose(got_new.to_numpy(), exp_new.to_numpy(), rtol=0.0, atol=0.0, equal_nan=True)

    # Oracle for HoursSinceSpotAndSwap
    cond = (df['symbol_swap'] != '') & (df['symbol_spot'] != '')
    first_time = ts.iloc[cond[cond].index[0]]
    exp_hrs = np.where(cond, (ts - first_time).dt.total_seconds() / 3600, np.nan)
    got_hrs = hours_mod.signal_multi_params(df, [1])[1]
    np.testing.assert_allclose(got_hrs.to_numpy(), exp_hrs, rtol=0.0, atol=0.0, equal_nan=True)
