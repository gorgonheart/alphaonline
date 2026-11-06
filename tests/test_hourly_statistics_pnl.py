from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest

NOW=datetime(2026,11,6,20,21,23,742944)
PREVIOUS=datetime(2026,11,6,19,21,26,163963)
RUN=datetime(2026,11,6,20,15)


def test_real_account_seconds_drift_is_not_missing_history(tmp_path,monkeypatch):
    from core.utils import statistics as st
    monkeypatch.setattr(st,'data_path',tmp_path)
    def path(*parts,**kwargs):
        out=tmp_path.joinpath(*[str(p) for p in parts if str(p)!=str(tmp_path)])
        out.parent.mkdir(parents=True,exist_ok=True);return out
    monkeypatch.setattr(st,'get_file_path',path)
    p=path('acct','账户信息','equity.csv')
    pd.DataFrame([dict(time=RUN-timedelta(hours=1),observed_at=PREVIOUS,账户总净值=16433.001473,
        equity_source='binance_pm_actualEquity',equity_currency='USD',offset_minute=15)]).to_csv(p,index=False,encoding='gbk')
    msg=st.save_and_send_equity_info(SimpleNamespace(name='acct',hour_offset_minute=15,bn=Mock()),None,
        pd.DataFrame(),pd.DataFrame(),0.,16422.337132,0.,0.,run_time=RUN,observed_at=NOW,
        options=dict(notify=False,details=False),metrics=dict(equity_source='binance_pm_actualEquity',equity_currency='USD'))
    assert '1小时账户损益：-11 USD' in msg
    assert '起点 11-06 19:21:26' in msg
    assert '实际间隔 59分58秒' in msg
    assert '24小时账户损益：历史不足' in msg


@pytest.mark.parametrize('delta,slot_delta,usable',[(3,0,True),(-3,0,True),(601,0,False),(3,1,False),(3,-60,False),(3600,0,False)])
def test_hourly_selection_requires_right_round_and_bounded_prior_observation(delta,slot_delta,usable):
    from tools.statistics_snapshot import observation_baseline_mask
    now=pd.Timestamp('2026-11-06 20:21:00')
    times=pd.Series([now-pd.Timedelta(hours=1)+pd.Timedelta(seconds=delta)])
    slots=pd.Series([RUN-timedelta(hours=1)+timedelta(minutes=slot_delta)])
    mask=observation_baseline_mask(times,now,1,run_times=slots,run_time=RUN)
    assert bool(mask.iloc[0]) is usable


def test_two_position_history_filters_keep_the_same_real_baseline():
    from tools.statistics_snapshot import position_history
    from core.utils.statis_func import get_historical_position
    frame=pd.DataFrame(dict(symbol=['ETHUSDT'],market_value=[100.],pos_amount=[1.],cur_price=[100.],side=[1]))
    frame.attrs.update(phase='post_trade',snapshot_source='native',offset_minute=15,run_time=RUN-timedelta(hours=1),observed_at=PREVIOUS)
    wrong=frame.copy();wrong.attrs.update(offset_minute=25)
    history=position_history({1:frame,2:wrong},source='native',offset_minute=15,observed_at=NOW,run_time=RUN)
    assert len(history)==1
    result=get_historical_position('acct',1,observed_at=NOW,run_time=RUN,history=history)
    assert result.attrs['valid_baseline']
    assert result.attrs['baseline_time']==pd.Timestamp(PREVIOUS)
    assert result.market_value.tolist()==[100.]


def test_24h_and_legacy_calls_keep_no_future_target_rule():
    from tools.statistics_snapshot import observation_baseline_mask
    times=pd.Series([PREVIOUS])
    assert not observation_baseline_mask(times,NOW,1).iloc[0]
    assert not observation_baseline_mask(times,NOW+timedelta(hours=23),24,run_times=pd.Series([RUN-timedelta(hours=1)]),run_time=RUN+timedelta(hours=23)).iloc[0]


def test_equity_entry_without_scheduled_round_keeps_legacy_window(tmp_path,monkeypatch):
    from core.utils import statistics as st
    monkeypatch.setattr(st,'data_path',tmp_path)
    def path(*parts,**kwargs):
        out=tmp_path.joinpath(*[str(p) for p in parts if str(p)!=str(tmp_path)])
        out.parent.mkdir(parents=True,exist_ok=True);return out
    monkeypatch.setattr(st,'get_file_path',path)
    pd.DataFrame([dict(time=RUN-timedelta(hours=1),observed_at=NOW-timedelta(hours=1,seconds=3),账户总净值=100.,
        equity_source='native',equity_currency='USD')]).to_csv(path('legacy','账户信息','equity.csv'),index=False,encoding='gbk')
    msg=st.save_and_send_equity_info(SimpleNamespace(name='legacy',bn=Mock()),None,pd.DataFrame(),pd.DataFrame(),0.,110.,0.,0.,
        observed_at=NOW,options=dict(notify=False,details=False),metrics=dict(equity_source='native',equity_currency='USD'))
    assert '1小时账户损益：10 USD' in msg


def test_position_report_passes_none_for_unspecified_round(tmp_path,monkeypatch):
    from core.utils import statis_func as sf
    monkeypatch.setattr(sf,'data_path',tmp_path)
    actual=sf.get_historical_position
    calls=[]
    def capture(*args,**kwargs):
        calls.append(kwargs.get('run_time'))
        return actual(*args,**kwargs)
    monkeypatch.setattr(sf,'get_historical_position',capture)
    monkeypatch.setattr(sf,'send_wechat_work_msg',Mock())
    account=SimpleNamespace(name='legacy',bn=Mock(),hour_offset_minute=15,wechat_webhook_url='')
    sf.calculate_and_send_pnl_report(account,pd.DataFrame(),pd.DataFrame(),pd.Series(dtype=float),observed_at=NOW,
        current_frames=(pd.DataFrame(),pd.DataFrame()),notify=False,history_cache={'spot':{},'swap':{}},snapshot_source='native')
    assert calls==[None]*4
