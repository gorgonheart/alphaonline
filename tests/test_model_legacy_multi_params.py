"""MODEL legacy factors: independent multi-parameter entry points, unchanged math.

The legacy signal functions remain independent compatibility oracles.  Literal
boundary cases additionally protect the less obvious original definitions.
"""
import importlib
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


PARAMS = {
    'Atr': [2, 5], 'Bias': [2, 5], 'Bias (2)': [2, 5],
    'BiasV1': [2, 5], 'Cci过滤无现货删数据': [2, 5],
    'CirculatingMcap': [1, 2], 'CorrBTC': [2, 5],
    'HoursSinceSpotAndSwap': [1, 2], 'MinMax': [2, 5],
    'SelectCoin': ['BTC-USDT', 'ETHUSDT'], 'SpotSwapBoth': [1, 2],
    'ZfMeanQ': [2, 5], 'ZfStd': [2, 5], '双均线': [6, 10],
    '新币': [1, 2], '新币落九天用的': [1, 2], '资金费率绝对值MAX': [2, 5],
}


@lru_cache(maxsize=1)
def model_parameters():
    """Read just pure config assignments, without executing account modules."""
    from tools.robust40.offline import assignment
    result = {name: set() for name in PARAMS}

    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            if len(value) > 1 and isinstance(value[0], str) and value[0] in result:
                result[value[0]].add(value[2] if isinstance(value[1], bool) else value[1])
            if len(value) == 3 and value[1] == 'Cci过滤无现货删数据':
                result[value[1]].add(value[2])
            for item in value:
                visit(item)

    root = Path(__file__).resolve().parents[1]
    for offset in [20, 40, 55]:
        path = root / f'accounts/{offset}m_robust_4h.py'
        visit(assignment(path, 'strategy_pool'))
        visit(assignment(path, 'strategy_config'))
    assert all(result.values())
    return {name: sorted(values) for name, values in result.items()}


def candles(length=32, case='normal'):
    x = np.arange(length)
    close = 20 + x * .03 + np.sin(x / 3)
    df = pd.DataFrame(dict(
        candle_begin_time=pd.date_range('2025-01-01', periods=length, freq='h'),
        open=close - .2, high=close + 1, low=close - .5, close=close,
        btc_close=30 + np.sin(x / 4), circulating_supply=100 + x,
        funding_fee=np.resize([0., -.03, 0., -.01, .02, np.nan], length),
        symbol='BTCUSDT', symbol_spot='BTCUSDT', symbol_swap='BTCUSDT'))
    if length:
        df.loc[df.index[:2], 'symbol_spot'] = ''
        df.loc[df.index[5::7], 'symbol_swap'] = ''
    if case == 'edge' and length:
        df.loc[df.index[::9], ['close', 'high', 'low']] = np.nan
        df.loc[df.index[1::8], ['open', 'close']] = 0.
        df.loc[df.index[4::11], 'high'] = np.nan
    if case == 'inf' and length:
        df.loc[df.index[::5], ['open', 'high', 'close', 'btc_close']] = np.inf
        df.loc[df.index[1::7], ['low', 'funding_fee']] = -np.inf
    if case == 'float32':
        for col in ['open', 'high', 'low', 'close', 'btc_close', 'funding_fee']:
            df[col] = df[col].astype('float32')
    if case == 'nullable_symbols':
        for col in ['symbol_spot', 'symbol_swap']:
            df[col] = df[col].astype('string')
            df.loc[df.index[3::7], col] = pd.NA
    if case == 'constant':
        df[['open', 'high', 'low', 'close', 'btc_close']] = 3.
    if case == 'index':
        df.index = pd.Index(np.arange(length) * 3 + 101, name='row')
    if case == 'missing_btc':
        df = df.drop(columns='btc_close')
    if case == 'no_pair':
        df['symbol_spot'] = ''
    return df


def assert_same(actual, expected):
    pd.testing.assert_series_equal(actual, expected, check_names=False, check_exact=True)


@pytest.mark.parametrize('name', PARAMS)
def test_empty_parameters_do_not_require_any_input_columns(name):
    module = importlib.import_module(f'factors.{name}')
    assert module.signal_multi_params(pd.DataFrame(), []) == {}


