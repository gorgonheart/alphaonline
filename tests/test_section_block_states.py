"""Independent numerical contracts for time-block state handoff."""
import importlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl
import pytest


def test_topk_blocks_match_full_with_missing_hours():
    from sections import TopKTo2K as section
    from test_section_resume_state import candles
    data=candles().iloc[:900].copy()
    data=data.drop(data.index[::13]).sort_values(['symbol','candle_begin_time'])
    data.loc[data.index[::17],'VolumeMaxBias_17']=np.nan
    params=(9,'VolumeMaxBias',17)
    rank=data.groupby('candle_begin_time').VolumeMaxBias_17.rank(method='min',ascending=False)
    size=data.groupby('candle_begin_time').symbol.transform('size')
    top=(rank<=np.maximum(1,np.ceil(size*.1))).astype(int)
    expected=(top.groupby(data.symbol).transform(lambda s:s.rolling(9,min_periods=1).max()).gt(0)
              & rank.ge(np.maximum(1,np.ceil(size*.21)))).astype(float)
    # Polars preserves null output at missing ranks when historical top is true.
    rolling=top.groupby(data.symbol).transform(lambda s:s.rolling(9,min_periods=1).max())
    expected.loc[rank.isna() & rolling.gt(0)]=np.nan
    frame=pl.from_pandas(data).with_row_index('_row')
    state=None;outputs=[]
    for part in frame.with_columns((pl.col('candle_begin_time').dt.epoch('s')//3600//7).alias('block')).partition_by('block',maintain_order=True):
        result,state=section.signal_block(part.drop('block'),params,'chosen',state)
        outputs.append(result.select('_row','chosen'))
    actual=pl.concat(outputs).sort('_row')['chosen'].to_numpy()
    np.testing.assert_array_equal(actual,expected.to_numpy())


def test_s3_blocks_preserve_25_common_points():
    from test_section_resume_state import candles,oracle
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    data=candles();params=(17,.25,.6,'2025-01-02 00:20')
    expected=oracle(data,params)
    state=None;outputs=[];end=data.candle_begin_time.max()
    frame=pl.from_pandas(data).with_row_index('_row')
    for part in frame.with_columns((pl.col('candle_begin_time').dt.epoch('s')//3600//19).alias('block')).partition_by('block',maintain_order=True):
        result,state=section.signal_block(part.drop('block'),params,'chosen',state,
            checkpoint_start=end-pd.Timedelta(hours=24),checkpoint_hours=25)
        outputs.append(result.select('_row','chosen'))
    np.testing.assert_array_equal(pl.concat(outputs).sort('_row')['chosen'].to_numpy(),expected.to_numpy())
    assert len(state['checkpoints'])==25
    assert state['cutoff']==end


def test_failed_block_does_not_install_section_state(tmp_path):
    from core.utils.factor_data import FactorBlocks
    from core.utils.section_state import run_section_blocks
    frame=pl.DataFrame({'candle_begin_time':pd.date_range('2026-01-01',periods=4,freq='h'),
                        'symbol':['A']*4,'f_1':[1.,2.,3.,4.]})
    store=FactorBlocks(tmp_path/'blocks',hours=2,minute=0);store.append(frame,0)
    folder=tmp_path/'snapshot';folder.mkdir()
    def callback(frame,params,column,state=None,**kwargs):
        if state is not None:raise RuntimeError('block failed')
        return frame.with_columns(pl.lit(1).alias(column)),{'cutoff':frame['candle_begin_time'].max()}
    factor=SimpleNamespace(signal_block=callback,signal_with_state=lambda:None)
    conf=SimpleNamespace(section_state=True,checkpoint_hours=25,incremental_lookback_hours=0,
                          get_snapshot_folder=lambda:folder)
    with pytest.raises(RuntimeError,match='block failed'):
        run_section_blocks(conf,factor,store,['symbol','f_1'],1,'chosen')
    assert not list(folder.rglob('*.pkl'))
    assert store.section_columns==[]
    store.close()


def test_restored_s3_matches_corrected_full_history():
    from test_section_resume_state import candles,oracle
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    data=candles();params=(17,.25,.6,'2025-01-02 00:20')
    cutoff=data.candle_begin_time.min()+pd.Timedelta(hours=130)
    _,state=section.signal_with_checkpoints(data[data.candle_begin_time<=cutoff].copy(),params,'chosen',hours=25)
    data.loc[data.candle_begin_time>cutoff,'VolumeMaxBias_17']*=data.loc[data.candle_begin_time>cutoff].symbol.str[1:].astype(int)+1
    expected=oracle(data,params)
    frame=pl.from_pandas(data).with_row_index('_row');outputs=[]
    for part in frame.with_columns((pl.col('candle_begin_time').dt.epoch('s')//3600//19).alias('block')).partition_by('block',maintain_order=True):
        result,state=section.signal_block(part.drop('block'),params,'chosen',state,
            checkpoint_start=data.candle_begin_time.max()-pd.Timedelta(hours=24),checkpoint_hours=25)
        outputs.append(result.select('_row','chosen'))
    np.testing.assert_array_equal(pl.concat(outputs).sort('_row')['chosen'].to_numpy(),expected.to_numpy())
    assert len(state['checkpoints'])==25


def test_s3_snapshot_output_preserves_original_rows_and_sparse_checkpoint_times(tmp_path):
    from core.utils.factor_data import FactorBlocks
    from core.utils.section_state import run_section_blocks
    from test_section_resume_state import candles
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    data=candles().sort_values(['symbol','candle_begin_time']).reset_index(drop=True)
    # Whole-market gaps: checkpoint_hours is a count of observed boundaries.
    times=data.candle_begin_time.drop_duplicates().sort_values()
    data=data[~data.candle_begin_time.isin(times.iloc[-24::3])].reset_index(drop=True)
    params=(17,.25,.6,'2025-01-02 00:20')
    _,expected=section.signal_with_checkpoints(data.copy(),params,'chosen',hours=25)
    store=FactorBlocks(tmp_path/'blocks',hours=19,minute=20)
    store.append(pl.from_pandas(data),0)
    folder=tmp_path/'snapshot'
    conf=SimpleNamespace(section_state=True,checkpoint_hours=25,incremental_lookback_hours=0,
                         get_snapshot_folder=lambda:folder)
    actual=run_section_blocks(conf,section,store,section.get_state_input_columns(params),params,'chosen')
    assert list(actual['checkpoints'])==list(expected['checkpoints'])
    pd.testing.assert_frame_equal(actual['output'],expected['output'])
    store.close()


def test_generic_block_state_does_not_require_s3_output_schema(tmp_path):
    from core.utils.factor_data import FactorBlocks
    from core.utils.section_state import run_section_blocks
    frame=pl.DataFrame({'candle_begin_time':[pd.Timestamp('2026-01-01')],'symbol':['A']})
    store=FactorBlocks(tmp_path/'blocks',hours=24,minute=0);store.append(frame,0)
    def calculate(frame,params,column,state=None,**options):
        return frame.with_columns(pl.lit(1).alias(column)),{'counter':1}
    factor=SimpleNamespace(signal_block=calculate,signal_with_state=lambda:None)
    folder=tmp_path/'snapshot'
    conf=SimpleNamespace(section_state=True,checkpoint_hours=25,incremental_lookback_hours=0,
                         get_snapshot_folder=lambda:folder)
    result=run_section_blocks(conf,factor,store,['symbol'],1,'chosen')
    assert result==pd.read_pickle(folder/'sections/chosen.pkl')=={'counter':1}
    store.close()

def test_late_block_failure_preserves_existing_section_state(tmp_path):
    from core.utils.factor_data import FactorBlocks
    from core.utils.section_state import run_section_blocks
    frame=pl.DataFrame({'candle_begin_time':pd.date_range('2026-01-01',periods=4,freq='h'),'symbol':['A']*4})
    store=FactorBlocks(tmp_path/'blocks',hours=2,minute=0);store.append(frame,0)
    folder=tmp_path/'snapshot';(folder/'sections').mkdir(parents=True)
    saved=folder/'sections/chosen.pkl';pd.to_pickle({'counter':9},saved)
    before=saved.read_bytes();calls=[]
    def calculate(frame,params,column,state=None,**options):
        calls.append(state['counter'])
        if len(calls)==2:raise RuntimeError('late block failure')
        return frame.with_columns(pl.lit(1).alias(column)),{'counter':state['counter']+1}
    factor=SimpleNamespace(signal_block=calculate,signal_with_state=lambda:None)
    conf=SimpleNamespace(section_state=True,checkpoint_hours=0,incremental_lookback_hours=0,get_snapshot_folder=lambda:folder)
    with pytest.raises(RuntimeError,match='late block failure'):
        run_section_blocks(conf,factor,store,['symbol'],1,'chosen')
    assert calls==[9,10] and saved.read_bytes()==before
    assert store.section_columns==[]
    store.close()
