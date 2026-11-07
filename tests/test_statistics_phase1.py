"""Statistics phase one: offline oracles and request/IO budgets."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import socket

import numpy as np
import pandas as pd
import pytest

import core.utils.statis_func as sf
import core.utils.statistics as st


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    def path(*parts, **kwargs):
        p = tmp_path.joinpath(*[str(x) for x in parts if str(x) != str(tmp_path)])
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('network forbidden')))
    for module in (sf, st):
        monkeypatch.setattr(module, 'data_path', tmp_path)
        monkeypatch.setattr(module, 'get_file_path', path)
        monkeypatch.setattr(module, 'send_wechat_work_msg', Mock())
        monkeypatch.setattr(module, 'send_wechat_work_img', Mock())
    monkeypatch.setattr(st, 'refresh_diff_time', Mock())
    return path


@pytest.mark.parametrize('ledger_amount', [None, 22472.69245, 1544.2189, 100.])
def test_eth_market_value_without_ledger_io(monkeypatch, isolated, ledger_amount):
    if ledger_amount is not None:
        ledger_qty = 5.6401513 if ledger_amount == 22472.69245 else .6294843
        pd.DataFrame([{'time':'2026-10-30', 'symbol':'ETHUSDT', '持仓量':ledger_qty,
                       '持仓额':ledger_amount, '持仓均价':3984.4131}]).to_csv(
            isolated('45m','持仓信息','ETHUSDT.csv'),encoding='gbk',index=False)
    source = pd.DataFrame({'symbol': ['ETHUSDT'], '当前持仓量': [.6294843]})
    before = source.copy(deep=True)
    read = Mock(side_effect=AssertionError('hot path must not read legacy cost'))
    monkeypatch.setattr(pd, 'read_csv', read)
    result = sf.calc_spot_position(source, '45m', pd.Series({'ETHUSDT': 2407.22}))
    assert result.loc['ETHUSDT', 'pos_u'] == pytest.approx(.6294843 * 2407.22)
    assert result.loc['ETHUSDT', 'pos_amount'] == .6294843
    assert result.loc['ETHUSDT', 'avg_price'] == 2407.22
    assert result.loc['ETHUSDT', 'pnl_u'] == 0
    assert result.loc['ETHUSDT', 'cost_status'] == '期初估算'
    pd.testing.assert_frame_equal(source, before)
    read.assert_not_called()


def test_history_no_future_or_stale_and_closed_position(isolated):
    now = datetime(2026, 10, 31, 12, 45)
    hist = pd.DataFrame([dict(symbol='ETHUSDT', timestamp=now-timedelta(hours=1),
                              pos_amount=.6294843, cur_price=2000., market_value=1258.9686, pos_u=1258.9686, side=1)])
    hist.to_pickle(isolated('acct', '账户信息', 'spot_position_history.pkl'))
    assert sf.get_historical_position('acct', 24, observed_at=now).empty
    assert sf.get_historical_position('acct', 1, observed_at=now+timedelta(minutes=11)).empty
    baseline = sf.get_historical_position('acct', 1, observed_at=now)
    result = sf.calc_position_pnl(pd.DataFrame(), baseline, pd.Series({'ETHUSDT': 2407.22}))
    assert result.iloc[0].total_pnl == pytest.approx(.6294843 * 407.22)
    assert pd.isna(sf.calc_position_pnl(pd.DataFrame(), baseline, pd.Series(dtype=float)).iloc[0].total_pnl)
    assert sf.calc_position_pnl(pd.DataFrame(), baseline.drop(columns='market_value'), pd.Series({'ETHUSDT': 2407.22})).empty


def fixture_account(offset=20):
    spot = pd.DataFrame({'当前持仓量': [.6294843], '仓位价值': [.6294843*2407.22]}, index=pd.Index(['ETHUSDT'], name='symbol'))
    swap = pd.DataFrame(columns=['当前持仓量','仓位价值','当前标记价格','均价','持仓盈亏'], index=pd.Index([],name='symbol'))
    bn = Mock()
    bn.get_account_overview.return_value = dict(account_equity=2000.,seed_coin_spot_equity=0.,
        spot_assets=dict(spot_position_df=spot,equity=2000.,usdt=484.685426354),
        swap_assets=dict(swap_position_df=swap,equity=0.))
    account = SimpleNamespace(name='target',hour_offset_minute=offset,use_spot=True,bn=bn,
        update_account_info=Mock(side_effect=AssertionError('trading update forbidden')),if_transfer_bnb=True,
        account_type='unified',leverage=1.,wechat_webhook_url='fake')
    return account


def test_weight_message_uses_child_names_in_csv_column_order(tmp_path,monkeypatch,isolated):
    import config
    account=fixture_account();account.bn.get_unimmr.return_value=8.25
    children=[SimpleNamespace(name='S1-多头策略',original_name='多头策略'),
              SimpleNamespace(name='S2-空头策略')]
    me=SimpleNamespace(factory=SimpleNamespace(result_folder=tmp_path,config_list=children))
    pd.DataFrame({'1':[.2],'0':[.8]},index=['2026-10-31 12:20']).to_csv(tmp_path/'仓位比例.csv')
    monkeypatch.setattr(st,'init_system',lambda _: (account,me))
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('target.py')])
    monkeypatch.setattr(st,'send_pos_strategy_info',Mock())
    monkeypatch.setattr(st,'build_symbol_strategy_columns',lambda _: ({},{}))
    monkeypatch.setattr(st,'send_img_for_dataframe',Mock())
    monkeypatch.setattr(config,'statistics_config',dict(enabled=True,notify=True,details=False,charts=False))
    monkeypatch.setattr(st.sys,'argv',['stats',str(int(datetime(2026,10,31,12,20).timestamp())),'target'])
    st.run()
    messages=[call.args[0] for call in st.send_wechat_work_msg.call_args_list]
    weights=next(msg for msg in messages if msg.startswith('【子策略权重】'))
    assert weights.splitlines()[1]=='空头策略: 20.0%'
    assert weights.splitlines()[2]=='多头策略: 80.0%'
    assert 'S1-' not in weights and 'S2-' not in weights
    assert '账户UniMMR：8.25' in messages[0]
    account.bn.get_account_overview.assert_called_once()
    account.bn.get_unimmr.assert_called_once()


@pytest.mark.parametrize('current,high,low',[(2000.,3000.,1200.),(4000.,4000.,1200.),(1000.,3000.,1000.)])
def test_equity_extremes_reuse_history_include_current_exclude_future(isolated,monkeypatch,current,high,low):
    account=fixture_account();account.bn.get_unimmr.return_value=6.75
    now=datetime(2026,10,31,12,20)
    path=isolated('target','账户信息','equity.csv')
    pd.DataFrame({'time':[now-timedelta(hours=h) for h in (5,4,3,2,-1)],
                  '账户总净值':[1200.,3000.,np.nan,np.inf,999999.]}).to_csv(path,index=False,encoding='gbk')
    real_read=pd.read_csv;read=Mock(side_effect=real_read)
    monkeypatch.setattr(pd,'read_csv',read)
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,current,0.,0.,
        run_time=now,observed_at=now,options=dict(details=False,charts=False))
    assert f'历史最高账户总净值：{int(high)}' in msg
    assert f'历史最低账户总净值：{int(low)}' in msg
    assert '账户UniMMR：6.75' in msg
    assert read.call_count==1
    account.bn.get_unimmr.assert_called_once()
    account.bn.fetch_transfer_history.assert_not_called()


def test_first_equity_observation_and_unimmr_failure_keep_saved_data(isolated):
    account=fixture_account()
    account.bn.get_unimmr.side_effect=RuntimeError('offline UniMMR timeout')
    now=datetime(2026,10,31,12,20)
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,2000.,0.,0.,
        run_time=now,observed_at=now,options=dict(details=False,charts=False))
    assert '历史最高账户总净值：2000' in msg
    assert '历史最低账户总净值：2000' in msg
    assert '账户UniMMR：未知' in msg
    account.bn.get_unimmr.assert_called_once()
    saved=pd.read_csv(isolated('target','账户信息','equity.csv'),encoding='gbk')
    assert saved['账户总净值'].tolist()==[2000.]


@pytest.mark.parametrize('offset', range(0,60,5))
def test_target_single_overview_no_trade_or_backfill(tmp_path, monkeypatch, isolated, offset):
    import config
    account=fixture_account(offset)
    me=SimpleNamespace(factory=SimpleNamespace(result_folder=tmp_path,config_list=[]))
    init=Mock(return_value=(account,me))
    monkeypatch.setattr(st,'init_system',init)
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('other.py'),Path('target.py')])
    monkeypatch.setattr(config,'statistics_config',dict(enabled=True,charts=False,details=False,price_impact=False,notify=False),raising=False)
    monkeypatch.setattr(st,'refresh_diff_time',Mock())
    monkeypatch.setattr(st,'get_dummy_client',Mock(side_effect=AssertionError('market universe forbidden')))
    monkeypatch.setattr(st,'get_all_order_info',Mock(side_effect=AssertionError('history reconstruction forbidden')))
    stamp=datetime(2026,10,31,12,offset)
    monkeypatch.setattr(st.sys,'argv',['statistics.py',str(int(stamp.timestamp())),'target'])
    st.run()
    st.run()  # same round must replace, not append
    assert init.call_args_list == [(( 'target',),), (( 'target',),)]
    assert account.bn.get_account_overview.call_count == 2
    account.update_account_info.assert_not_called()
    account.bn.get_spot_ticker_price_series.assert_not_called()
    account.bn.fetch_transfer_history.assert_not_called()
    account.bn.get_unimmr.assert_not_called()  # notify=False only persists base observations.
    equity=pd.read_csv(isolated('target','账户信息','equity.csv'),encoding='gbk')
    assert len(equity)==1
    positions=pd.read_pickle(isolated('target','账户信息','pos_spot.pkl'))
    assert list(positions)==[int(stamp.timestamp()*1000)]


def test_pretrade_snapshot_precision_and_same_round_replacement(isolated):
    account=fixture_account()
    overview=account.bn.get_account_overview.return_value
    account.spot_position=overview['spot_assets']['spot_position_df']
    account.swap_position=overview['swap_assets']['swap_position_df']
    account.bn.get_spot_ticker_price_series.return_value=pd.Series({'ETHUSDT':2407.22})
    stamp=datetime.now().replace(second=0,microsecond=0)
    sf.save_position_snapshot(account,stamp)
    first=pd.read_pickle(isolated('target','账户信息','spot_position_history.pkl'))
    assert first.iloc[0].pos_amount==.6294843
    assert first.iloc[0].cur_price==2407.22
    account.spot_position=account.spot_position.iloc[:0]
    sf.save_position_snapshot(account,stamp)
    stored=pd.read_pickle(isolated('target','账户信息','spot_position_history.pkl'))
    assert len(stored)==1 and stored.iloc[0].symbol=='EMPTY'


def test_failed_optional_tasks_cannot_lose_base(isolated, tmp_path, monkeypatch):
    account=fixture_account()
    me=SimpleNamespace(factory=SimpleNamespace(result_folder=tmp_path,config_list=[]))
    overview=account.bn.get_account_overview.return_value
    account.bn.fetch_transfer_history.side_effect=RuntimeError('TLS failed')
    msg=st.save_and_send_equity_info(account,me,overview['spot_assets']['spot_position_df'],
        overview['swap_assets']['swap_position_df'],2000.,2000.,484.,0.,
        run_time=datetime(2026,10,31,12,20),options=dict(charts=True,details=True,notify=False))
    assert len(pd.read_csv(isolated('target','账户信息','equity.csv'),encoding='gbk'))==1
    assert '最近1小时盈亏' not in msg
    assert '现货净投入/回本价/持仓周期损益：待对账' not in msg


@pytest.mark.parametrize('reason', ['offset','identity','disabled'])
def test_filter_before_any_api(tmp_path, monkeypatch, reason):
    import config
    account=fixture_account()
    if reason=='identity':
        account.name='wrong'
    monkeypatch.setattr(config,'statistics_config',dict(enabled=reason!='disabled'),raising=False)
    init=Mock(return_value=(account,SimpleNamespace()))
    monkeypatch.setattr(st,'init_system',init)
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('target.py')])
    minute=25 if reason=='offset' else 20
    monkeypatch.setattr(st.sys,'argv',['stats',str(int(datetime(2026,10,31,12,minute).timestamp())),'target'])
    if reason=='identity':
        with pytest.raises(RuntimeError,match='identity'):
            st.run()
    else:
        st.run()
    account.bn.get_account_overview.assert_not_called()
    if reason=='disabled':
        init.assert_not_called()


def test_unknown_overview_not_saved_as_zero(tmp_path, monkeypatch):
    import config
    account=fixture_account()
    account.bn.get_account_overview.return_value=None
    monkeypatch.setattr(config,'statistics_config',dict(enabled=True),raising=False)
    monkeypatch.setattr(st,'init_system',lambda _: (account,SimpleNamespace()))
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('target.py')])
    monkeypatch.setattr(st.sys,'argv',['stats',str(int(datetime(2026,10,31,12,20).timestamp())),'target'])
    with pytest.raises(RuntimeError,match='unavailable'):
        st.run()
    assert not list(tmp_path.rglob('equity.csv'))


def test_two_history_reads_and_one_ticker_per_market_save_before_notify(isolated,monkeypatch):
    account=fixture_account()
    now=datetime(2026,10,31,12,20)
    for market,qty in [('spot',.123456789),('swap',-.987654321)]:
        hist=pd.DataFrame([dict(symbol='OLDUSDT',timestamp=now-timedelta(hours=h),observed_at=now-timedelta(hours=h),
             pos_amount=qty,cur_price=100.,market_value=qty*100,pos_u=qty*100,side=np.sign(qty)) for h in (1,24)])
        hist.to_pickle(isolated('target','账户信息',f'{market}_position_history.pkl'))
    account.bn.get_spot_ticker_price_series.return_value=pd.Series({'OLDUSDT':110.})
    account.bn.get_swap_ticker_price_series.return_value=pd.Series({'OLDUSDT':120.})
    real_read=pd.read_pickle
    read=Mock(side_effect=real_read)
    monkeypatch.setattr(pd,'read_pickle',read)
    def failed_send(*args):
        assert isolated('target','账户信息','pnl_history.pkl').exists()
        raise RuntimeError('notification failed')
    monkeypatch.setattr(sf,'send_wechat_work_msg',failed_send)
    with pytest.raises(RuntimeError,match='notification'):
        sf.calculate_and_send_pnl_report(account,pd.DataFrame(),pd.DataFrame(),pd.Series(dtype=float),
            observed_at=now,run_time=now,current_frames=(pd.DataFrame(),pd.DataFrame()),total_equity=1000.)
    assert read.call_count==2
    account.bn.get_spot_ticker_price_series.assert_called_once()
    account.bn.get_swap_ticker_price_series.assert_called_once()
    record=real_read(isolated('target','账户信息','pnl_history.pkl'))[int(now.timestamp()*1000)]
    assert record['1h'][0]['total_pnl']==pytest.approx(.123456789*10)
    assert record['1h'][1]['total_pnl']==pytest.approx(-.987654321*20)
    assert record['24h_status']['spot']['usable']


def test_child_equity_cache_fourteen_reads_for_all_three_views(isolated,tmp_path,monkeypatch):
    account=fixture_account()
    confs=[]
    times=pd.date_range('2026-10-30 00:20',periods=3,freq='h')
    for i in range(14):
        directory=tmp_path/f'child{i}'
        directory.mkdir()
        pd.DataFrame({'candle_begin_time':times,'equity':[100.,101.,102.]}).to_csv(directory/'资金曲线.csv')
        confs.append(SimpleNamespace(name=f'S{i}',get_result_folder=lambda directory=directory:directory))
    me=SimpleNamespace(factory=SimpleNamespace(config_list=confs,result_folder=tmp_path))
    real_read=pd.read_csv
    read=Mock(side_effect=real_read)
    monkeypatch.setattr(pd,'read_csv',read)
    import matplotlib.figure
    monkeypatch.setattr(matplotlib.figure.Figure,'savefig',lambda *a,**k:None)
    cache=st.load_child_equities(me)
    equity=pd.DataFrame({'time':times,'账户总净值':[100.,101.,102.],
        '多头仓位':10.,'空头仓位':-5.,'多头选币':1,'空头选币':1,'多头单币最大持仓':.1,'空头单币最大持仓':.05})
    for send in (False,True,True):
        st.draw_equity_and_send_pic(me,equity.copy(),pd.DataFrame(),'test',account,is_send=send,child_cache=cache)
    assert read.call_count==14
    saved=pd.read_pickle(isolated('target','账户信息','equity.pkl'))
    assert set(saved.time.dt.minute)=={20}
    paths=[call.args[0] for call in st.send_wechat_work_img.call_args_list]
    assert len(paths)==2 and len(set(paths))==2
    assert all(not Path(path).exists() for path in paths)
    assert cache['S0'].candle_begin_time.iloc[0]==times[0]


def test_zero_equity_and_missing_spot_price(isolated,tmp_path):
    account=fixture_account()
    empty=pd.DataFrame()
    msg=st.save_and_send_equity_info(account,None,empty,empty,0.,0.,0.,0.,run_time=datetime(2026,10,31,12,20))
    assert '账户净值：0\n' in msg
    result=sf.calc_spot_position(pd.DataFrame({'symbol':['DUST'],'当前持仓量':[.000000003]}),'target',pd.Series(dtype=float))
    assert result.iloc[0].pos_amount==.000000003
    assert pd.isna(result.iloc[0].pos_u)


def test_atomic_failure_preserves_previous_file(tmp_path):
    from tools.statistics_runtime import atomic_save
    path=tmp_path/'state.pkl'
    path.write_bytes(b'previous')
    def fail(temporary):
        temporary.write_bytes(b'partial')
        raise RuntimeError('write interrupted')
    with pytest.raises(RuntimeError):
        atomic_save(path,fail)
    assert path.read_bytes()==b'previous'
    assert list(tmp_path.iterdir())==[path]


def test_per_account_lock_and_scheduler_no_backlog(tmp_path):
    from tools.statistics_runtime import account_lock, StatisticsTasks
    from threading import Event
    with account_lock(tmp_path/'a') as first:
        with account_lock(tmp_path/'a') as second:
            with account_lock(tmp_path/'b') as other:
                assert first and not second and other
    with account_lock(tmp_path/'a') as unlocked:
        assert unlocked
    messages=[]
    queue=StatisticsTasks(2,messages.append)
    release=Event()
    try:
        assert queue.submit('a',release.wait,3)
        assert not queue.submit('a',release.wait,3)
        assert queue.submit('b',release.wait,3)
        assert not queue.submit('c',release.wait,3)
    finally:
        release.set()
        queue.pool.shutdown(wait=True)
    assert not queue.active
    failed=StatisticsTasks(1,messages.append)
    def error():
        raise RuntimeError('observed worker failure')
    failed.submit('error',error)
    failed.pool.shutdown(wait=True)
    assert any('observed worker failure' in msg for msg in messages)


def test_standalone_import_and_process_lock(tmp_path):
    import subprocess
    import sys
    from tools.statistics_runtime import account_lock
    root=Path(__file__).resolve().parents[1]
    # Exercise script import from a different cwd with all networking forbidden.
    script="""
