"""主链路对账：截面文件 → Polars 选币 → 仓位/订单 → 统计输入。"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import polars as pl
import pytest

import core.select_coin as select
from core.model.strategy_config import StrategyConfig, FactorConfig


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    def path(*parts, **kwargs):
        result = tmp_path.joinpath(*map(str, parts))
        result.parent.mkdir(parents=True, exist_ok=True)
        return result
    monkeypatch.setattr(select, 'get_file_path', path)
    monkeypatch.setattr(select, 'job_num', 2)
    return path


def panel():
    rows = []
    for i, t in enumerate(pd.date_range('2026-10-01', periods=8, freq='h')):
        for j, symbol in enumerate(['AAAUSDT', 'BBBUSDT', 'CCCUSDT']):
            rows.append(dict(candle_begin_time=t, symbol=symbol, is_spot=0, close=10. + j,
                             next_close=10. + j, symbol_spot='', symbol_swap=symbol,
                             **{'是否交易': 1, '当前回撤_24': -0.05 * (1 + (i + j) % 3)}))
    return pd.DataFrame(rows)


def strategy(mode='standard', hold_period='1H', offsets=(0,)):
    return StrategyConfig(name='Parity', hold_period=hold_period, offset_list=offsets,
                          select_mode=mode, use_custom_func=False, long_select_coin_num=1,
                          short_select_coin_num=0, long_cap_weight=1, short_cap_weight=0,
                          long_factor_list=tuple(FactorConfig.parse_config_list([('动态回撤阈值', True, 24, 1)])))


def section_outputs(workspace, monkeypatch, snapshot):
    df = panel()
    full = pl.from_pandas(df[select.KLINE_COLS])
    full.write_parquet(workspace(*select.ALL_KLINE_FULL_PATH_TUPLE))
    pl.from_pandas(df[['当前回撤_24']]).write_parquet(workspace('data', 'cache', 'factor_full_当前回撤_24.parquet'))
    conf = SimpleNamespace(name='parity', max_hold_period='1H', lookback_days=0,
                           incremental_lookback_hours=6, section_params_dict={'动态回撤阈值': [24]},
                           section_depend_factor_col_name_list=['当前回撤_24'],
                           factor_col_name_list=['动态回撤阈值_24'])
    monkeypatch.setattr(select, 'get_snapshot_time', lambda _: snapshot)
    outputs = []
    for calc in [select.calc_cross_sections_pandas, select.calc_cross_sections_polars]:
        calc(conf)
        outputs.append(pl.read_parquet(workspace('data', 'cache', 'factor_动态回撤阈值_24.parquet')).to_pandas())
    pd.testing.assert_frame_equal(outputs[0], outputs[1], atol=1e-12, rtol=0)
    cutoff = df.candle_begin_time.min() if not snapshot else pd.Timestamp(snapshot) - pd.Timedelta(hours=6)
    return df[df.candle_begin_time >= cutoff].reset_index(drop=True), outputs


@pytest.mark.parametrize('snapshot', [None, '2026-10-01 07:00:00'])
@pytest.mark.parametrize('mode', ['standard', 'entry_exit'])
def test_sections_through_positions_orders_and_strategy_labels(workspace, monkeypatch, tmp_path, snapshot, mode):
    import core.position as position
    import core.utils.statis_func as stats
    from core.model.account_config import AccountConfig
    df, outputs = section_outputs(workspace, monkeypatch, snapshot)
    stg = strategy(mode)
    conf = SimpleNamespace(strategy_list=[stg], is_day_period=False, hold_period_type='H',
                           max_hold_period='1H', incremental_lookback_hours=6)
    routes = []
    for i, factors in enumerate(outputs):
        result_dir = tmp_path / f'route{i}'
        result_dir.mkdir()
        joined = pd.concat([df, factors], axis=1)
        monkeypatch.setattr(select, '_GLOBAL_FACTOR_LF', pl.from_pandas(joined).lazy())
        select.process_strategy_thread(stg, result_dir, unified_time='2026-10-01')
        selected = pd.read_pickle(result_dir / f'{stg.get_fullname(as_folder_name=True)}.pkl')
        spot, swap = select.agg_multi_strategy_ratio(conf, selected.copy(), df.candle_begin_time.max())
        spot.to_pickle(result_dir / 'df_spot_ratio.pkl')
        swap.to_pickle(result_dir / 'df_swap_ratio.pkl')
        monkeypatch.setattr(position, 'runtime_folder', result_dir)
        symbols = ['AAAUSDT', 'BBBUSDT', 'CCCUSDT']
        prices = pd.Series([10., 11., 12.], index=pd.Index(symbols, name='symbol'), name='price')
        account = AccountConfig('parity', buy_bnb_value=0)
        account.spot_equity, account.swap_equity = 0., 1000.
        account.spot_position = pd.DataFrame({'当前持仓量': pd.Series(dtype=float)}, index=pd.Index([], name='symbol'))
        account.swap_position = account.spot_position.copy()
        market = {'symbol_list': symbols, 'min_qty': dict.fromkeys(symbols, 3)}
        account.bn = Mock(market_info={'spot': market, 'swap': market})
        account.bn.get_spot_ticker_price_series.return_value = prices
        account.bn.get_swap_ticker_price_series.return_value = prices
        targets = position.calc_target_position(account, is_debug=True)
        orders = account.calc_order_amount(targets)
        selected.to_pickle(result_dir / '最新持仓周期选币结果.pkl')
        pd.DataFrame({'0': [1.]}).to_csv(result_dir / '仓位比例.csv')
        sub = SimpleNamespace(name='Parent', get_result_folder=lambda: result_dir)
        me = SimpleNamespace(factory=SimpleNamespace(result_folder=result_dir, config_list=[sub]))
        labels = stats.build_symbol_strategy_columns(me)
        assert not orders.empty and labels[1]
        routes.append((selected, spot, swap, targets, orders, labels))
    for a, b in zip(routes[0][:-1], routes[1][:-1]):
        pd.testing.assert_frame_equal(a, b, atol=1e-12, rtol=0)
    assert routes[0][-1] == routes[1][-1]


def test_long_period_offsets_survive_selection_serialization(tmp_path, monkeypatch):
    df = panel()
    df['candle_begin_time'] += pd.Timedelta(hours=130)
    df['动态回撤阈值_24'] = df['当前回撤_24']
    stg = strategy(hold_period='168H', offsets=tuple(range(168)))
    monkeypatch.setattr(select, '_GLOBAL_FACTOR_LF', pl.from_pandas(df).lazy())
    select.process_strategy_thread(stg, tmp_path, unified_time='2026-10-01')
    result = pd.read_pickle(tmp_path / f'{stg.get_fullname(as_folder_name=True)}.pkl')
    assert result.offset.tolist() == list(range(131, 139))


def test_swap_selection_spot_execution_loads_both_markets(tmp_path, monkeypatch):
    from core.utils import functions
    conf = SimpleNamespace(select_scope_set={'swap'}, order_first_set={'spot'}, black_list=[], white_list=[])
    loader = Mock(return_value={})
    monkeypatch.setattr(functions, 'load_bmac_data', loader)
    monkeypatch.setattr(functions, 'get_file_path', lambda *args, **kwargs: tmp_path / 'candles.pkl')
    functions.load_realtime_data(conf, datetime(2026, 10, 14))
    assert [call.args[0] for call in loader.call_args_list] == ['swap', 'spot']


@pytest.mark.parametrize('quote,avg', [(None, '12'), ('0', '0'), ('24', '12')])
def test_execution_records_feed_recent_volume(tmp_path, monkeypatch, quote, avg):
    from core.utils.functions import save_order_history
    from core.utils import statis_func as stats
    monkeypatch.setattr(stats, 'data_path', tmp_path)
    rt = datetime(2026, 10, 14, 12)
    orders = pd.DataFrame({'symbol': ['AAAUSDT', 'BBBUSDT'],
                           'symbol_type': ['spot', 'swap'], '交易模式': ['建仓', '调仓']})
    spot = dict(symbol='AAAUSDT', status='FILLED', executedQty='2',
                origQty='2', cummulativeQuoteQty='20', price='10', side='BUY')
    swap = dict(symbol='BBBUSDT', status='PARTIALLY_FILLED', executedQty='2',
                origQty='3', avgPrice=avg, price='12', side='SELL')
    if quote is not None:
        swap['cumQuote'] = quote
    pending = dict(swap, status='NEW', executedQty='0')
    save_order_history(orders, rt, 'parity', [spot], [swap, pending])
    assert stats.compute_recent_volume('parity', rt) == (20., 24.)
    assert stats.compute_recent_volume('parity', datetime(2026, 10, 14, 13)) == (0., 0.)
    records = pd.read_csv(tmp_path / 'parity/订单记录/order_history_202610.csv', encoding='gbk')
    assert records.trade_mode.tolist() == ['建仓', '调仓']
    assert len(records) == 2


def test_parallel_section_write_failure_is_not_silenced(workspace, monkeypatch):
    def fail(_):
        raise OSError('test disk write failure')
    monkeypatch.setattr(select, 'write_factor_file_polars', fail)
    with pytest.raises(OSError, match='test disk write failure'):
        section_outputs(workspace, monkeypatch, None)


def test_arrow_and_pkl_share_new_exchange_filters(tmp_path, monkeypatch, producer_root):
    import core.utils.datatools as dt
    from core.utils import exchange_hub
    from core.utils.arrow_reader import read_arrow_market_dict
    import importlib.util
    spec = importlib.util.spec_from_file_location('producer_filter_publish', producer_root/'core/arrow_publish.py')
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    symbols = ['AAAUSDT', 'STOCKUSDT', 'USDCUSDT', 'USDC-USDT', 'OLDUSDT']
    times = pd.date_range('2026-10-01', periods=4, freq='h')
    candles = {symbol: pd.DataFrame(dict(candle_begin_time=times, first_candle_time=times[0],
                                         last_candle_time=times[-1], symbol=symbol,
                                         symbol_swap=symbol, symbol_spot='', close=10.))
               for symbol in symbols}
    conf = SimpleNamespace(black_list=[], white_list=[], min_kline_num=1, get_kline_num=lambda: 100)
    monkeypatch.setattr(exchange_hub, 'get_bstocks_coins', lambda: {'STOCK'})
    exchange_hub.apply_bstocks_black_list(conf)
    monkeypatch.setattr(dt, 'bmac_data_path', tmp_path)
    client = Mock()
    client.get_market_info.return_value = {'symbol_list': symbols[:-1]}
    monkeypatch.setattr(dt, 'get_dummy_client', lambda: client)
    (tmp_path / '0m').mkdir()
    pd.to_pickle(candles, tmp_path / '0m/swap_dict_batch0.pkl')
    producer.publish_arrow_snapshot(candles, tmp_path / 'swap.arrow', 4)
    routes = []
    for arrow in [True, False]:
        monkeypatch.setattr(dt, 'read_arrow_market_dict_for_run_time',
                            lambda *args: read_arrow_market_dict(tmp_path / 'swap.arrow') if arrow else None)
        routes.append(dt.load_bmac_data('swap', datetime(2026, 10, 14), conf))
    assert list(routes[0]) == list(routes[1]) == ['AAAUSDT']
    pd.testing.assert_frame_equal(routes[0]['AAAUSDT'], routes[1]['AAAUSDT'])
