"""Stage-native parallelism consumes the existing budget, without nesting pools."""
import os
from concurrent.futures import Future
import pytest


@pytest.mark.parametrize('fail',[False,True])
def test_stage_budget_is_inherited_and_parent_environment_restored(monkeypatch,fail):
    from core import backtest
    seen=[]
    monkeypatch.setenv('POLARS_MAX_THREADS','7')
    monkeypatch.delenv('PMT_STAGE_POLARS_THREADS',raising=False)
    class Pool:
        def __init__(self,**kwargs):assert kwargs['max_workers']==1
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def submit(self,fn,*a,**kw):
            seen.append((os.environ.get('POLARS_MAX_THREADS'),os.environ.get('PMT_STAGE_POLARS_THREADS'),kw))
            f=Future()
            if fail:f.set_exception(RuntimeError('stage failed'))
            else:f.set_result(3)
            return f
    monkeypatch.setattr(backtest,'ProcessPoolExecutor',Pool)
    if fail:
        with pytest.raises(RuntimeError,match='stage failed'):
            backtest._run_block_stage(sum,[1,2],_polars_threads=4)
    else:assert backtest._run_block_stage(sum,[1,2],_polars_threads=4)==3
    assert seen==[('4','4',{})]
    assert os.environ['POLARS_MAX_THREADS']=='7'
    assert 'PMT_STAGE_POLARS_THREADS' not in os.environ


@pytest.mark.parametrize('fail',[False,True])
def test_traditional_stage_limits_all_native_pools_and_restores_parent(monkeypatch,fail):
    from core import backtest
    names=('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS')
    for name in names:monkeypatch.setenv(name,'8')
    class Pool:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def submit(self,fn,*a,**kw):
            assert all(os.environ[name]=='1' for name in names)
            f=Future()
            if fail:f.set_exception(RuntimeError('stage failed'))
            else:f.set_result(3)
            return f
    monkeypatch.setattr(backtest,'ProcessPoolExecutor',Pool)
    if fail:
        with pytest.raises(RuntimeError,match='stage failed'):
            backtest._run_block_stage(sum,[1,2])
    else:assert backtest._run_block_stage(sum,[1,2])==3
    assert all(os.environ[name]=='8' for name in names)


@pytest.mark.parametrize('role',['prepare','initialize','compute','trade'])
def test_model_worker_preserves_only_explicit_stage_polars_budget(monkeypatch,role):
    from tools.robust40 import resources
    monkeypatch.setattr(resources.sys,'platform','win32')
    monkeypatch.setenv('PMT_STAGE_POLARS_THREADS','3')
    resources.configure_process(role)
    assert os.environ['POLARS_MAX_THREADS']=='3'
    assert all(os.environ[name]=='1' for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'))
    monkeypatch.delenv('PMT_STAGE_POLARS_THREADS')
    resources.configure_process(role)
    assert os.environ['POLARS_MAX_THREADS']=='1'
