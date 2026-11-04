"""The shared switch must not silently replace the older opt-in initializer."""
from contextlib import nullcontext
from types import SimpleNamespace
import pandas as pd
import pytest


@pytest.mark.parametrize('shared',[False,True])
def test_initializer_front_half_respects_shared_switch(tmp_path,monkeypatch,shared):
    import config
    from tools import initialization_pipeline as pipeline,initialization_data,streaming_factors
    from core import backtest,select_coin
    from core.utils import commons,exchange_hub
    from tools.robust40 import resources
    calls=[]
    child=SimpleNamespace(save=lambda:None)
    conf=SimpleNamespace(name='switch')
    factory=SimpleNamespace(config_list=[child],generate_all_factor_config=lambda:conf)
    store=SimpleNamespace(sections={},section_columns=[])
    monkeypatch.setattr(config,'shared_factor_pipeline',shared)
    monkeypatch.setattr(commons,'get_snapshot_time',lambda _:None)
    monkeypatch.setattr(exchange_hub,'apply_bstocks_black_list',lambda _:None)
    monkeypatch.setattr(pipeline,'supported',lambda *a:True)
    monkeypatch.setattr(select_coin,'get_file_path',lambda *a,**kw:tmp_path/'blocks')
    monkeypatch.setattr(initialization_data,'FactorBlocks',lambda *a,**kw:store)
    monkeypatch.setattr(resources,'phase',lambda *a:nullcontext())
    monkeypatch.setattr(pipeline,'_isolated',lambda fn,*a:fn(*a))
    monkeypatch.setattr(streaming_factors,'factor_blocks',lambda *a:calls.append('old_factors'))
    monkeypatch.setattr(pipeline,'sections',lambda *a:(calls.append('old_sections') or ({},[])))
    monkeypatch.setattr(pipeline,'selections',lambda *a:(calls.append('old_selection') or {}))
    monkeypatch.setattr(backtest,'prepare_factor_selection',lambda *a,**kw:(calls.append('shared') or (store,{})))
    def stop(_):raise RuntimeError('front half complete')
    monkeypatch.setattr(pipeline,'_swap_mapping',stop)
    with pytest.raises(RuntimeError,match='front half complete'):
        pipeline.run(SimpleNamespace(factory=factory),pd.Timestamp('2026-01-01 00:55'),55)
    assert calls==(['shared'] if shared else ['old_factors','old_sections','old_selection'])


@pytest.mark.parametrize('shared,legacy,expected',[
    (False,False,'core'),(False,True,'legacy_blocks'),
    (True,False,'shared_blocks'),(True,True,'shared_blocks')])
def test_compute_routes_both_feature_switches(tmp_path,monkeypatch,shared,legacy,expected):
    import config
    from tools.robust40.cli import server_robust40 as worker
    from tools.robust40 import server,resources
    from tools.robust40.initialization_config import INITIALIZATION
    from tools import initialization_pipeline as pipeline
    from core.model import backtest_config
    from core.utils import datatools
    monkeypatch.setattr(config,'shared_factor_pipeline',shared)
    monkeypatch.setitem(INITIALIZATION,'data_pipeline',legacy)
    monkeypatch.setattr(config,'account_config',dict(hour_offset='55m',rebalance_mode={}))
    monkeypatch.setattr(server,'settings',lambda _:({},{}))
    monkeypatch.setattr(server,'location',lambda *a:tmp_path/'snapshot')
    monkeypatch.setattr(pd,'read_pickle',lambda _: {})
    monkeypatch.setattr(datatools,'snapshot_bmac_exginfo',lambda:None)
    monkeypatch.setattr(resources,'phase',lambda *a:nullcontext())
    def stop(name):raise RuntimeError(name)
    mef=SimpleNamespace(factory=None,backtest_strategies=lambda *a:stop('core'))
    monkeypatch.setattr(backtest_config,'MultiEquityBacktestConfig',lambda:mef)
    monkeypatch.setattr(pipeline,'supported',lambda *a:True)
    monkeypatch.setattr(pipeline,'run',lambda *a:stop('shared_blocks' if shared else 'legacy_blocks'))
    with pytest.raises(RuntimeError,match='^'+expected+'$'):
        worker.compute(tmp_path/'account.py',tmp_path,1793538900,tmp_path/'destination')