@pytest.mark.parametrize('name', PARAMS)
@pytest.mark.parametrize('case,length', [
    ('normal', 32), ('edge', 32), ('constant', 10), ('index', 32),
    ('missing_btc', 16), ('no_pair', 8), ('normal', 1), ('normal', 0),
    ('inf', 32), ('float32', 32),
    ('nullable_symbols', 32),
])
def test_multi_matches_legacy_values_indices_dtypes_or_exception(name, case, length):
    module = importlib.import_module(f'factors.{name}')
    assert callable(getattr(module, 'signal_multi_params', None)), name
    df = candles(length, case)
    before = df.copy(deep=True)
    params = PARAMS[name]
    try:
        expected = {param: module.signal(df.copy(deep=True), param, 'result')['result']
                    for param in params}
    except (IndexError, TypeError, ValueError) as exc:
        with pytest.raises(type(exc)):
            module.signal_multi_params(df, params)
    else:
        result = module.signal_multi_params(df, params)
        assert set(result) == set(params)
        for param in params:
            assert_same(result[param], expected[param])
    pd.testing.assert_frame_equal(df, before, check_exact=True)


@pytest.mark.parametrize('name', PARAMS)
@pytest.mark.parametrize('container', [list, tuple, set])
def test_multi_is_independent_of_legacy_entry_point_and_parameter_order(name, container):
    module = importlib.import_module(f'factors.{name}')
    assert callable(getattr(module, 'signal_multi_params', None)), name
    df = candles()
    before = df.copy(deep=True)
    params = PARAMS[name]
    expected = {param: module.signal(df.copy(deep=True), param, 'result')['result']
                for param in params}
    # The new path must calculate directly, rather than wrap signal per parameter.
    with patch.object(module, 'signal', side_effect=AssertionError('legacy path')):
        first = module.signal_multi_params(df, container(params))
        reverse = module.signal_multi_params(df, container(params[::-1]))
        assert module.signal_multi_params(df, []) == {}
    for param in params:
        assert_same(first[param], expected[param])
        assert_same(reverse[param], expected[param])
    pd.testing.assert_frame_equal(df, before, check_exact=True)


def test_literal_boundary_oracles_keep_original_factor_definitions():
    df = candles(4)
    df[['open', 'high', 'low', 'close']] = np.array([
        [2., 3., 1., 2.], [4., 5., 3., 4.],
        [2., 3., 1., 2.], [2., 3., 1., 2.]])
    df['funding_fee'] = [0., -.03, 0., -.01]
    df['symbol_spot'] = ['', '', 'BTCUSDT', 'BTCUSDT']
    expected = {
        'Bias (2)': (2, [0., 1/3, -1/3, 0.]),
        'Bias': (2, [np.nan, np.nan, -1/3, -.25]),
        'MinMax': (2, [np.nan, .5, -.5, np.nan]),
        'ZfMeanQ': (2, [1., .5, .75, 1.]),
        'ZfStd': (2, [np.nan, np.sqrt(1.125), np.sqrt(1.125), 0.]),
        '双均线': (7, [0., 0., 1/3, -.5]),
        'HoursSinceSpotAndSwap': (1, [np.nan, np.nan, 0., 1.]),
        '新币落九天用的': (1, [np.nan, np.nan, 2., 3.]),
        '资金费率绝对值MAX': (2, [np.nan, .03, .03, .01]),
        'SpotSwapBoth': (1, [0, 0, 1, 1]),
    }
    for name, (param, values) in expected.items():
        module = importlib.import_module(f'factors.{name}')
        actual = module.signal_multi_params(df, [param])[param]
        pd.testing.assert_series_equal(actual, pd.Series(values), check_names=False,
                                       rtol=1e-14, atol=1e-14)


@pytest.mark.parametrize('name', PARAMS)
@pytest.mark.parametrize('shift', [-1, 0, 2])
def test_actual_core_dispatch_and_shift_match_legacy_continuous_parameters(name, shift, monkeypatch):
    from core.factor import calc_factor_vals
    from core.utils.factor_hub import FactorHub
    module = importlib.import_module(f'factors.{name}')
    assert callable(getattr(module, 'signal_multi_params', None)), name
    df = candles()
    before = df.copy(deep=True)
    params = PARAMS[name]
    legacy = df.copy(deep=True)
    expected = {}
    for param in params:
        column = f'{name}_{param}'
        legacy = module.signal(legacy, param, column)
        expected[column] = legacy[column].shift(shift).to_numpy()
    # External columns are already in this fixture.  Keep real FactorHub and
    # calc_factor_vals dispatch, disabling only redundant local data loading.
    factor = FactorHub.get_by_name(name)
    monkeypatch.setattr(factor, 'extra_data_dict', {}, raising=False)
    actual = calc_factor_vals(df, name, params, shift=shift)
    reverse = calc_factor_vals(df, name, params[::-1], shift=shift)
    assert actual.keys() == expected.keys()
    for column in expected:
        np.testing.assert_array_equal(actual[column], expected[column])
        np.testing.assert_array_equal(reverse[column], expected[column])
    pd.testing.assert_frame_equal(df, before, check_exact=True)


