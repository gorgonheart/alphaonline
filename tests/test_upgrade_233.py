"""Offline behavior oracles for the 2.3.3 incremental integration."""
from datetime import datetime
import importlib.util
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from core.model.account_config import AccountConfig
from core.binance.base_client import BinanceClient
from core.binance.portfolio_margin_client import PortfolioMarginClient


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('offline only')))
    monkeypatch.setattr(AccountConfig, 'load_delist', staticmethod(lambda: []))


def orders():
    return pd.DataFrame({
        'symbol': ['ABCUSDT', 'ABCUSDT', 'OTHERUSDT'],
        'symbol_type': ['spot', 'swap', 'swap'],
        '实际下单资金': [1000., -1000., 600.],
        '实际下单量': [10., -10., 6.],
        '目标持仓量': [10., 0., -4.],
        '交易模式': ['建仓', '清仓', '调仓'],
    }).set_index(['symbol', 'symbol_type'])


@pytest.mark.parametrize('limits,want', [
    ({}, [1000., -1000., 600.]),
    ({'*': 300, 'ABCUSDT': 200}, [200., -200., 300.]),
    ({'ABCUSDT': 200}, [200., -200., 600.]),
    ({'*': 7}, [7., -7., 7.]),
])
def test_limit_preserves_target_modes_and_input(limits, want):
    account = AccountConfig('offline', max_symbol_order_amount=limits)
    before = orders()
    frame = before.copy(deep=True)
    result = account.limit_symbol_order_amount(frame)
    assert result['实际下单资金'].tolist() == want
    assert result['实际下单量'].tolist() == pytest.approx([v / 100 for v in want])
    pd.testing.assert_frame_equal(frame, before)
    pd.testing.assert_series_equal(result['目标持仓量'], before['目标持仓量'])
    pd.testing.assert_series_equal(result['交易模式'], before['交易模式'])
    assert account.max_symbol_order_amount == limits


@pytest.mark.parametrize('bad', [None, [], False, 2, '300', {'': 20}, {1: 20},
    {'ABCUSDT': 0}, {'*': -1}, {'*': True}, {'*': '300'},
    {'*': float('nan')}, {'*': float('inf')}])
def test_invalid_limits_never_silently_disable_protection(bad):
    with pytest.raises(ValueError, match='max_symbol_order_amount'):
        AccountConfig('offline', max_symbol_order_amount=bad)


def test_empty_disabled_and_unmatched_fast_paths():
    account = AccountConfig('offline')
    frame = orders()
    assert account.limit_symbol_order_amount(frame) is frame
    account.max_symbol_order_amount = {'MISSING': 10}
    assert account.limit_symbol_order_amount(frame) is frame
    empty = frame.iloc[:0]
    assert account.limit_symbol_order_amount(empty) is empty


def trading_account(limits, current=0., price=100.):
    account = AccountConfig('offline', max_symbol_order_amount=limits)
    account.use_spot = False
    account.swap_position = pd.DataFrame({'symbol': ['ABCUSDT'], '当前持仓量': [current]}).set_index('symbol')
    prices = pd.Series({'ABCUSDT': price}, name='price').rename_axis('symbol')
    account.bn = SimpleNamespace(get_swap_ticker_price_series=lambda: prices,
        get_spot_ticker_price_series=lambda: prices.iloc[:0])
    return account


def target(quantity):
    return pd.DataFrame({'symbol': ['ABCUSDT'], 'symbol_type': ['swap'], '目标持仓量': [quantity]})


@pytest.mark.parametrize('current,desired,quantity,mode', [
    (0., 10., 2., '建仓'), (10., 0., -2., '清仓'),
    (10., 20., 2., '加仓'), (10., 5., -2., '减仓'), (10., -10., -2., '调仓'),
])
def test_real_order_calculation_routes_all_modes(current, desired, quantity, mode):
    account = trading_account({'*': 200}, current)
    plan = target(desired)
    before = plan.copy(deep=True)
    result = account.calc_order_amount(plan)
    assert result.loc[0, '实际下单量'] == quantity
    assert result.loc[0, '实际下单资金'] == quantity * 100
    assert result.loc[0, '交易模式'] == mode
    assert result.loc[0, '目标持仓量'] == desired
    pd.testing.assert_frame_equal(plan, before)


