"""Actual 5m/15m saved targets through calc_equity; all writes stay in tmp_path."""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest


@pytest.mark.parametrize('account', ['5m-onheart1020.py','15m-nheart1020.py','25m-eart1020.py','45m_gonheart1020.py'])
def test_current_legacy_strategy_pool_does_not_enable_model_replay(account,legacy_account_path):
    import ast
    from shared_account_config import shared_config
    from core.model.strategy_config import PosStrategyConfig
    from core.model.backtest_config import BacktestConfig
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse(legacy_account_path(account).read_text(encoding='utf-8-sig'))
    values={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('strategy_config','strategy_pool'):
            values[node.targets[0].id]=eval(compile(ast.Expression(node.value),account,'eval'),
                {'__builtins__':{},'dict':dict,'range':range,'zip':zip,'list':list,'shared_config':shared_config})
    parent=PosStrategyConfig(**values['strategy_config']);parent.load()
    assert parent.name=='HybridRotationDriftStrategy'
    assert 'prepare_live' not in parent.core_funcs and 'transform_targets' not in parent.core_funcs
    assert len(values['strategy_pool'])==14
    for spec in values['strategy_pool']:
        conf=BacktestConfig('offline-checkpoint-trial')
        conf.load_strategy_config(spec['strategy_list'],spec.get('re_timing'))
        assert conf.section_state is False and conf.incremental_lookback_hours==0
        assert conf.rebalance_mode.mode=='RebAlways'


@pytest.mark.parametrize('account', ['5m-onheart1020_', '15m-nheart1020_'])
@pytest.mark.parametrize('child', ['S1-大学生实盘多头_后置过滤','S3-GA实盘多头0317'])
@pytest.mark.parametrize('capture_only', [False, True])
@pytest.mark.parametrize('state_kind',['state0','state1'])
def test_real_legacy_snapshot_restore_and_rewrite_matches_full(tmp_path,monkeypatch,account,child,capture_only,state_kind,producer_root):
    import socket
    def blocked(*a,**k):raise AssertionError('Offline test attempted network access')
    monkeypatch.setattr(socket.socket,'connect',blocked)
    monkeypatch.setattr(socket,'create_connection',blocked)
    from core import equity
    from core.model.rebalance_mode import RebalanceMode
    root=Path(__file__).resolve().parents[1]
    source=root/'data/snapshot'/account/child
    if not source.exists():pytest.skip('Local saved account is unavailable')
    lots=pd.read_pickle(source/'lots_state0.pkl')
    ratios={k:pd.read_pickle(source/f'ratio_{k}.pkl').tail(240) for k in ('spot','swap')}
    index=ratios['swap'].index;assert ratios['spot'].index.equals(index)
    pivots={};units={}
    for kind in ('spot','swap'):
        path=producer_root/'data/preprocess_1h_resample'/(account.split('-')[0])/f'market_pivot_{kind}_2026.pkl'
        pivots[kind]={k:v.reindex(index=index,columns=ratios[kind].columns).copy()
                      for k,v in pd.read_pickle(path).items()}
        units[kind]=pd.DataFrame({'lot_size':lots[kind]['sim'].lot_sizes},index=lots[kind]['columns'])
    monkeypatch.setattr(equity,'read_bmac_market_pivot',lambda offset,kind:{k:v.copy() for k,v in pivots[kind].items()})
    monkeypatch.setattr(equity,'read_bmac_exginfo',lambda kind:units[kind].rename_axis('symbol').reset_index())
    def config(name):
        result=tmp_path/name/'result';state=tmp_path/name/'state'
        result.mkdir(parents=True);state.mkdir()
        return SimpleNamespace(get_result_folder=lambda:result,get_snapshot_folder=lambda:state,
            incremental_lookback_hours=0 if capture_only else 24,checkpoint_hours=24 if capture_only else 0,
            initial_usdt=10000.,is_day_period=False,leverage=1.,margin_rate=.05,
            spot_c_rate=.002,swap_c_rate=.0006,spot_min_order_limit=20.,swap_min_order_limit=20.,
            rebalance_mode=RebalanceMode.init(None))
    def run(conf, stop):
        for kind,frame in ratios.items():
            frame=frame.iloc[:stop].copy();frame.index=frame.index.tz_convert('UTC').tz_localize(None)
            frame.to_pickle(conf.get_result_folder()/f'df_{kind}_ratio.pkl')
        run_time=(index[stop-1]+pd.Timedelta(hours=1)).to_pydatetime()
        base=equity.calc_equity(conf,run_time,state_name='state0')
        if state_kind=='state0':return base
        return equity.calc_equity(conf,run_time,state_name='state1',leverage=pd.Series(1.,index=range(stop)))
    resumed=config('resumed');run(resumed,239)
    positions=np.flatnonzero(ratios['swap'].iloc[-4].to_numpy()!=0)
    assert len(positions)>0
    pivots['swap']['close'].iloc[-4,positions[0]]*=1.01
    if capture_only:
        from tools.robust40.handoff import restore_child
        restore_child(resumed.get_snapshot_folder(),index[-5])
    full=config('full');expected=run(full,240);actual=run(resumed,240)
    pd.testing.assert_frame_equal(actual,expected,check_exact=False,check_dtype=False,rtol=1e-12,atol=1e-9)
    for kind in ('spot','swap'):
        pd.testing.assert_frame_equal(pd.read_pickle(resumed.get_snapshot_folder()/f'ratio_{kind}.pkl'),
                                      pd.read_pickle(full.get_snapshot_folder()/f'ratio_{kind}.pkl'))
    final=pd.read_pickle(resumed.get_snapshot_folder()/'lots_state0.pkl')
    assert final['simulate_to']==index[-1] and len(final['snapshot_queue'])==24
