from types import SimpleNamespace
import pandas as pd
import numpy as np
import pytest


def test_retiming_reuses_market_and_state0_curve(tmp_path, monkeypatch):
    from core import backtest
    from core.utils import datatools
    curve = pd.DataFrame({'candle_begin_time':pd.date_range('2026-01-01',periods=3,freq='h'), 'equity':[1.,1.1,1.2]})
    reads=[]; calls=[]
    monkeypatch.setattr(datatools,'read_bmac_market_pivot',lambda offset,kind:reads.append((offset,kind)) or {'close':curve})
    monkeypatch.setattr(datatools,'read_bmac_exginfo',lambda kind:pd.DataFrame({'symbol':['A'],'lot_size':[.1]}))
    monkeypatch.setattr(backtest,'TimingSignal',SimpleNamespace)
    conf=SimpleNamespace(name='S5', timing=SimpleNamespace(),leverage=1.,get_snapshot_folder=lambda:tmp_path,
                         get_fullname=lambda **kw:'S5')
    monkeypatch.setattr(backtest,'save_performance_df_csv',lambda *a,**kw:None)
    monkeypatch.setattr(backtest,'calc_timing_leverages',lambda c,df:pd.Series([1.,0.,1.]) if df is curve else (_ for _ in ()).throw(AssertionError('curve copied/read')))
    def simulate(*a,**kw):
        calls.append(kw)
        return curve
    monkeypatch.setattr(backtest,'calc_equity',simulate)
    curve.to_pickle(tmp_path/'account_state0.pkl')
    def forbidden(*args,**kwargs):
        raise AssertionError('Just-produced state0 curve reread from disk')
    monkeypatch.setattr(pd,'read_pickle',forbidden)
    result=backtest.step6_simulate_performance(conf,pd.Timestamp('2026-01-01 04:25'))
    assert result is curve
    assert reads==[('25m','spot'),('25m','swap')]
    assert calls[0]['market_inputs'] is calls[1]['market_inputs']
    assert calls[0]['state_name']=='state0' and calls[1]['state_name']=='state1'
    assert calls[1]['leverage'].tolist()==[1.,0.,1.]


@pytest.mark.parametrize('minute,lookback',[(5,0),(25,0),(45,3)])
def test_real_retiming_pair_matches_file_path_across_continuation(tmp_path, monkeypatch, minute, lookback):
    from core import equity, backtest
    from core.utils import datatools
    from core.model.rebalance_mode import RebalanceMode
    from core.model.timing_signal import TimingSignal
    index=pd.date_range(f'2026-01-01 00:{minute:02}',periods=80,freq='h',tz='UTC')
    ratio=pd.DataFrame({'A':np.where(np.arange(80)%9<5,.3,0.)},index=index.tz_localize(None))
    market={kind:{field:pd.DataFrame({'A':np.arange(80)/100+10 if field!='funding_rate' else np.full(80,.0001)},index=index)
                  for field in ['open','close','vwap1m','funding_rate']} for kind in ['spot','swap']}
    units=pd.DataFrame({'symbol':['A'],'lot_size':[.01]})
    for module in (equity,datatools):
        monkeypatch.setattr(module,'read_bmac_market_pivot',lambda offset,kind:dict(market[kind]))
        monkeypatch.setattr(module,'read_bmac_exginfo',lambda kind:units)
    def config(name):
        path=tmp_path/name;path.mkdir()
        timing=TimingSignal.__new__(TimingSignal)
        timing.impl_flags={'dynamic_leverage':True}
        timing.get_dynamic_leverage=lambda e:pd.Series(np.where(np.arange(len(e))%3==0,0.,1.))
        return SimpleNamespace(name=name,timing=timing,get_fullname=lambda **kw:name,
            get_result_folder=lambda:path,get_snapshot_folder=lambda:path,
            incremental_lookback_hours=lookback,checkpoint_hours=25,initial_usdt=10000.,is_day_period=False,
            leverage=1.,margin_rate=.05,spot_c_rate=.002,swap_c_rate=.0006,spot_min_order_limit=20.,
            swap_min_order_limit=20.,rebalance_mode=RebalanceMode.init(None))
    old,new=config('old'),config('new')
    for stop in (78,79,80):
        for c in (old,new):
            for kind in ('spot','swap'):ratio.iloc[:stop].to_pickle(c.get_result_folder()/f'df_{kind}_ratio.pkl')
        run=index[stop-1]+pd.Timedelta(hours=1)
        a=equity.calc_equity(old,run,state_name='state0')
        backtest.save_performance_df_csv(old,资金曲线=a)
        expected=backtest.simu_timing(old,run)
        actual=backtest.step6_simulate_performance(new,run)
        pd.testing.assert_frame_equal(actual,expected,check_exact=True)
        for file in ('lots_state0.pkl','lots_state1.pkl'):
            _assert_state_exact(pd.read_pickle(new.get_snapshot_folder()/file), pd.read_pickle(old.get_snapshot_folder()/file))
        for file in ('account_state0.pkl','account_state1.pkl','ratio_spot.pkl','ratio_swap.pkl'):
            pd.testing.assert_frame_equal(pd.read_pickle(new.get_snapshot_folder()/file),
                                          pd.read_pickle(old.get_snapshot_folder()/file),check_exact=True)


def _assert_state_exact(actual, expected):
    from core.simulator import Simulator
    if isinstance(actual, dict):
        assert actual.keys() == expected.keys()
        for key in actual:_assert_state_exact(actual[key], expected[key])
    elif isinstance(actual, (tuple, list)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for a,b in zip(actual,expected):_assert_state_exact(a,b)
    elif isinstance(actual,pd.Index):
        pd.testing.assert_index_equal(actual,expected,exact=True)
    elif isinstance(actual, pd.Series):
        pd.testing.assert_series_equal(actual,expected,check_exact=True)
    elif isinstance(actual, Simulator):
        assert type(actual) is type(expected)
        _assert_state_exact(vars(actual),vars(expected))
    else:
        np.testing.assert_equal(actual,expected)