import socket, runpy, sys
def forbidden(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect=forbidden
runpy.run_path(sys.argv[1], run_name='offline_import')
print('IMPORT_OK')
"""
    result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(root/'core/utils/statistics.py')],
                          cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==0, result.stderr
    assert 'IMPORT_OK' in result.stdout
    script="""
import sys
sys.path.insert(0,sys.argv[1])
from tools.statistics_runtime import account_lock
with account_lock(sys.argv[2]) as acquired:
    print(acquired)
"""
    with account_lock(tmp_path) as acquired:
        assert acquired
        result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(root),str(tmp_path)],
                              capture_output=True,text=True,timeout=10)
        assert result.returncode==0 and result.stdout.strip()=='False'


@pytest.mark.parametrize('rows',[0,1])
def test_timing_short_history(tmp_path,monkeypatch,rows):
    conf=SimpleNamespace(timing=SimpleNamespace(name='timing',params=[1]),get_result_folder=lambda:tmp_path)
    pd.DataFrame({'candle_begin_time':pd.date_range('2026-10-31',periods=rows,freq='h'),'动态杠杆':[1]*rows}).to_csv(tmp_path/'再择时动态杠杆.csv',index=False)
    me=SimpleNamespace(factory=SimpleNamespace(config_list=[conf]))
    send=Mock()
    monkeypatch.setattr(st,'send_img_for_dataframe',send)
    st.send_fixed_ratio_info(me,SimpleNamespace(wechat_webhook_url='fake'))
    if rows:
        assert send.call_count==2
        assert pd.isna(send.call_args.args[0].iloc[0].last_hour_status)


def test_debug_switch_off_before_network(monkeypatch):
    import config
    import core.utils.statistics_debug as debug
    monkeypatch.setattr(config,'statistics_config',dict(enabled=False,notify=False,charts=False))
    refresh=Mock(side_effect=AssertionError('disabled debug must not query'))
    monkeypatch.setattr(debug,'refresh_diff_time',refresh)
    debug.run()
    refresh.assert_not_called()


def test_ineligible_history_does_not_fetch_closed_symbol_prices(isolated):
    account=fixture_account()
    now=datetime(2026,10,31,12,20)
    for market in ('spot','swap'):
        pd.DataFrame([dict(symbol='OLDUSDT', timestamp=now-timedelta(days=2), market_value=100.)]).to_pickle(
            isolated('target','账户信息',f'{market}_position_history.pkl'))
    record=sf.calculate_and_send_pnl_report(account,pd.DataFrame(),pd.DataFrame(),pd.Series(dtype=float),
        observed_at=now,run_time=now,current_frames=(pd.DataFrame(),pd.DataFrame()),notify=False)
    account.bn.get_spot_ticker_price_series.assert_not_called()
    account.bn.get_swap_ticker_price_series.assert_not_called()
    assert not record['1h_status']['spot']['usable']


def test_run_all_optional_failures_preserve_basic_and_estimate(isolated,tmp_path,monkeypatch):
    import config
    account=fixture_account()
    me=SimpleNamespace(factory=SimpleNamespace(result_folder=tmp_path,config_list=[]))
    monkeypatch.setattr(config,'statistics_config',dict(enabled=True,notify=True,charts=True,details=True,price_impact=True))
    monkeypatch.setattr(st,'load_multi_accounts',lambda:[Path('target.py')])
    monkeypatch.setattr(st,'init_system',lambda _: (account,me))
    stamp=datetime(2026,10,31,12,20)
    monkeypatch.setattr(st.sys,'argv',['stats',str(int(stamp.timestamp())),'target'])
    for name in ('send_wechat_work_msg','send_pos_strategy_info','send_img_for_dataframe','build_symbol_strategy_columns'):
        monkeypatch.setattr(st,name,Mock(side_effect=RuntimeError(name+' failed')))
    monkeypatch.setattr(sf,'send_wechat_work_msg',Mock(side_effect=RuntimeError('estimate notification failed')))
    account.bn.fetch_transfer_history.side_effect=RuntimeError('transfer TLS failed')
    account.bn.get_unimmr.side_effect=RuntimeError('UniMMR timeout')
    st.run()
    for name in ('pos_spot.pkl','pos_swap.pkl','pnl_history.pkl'):
        assert isolated('target','账户信息',name).exists()
    assert len(pd.read_csv(isolated('target','账户信息','equity.csv'),encoding='gbk'))==1
    account.bn.get_account_overview.assert_called_once()
    account.update_account_info.assert_not_called()
    st.send_wechat_work_msg.assert_called_once()
    sf.send_wechat_work_msg.assert_called_once()