def test_each_round_recomputes_current_target_and_has_no_hourly_state():
    for current, desired, want in [(0., 10., 2.), (2., 10., 2.), (4., 3., -1.)]:
        result = trading_account({'*': 200}, current).calc_order_amount(target(desired))
        assert result.loc[0, '实际下单量'] == want


@pytest.mark.parametrize('price', [np.nan, 0., -1., np.inf])
def test_limited_invalid_price_fails_before_order_dispatch(price):
    with pytest.raises(ValueError, match='报价'):
        trading_account({'*': 200}, price=price).calc_order_amount(target(10.))


def test_missing_unlimited_price_keeps_legacy_behavior():
    result = trading_account({'OTHER': 200}, price=np.nan).calc_order_amount(target(10.))
    assert result.loc[0, '实际下单资金'] == 1.


@pytest.mark.parametrize('current,desired,maker,reduce_only,tif', [
    (0., 10., 0., 'false', 'IOC'), (10., 0., 1., 'true', 'IOC'),
    (10., 20., 1., 'false', 'GTC'), (10., 5., 1., 'true', 'GTC'),
    (10., -10., 1., 'false', 'GTC'),
])
def test_limit_flows_through_real_split_and_client_parameters(monkeypatch, current, desired, maker, reduce_only, tif):
    import core.model.account_config as accounts
    import core.utils.exchange_hub as hub
    account = trading_account({'*': 200}, current)
    account.maker_ratio = maker
    plan = account.calc_order_amount(target(desired))
    client = BinanceClient.__new__(BinanceClient)
    info = dict(min_qty={'ABCUSDT': 3}, price_precision={'ABCUSDT': 2}, min_notional={'ABCUSDT': 5})
    monkeypatch.setattr(hub, 'get_market_info_store', lambda: {'swap': info})
    client.get_market_info = lambda _: info
    client._swap_ticker_price = {'ABCUSDT': 100.}
    client.fetch_ticker_price = lambda **kw: [{'symbol': 'ABCUSDT', 'price': '100'}]
    client.fetch_book_ticker = lambda *a: {'bidPrice': '99', 'askPrice': '101'}
    client.order_money_limit = {'swap': 5}
    calls = []
    def place(**kwargs):
        calls.append(kwargs)
        return dict(status='FILLED', timeInForce=kwargs['timeInForce'], orderId=len(calls),
                    origQty=kwargs['quantity'], executedQty=kwargs['quantity'])
    client.place_swap_order = place
    account.bn = client
    account.max_one_order_amount = 1000
    monkeypatch.setattr(accounts.time, 'sleep', lambda _: None)
    success, failed = account.proceed_swap_order(plan, datetime(2026, 11, 6, 1))
    assert len(success) == 1 and failed.empty
    assert calls[0]['quantity'] == '2'
    assert calls[0]['timeInForce'] == tif and calls[0]['reduceOnly'] == reduce_only
    assert calls[0]['side'] == ('BUY' if desired > current else 'SELL')
    expected_price = ('99.00' if desired > current else '101.00') if tif == 'GTC' else (
        '101.50' if desired > current else '98.50')
    assert calls[0]['price'] == expected_price


def test_default_real_calculation_keeps_unlimited_target_gap():
    result = trading_account({}).calc_order_amount(target(10.))
    assert result.loc[0, '实际下单量'] == 10.
    assert result.loc[0, '实际下单资金'] == 1000.


def test_subminimum_limit_is_not_raised_to_market_minimum(monkeypatch):
    import core.utils.exchange_hub as hub
    account = trading_account({'*': 3})
    plan = account.calc_order_amount(target(10.))
    monkeypatch.setattr(hub, 'get_market_info_store', lambda: {
        'swap': dict(min_qty={'ABCUSDT': 3}, min_notional={'ABCUSDT': 5})})
    account.bn.place_swap_orders_bulk2 = Mock(side_effect=AssertionError('too small'))
    success, failures = account.proceed_swap_order(plan, datetime(2026, 11, 6))
    assert success == [] and failures.empty
    assert account.max_symbol_order_amount == {'*': 3}


