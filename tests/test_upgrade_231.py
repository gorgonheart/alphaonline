"""升级的高风险接口：离线验证下单、干跑、空选币和续算窗口。"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from core.model.account_config import AccountConfig
from core.model.strategy_config import StrategyConfig
from core.trade import mark_gtc_orders


@pytest.mark.parametrize('ratio', [0, 1])
def test_order_route_preserves_open_close_and_input(ratio):
    batch = pd.DataFrame({
        'symbol': ['AUSDT', 'BUSDT', 'CUSDT', 'DUSDT'],
        'symbol_type': ['swap'] * 4,
        '交易模式': ['建仓', '清仓', '加仓', '减仓'],
        '拆单金额': [100, -100, 100, -100],
    })
    before = batch.copy(deep=True)
    result = mark_gtc_orders([batch], ratio, datetime(2026, 10, 14), 'test')[0]
    assert result.timeInForce.tolist() == ['IOC', 'IOC'] + (['GTC'] * 2 if ratio else ['IOC'] * 2)
    pd.testing.assert_frame_equal(batch, before)


def test_default_account_keeps_ioc_and_empty_order_contract():
    account = AccountConfig('test')
    assert account.maker_ratio == 0
    success, failures = account.proceed_spot_order(pd.DataFrame({'symbol_type': []}), True)
    assert success == [] and failures.empty


@pytest.mark.parametrize('client_type', ['standard', 'portfolio_margin'])
@pytest.mark.parametrize('enabled', [False, True])
def test_fee_burn_sync_uses_account_preference(monkeypatch, client_type, enabled):
    import importlib
    module = importlib.import_module(f'core.binance.{client_type}_client')
    cls = module.StandardClient if client_type == 'standard' else module.PortfolioMarginClient
    client = cls.__new__(cls)
    client.exchange = Mock()
    retry = Mock(side_effect=[{'feeBurn': not enabled}, {}])
    monkeypatch.setattr(module, 'retry_wrapper', retry)
    client.set_fee_burn(enabled)
    assert retry.call_args.kwargs['params']['feeBurn'] == ('true' if enabled else 'false')


@pytest.mark.parametrize('ratio', [-0.1, 1.1, float('nan')])
def test_invalid_maker_ratio_rejected(ratio):
    with pytest.raises(ValueError, match='maker_ratio'):
        AccountConfig('test', maker_ratio=ratio)


@pytest.mark.parametrize('market', ['spot', 'swap'])
def test_accepted_gtc_is_not_resubmitted(market):
    from core.binance.base_client import BinanceClient
    client = BinanceClient.__new__(BinanceClient)
    client.order_money_limit = {'spot': 10, 'swap': 5}
    client._spot_ticker_price = client._swap_ticker_price = {'BTCUSDT': 100.}
    client.get_market_info = Mock(return_value={
        'min_qty': {'BTCUSDT': 3}, 'price_precision': {'BTCUSDT': 2},
        'min_notional': {'BTCUSDT': 5},
    })
    client.fetch_book_ticker = Mock(return_value={'bidPrice': '99', 'askPrice': '101'})
    client.get_spot_ticker_price_series = client.get_swap_ticker_price_series = Mock()
    place = Mock(return_value={'orderId': 1, 'status': 'NEW', 'timeInForce': 'GTC'})
    setattr(client, f'place_{market}_order', place)
    orders = pd.DataFrame([{'symbol': 'BTCUSDT', '交易模式': '加仓',
                            '拆单金额': 100., '实际下单量': 1., 'timeInForce': 'GTC'}])
    success, errors = getattr(client, f'place_{market}_orders_bulk2')(orders)
    assert len(success) == 1 and errors == []
    place.assert_called_once()
    assert place.call_args.kwargs['price'] == '99.00'


@pytest.mark.parametrize('hold_period,expected_floor', [('1H', 25), ('24H', 2), ('48H', 2)])
def test_continuation_floor_uses_sections(tmp_path, hold_period, expected_floor):
    strategy = StrategyConfig(name='test', hold_period=hold_period)
    strategy.ee_state_dir = tmp_path
    result = strategy._ee_cont_kwargs('long', 0, 0, 0, 1, [], [], [], [], [])
    assert result['n_floor'] == expected_floor
    assert result['cap_cs'] >= result['n_floor']


def test_continuation_replaces_overlapping_unclosed_candles(tmp_path):
    import polars as pl
    from core.utils.ee_continuation import run_continuation
    times = pd.date_range('2026-10-01', periods=4, freq='h')
    rows = []
    for t in times:
        for symbol, score in [('A', 1.), ('B', 2.)]:
            rows.append({'candle_begin_time': t, 'symbol': symbol, '_ee_score': score,
                         '_ee_in': 1, '_ee_out': 0, '_ee_target': 1})
    original = pl.from_pandas(pd.DataFrame(rows))
    state = tmp_path / 'continuation.pkl'
    kwargs = dict(fill_lag=0, exit_cooldown=0, max_hold_hours=0, direction=1,
                  cfg_fp='fixture', meta=None, n_floor=1, cap_cs=30)
    run_continuation(original, state_path=state, **kwargs).collect()
    # 首根未闭合时错误选 A；修正首根排序后应全程持有 B，与冷算一致。
    corrected = original.with_columns(
        pl.when(pl.col('symbol') == 'B').then(pl.lit(0.)).otherwise(pl.col('_ee_score')).alias('_ee_score'))
    resumed = run_continuation(corrected, state_path=state, **kwargs).collect()
    cold = run_continuation(corrected, state_path=tmp_path / 'cold.pkl', **kwargs).collect()
    assert resumed['symbol'].to_list() == ['B'] * 4
    pd.testing.assert_frame_equal(resumed.to_pandas(), cold.to_pandas())


def test_empty_selection_retains_historical_columns(tmp_path):
    from core.equity import create_ratio_snapshot
    old = pd.DataFrame({'BTCUSDT': [0.5]}, index=pd.to_datetime(['2026-10-13']))
    old.to_pickle(tmp_path / 'ratio_swap.pkl')
    empty = pd.DataFrame(index=pd.to_datetime(['2026-10-14']))
    result = create_ratio_snapshot(SimpleNamespace(get_snapshot_folder=lambda: tmp_path), empty, 'swap')
    assert result['BTCUSDT'].tolist() == [0.5, 0.0]


def test_dry_run_account_refresh_disables_account_mutations(monkeypatch):
    import config
    from core.utils import functions
    monkeypatch.setattr(config, 'is_simulate', 'dry_run')
    monkeypatch.setattr(functions.time, 'sleep', lambda _: None)
    account = Mock(is_usable=True)
    functions.update_accounts_info({'test': account})
    account.update_account_info.assert_called_once_with(False, False)


@pytest.mark.parametrize('mode', ['dry_run', 'none'])
def test_trading_pipeline_routes_time_and_preserves_dry_run(tmp_path, monkeypatch, mode):
    import core.real_trading as trading
    monkeypatch.setattr(trading, 'is_simulate', mode)
    monkeypatch.setattr(trading, 'is_debug', False)
    monkeypatch.setattr(trading, 'runtime_folder', tmp_path)
    def folder(*parts, **kwargs):
        path = tmp_path.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path
    monkeypatch.setattr(trading, 'get_folder_path', folder)
    for name in ['refresh_diff_time', 'snapshot_bmac_exginfo', 'save_position_snapshot',
                 'save_position_results', 'save_final_select_results', 'save_symbol_order', 'save_order_history']:
        monkeypatch.setattr(trading, name, Mock())
    positions = pd.DataFrame({'目标持仓量': [1.]})
    monkeypatch.setattr(trading, 'calc_target_position', Mock(return_value=positions))
    account = Mock(hour_offset_minute=0, hour_offset='0m')
    account.name = 'test'
    account.proceed_spot_order.return_value = ([], pd.DataFrame())
    account.proceed_swap_order.return_value = ([], pd.DataFrame())
    me = Mock()
    from core.model.strategy_config import PosStrategyConfig
    me.strategy = PosStrategyConfig('legacy', '1H')
    ratio = pd.DataFrame({'BTCUSDT': [0.5]}, index=pd.to_datetime(['2026-10-14']))
    me.agg_pos_ratio.return_value = (ratio, ratio)
    me.apply_position_limits.return_value = (ratio, ratio)
    rt = datetime(2026, 10, 14)
    trading.run_by_account(account, me, rt)
    if mode == 'dry_run':
        trading.save_order_history.assert_not_called()
        for method in [account.proceed_spot_order, account.proceed_swap_order,
                       account.bn.cancel_all_spot_orders, account.bn.cancel_all_swap_orders,
                       account.bn.collect_asset, account.bn.adjust_base_margin]:
            method.assert_not_called()
    else:
        trading.save_order_history.assert_called_once_with(account.calc_order_amount.return_value, rt, 'test', [], [])
        account.proceed_swap_order.assert_called_once_with(account.calc_order_amount.return_value, rt)
        assert account.proceed_spot_order.call_count == 2
        assert all(call.kwargs['run_time'] == rt for call in account.proceed_spot_order.call_args_list)
