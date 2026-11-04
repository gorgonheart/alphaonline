"""Stage equivalence including stateful sections and two original continuations."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import polars as pl
import pytest


def test_disabled_blocks_and_custom_strategies_keep_original_entry():
    from tools.initialization_pipeline import supported
    strategy=SimpleNamespace(use_custom_func=False,select_mode='standard')
    factory=SimpleNamespace(config_list=[SimpleNamespace(strategy_list=[strategy])])
    assert supported(factory,2016)
    assert not supported(factory,0)
    strategy.use_custom_func=True
    assert not supported(factory,2016)
    strategy.use_custom_func=False;strategy.select_mode='entry_exit'
    assert not supported(factory,2016)


@pytest.mark.parametrize('minute',[20,40,55])
def test_actual_offset_pools_load_independently(minute):
    from pathlib import Path
    from tools.robust40.server import settings,check_dependencies,configured_s3
    from tools.robust40.offline import assignment
    from tools.initialization_pipeline import supported
    from core.model.backtest_config import BacktestConfig
    from core.model.strategy_config import PosStrategyConfig
    path=Path(__file__).resolve().parents[1]/'accounts'/f'{minute}m_robust_4h.py'
    if not path.exists():
        path = Path(__file__).resolve().parents[1]/'tests/fixtures/model_accounts'/path.name
    values,options=settings(path)
    assert options['minute']==minute
    assert configured_s3(values,minute)[0].minute==minute
    check_dependencies(values)
    # settings() normalizes tuples to JSON lists for identity comparison.
    # Load executable account values with their original tuple parameter types.
    parent=PosStrategyConfig(**assignment(path,'strategy_config'));parent.load()
    children=[]
    for item in assignment(path,'strategy_pool'):
        conf=BacktestConfig('offline-offset-contract')
        conf.load_strategy_config(item['strategy_list'],item.get('re_timing'))
        children.append(conf)
    assert len(children)==14
    assert supported(SimpleNamespace(config_list=children),2016)


def test_sections_span_blocks_without_resetting_history(tmp_path):
    import importlib
    from tools.initialization_data import FactorBlocks
    from tools.initialization_pipeline import sections
    from core.utils.section_state import run_section
    from test_section_resume_state import candles
    data=candles(20).assign(close=1.,next_close=1.)
    data=data.sort_values(['symbol','candle_begin_time'],kind='stable').reset_index(drop=True)
    params=(17,.25,.6,'20250102T002000');name='VolumeMaxBias_进出场阈值分离官方'
    top=(20,'VolumeMaxBias',17);topname='TopKTo2K'
    names=[f'{name}_{params}',f'{topname}_{top}']
    def conf(folder):
        folder.mkdir()
        return SimpleNamespace(section_state=True,incremental_lookback_hours=0,checkpoint_hours=25,
            section_params_dict={name:[params],topname:[top]},factor_col_name_list=names,
            get_snapshot_folder=lambda:folder)
    original=conf(tmp_path/'old');candidate=conf(tmp_path/'new')
    expected=pl.from_pandas(data).lazy()
    for n,p,c in [(name,params,names[0]),(topname,top,names[1])]:
        expected=run_section(original,importlib.import_module('sections.'+n),expected,p,c,polars=True)
    expected=expected.collect()
    store=FactorBlocks(tmp_path/'blocks',hours=19,minute=20,backend='ipc')
    for i,group in enumerate(data.groupby('symbol',sort=False)):
        store.append(pl.from_pandas(group[1]),i)
    sections(candidate,store)
    assert store.read_history(names).equals(expected.select(names))
    old=pd.read_pickle(next((tmp_path/'old/sections').glob('*.pkl')))
    new=pd.read_pickle(next((tmp_path/'new/sections').glob('*.pkl')))
    assert old['cutoff']==new['cutoff']
    assert len(new['checkpoints'])==len(old['checkpoints'])==25
    store.close()


def test_partition_selection_matches_original(tmp_path):
    from test_streaming_selection import inputs,strategy
    from core import select_coin as select
    from tools.initialization_data import FactorBlocks
    from tools.initialization_pipeline import selections
    data=inputs(tmp_path/'source')
    conf=SimpleNamespace(strategy_list=[strategy()],get_result_folder=lambda:tmp_path,
                         unified_time='2017-01-01',name='test')
    expected=select.process_strategy_thread(conf.strategy_list[0],tmp_path,factor_source=data.lazy(),return_result=True)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=55,backend='ipc')
    for i,part in enumerate(data.partition_by('symbol',maintain_order=True)):store.append(part,i)
    original=store.iter_blocks
    def projected(columns,**options):
        # The existing selector sorts only each strategy's projected columns;
        # do not first duplicate/sort the entire wide time block.
        assert options.get('sort_time') is False
        return original(columns,**options)
    store.iter_blocks=projected
    paths=selections([conf],store,tmp_path/'selected',2)
    actual=pl.concat([pl.read_ipc(p,memory_map=False) for p in paths[0][0]])
    assert actual.equals(expected)
    store.close()


def test_sorted_block_input_preserves_ties_and_missing_symbols(tmp_path):
    from test_streaming_selection import inputs,strategy
    from core import select_coin as select
    frame=inputs(tmp_path/'source')
    expected=select.process_strategy_thread(strategy(),tmp_path,factor_source=frame.lazy(),return_result=True)
    ordered=frame.sort(['candle_begin_time','symbol'])
    actual=select.process_strategy_thread(strategy(),tmp_path,factor_source=ordered.lazy(),
                                          source_sorted=True,return_result=True)
    assert actual.equals(expected)


@pytest.mark.parametrize('scope,preferred',[('spot','spot'),('swap','swap'),('mix','spot'),('mix','swap')])
def test_shared_sort_preserves_market_preference_and_invalid_row_filter(tmp_path,scope,preferred):
    from test_streaming_selection import inputs,strategy
    from core import select_coin as select
    swap=inputs(tmp_path/'source').with_columns(pl.col('symbol').alias('symbol_spot'))
    spot=swap.with_columns(pl.lit(1,dtype=swap.schema['is_spot']).alias('is_spot'),(pl.col('f')+.25).alias('f'))
    invalid=spot.head(1).with_columns(pl.lit(None,dtype=pl.String).alias('symbol'))
    closed=spot.head(1).with_columns(pl.lit(0,dtype=spot.schema['是否交易']).alias('是否交易'))
    frame=pl.concat([swap,spot,invalid,closed]).sample(fraction=1.,shuffle=True,seed=21)
    stg=strategy();stg.select_scope=scope;stg.order_first=preferred
    expected=select.process_strategy_thread(stg,tmp_path,factor_source=frame.lazy(),return_result=True)
    actual=select.process_strategy_thread(stg,tmp_path,
        factor_source=frame.sort(['candle_begin_time','symbol']).lazy(),source_sorted=True,return_result=True)
    assert actual.equals(expected)
    assert set(actual['is_spot'])=={int(preferred=='spot')}
    assert actual['symbol'].null_count()==0


def test_isolated_section_handoff_matches_direct_and_releases_files(tmp_path):
    from tools.initialization_data import FactorBlocks
    from tools.streaming_factors import section_blocks
    from tools.initialization_pipeline import _isolated
    from test_section_resume_state import candles
    data=pl.from_pandas(candles(20).assign(close=1.,next_close=1.)
                       .sort_values(['symbol','candle_begin_time']).reset_index(drop=True))
    param=(20,'VolumeMaxBias',17);column=f'TopKTo2K_{param}'
    conf=SimpleNamespace(section_state=False,section_params_dict={'TopKTo2K':[param]},factor_col_name_list=[column])
    stores=[]
    for label in ('direct','isolated'):
        store=FactorBlocks(tmp_path/label,hours=19,minute=20)
        store.append(data,0);stores.append(store)
    section_blocks(conf,stores[0])
    stores[1].sections,stores[1].section_columns=_isolated(section_blocks,conf,stores[1])
    assert stores[0].read_history([column]).equals(stores[1].read_history([column]))
    for store in stores:store.close()
    assert not (tmp_path/'isolated').exists()


def test_failed_section_worker_does_not_publish_parent_metadata(tmp_path):
    from tools.initialization_data import FactorBlocks
    from tools.streaming_factors import section_blocks
    from tools.initialization_pipeline import _isolated
    from test_section_resume_state import candles
    store=FactorBlocks(tmp_path/'failed',hours=19,minute=20)
    store.append(pl.from_pandas(candles(20).assign(close=1.,next_close=1.)),0)
    param=(20,'VolumeMaxBias',999);column=f'TopKTo2K_{param}'
    conf=SimpleNamespace(section_state=False,section_params_dict={'TopKTo2K':[param]},factor_col_name_list=[column])
    with pytest.raises(KeyError):
        store.sections,store.section_columns=_isolated(section_blocks,conf,store)
    assert store.sections=={}
    store.close()


def test_swap_mapping_owns_buffers_after_factor_store_is_deleted(tmp_path):
    import gc
    from tools.initialization_data import FactorBlocks
    from tools.initialization_pipeline import _swap_mapping
    frame=pl.DataFrame(dict(candle_begin_time=pd.date_range('2026-01-01 00:20',periods=4096,freq='h'),
        symbol=['A']*4096,is_spot=[0]*4096,symbol_spot=['A']*4096,
        close=np.arange(4096,dtype=float),next_close=np.arange(4096,dtype=float)+1))
    store=FactorBlocks(tmp_path/'blocks',hours=512,minute=20)
    store.append(frame,0)
    was_enabled=gc.isenabled()
    gc.disable()  # Closing files must not depend on the next automatic GC cycle.
    try:
        actual=_swap_mapping(store)
        store.close()
    finally:
        if was_enabled:gc.enable()
        gc.collect()
    pd.testing.assert_frame_equal(actual,frame.to_pandas())


@pytest.mark.parametrize('minute',[5,15,20,25,40,45,55])
def test_memory_simulation_and_two_file_continuations(tmp_path,monkeypatch,minute):
    from core import equity
    from core.model.rebalance_mode import RebalanceMode
    index=pd.date_range(f'2026-01-01 00:{minute:02}',periods=80,freq='h',tz='UTC')
    ratios=tuple(pd.DataFrame({'A':np.where(np.arange(80)%9<5,.3,0.)},index=index.tz_localize(None)) for _ in range(2))
    market=dict(pivots={kind:{field:pd.DataFrame({'A':(np.arange(80)/100+10 if field!='funding_rate' else np.full(80,.0001))},index=index)
                            for field in ['open','close','vwap1m','funding_rate']} for kind in ['spot','swap']},
                units={k:pd.DataFrame({'symbol':['A'],'lot_size':[.01]}) for k in ['spot','swap']})
    monkeypatch.setattr(equity,'read_bmac_market_pivot',lambda offset,kind:dict(market['pivots'][kind]))
    monkeypatch.setattr(equity,'read_bmac_exginfo',lambda kind:market['units'][kind])
    def conf(label):
        folder=tmp_path/label;folder.mkdir()
        return SimpleNamespace(get_result_folder=lambda:folder,get_snapshot_folder=lambda:folder,
            incremental_lookback_hours=0,checkpoint_hours=25,initial_usdt=10000.,is_day_period=False,
            leverage=1.,margin_rate=.05,spot_c_rate=.002,swap_c_rate=.0006,spot_min_order_limit=20.,
            swap_min_order_limit=20.,rebalance_mode=RebalanceMode.init(None))
    def disk(c,stop):
        for kind,ratio in zip(['spot','swap'],ratios):ratio.iloc[:stop].to_pickle(c.get_result_folder()/f'df_{kind}_ratio.pkl')
        return equity.calc_equity(c,index[stop-1]+pd.Timedelta(hours=1),state_name='state0')
    original=conf('old');candidate=conf('new')
    expected=disk(original,78)
    output=[]
    actual=equity.calc_equity(candidate,index[78],state_name='state0',ratio_inputs=tuple(r.iloc[:78] for r in ratios),
                              market_inputs=market,ratio_outputs=output)
    pd.testing.assert_frame_equal(actual,expected)
    assert len(output)==2
    for stop in (79,80):pd.testing.assert_frame_equal(disk(candidate,stop),disk(original,stop))
    for kind in ['spot','swap']:
        pd.testing.assert_frame_equal(pd.read_pickle(candidate.get_snapshot_folder()/f'ratio_{kind}.pkl'),
                                      pd.read_pickle(original.get_snapshot_folder()/f'ratio_{kind}.pkl'))