@pytest.mark.parametrize('selling,maker', [(False, 0.), (False, 1.), (True, 0.), (True, 1.)])
def test_spot_limit_reaches_existing_buy_sell_routes(monkeypatch, selling, maker):
    import core.model.account_config as accounts
    import core.utils.exchange_hub as hub
    from core.model.account_type import AccountType
    account = AccountConfig('offline', max_symbol_order_amount={'*': 200}, maker_ratio=maker)
    account.account_type = AccountType.PORTFOLIO_MARGIN
    info = dict(min_qty={'ABCUSDT': 3}, price_precision={'ABCUSDT': 2}, min_notional={'ABCUSDT': 10})
    monkeypatch.setattr(hub, 'get_market_info_store', lambda: {'spot': info})
    client = BinanceClient.__new__(BinanceClient)
    client.get_market_info = lambda _: info
    client._spot_ticker_price = {'ABCUSDT': 100.}
    client.fetch_ticker_price = lambda **kw: [{'symbol': 'ABCUSDT', 'price': '100'}]
    client.fetch_book_ticker = lambda *a: {'bidPrice': '99', 'askPrice': '101'}
    client.order_money_limit = {'spot': 10}
    client.collect_asset = lambda: None
    sent = []
    def place(**kwargs):
        sent.append(kwargs)
        return dict(status='FILLED', orderId=1, timeInForce=kwargs['timeInForce'],
                    origQty=kwargs['quantity'], executedQty=kwargs['quantity'])
    client.place_spot_order = place
    account.bn = client
    account.max_one_order_amount = 1000
    monkeypatch.setattr(accounts.time, 'sleep', lambda _: None)
    sign = -1 if selling else 1
    frame = pd.DataFrame([dict(symbol='ABCUSDT', symbol_type='spot',
        实际下单资金=1000.*sign, 实际下单量=10.*sign, 交易模式='减仓' if selling else '加仓')]).set_index(['symbol', 'symbol_type'])
    limited = account.limit_symbol_order_amount(frame).reset_index()
    success, failures = account.proceed_spot_order(limited, False, is_only_sell=selling,
                                                  run_time=datetime(2026, 11, 6))
    assert len(success) == 1 and failures.empty
    assert sent[0]['quantity'] == '2' and sent[0]['side'] == ('SELL' if selling else 'BUY')
    assert sent[0]['timeInForce'] == ('GTC' if maker else 'IOC')
    assert 'reduceOnly' not in sent[0]


@pytest.fixture
def stocks(monkeypatch):
    import core.binance.base_client as base
    monkeypatch.setattr(BinanceClient, 'bstocks_coins', set())
    monkeypatch.setattr(BinanceClient, '_bstocks_last_update', 0)
    monkeypatch.setattr(base.time, 'time', lambda: 100000)
    monkeypatch.setattr(base, 'retry_wrapper', lambda fn, **kw: fn())
    monkeypatch.setattr(base, 'send_wechat_work_msg', lambda *a: None)
    client = BinanceClient.__new__(BinanceClient)
    client.api_key = 'offline'; client.filter_bstocks = True
    client.exchange = SimpleNamespace(sapi_get_capital_config_getall=Mock(return_value=[]),
        sapi_get_equity_market_tokenized_assets=Mock(return_value=[]))
    return client


def test_bstocks_combines_sources_and_parentheses(stocks):
    stocks.exchange.sapi_get_capital_config_getall.return_value = [
        {'coin': 'A', 'name': 'A（bStocks）'}, {'coin': 'B', 'name': 'B(bStocks)'},
        {'coin': 'BTC', 'name': 'Bitcoin'}]
    stocks.exchange.sapi_get_equity_market_tokenized_assets.return_value = [{'assetCode': 'C'}]
    assert stocks.fetch_bstocks_coins() == {'A', 'B', 'C'}
    assert BinanceClient._bstocks_last_update == 100000


@pytest.mark.parametrize('which', ['sapi_get_capital_config_getall', 'sapi_get_equity_market_tokenized_assets'])
def test_partial_failure_preserves_previous_list(stocks, which):
    BinanceClient.bstocks_coins = {'OLD'}
    getattr(stocks.exchange, which).side_effect = RuntimeError('offline failed')
    assert stocks.fetch_bstocks_coins() == {'OLD'}


@pytest.mark.parametrize('bad', [None, {}, [{'unexpected': 'value'}]])
def test_malformed_second_source_cannot_clear_known_cache(stocks, bad):
    BinanceClient.bstocks_coins = {'OLD'}
    stocks.exchange.sapi_get_equity_market_tokenized_assets.return_value = bad
    assert stocks.fetch_bstocks_coins() == {'OLD'}


