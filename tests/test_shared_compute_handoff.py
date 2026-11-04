"""The common front half hands original files to unchanged aggregation/reporting."""
from types import SimpleNamespace
import pandas as pd
import polars as pl
import pytest


@pytest.mark.parametrize('mode,snapshot', [('arrow_full_strict',None),('arrow_full_strict',''),('arrow_strict','2025-01-02 00:55'),('arrow_strict','2025-01-01 20:55')])
@pytest.mark.parametrize('budget',[1,2,4,8])
def test_initialize_continue_replay_use_same_factor_selection_entry(tmp_path,monkeypatch,mode,snapshot,budget):
    import config
    from core import backtest,select_coin as select
    from test_streaming_selection import inputs,strategy
    frame=inputs(tmp_path/'source')
    conf=SimpleNamespace(name='test',input_mode=mode,max_hold_period='3h',lookback_days=0,
                          incremental_lookback_hours=7)
    child=SimpleNamespace(strategy_list=[strategy()],unified_time='2017-01-01',get_result_folder=lambda:tmp_path)
    factory=SimpleNamespace(config_list=[child],generate_all_factor_config=lambda:conf)
    calls=[];budgets=[]
    monkeypatch.setattr(config,'job_num',budget)
    monkeypatch.setattr(backtest,'apply_bstocks_black_list',lambda conf:None)
    monkeypatch.setattr(backtest,'get_snapshot_time',lambda _:snapshot)
    def run_stage(fn,*a,_polars_threads=1,**kw):
        budgets.append(_polars_threads)
        return fn(*a,**kw)
    monkeypatch.setattr(backtest,'_run_block_stage',run_stage,raising=False)
    def factors(conf,**kwargs):
        calls.append('factor');kwargs['store'].append(frame,0)
    monkeypatch.setattr(backtest,'calc_factors',factors)
    monkeypatch.setattr(backtest,'calc_cross_sections',lambda conf,**kw:(calls.append('section') or ({},[])))
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    monkeypatch.setattr(config,'selection_block_hours',6,raising=False)
    monkeypatch.setattr(config,'select_coin_inner_threads',2,raising=False)
    store,paths=backtest.prepare_factor_selection(factory,pd.Timestamp('2025-01-02 07:55'),directory=tmp_path/'shared')
    backtest.persist_block_selections([child],paths)
    expected_frame=frame if not snapshot else frame.filter(pl.col('candle_begin_time')>=pd.Timestamp(snapshot)-pd.Timedelta(hours=7))
    expected=select.process_strategy_thread(child.strategy_list[0],tmp_path,factor_source=expected_frame.lazy(),return_result=True)
    actual=pd.read_pickle(tmp_path/'oracle.pkl')
    pd.testing.assert_frame_equal(actual,expected.to_pandas(),check_categorical=False)
    assert calls==['factor','section']
    assert budgets==[1,min(2,budget),budget]
    assert conf.incremental_lookback_hours==7
    assert store.read_history(['symbol','close']).height==frame.height
    store.close()


def test_special_strategies_keep_full_history_entry():
    from core.backtest import supports_factor_blocks
    from test_streaming_selection import strategy
    stg=strategy();factory=SimpleNamespace(config_list=[SimpleNamespace(strategy_list=[stg])])
    assert supports_factor_blocks(factory)
    stg.use_custom_func=True
    assert not supports_factor_blocks(factory)
    stg.use_custom_func=False;stg.select_mode='entry_exit'
    assert not supports_factor_blocks(factory)


def test_unreviewed_section_family_keeps_legacy_dispatch(monkeypatch):
    from core.backtest import supports_factor_blocks
    from core.utils.factor_hub import FactorHub
    from test_streaming_selection import strategy
    # Presence of a Polars entry does not prove equivalence or lack of mutation.
    monkeypatch.setattr(FactorHub,'get_by_name',lambda _:SimpleNamespace(signal_polars=lambda:None))
    child=SimpleNamespace(strategy_list=[strategy()],section_params_dict={'custom_section':[1]})
    assert not supports_factor_blocks(SimpleNamespace(config_list=[child]))


@pytest.mark.parametrize('start',[None,pd.Timestamp('2025-01-02 00:55')])
def test_reports_release_owned_directory_for_next_call(tmp_path,monkeypatch,start):
    from core import backtest,select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    data=inputs(tmp_path/'source')
    conf=SimpleNamespace(strategy_list=[strategy()],get_result_folder=lambda:tmp_path)
    factory=SimpleNamespace(config_list=[conf])
    cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setattr(select,'get_file_path',lambda *parts,**kw:cache/parts[-1])
    def prepare(factory,run_time,*,directory):
        directory.mkdir()
        store=FactorBlocks(directory/'factors',hours=24,minute=55)
        store.append(data,0)
        store.selection_start=start
        (directory/'selected').mkdir()
        return store,{0:{0:[]}}
    monkeypatch.setattr(backtest,'prepare_factor_selection',prepare)
    for _ in range(2):
        backtest._prepare_shared_reports(factory,pd.Timestamp('2025-01-02 00:55'))
        assert not (cache/'shared_compute').exists()
        expected=data if start is None else data.filter(pl.col('candle_begin_time')>=start)
        assert pl.read_parquet(cache/'all_factors_kline.parquet').equals(expected.select(select.KLINE_COLS))
