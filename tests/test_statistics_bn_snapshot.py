"""Offline oracles: exchange-native statistics, legacy trading defaults unchanged."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import socket

import pandas as pd
import pytest

from core.binance.portfolio_margin_client import PortfolioMarginClient
import core.utils.statistics as st
import core.utils.statis_func as sf


@pytest.fixture(autouse=True)
def offline(tmp_path,monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',Mock(side_effect=AssertionError('network forbidden')))
    def path(*parts,**kwargs):
        result=tmp_path.joinpath(*(str(p) for p in parts if str(p)!=str(tmp_path)))
        result.parent.mkdir(parents=True,exist_ok=True)
        return result
    for module in (st,sf):
        monkeypatch.setattr(module,'data_path',tmp_path)
        monkeypatch.setattr(module,'get_file_path',path)
        monkeypatch.setattr(module,'send_wechat_work_msg',Mock())
        monkeypatch.setattr(module,'send_wechat_work_img',Mock())
    monkeypatch.setattr(st,'refresh_diff_time',Mock())
    return path


def pm_client():
    client=PortfolioMarginClient.__new__(PortfolioMarginClient)
    client.base_margin={'USDT':1}
    client.exchange=Mock()
    client.exchange.papiGetAccount.return_value={'actualEquity':'2345.678901234','accountEquity':'2000','uniMMR':'8.251234'}
    client.exchange.papiGetUmPositionRisk.return_value=[dict(symbol='ETHUSDT',positionAmt='-.06',entryPrice='2200',
        markPrice='2000',unRealizedProfit='12.3456789',notional='-123.456789')]
    client.fetch_spot_ticker_price=Mock(return_value=[dict(symbol='BNBUSDT',price='500'),dict(symbol='ETHUSDT',price='2000'),dict(symbol='DUSTUSDT',price='10')])
    client.get_balance_df=Mock(return_value=pd.DataFrame(dict(
        symbol=['USDT','BNB','ETH','DUST'],totalWalletBalance=[100.,2.,.6,.0001],
        当前持仓量=[100.,2.,.6,.0001],crossMarginFree=[100.,2.,.6,.0001],
        crossMarginLocked=[0.]*4,crossMarginBorrowed=[0.]*4,crossMarginInterest=[0.]*4,
        umWalletBalance=[0.]*4,cmWalletBalance=[0.]*4,umUnrealizedPNL=[0.]*4,cmUnrealizedPNL=[0.]*4)))
    client.get_swap_usdt_balance=Mock(side_effect=AssertionError('redundant UM account query'))
    client.get_market_info=Mock(side_effect=AssertionError('trading filter not statistics'))
    return client


def test_native_pm_snapshot_uses_actual_equity_and_keeps_bnb_dust():
    client=pm_client()
    original=client.get_balance_df.return_value.copy(deep=True)
    snapshot=client.get_statistics_overview()
    assert snapshot['account_equity']==2345.678901234
    assert snapshot['statistics']['uniMMR']==8.251234
    assert snapshot['statistics']['equity_source']=='binance_pm_actualEquity'
    assert snapshot['statistics']['equity_currency']=='USD'
    spot=snapshot['spot_assets']['spot_position_df']
    assert set(spot.index)=={'BNBUSDT','ETHUSDT','DUSTUSDT'}
    assert spot.loc['DUSTUSDT','当前持仓量']==.0001
    assert snapshot['spot_assets']['prices']['BNBUSDT']==500.
    assert snapshot['swap_assets']['swap_position_df'].loc['ETHUSDT','notional']==-123.456789
    client.exchange.papiGetAccount.assert_called_once()
    client.exchange.papiGetUmPositionRisk.assert_called_once()
    client.get_swap_usdt_balance.assert_not_called()
    client.get_market_info.assert_not_called()
    pd.testing.assert_frame_equal(original,client.get_balance_df.return_value)


@pytest.mark.parametrize('actual',[None,'NaN','Infinity'])
def test_invalid_actual_equity_cannot_fall_back_to_filtered_total(actual):
    client=pm_client()
    client.exchange.papiGetAccount.return_value['actualEquity']=actual
    with pytest.raises(ValueError,match='actualEquity'):
        client.get_statistics_overview()


def test_swap_native_notional_and_legacy_columns():
    client=pm_client()
    legacy=client.get_swap_position_df()
    assert legacy.columns.tolist()==['当前持仓量','均价','持仓盈亏','当前标记价格','仓位价值']
    native=client.get_swap_position_df(for_statistics=True)
    result=sf.calc_swap_position(native)
    assert result.loc['ETHUSDT','pos_u']==-123.456789
    assert result.loc['ETHUSDT','avg_price']==2200.
    assert result.loc['ETHUSDT','pnl_u']==12.3456789
    assert result.loc['ETHUSDT','pos_amount']==-.06


def test_get_unimmr_still_returns_float():
    client=pm_client()
    assert client.get_unimmr()==8.251234
    client.exchange.papiGetAccount.assert_called_once()


def test_pm_default_overview_does_not_call_native_statistics(monkeypatch):
    client=pm_client()
    client.get_balance_df.return_value=client.get_balance_df.return_value.iloc[:0]
    client.get_swap_usdt_balance=Mock(return_value=0.)
    snapshot=client.get_account_overview()
    assert snapshot['account_equity']==0.
    assert 'statistics' not in snapshot
    client.exchange.papiGetAccount.assert_not_called()
    client.get_swap_usdt_balance.assert_called_once()


def test_missing_price_keeps_asset_and_zero_account_is_valid():
    client=pm_client()
    client.fetch_spot_ticker_price.return_value=[]
    snapshot=client.get_statistics_overview()
    assert 'ETHUSDT' in snapshot['spot_assets']['spot_position_df'].index
    assert pd.isna(snapshot['spot_assets']['spot_position_df'].loc['ETHUSDT','仓位价值'])
    assert snapshot['account_equity']==2345.678901234
    client.get_balance_df.return_value=client.get_balance_df.return_value.iloc[:0]
    client.exchange.papiGetUmPositionRisk.return_value=[]
    client.exchange.papiGetAccount.return_value['actualEquity']='0'
    assert client.get_statistics_overview()['account_equity']==0.


def test_bnb_replenishment_does_not_create_false_account_loss():
    client=pm_client()
    before=client.get_statistics_overview()
    balance=client.get_balance_df.return_value.copy()
    balance.loc[balance.symbol=='USDT','totalWalletBalance']-=10.
    balance.loc[balance.symbol=='BNB','totalWalletBalance']+=.02
    client.get_balance_df.return_value=balance
    after=client.get_statistics_overview()
    assert after['account_equity']==before['account_equity']
    assert after['spot_assets']['spot_position_df'].loc['BNBUSDT','当前持仓量']==2.02


def test_charts_do_not_query_transfers_for_any_account(tmp_path,offline,monkeypatch):
    account=SimpleNamespace(name='no_history',bn=Mock())
    account.bn.fetch_transfer_history.side_effect=AssertionError('no transfer history')
    me=SimpleNamespace(factory=SimpleNamespace(config_list=[]))
    draw=Mock()
    monkeypatch.setattr(st,'draw_equity_and_send_pic',draw)
    st.save_and_send_equity_info(account,me,pd.DataFrame(),pd.DataFrame(),0.,100.,0.,0.,
        options=dict(notify=False,charts=True))
    assert draw.call_count==1
    account.bn.fetch_transfer_history.assert_not_called()


@pytest.mark.parametrize('offset',range(0,60,5))
def test_stats_run_reuses_one_native_account_response_and_post_history(tmp_path,monkeypatch,offline,offset):
    import config
    client=pm_client()
    monkeypatch.setattr(client,'get_unimmr',Mock(side_effect=AssertionError('duplicate account query')))
    monkeypatch.setattr(client,'get_account_overview',Mock(side_effect=AssertionError('filtered overview')))
    account=SimpleNamespace(name='native',bn=client,hour_offset_minute=offset,leverage=1.,wechat_webhook_url='fake')
    me=SimpleNamespace(factory=SimpleNamespace(result_folder=tmp_path,config_list=[]))
    now=datetime(2026,11,1,12,offset)
    monkeypatch.setattr(st,'init_system',lambda _: (account,me))
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('native.py')])
    monkeypatch.setattr(st.sys,'argv',['statistics',str(int(now.timestamp())),'native'])
    monkeypatch.setattr(st,'send_pos_strategy_info',Mock())
    monkeypatch.setattr(st,'build_symbol_strategy_columns',lambda _: ({},{}))
    monkeypatch.setattr(st,'send_img_for_dataframe',Mock())
    monkeypatch.setattr(config,'statistics_config',dict(enabled=True,notify=True,details=False,charts=False,price_impact=True))
    monkeypatch.setattr(pd,'read_pickle',Mock(side_effect=AssertionError('new round must not read pre-order histories')))
    st.run()
    client.exchange.papiGetAccount.assert_called_once()
    client.get_unimmr.assert_not_called()
    stored=pd.read_csv(offline('native','账户信息','equity.csv'),encoding='gbk')
    assert stored['账户总净值'].iloc[0]==2345.678901234
    assert stored.equity_source.iloc[0]=='binance_pm_actualEquity'
    assert stored.equity_currency.iloc[0]=='USD'


def test_equity_windows_require_same_source_and_use_actual_intervals(offline):
    account=SimpleNamespace(name='native',bn=Mock())
    now=datetime(2026,11,1,12,20)
    path=offline('native','账户信息','equity.csv')
    pd.DataFrame(dict(time=[now-timedelta(hours=24),now-timedelta(hours=1),now-timedelta(minutes=59)],
        observed_at=[now-timedelta(hours=24),now-timedelta(hours=1),now-timedelta(minutes=59)],
        账户总净值=[2000.,2200.,99999.],equity_source=['binance_pm_actualEquity','binance_pm_actualEquity','legacy'],equity_currency=['USD']*3)).to_csv(path,index=False,encoding='gbk')
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,2300.,0.,0.,
        run_time=now,observed_at=now,options=dict(notify=True),
        metrics=dict(uniMMR=8.,equity_source='binance_pm_actualEquity',equity_currency='USD'))
    assert '1小时账户损益：100' in msg
    assert '24小时账户损益：300' in msg
    assert '99999' not in msg
    account.bn.get_unimmr.assert_not_called()


def test_missing_native_baseline_is_unknown_not_legacy_difference(offline):
    account=SimpleNamespace(name='native',bn=Mock())
    now=datetime(2026,11,1,12,20)
    pd.DataFrame(dict(time=[now-timedelta(hours=1)],observed_at=[now-timedelta(hours=1)],账户总净值=[1000.])).to_csv(
        offline('native','账户信息','equity.csv'),index=False,encoding='gbk')
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,2300.,0.,0.,
        run_time=now,observed_at=now,metrics=dict(uniMMR=8.,equity_source='binance_pm_actualEquity',equity_currency='USD'))
    assert '1小时账户损益：历史不足' in msg
    assert '历史最高账户总净值：2300' in msg


def test_post_history_uses_complete_empty_observation_and_ignores_pre_order_rows():
    from tools.statistics_snapshot import position_history
    now=datetime(2026,11,1,12,20)
    current=pd.DataFrame()
    current.attrs.update(phase='post_trade',observed_at=now,run_time=now,snapshot_source='native',offset_minute=20)
    wrong=pd.DataFrame(dict(symbol=['OLD'],market_value=[100.]))
    wrong.attrs.update(phase='pre_trade',observed_at=now,run_time=now)
    history=position_history({1:wrong,2:current},source='native',offset_minute=20)
    assert len(history)==1 and history.iloc[0].symbol=='EMPTY'
    assert history.iloc[0].observed_at==now


def test_negative_asset_is_not_reported_as_a_long(offline):
    spot=pd.DataFrame({'当前持仓量':[-.1],'仓位价值':[-200.]},index=pd.Index(['ETHUSDT'],name='symbol'))
    assert sf.calc_spot_position(spot,'negative',pd.Series({'ETHUSDT':2000.})).iloc[0].side==-1
    account=SimpleNamespace(name='negative',bn=Mock())
    st.save_and_send_equity_info(account,None,spot,pd.DataFrame(),-200.,1000.,0.,0.,
        metrics=dict(equity_source='binance_pm_actualEquity',equity_currency='USD',uniMMR=8.))
    row=pd.read_csv(offline('negative','账户信息','equity.csv'),encoding='gbk').iloc[0]
    assert row['多头仓位']==0 and row['空头仓位']==-200
    assert row['多头选币']==0 and row['空头选币']==1


def test_post_history_selects_two_observations_before_materializing():
    from tools.statistics_snapshot import position_history
    now=datetime(2026,11,1,12,20)
    records={}
    for hours in range(1,169):
        frame=pd.DataFrame({'market_value':[float(hours)]},index=pd.Index([str(hours)],name='symbol'))
        frame.attrs.update(phase='post_trade',snapshot_source='native',offset_minute=20,
                           observed_at=now-timedelta(hours=hours),run_time=now-timedelta(hours=hours))
        records[hours]=frame
    history=position_history(records,source='native',offset_minute=20,observed_at=now)
    assert set(history.symbol)=={'1','24'}
    for hours in (1,24):
        baseline=sf.get_historical_position('native',hours,observed_at=now,history=history)
        assert baseline.market_value.tolist()==[float(hours)]


def test_account_equity_does_not_compare_different_offsets(offline):
    now=datetime(2026,11,1,12,20)
    pd.DataFrame(dict(time=[now-timedelta(hours=1)],observed_at=[now-timedelta(hours=1)],
        账户总净值=[1000.],offset_minute=[40],equity_source=['binance_pm_actualEquity'],equity_currency=['USD'])).to_csv(
        offline('native','账户信息','equity.csv'),index=False,encoding='gbk')
    account=SimpleNamespace(name='native',bn=Mock(),hour_offset_minute=20)
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,2300.,0.,0.,
        run_time=now,observed_at=now,metrics=dict(uniMMR=8.,equity_source='binance_pm_actualEquity',equity_currency='USD'))
    assert '1小时账户损益：历史不足' in msg