def test_cold_partial_empty_is_not_success(stocks):
    stocks.exchange.sapi_get_equity_market_tokenized_assets.side_effect = RuntimeError('failed')
    assert stocks.fetch_bstocks_coins() == set()
    assert BinanceClient._bstocks_last_update == 0


def test_both_success_empty_can_remove_old_list(stocks):
    BinanceClient.bstocks_coins = {'OLD'}
    assert stocks.fetch_bstocks_coins() == set()
    assert BinanceClient._bstocks_last_update == 100000


@pytest.mark.parametrize('guard', ['disabled', 'dummy', 'cached'])
def test_bstocks_fast_paths_make_no_requests(stocks, guard):
    if guard == 'disabled': stocks.filter_bstocks = False
    if guard == 'dummy': stocks.api_key = ''
    if guard == 'cached': BinanceClient._bstocks_last_update = 99999
    stocks.fetch_bstocks_coins()
    stocks.exchange.sapi_get_capital_config_getall.assert_not_called()
    stocks.exchange.sapi_get_equity_market_tokenized_assets.assert_not_called()


def wallet_row(symbol='USDT', free=100., um=0., cm=0., locked=0., borrowed=0., interest=0., pnl=0.):
    return dict(symbol=symbol, 当前持仓量=free, crossMarginLocked=locked, crossMarginBorrowed=borrowed,
        crossMarginInterest=interest, umWalletBalance=um, cmWalletBalance=cm, umUnrealizedPNL=pnl, cmUnrealizedPNL=0.)


def wallet_client(rows, swap_pnl=0.):
    client = PortfolioMarginClient.__new__(PortfolioMarginClient)
    client.base_margin = {'USDT': .5, 'USDC': .5}
    client.base_coin_amount = {}
    client.coin_margin = {}; client.seed_coins = []
    client.market_info = {'spot': {'symbol_list': []}}
    client.fetch_spot_ticker_price = lambda: []
    client.get_balance_df = lambda: pd.DataFrame(rows)
    # Deliberately inconsistent second response must not leak into the same snapshot.
    client.get_swap_usdt_balance = Mock(return_value=999.)
    client.get_swap_position_df = lambda: pd.DataFrame({'symbol': ['BTCUSDT'], '当前持仓量': [1.],
        '仓位价值': [100.], '持仓盈亏': [swap_pnl]}).set_index('symbol')
    return client


@pytest.mark.parametrize('rows,pnl,spot,swap,equity', [
    ([wallet_row(um=20)], 0., 100., 20., 120.),
    ([wallet_row(um=20, cm=30)], 0., 130., 20., 150.),
    ([wallet_row(um=-20)], 0., 100., -20., 80.),
    ([wallet_row(free=-10, um=20)], 0., -10., 20., 10.),
    ([wallet_row(um=20), wallet_row('USDC', 50, 30, 10)], 0., 190., 20., 210.),
    ([wallet_row(um=20, locked=3, borrowed=10, interest=1, pnl=7)], 7., 103., 27., 119.),
    ([wallet_row(um=0, cm=-10)], 0., 100., 0., 90.),
])
def test_wallet_allocates_each_balance_once(rows, pnl, spot, swap, equity):
    client = wallet_client(rows, pnl)
    result = client.get_account_overview()
    assert result['spot_assets']['equity'] == spot
    assert result['swap_assets']['equity'] == swap
    assert result['account_equity'] == equity
    client.get_swap_usdt_balance.assert_not_called()


def test_encryption_tool_round_trips_optional_password_without_import_side_effects(capsys):
    from core.utils.encryptor import AESEncryptor
    path = Path(__file__).resolve().parents[1] / '加密apikey.py'
    assert path.exists(), 'missing standalone encryption tool'
    spec = importlib.util.spec_from_file_location('encrypt_tool', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert capsys.readouterr().out == ''
    result = module.encrypt_credentials('offline key', 'sample-api', 'sample-secret', 'sample-passphrase')
    decrypt = AESEncryptor('offline key').decrypt
    assert {key: decrypt(value) for key, value in result.items()} == {
        'apiKey': 'sample-api', 'secret': 'sample-secret', 'password': 'sample-passphrase'}
    assert 'password' not in module.encrypt_credentials('offline key', 'sample-api', 'sample-secret')