@pytest.mark.parametrize('params', [[2, 3], [3, 2], [2, 3, 2]])
@pytest.mark.parametrize('zero', [0, '0'])
def test_funding_dirty_values_keep_legacy_continuous_preprocessing(params, zero):
    from core.factor import calc_factor_vals
    module = importlib.import_module('factors.资金费率绝对值MAX')
    df = candles(8)
    df['funding_fee'] = ['-0.03', 'bad', zero, zero, zero, '-0.01', zero, '-0.02']
    before = df.copy(deep=True)
    legacy = df.copy(deep=True)
    expected = {}
    for param in params:
        legacy = module.signal(legacy, param, 'result')
        expected[param] = legacy['result'].copy()
    actual = module.signal_multi_params(df, params)
    core = calc_factor_vals(df, '资金费率绝对值MAX', params)
    for param in expected:
        assert_same(actual[param], expected[param])
        np.testing.assert_array_equal(core[f'资金费率绝对值MAX_{param}'], expected[param].to_numpy())
    pd.testing.assert_frame_equal(df, before, check_exact=True)


@pytest.mark.parametrize('length', [4050, 14050])
@pytest.mark.parametrize('name', PARAMS)
def test_actual_model_parameters_match_legacy_continuous_windows(name, length):
    module = importlib.import_module(f'factors.{name}')
    df = candles(length)
    before = df.copy(deep=True)
    params = model_parameters()[name]
    legacy = df.copy(deep=True)
    expected = {}
    for param in params:
        column = f'{name}_{param}'
        legacy = module.signal(legacy, param, column)
        expected[param] = legacy[column].copy()
    actual = module.signal_multi_params(df, params)
    for param in params:
        assert_same(actual[param], expected[param])
    pd.testing.assert_frame_equal(df, before, check_exact=True)


@pytest.mark.parametrize('name,data_name,column', [
    ('CirculatingMcap', 'coin-cap', 'circulating_supply'),
    ('CorrBTC', 'coin-btc', 'btc_close'),
])
def test_real_core_external_data_bridge_injects_columns_before_multi(name, data_name, column,
                                                                   tmp_path, monkeypatch):
    from core.factor import calc_factor_vals
    from core.utils.datatools import merge_data
    import config
    df = candles().drop(columns=column)
    if data_name == 'coin-cap':
        source = pd.DataFrame(dict(candle_begin_time=[pd.Timestamp('2024-12-31')],
                                   usd_price=[20.], circulating_supply=[100.]))
        source.to_pickle(tmp_path / 'BTCUSDT.pkl')
        loader = 'load_coin_cap'
    else:
        (tmp_path / '0m').mkdir()
        source = candles()[['candle_begin_time', 'btc_close']].rename(columns={'btc_close': 'close'})
        source.to_pickle(tmp_path / '0m/BTCUSDT.pkl')
        loader = 'load_coin_btc'
    monkeypatch.setitem(config.data_source_dict, data_name, (loader, tmp_path))
    expected_input = df.copy(deep=True)
    expected_input[column] = merge_data(df, data_name, [column])[column].to_numpy()
    module = importlib.import_module(f'factors.{name}')
    params = PARAMS[name]
    legacy = expected_input.copy(deep=True)
    expected = {}
    for param in params:
        key = f'{name}_{param}'
        legacy = module.signal(legacy, param, key)
        expected[key] = legacy[key].shift(1).to_numpy()
    actual = calc_factor_vals(df, name, params, shift=1)
    for key in expected:
        np.testing.assert_array_equal(actual[key], expected[key])
    pd.testing.assert_frame_equal(df, expected_input, check_exact=True)
