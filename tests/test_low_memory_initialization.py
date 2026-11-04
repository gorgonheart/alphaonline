"""Offline resource contracts and independent S3 membership oracle."""
import ast
import importlib
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl
import pytest

from test_section_resume_state import candles, oracle

ROOT = Path(__file__).resolve().parents[1]
SECTION = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')


@pytest.mark.parametrize('initializing', [True, False])
@pytest.mark.parametrize('block_hours', [0, 672])
def test_model_profile_limits_every_pool_and_initialization_columns(tmp_path, initializing, block_hours, monkeypatch):
    from tools.robust40.cli.server_robust40 import apply_profile
    from tools.robust40.initialization_config import MODEL_JOB_NUM, INITIALIZATION
    monkeypatch.setitem(INITIALIZATION,'selection_block_hours',block_hours)
    c = SimpleNamespace(strategy_pool=[dict(filter_list=[
        ('VolumeMaxBias_进出场阈值分离官方', (720,.1,.9,'2025-01-01'), 'val:==1')])],
        account_config={'hour_offset':'55m'}, incremental_lookback_hours=0, data_config={},
        strategy_config={'params':{'runtime':{'server_init':True}}},
        factor_col_limit=80, cross_section_chunk_size=80)
    apply_profile(c, '2025-01-01 00:55', tmp_path, initializing)
    assert c.job_num == MODEL_JOB_NUM == 4
    assert c.backtest_job_num == (1 if initializing else 3)
    assert c.select_coin_inner_threads == (2 if initializing and block_hours else 1)
    assert not hasattr(c, 'selection_tasks_per_worker')
    assert callable(c.compute_gate) if initializing else c.compute_gate is None
    assert callable(c.selection_pool_runner) if initializing and block_hours else c.selection_pool_runner is None
    assert c.factor_col_limit == (16 if initializing else 80)
    assert c.cross_section_chunk_size == (1 if initializing else 80)


@pytest.mark.parametrize('limit', [None, 1, 2])
@pytest.mark.parametrize('pool_selection',[False,True])
def test_actual_backtest_orchestration_obeys_resource_override(tmp_path, monkeypatch, limit, pool_selection):
    # Execute the actual orchestration with no IO outside tmp_path and no workers/API calls.
    tree = ast.parse((ROOT/'core/backtest.py').read_text(encoding='utf-8-sig'))
    function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_backtest_multi')
    fake_config = SimpleNamespace(**({} if limit is None else {'backtest_job_num':limit}))
    selected=[]
    def pool_callback(configs,**kwargs):selected.append((len(configs),kwargs))
    if pool_selection:fake_config.selection_pool_runner=pool_callback
    monkeypatch.setitem(sys.modules, 'config', fake_config)
    sizes=[]
    class Pool:
        def __init__(self, max_workers): sizes.append(max_workers)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def submit(self, func, *args):return SimpleFuture(func(*args))
    class SimpleFuture:
        def __init__(self,value):self.value=value
        def result(self):return self.value
    noop=lambda *a,**k:None
    logger=SimpleNamespace(**{k:noop for k in ('debug','info','ok','setLevel')})
    confs=[SimpleNamespace(name=f'S{i}',strategy_list=[1], save=noop,
                           get_fullname=lambda:'test') for i in range(6)]
    factory=SimpleNamespace(result_folder=tmp_path/'results',config_list=confs,
        generate_all_factor_config=lambda:SimpleNamespace(name='all',is_day_period=False))
    ns=dict(time=time,pd=pd,shutil=shutil,BacktestConfigFactory=object,
        logger=logger,divider=noop,
        mem_save_exe=lambda f,*a,**kw:f(*a,**kw) if f is pool_callback else None,load_realtime_data=noop,
        calc_factors=noop,calc_cross_sections=noop,apply_bstocks_black_list=noop,
        get_snapshot_time=lambda *a:None,set_snapshot_time=noop,utc_offset=0,
        ProcessPoolExecutor=Pool,as_completed=lambda fs:iter(fs),
        _step4_worker=noop,_step5_worker=noop,
        _step6_worker=lambda *a:pd.DataFrame({'candle_begin_time':[pd.Timestamp('2026-01-01')]}),
        logging=SimpleNamespace(DEBUG=10))
    exec(compile(ast.Module(body=[function],type_ignores=[]),'backtest-orchestration','exec'),ns)
    assert len(ns['run_backtest_multi'](factory,pd.Timestamp('2026-01-01')))==6
    assert sizes == [4 if limit is None else limit]*(2 if pool_selection else 3)
    assert selected==([(6,{'multi_process':False})] if pool_selection else [])


@pytest.mark.parametrize('declare', [True, False])
def test_section_projection_preserves_other_columns(tmp_path, declare):
    from core.utils.section_state import run_section
    seen=[]
    def calculate(df,*args):
        seen.append(list(df.columns))
        return df.assign(chosen=[1,0]), {'cutoff':'test'}
    factor=SimpleNamespace(signal_with_state=calculate)
    if declare:factor.get_state_input_columns=lambda params:['x']
    conf=SimpleNamespace(section_state=True,get_snapshot_folder=lambda:tmp_path)
    frame=pl.DataFrame({'x':[1,2],'unrelated':[7.,8.]})
    out=run_section(conf,factor,frame.lazy(),(), 'chosen',polars=True).collect()
    assert seen == [['x'] if declare else ['x','unrelated']]
    assert out['unrelated'].to_list()==[7.,8.]
    assert out['chosen'].to_list()==[1,0]


