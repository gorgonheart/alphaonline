"""Ownership and real-selector parity for the common block consumer."""
from types import SimpleNamespace
import weakref

import polars as pl


def test_wide_block_is_not_sorted_for_each_dependency_group(tmp_path,monkeypatch):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    data=inputs(tmp_path/'source')
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=55)
    store.append(data,0)
    strategies=[strategy() for _ in range(5)]
    conf=SimpleNamespace(strategy_list=strategies,get_result_folder=lambda:tmp_path,unified_time='2017-01-01')
    orders=[];original=store.row_order
    def order(key,*args,**kwargs):
        orders.append(key);return original(key,*args,**kwargs)
    monkeypatch.setattr(store,'row_order',order)
    expected=select.process_strategy_thread(strategies[0],tmp_path,factor_source=data.lazy(),return_result=True)
    paths=select.select_factor_blocks([conf],store,tmp_path/'selected',workers=2)
    assert orders==list(store.block_keys())
    for parts in paths[0].values():
        assert all(hasattr(path,'read_bytes') for path in parts)
        assert pl.concat([pl.read_ipc(p,memory_map=False) for p in parts]).equals(expected)
    store.close()


def test_completed_group_does_not_retain_factor_buffers(tmp_path,monkeypatch):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    store=FactorBlocks(tmp_path/'blocks',hours=100,minute=55)
    store.append(inputs(tmp_path/'source'),0)
    conf=SimpleNamespace(strategy_list=[strategy() for _ in range(35)],get_result_folder=lambda:tmp_path,
                          unified_time='2017-01-01')
    refs=[];original=store.read_block
    def read(key,columns,**options):
        if 'f' in columns:
            assert all(ref() is None for ref in refs)
        frame=original(key,columns,**options)
        if 'f' in columns:refs.append(weakref.ref(frame))
        return frame
    monkeypatch.setattr(store,'read_block',read)
    paths=select.select_factor_blocks([conf],store,tmp_path/'selected',workers=2)
    assert paths and refs and all(ref() is None for ref in refs)
    store.close()


def test_continuation_crops_narrow_order_before_wide_reads(tmp_path,monkeypatch):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    data=inputs(tmp_path/'source')
    start=data['candle_begin_time'].max()
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=55);store.append(data,0)
    conf=SimpleNamespace(strategy_list=[strategy()],get_result_folder=lambda:tmp_path,unified_time='2017-01-01')
    original=store.read_block;seen=[]
    def read(key,columns,**options):
        if 'f' in columns:
            seen.append(key)
            assert key==store.block_keys()[-1]
            assert len(options['order'])==data.filter(pl.col('candle_begin_time')>=start).height
        return original(key,columns,**options)
    monkeypatch.setattr(store,'read_block',read)
    select.select_factor_blocks([conf],store,tmp_path/'selected',workers=2,start=start)
    assert seen==[store.block_keys()[-1]]
    store.close()

import datetime
import pytest


@pytest.mark.parametrize('minute',range(0,60,5))
@pytest.mark.parametrize('variant',['daily','ratio','linked_short','mix','empty'])
def test_all_offsets_keep_original_selection_semantics(tmp_path,minute,variant):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    data=inputs(tmp_path/'source').with_columns(
        (pl.col('candle_begin_time')+datetime.timedelta(minutes=minute-55)).alias('candle_begin_time'))
    stg=strategy()
    if variant=='daily':stg.is_day_period=True
    if variant=='ratio':stg.long_select_coin_num=.5
    if variant=='linked_short':
        stg.short_cap_weight=.4;stg.short_select_coin_num='long_nums';stg.short_select_coin_num_min=None
    if variant=='mix':
        data=data.with_columns(pl.col('symbol').alias('symbol_spot'))
        data=pl.concat([data,data.with_columns(pl.lit(1,dtype=data.schema['is_spot']).alias('is_spot'))])
        stg.select_scope='mix';stg.order_first='spot'
    if variant=='empty':data=data.with_columns(pl.lit(None,dtype=pl.Float64).alias('f'))
    else:
        data=data.with_row_index('_id').with_columns(
            pl.when(pl.col('_id')%11==0).then(None)
            .when(pl.col('_id')%13==0).then(float('nan')).otherwise(pl.col('f')).alias('f')).drop('_id')
    conf=SimpleNamespace(strategy_list=[stg],get_result_folder=lambda:tmp_path,unified_time='2017-01-01')
    expected=select.process_strategy_thread(stg,tmp_path,factor_source=data.lazy(),return_result=True)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=minute);store.append(data,0)
    paths=select.select_factor_blocks([conf],store,tmp_path/'selected',workers=1)[0][0]
    if expected is None:assert paths==[]
    else:assert pl.concat([pl.read_ipc(p,memory_map=False) for p in paths]).equals(expected)
    store.close()

