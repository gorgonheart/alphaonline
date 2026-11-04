"""Initialization storage contracts; independent row/time oracle, no network."""
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest


@pytest.mark.parametrize('minute',range(0,60,5))
@pytest.mark.parametrize('backend',['memory','ipc','lz4','parquet'])
def test_blocks_keep_full_cross_section_and_stable_rows(tmp_path,minute,backend):
    from tools.initialization_data import FactorBlocks
    source=pl.DataFrame(dict(candle_begin_time=pd.date_range(f'2026-01-01 00:{minute:02}',periods=7,freq='h'),
        symbol=['A']*7,score=[np.nan,2.,2.,1.,None,3.,4.])).with_columns(pl.col('symbol').cast(pl.Categorical))
    other=source.with_columns(pl.lit('B').cast(pl.Categorical).alias('symbol'))
    store=FactorBlocks(tmp_path/'blocks',hours=3,minute=minute,backend=backend)
    store.append(source,shard=0)
    store.append(other,shard=1)
    expected=pl.concat([source,other]).with_row_index('_row').sort(['candle_begin_time','_row'])
    got=pl.concat(list(store.iter_blocks(['candle_begin_time','symbol','score','_row'])))
    assert got.equals(expected.select(got.columns))
    assert all(set(b['symbol'].cast(pl.String))=={'A','B'} for b in store.iter_blocks(['symbol']))
    store.close()
    assert not (tmp_path/'blocks').exists()


def test_projected_section_output_aligns_original_rows(tmp_path):
    from tools.initialization_data import FactorBlocks
    store=FactorBlocks(tmp_path/'blocks',hours=2,minute=20,backend='ipc')
    frame=pl.DataFrame(dict(candle_begin_time=pd.date_range('2026-01-01 00:20',periods=5,freq='h'),
        symbol=['A']*5,value=[1.,2.,3.,4.,5.]))
    store.append(frame,0);store.append(frame.with_columns(pl.lit('B').alias('symbol')),1)
    original=store.read_history(['_row','value'])
    store.add_sections(original.select('_row',(pl.col('value')*2).alias('section')))
    got=pl.concat(list(store.iter_blocks(['_row','value','section'])))
    assert got['section'].equals(got['value']*2)
    assert 'value' not in store.read_history(['section']).columns
    store.close()


def test_projection_preserves_full_model_and_revision_windows(tmp_path):
    from tools.initialization_data import read_market
    import json
    frame=pd.DataFrame(dict(candle_begin_time=pd.date_range('2025-01-01',periods=200,freq='h',tz='UTC'),
        first_candle_time=pd.Timestamp('2024-01-01',tz='UTC'),close=np.arange(200,dtype=float),unused=1))
    batch=pa.RecordBatch.from_pandas(frame,preserve_index=False)
    schema=batch.schema.with_metadata({b'symbols':json.dumps(['A']).encode(),b'full_history':b'1'})
    path=tmp_path/'market.arrow'
    with pa.ipc.new_file(str(path),schema) as out:out.write_batch(batch)
    full=dict(read_market(path,columns=['candle_begin_time','close'],require_full=True))['A']
    pd.testing.assert_frame_equal(full,frame[['candle_begin_time','close']])
    tail=dict(read_market(path,columns=['candle_begin_time','close'],start=frame.candle_begin_time.iloc[-8],
                          end=frame.candle_begin_time.iloc[-2]))['A']
    pd.testing.assert_frame_equal(tail,frame[['candle_begin_time','close']].iloc[-8:-1].reset_index(drop=True))


def test_duplicate_shard_is_not_silently_overwritten(tmp_path):
    from tools.initialization_data import FactorBlocks
    store=FactorBlocks(tmp_path/'blocks',hours=2,minute=0,backend='memory')
    frame=pl.DataFrame(dict(candle_begin_time=[pd.Timestamp('2026-01-01')],x=[1]))
    store.append(frame,0)
    with pytest.raises(ValueError,match='shard'):store.append(frame,0)
    store.close()


def test_admission_happens_before_block_allocation(tmp_path):
    from tools.initialization_data import FactorBlocks
    store=FactorBlocks(tmp_path/'blocks',hours=2,minute=0,backend='memory')
    store.append(pl.DataFrame({'candle_begin_time':pd.date_range('2026-01-01',periods=4,freq='h'),'value':[1,2,3,4]}),0)
    events=[];read=store._read
    def tracked(*args):
        assert events[-1]=='admit'
        events.append('read');return read(*args)
    store._read=tracked
    for block in store.iter_blocks(['value'],sort_time=False,before_read=lambda:events.append('admit')):del block
    assert events==['admit','read','admit','read']
    store.close()
