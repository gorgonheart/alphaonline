"""No network or notifications: exercise the actual bStocks refresh with stubs."""
from types import SimpleNamespace
import socket

import pytest


@pytest.fixture
def setup_market(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Network forbidden in offline bStocks tests')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',forbidden)
    import core.binance.base_client as base
    # This patch covers every test branch, including a malformed fixture.
    notifications=[]
    monkeypatch.setattr(base,'send_wechat_work_msg',lambda *a:notifications.append('suppressed'))
    monkeypatch.setattr(base.BinanceClient,'bstocks_coins',set())
    monkeypatch.setattr(base.BinanceClient,'_bstocks_last_update',0)
    client=object.__new__(base.BinanceClient)
    client.filter_bstocks=True;client.api_key='offline-placeholder'
    client.exchange=SimpleNamespace(sapi_get_capital_config_getall=lambda:None)
    import core.model.account_config as accounts
    monkeypatch.setattr(accounts,'AccountConfig',lambda *a,**k:SimpleNamespace(
        bn=client,init_exchange=lambda *a:None))
    from tools.robust40 import market_inputs as warm
    calls=[]
    public=SimpleNamespace(fetch_market_info=lambda kind:calls.append(kind))
    monkeypatch.setattr(warm,'public_market_client',lambda:public)
    return base,client,warm,calls,notifications


def test_first_refresh_failure_stops_before_public_market_fetch(setup_market,monkeypatch):
    base,client,warm,calls,notifications=setup_market
    def fail(*args,**kwargs):raise RuntimeError('offline refresh failure')
    monkeypatch.setattr(base,'retry_wrapper',fail)
    with pytest.raises(ValueError,match='bStocks'):
        warm.prepare_public_market()
    assert calls==[]
    assert notifications==['suppressed']


@pytest.mark.parametrize('rows,expected', [([],set()),
    ([{'coin':'STOCK','name':'Example (bStocks)'},{'coin':'BTC','name':'Bitcoin'}],{'STOCK'})])
def test_successful_automatic_refresh_including_empty_result(setup_market,monkeypatch,rows,expected):
    base,client,warm,calls,notifications=setup_market
    monkeypatch.setattr(base,'retry_wrapper',lambda *a,**k:rows)
    warm.prepare_public_market()
    assert base.BinanceClient.bstocks_coins==expected
    assert base.BinanceClient._bstocks_last_update>0
    assert calls==['spot','swap'] and not notifications


def test_existing_cache_failure_policy_is_preserved(setup_market,monkeypatch):
    base,client,warm,calls,notifications=setup_market
    base.BinanceClient.bstocks_coins={'OLD'}
    base.BinanceClient._bstocks_last_update=1
    def fail(*args,**kwargs):raise RuntimeError('offline refresh failure')
    monkeypatch.setattr(base,'retry_wrapper',fail)
    warm.prepare_public_market()
    assert base.BinanceClient.bstocks_coins=={'OLD'}
    assert calls==['spot','swap'] and notifications==['suppressed']


def test_disabled_filter_does_not_require_an_automatic_list(setup_market,monkeypatch):
    base,client,warm,calls,notifications=setup_market
    client.filter_bstocks=False
    def forbidden(*args,**kwargs):raise AssertionError('Disabled filter must not request a list')
    monkeypatch.setattr(base,'retry_wrapper',forbidden)
    warm.prepare_public_market()
    assert calls==['spot','swap'] and not notifications


def test_exchange_without_bstocks_keeps_original_path(setup_market):
    base,client,warm,calls,notifications=setup_market
    client.fetch_bstocks_coins=None
    warm.prepare_public_market()
    assert calls==['spot','swap'] and not notifications
