"""Real CCXT signing with a simulated server clock; no HTTP or account access."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import ccxt
import pytest

from core.binance.base_client import BinanceClient
from core.binance.portfolio_margin_client import PortfolioMarginClient
from core.utils import commons, functions


@pytest.fixture
def clocked_exchange(monkeypatch):
    clock = [1000.]
    exchange = ccxt.binance(dict(apiKey='offline-test', secret='offline-test',
        enableRateLimit=False, options=dict(recvWindow=10000)))
    monkeypatch.setattr(commons.time, 'time', lambda: clock[0])
    monkeypatch.setattr(commons.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(commons.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(BinanceClient, 'diff_timestamp', 0)
    log = Mock()
    monkeypatch.setattr(commons, 'logger', log)
    monkeypatch.setattr(commons, 'divider', Mock())
    refresh = Mock()
    monkeypatch.setattr(functions, 'refresh_diff_time', refresh)
    return clock, exchange, log, refresh


def test_balance_survives_observed_18_second_transit_without_retry(clocked_exchange, monkeypatch):
    clock, exchange, _, refresh = clocked_exchange
    requests = []
    result = [{'asset': 'USDT', 'totalWalletBalance': '100'}]
    def fetch(url, method, headers, body):
        assert method == 'GET' and urlsplit(url).path == '/papi/v1/balance'
        query = parse_qs(urlsplit(url).query)
        requests.append(query)
        clock[0] += 18.676  # Same request-age scale as the reported failure.
        age = int(clock[0]*1000)-int(query['timestamp'][0])
        if age > int(query['recvWindow'][0]):
            raise ccxt.BadRequest('binance {"code":-1021,"msg":"outside of the recvWindow"}')
        return result
    monkeypatch.setattr(exchange, 'fetch', fetch)
    client = PortfolioMarginClient.__new__(PortfolioMarginClient)
    client.exchange = exchange; client.balance = None
    assert client.get_balance() == result
    assert len(requests) == 1
    assert int(requests[0]['recvWindow'][0]) == 30000
    assert exchange.options['recvWindow'] == 10000
    refresh.assert_not_called()


def test_timestamp_retry_refreshes_and_logs_recovery_once(clocked_exchange, monkeypatch):
    clock, exchange, log, refresh = clocked_exchange
    calls = []
    def fetch(url, method, headers, body):
        query = parse_qs(urlsplit(url).query); calls.append(query)
        if len(calls) == 1:
            clock[0] += 18.676
            raise ccxt.BadRequest('binance {"code":-1021,"msg":"outside of the recvWindow"}')
        return {'ok': True}
    def sync():
        clock[0] += .2
        BinanceClient.diff_timestamp = 350
    refresh.side_effect = sync
    monkeypatch.setattr(exchange, 'fetch', fetch)
    assert commons.retry_wrapper(exchange.papi_get_balance, {'timestamp': ''}, 'balance') == {'ok': True}
    assert len(calls) == 2
    assert int(calls[1]['timestamp'][0]) == int(clock[0]*1000)-350
    assert int(calls[1]['timestamp'][0]) > int(calls[0]['timestamp'][0])
    refresh.assert_called_once()
    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert sum('-1021' in s for s in errors) == 1
    assert any('18.676' in s and '1/5' in s for s in errors)
    assert any('重试成功' in str(c.args[0]) for c in log.info.call_args_list)


@pytest.mark.parametrize('method,endpoint', [('GET', 'papi_get_um_account'), ('POST', 'papi_post_um_order')])
def test_other_signed_calls_keep_ten_second_window(clocked_exchange, monkeypatch, method, endpoint):
    _, exchange, _, _ = clocked_exchange
    def fetch(url, actual_method, headers, body):
        assert actual_method == method
        query = parse_qs(urlsplit(url).query if method == 'GET' else body)
        assert query['recvWindow'] == ['10000']
        return {'ok': True}
    monkeypatch.setattr(exchange, 'fetch', fetch)
    assert commons.retry_wrapper(getattr(exchange, endpoint), {'timestamp': '', 'symbol': 'BTCUSDT'}) == {'ok': True}


def test_persistent_rejection_raises_and_never_claims_recovery(clocked_exchange, monkeypatch):
    _, exchange, log, refresh = clocked_exchange
    fetch = Mock(side_effect=ccxt.BadRequest('binance {"code":-1021,"msg":"outside of the recvWindow"}'))
    monkeypatch.setattr(exchange, 'fetch', fetch)
    with pytest.raises(ValueError, match='重试次数超过上限'):
        commons.retry_wrapper(exchange.papi_get_balance, {'timestamp': ''}, retry_times=2)
    assert fetch.call_count == 2
    assert refresh.call_count == 2
    assert not any('重试成功' in str(c.args[0]) for c in log.info.call_args_list)


def test_insufficient_balance_still_stops_immediately(clocked_exchange, monkeypatch):
    _, exchange, _, refresh = clocked_exchange
    fetch = Mock(side_effect=ccxt.InsufficientFunds('binance Account has insufficient balance for requested action'))
    monkeypatch.setattr(exchange, 'fetch', fetch)
    with pytest.raises(ValueError, match='现货下单资金不足'):
        commons.retry_wrapper(exchange.papi_post_margin_order, {'timestamp': ''})
    assert fetch.call_count == 1
    refresh.assert_not_called()


def test_balance_over_thirty_seconds_stops_instead_of_returning_cached_assets(clocked_exchange, monkeypatch):
    clock, exchange, log, _ = clocked_exchange
    calls = []
    def fetch(url, method, headers, body):
        query = parse_qs(urlsplit(url).query); calls.append(query)
        clock[0] += 31
        assert int(clock[0]*1000)-int(query['timestamp'][0]) > int(query['recvWindow'][0])
        raise ccxt.BadRequest('binance {"code":-1021,"msg":"outside of the recvWindow"}')
    monkeypatch.setattr(exchange, 'fetch', fetch)
    client = PortfolioMarginClient.__new__(PortfolioMarginClient)
    client.exchange = exchange; client.balance = [{'asset': 'STALE'}]
    with pytest.raises(ValueError, match='重试次数超过上限'):
        client.get_balance()
    assert len(calls) == 5
    assert client.balance == [{'asset': 'STALE'}]
    assert not any('重试成功' in str(c.args[0]) for c in log.info.call_args_list)