def test_unmapped_same_symbol_markets_keep_original_tie_rows(tmp_path):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs,strategy
    data=inputs(tmp_path/'source',periods=97).with_columns(pl.lit('').alias('symbol_spot'),pl.lit('').alias('symbol_swap'))
    spot=data.with_columns(pl.lit(1,dtype=data.schema['is_spot']).alias('is_spot'))
    data=pl.concat([data,spot])
    stg=strategy();stg.select_scope='mix';stg.order_first='swap'
    conf=SimpleNamespace(strategy_list=[stg],get_result_folder=lambda:tmp_path,unified_time='2017-01-01')
    expected=select.process_strategy_thread(stg,tmp_path,factor_source=data.lazy(),return_result=True)
    store=FactorBlocks(tmp_path/'blocks',hours=24,minute=55)
    store.append(data,0)
    paths=select.select_factor_blocks([conf],store,tmp_path/'selected',workers=1)[0][0]
    assert pl.concat([pl.read_ipc(p,memory_map=False) for p in paths]).equals(expected)
    store.close()


def test_mixed_history_reorders_only_narrow_keys(tmp_path, monkeypatch):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    from test_streaming_selection import inputs, strategy
    data=inputs(tmp_path/'source',periods=97).with_columns(
        pl.lit('').alias('symbol_spot'),pl.lit('').alias('symbol_swap'))
    data=pl.concat([data,data.with_columns(pl.lit(1,dtype=data.schema['is_spot']).alias('is_spot'))])
    data=data.with_row_index('_id').with_columns(
        pl.when(pl.col('_id')%11==0).then(None).otherwise(pl.col('f')).alias('f'),
        pl.when(pl.col('_id')%7==0).then(0).otherwise(pl.col('是否交易')).alias('是否交易')).drop('_id')
    stg=strategy();stg.select_scope='mix';stg.order_first='swap'
    start=data['candle_begin_time'].min()+datetime.timedelta(hours=5)
    conf=SimpleNamespace(strategy_list=[stg],get_result_folder=lambda:tmp_path,unified_time='2017-01-01')
    expected=select.process_strategy_thread(stg,tmp_path,
        factor_source=data.filter(pl.col('candle_begin_time')>=start).lazy(),return_result=True)
    store=FactorBlocks(tmp_path/'blocks',hours=24,minute=55);store.append(data,0)
    def forbidden(*args,**kwargs):
        raise AssertionError('Mixed selection must not sort a full wide history')
    monkeypatch.setattr(store,'read_history',forbidden)
    sort=pl.DataFrame.sort
    def narrow_sort(frame,*args,**kwargs):
        assert frame.width<=4,frame.columns
        return sort(frame,*args,**kwargs)
    monkeypatch.setattr(pl.DataFrame,'sort',narrow_sort)
    paths=select.select_factor_blocks([conf],store,tmp_path/'selected',workers=1,start=start)[0][0]
    assert pl.concat([pl.read_ipc(p,memory_map=False) for p in paths]).equals(expected)
    store.close()
