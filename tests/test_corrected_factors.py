"""因子替换：输入隔离、因果性、版本化配置与全历史回退。"""
import ast
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

MOMENTUM = '动量210401_双项异常隔离V2'
REPLACEMENTS = {'111': '111_输入隔离', 'Cci按异常0401修正': 'Cci异常隔离168_因果版',
                '动量因子210401修正': MOMENTUM}


def candles(length=1200):
    x = np.arange(length)
    price = 100 + x * .002 + np.sin(x / 15)
    return pd.DataFrame(dict(candle_begin_time=pd.date_range('2025-01-01', periods=length, freq='h'),
                             first_candle_time=pd.Timestamp('2025-01-01'),
                             last_candle_time=pd.Timestamp('2025-04-01'),
                             high=price+1, low=price-1, close=price, quote_volume=100+x,
                             taker_buy_quote_asset_volume=40+x*.4,
                             symbol='BTCUSDT', symbol_swap='BTCUSDT', symbol_spot='',
                             is_spot=0, **{'是否交易': 1}))


@pytest.mark.parametrize('name', list(REPLACEMENTS.values()))
def test_new_factors_do_not_mutate_input_or_depend_on_parameter_order(name):
    df = candles()
    before = df.copy(deep=True)
    module = importlib.import_module(f'factors.{name}')
    params = [168, 800]
    normal = module.signal_multi_params(df, params)
    reverse = module.signal_multi_params(df, params[::-1])
    pd.testing.assert_frame_equal(df, before, check_exact=True)
    for param in params:
        pd.testing.assert_series_equal(normal[str(param)], reverse[str(param)], check_exact=True)


def test_111_preserves_math_on_isolated_input():
    df = candles()
    old = importlib.import_module('factors.111').signal_multi_params(df.copy(deep=True), [26])
    new = importlib.import_module('factors.111_输入隔离').signal_multi_params(df, [26])
    pd.testing.assert_series_equal(old['26'], new['26'], check_exact=True)


def test_cci_prefix_and_cooldown_are_causal():
    module = importlib.import_module('factors.Cci异常隔离168_因果版')
    df = candles(1600)
    df.loc[900, 'high'] = 10000
    result = module.signal_multi_params(df, [1200])['1200']
    assert result.iloc[899] == result.iloc[899]
    assert result.iloc[900:1069].isna().all()
    assert np.isfinite(result.iloc[1070])
    for end in [1, 2, 168, 900, 901, 1069, 1070, 1400]:
        pd.testing.assert_series_equal(module.signal_multi_params(df.iloc[:end], [1200])['1200'],
                                       result.iloc[:end], check_exact=True)


def test_active_account_and_producer_use_new_names():
    for path in Path('accounts').glob('*.py'):
        if path.name.startswith('_'):
            continue
        values = [n.value for n in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig')))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not set(REPLACEMENTS).intersection(values), path
    producer_tree = ast.parse(Path('D:/alpha/config.py').read_text(encoding='utf-8-sig'))
    conf = next(ast.literal_eval(n.value) for n in producer_tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'precompute_factor_configs' for t in n.targets))
    assert MOMENTUM in conf and '动量因子210401修正' not in conf
    assert 'CciV3' in conf and 'ILLQStdBbi45208' in conf


@pytest.mark.parametrize('arrow_state', ['absent', 'old_columns', 'new_columns'])
def test_versioned_precompute_fallback_uses_history_before_trimming(tmp_path, monkeypatch, arrow_state):
    import core.utils.datatools as dt
    df = candles(1600)
    param = 8568
    column = f'{MOMENTUM}_{param}'
    expected = importlib.import_module(f'factors.{MOMENTUM}').signal_multi_params(df, [param])[str(param)]
    (tmp_path / '0m').mkdir()
    pd.to_pickle({'BTCUSDT': df}, tmp_path / '0m/swap_dict_batch1.pkl')
    arrow = df.tail(500).copy()
    if arrow_state == 'new_columns':
        arrow[column] = expected.tail(500)
    else:
        arrow['动量因子210401修正_8568'] = 123.0
    monkeypatch.setattr(dt, 'bmac_data_path', tmp_path)
    monkeypatch.setattr(dt, 'get_dummy_client', lambda: Mock(**{'get_market_info.return_value': {'symbol_list': ['BTCUSDT']}}))
    monkeypatch.setattr(dt, 'read_arrow_market_dict_for_run_time',
                        lambda *args: None if arrow_state == 'absent' else {'BTCUSDT': arrow})
    conf = SimpleNamespace(black_list=[], white_list=[], min_kline_num=1,
                           get_kline_num=lambda: 400, precompute_factor_configs={MOMENTUM: [param]})
    actual = dt.load_bmac_data('swap', pd.Timestamp('2026-01-01'), conf)['BTCUSDT']
    np.testing.assert_allclose(actual[column], expected.tail(400), rtol=2e-11, atol=2e-11)