def test_section_projection_keeps_existing_output_for_callback(tmp_path):
    from core.utils.section_state import run_section
    def calculate(df,*args):
        assert list(df)==['x','chosen']
        return df, {}
    factor=SimpleNamespace(signal_with_state=calculate,get_state_input_columns=lambda params:['x'])
    conf=SimpleNamespace(section_state=True,get_snapshot_folder=lambda:tmp_path)
    frame=pl.DataFrame({'x':[1,2],'chosen':[3,4],'other':[5,6]})
    actual=run_section(conf,factor,frame.lazy(),(),'chosen',polars=True).collect()
    assert actual.equals(frame)


def test_legacy_polars_repeated_output_preserves_original_semantics():
    frame=candles();frame['chosen']=42
    params=(17,.25,.6,'2025-01-01 17:20')
    expected=SECTION.signal(frame.copy(),params,'chosen')
    actual=SECTION.signal_polars(pl.from_pandas(frame).lazy(),params,'chosen').collect()
    np.testing.assert_array_equal(actual['chosen'].to_numpy(),expected.chosen)


def test_s3_initialization_does_not_concatenate_all_history_groups(monkeypatch):
    frame=candles(n=17)
    params=(17,.25,.6,str(frame.candle_begin_time.min()+pd.Timedelta(hours=17)))
    expected=oracle(frame,params)
    original=pd.concat
    def bounded(objects,*args,**kwargs):
        if isinstance(objects,(tuple,list)):
            assert len(objects)<=32, 'Historical group copies must not accumulate'
        return original(objects,*args,**kwargs)
    monkeypatch.setattr(pd,'concat',bounded)
    actual,state=SECTION.signal_with_state(frame.copy(),params,'chosen')
    np.testing.assert_array_equal(actual.chosen,expected)
    assert len(state['ranks'])==17%11


def test_s3_declares_only_actual_state_inputs():
    assert SECTION.get_state_input_columns((720,.1,.9,'2025-01-01')) == [
        'candle_begin_time','symbol','is_spot','symbol_spot','symbol_swap','是否交易','VolumeMaxBias_720']


def test_first_eligible_section_preserves_state_membership_order():
    frame=candles().iloc[:18*12].copy()
    frame=frame[frame.symbol.isin(['C0','C1'])]
    frame['VolumeMaxBias_17']=frame.symbol.map({'C0':2.,'C1':1.})
    params=(17,1.,1.,str(frame.candle_begin_time.max()))
    _,state=SECTION.signal_with_state(frame,params,'chosen')
    assert state['held']==['C1','C0']
    assert state['ranks']==[]


def test_initial_checkpoint_output_avoids_full_history_multiindex(monkeypatch):
    frame=candles().sample(frac=1,random_state=4)
    before=frame.copy(deep=True)
    params=(17,.25,.6,'2025-01-01 17:20')
    expected=oracle(frame.sort_index(),params)
    original=pd.MultiIndex.from_frame
    def bounded(df,*a,**kw):
        assert len(df)<len(frame), 'Fresh initialization must not align the entire output through a MultiIndex'
        return original(df,*a,**kw)
    monkeypatch.setattr(pd.MultiIndex,'from_frame',bounded)
    actual,state=SECTION.signal_with_checkpoints(frame,params,'chosen',hours=25)
    np.testing.assert_array_equal(actual.sort_index().chosen,expected)
    pd.testing.assert_frame_equal(frame,before)
    assert len(state['checkpoints'])==25


def test_initial_checkpoint_calculation_bounds_each_time_block(monkeypatch):
    times=pd.date_range('2025-01-01 00:55',periods=1200,freq='h')
    frame=pd.DataFrame(dict(candle_begin_time=np.repeat(times,2),symbol=['A','B']*len(times),
        is_spot=0,symbol_spot='',symbol_swap='',是否交易=1,VolumeMaxBias_17=[1.,2.]*len(times)))
    original=SECTION.signal_with_state
    def bounded(df,*a,**kw):
        assert df.candle_begin_time.nunique()<=168
        return original(df,*a,**kw)
    monkeypatch.setattr(SECTION,'signal_with_state',bounded)
    actual,state=SECTION.signal_with_checkpoints(frame,(17,.5,.9,str(times[17])),'chosen',hours=25)
    expected=np.tile([1,0],len(times));expected[:34]=0
    np.testing.assert_array_equal(actual.chosen,expected)
    assert len(state['checkpoints'])==25


def test_bounded_pool_does_not_eagerly_consume_every_input():
    from concurrent.futures import ThreadPoolExecutor
    from tools.bounded_pool import iter_results
    consumed=[]
    def inputs():
        for i in range(100):
            consumed.append(i)
            yield (i,)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=iter_results(pool, lambda i:i*2, inputs(), max_pending=4)
        first=next(results)
        assert len(consumed)<=4
        assert sorted([first,*results])==list(range(0,200,2))


def test_bounded_pool_propagates_worker_failure():
    from concurrent.futures import ThreadPoolExecutor
    from tools.bounded_pool import iter_results
    def fail(value):raise ValueError('worker failed')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError,match='worker failed'):
            list(iter_results(pool,fail,[(1,)],max_pending=2))
