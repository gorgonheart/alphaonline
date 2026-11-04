"""Independent ordering/null oracle and one-read-per-block pool contract."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import polars as pl
import pytest
from test_streaming_selection import inputs, strategy
from core import select_coin as select


@pytest.mark.parametrize('workers',[1,2])
def test_column_cache_preserves_row_alignment_nulls_and_nan(tmp_path,workers):
    from tools.streaming_selection import prepare_columns, read_blocks
    source=inputs(tmp_path/'cache')
    extra=pl.Series('extra',[None if i%5==0 else float('nan') if i%7==0 else float(i) for i in range(len(source))])
    source=source.with_columns(extra)
    extra.to_frame().write_parquet(tmp_path/'cache/factor_extra.parquet',row_group_size=13)
    paths=prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6,workers=workers)
    frames=list(read_blocks(paths,source.columns))
    actual=pl.concat(frames).sort(['symbol','candle_begin_time'])
    assert actual.equals(source.sort(['symbol','candle_begin_time']))
    seen=set()
    for frame in frames:
        times=set(frame['candle_begin_time']);assert not seen.intersection(times);seen.update(times)


@pytest.mark.parametrize('workers',[1,2])
def test_pool_reads_each_block_once_for_multiple_combinations(tmp_path,monkeypatch,workers):
    import config
    import tools.streaming_selection as streaming
    source=inputs(tmp_path/'cache')
    monkeypatch.setattr(select,'get_file_path',lambda *p,**kw:tmp_path/'cache'/p[-1])
    monkeypatch.setattr(config,'selection_block_hours',6,raising=False)
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    monkeypatch.setattr(config,'select_coin_inner_threads',workers,raising=False)
    monkeypatch.setattr(config,'job_num',workers,raising=False)
    configs=[];expected=[]
    for i in range(2):
        folder=tmp_path/str(i);folder.mkdir()
        s=strategy();s.cap_weight=.8+i*.1
        c=SimpleNamespace(strategy_list=[s],name=str(i),unified_time='2017-01-01',get_result_folder=lambda f=folder:f)
        select._GLOBAL_FACTOR_LF=source.lazy()
        select.process_strategy_thread(s,folder,unified_time=c.unified_time)
        expected.append(pd.read_pickle(folder/'oracle.pkl'));configs.append(c)
    import threading
    threads=set();original_strategy=select.process_strategy_thread
    def observed(*args,**kwargs):
        threads.add(threading.get_ident())
        return original_strategy(*args,**kwargs)
    monkeypatch.setattr(select,'process_strategy_thread',observed)
    reads=[];original=streaming.read_blocks
    def read(*args,**kwargs):
        for frame in original(*args,**kwargs):reads.append(frame.height);yield frame
    monkeypatch.setattr(streaming,'read_blocks',read)
    streaming.run_pool(configs)
    assert not (tmp_path/'cache/selection_columns_6h').exists()
    assert (tmp_path/'cache/all_factors_kline.parquet').exists()
    assert len(threads)==workers
    assert sum(reads)==len(source)  # Not multiplied by number of combinations.
    for c,e in zip(configs,expected):
        actual=pd.read_pickle(c.get_result_folder()/'oracle.pkl')
        keys=['candle_begin_time','symbol']
        pd.testing.assert_frame_equal(actual.sort_values(keys).reset_index(drop=True),
            e.sort_values(keys).reset_index(drop=True),check_exact=True,check_categorical=False)


def test_column_cache_rejects_misaligned_source(tmp_path):
    from tools.streaming_selection import prepare_columns
    inputs(tmp_path/'cache')
    pl.DataFrame({'f':[1.]}).write_parquet(tmp_path/'cache/factor_f.parquet')
    with pytest.raises(ValueError,match='row'):
        prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6)
    assert not (tmp_path/'sorted').exists()


def test_pool_keeps_cache_when_result_write_fails(tmp_path,monkeypatch):
    import config
    import tools.streaming_selection as streaming
    inputs(tmp_path/'cache')
    folder=tmp_path/'results';folder.mkdir()
    conf=SimpleNamespace(strategy_list=[strategy()],unified_time='2017-01-01',get_result_folder=lambda:folder)
    monkeypatch.setattr(select,'get_file_path',lambda *p,**kw:tmp_path/'cache'/p[-1])
    monkeypatch.setattr(config,'selection_block_hours',6,raising=False)
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    monkeypatch.setattr(config,'job_num',1,raising=False)
    monkeypatch.setattr(config,'select_coin_inner_threads',1,raising=False)
    def fail(*args,**kwargs):raise OSError('injected disk full')
    monkeypatch.setattr(pd.DataFrame,'to_pickle',fail)
    with pytest.raises(OSError,match='disk full'):
        streaming.run_pool([conf])
    assert (tmp_path/'cache/selection_columns_6h/kline.ipc').exists()
    assert (tmp_path/'cache/all_factors_kline.parquet').exists()


def test_column_cache_accepts_categorical_kline_across_rowgroups(tmp_path):
    from tools.streaming_selection import prepare_columns, read_blocks
    source=inputs(tmp_path/'cache').with_columns(pl.col('symbol').cast(pl.Categorical))
    source.select(select.KLINE_COLS).write_parquet(tmp_path/'cache/all_factors_kline.parquet',row_group_size=7)
    folder=prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6)
    actual=pl.concat(list(read_blocks(folder,source.columns)))
    assert actual.sort(['symbol','candle_begin_time']).equals(source.sort(['symbol','candle_begin_time']))


def test_parallel_conversion_failure_never_publishes_cache(tmp_path,monkeypatch):
    import tools.streaming_selection as streaming
    inputs(tmp_path/'cache')
    original=streaming.pq.read_table
    def failed(path,**kwargs):
        if path.name=='factor_f.parquet':raise OSError('injected factor read failure')
        return original(path,**kwargs)
    monkeypatch.setattr(streaming.pq,'read_table',failed)
    with pytest.raises(OSError,match='injected'):
        streaming.prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6,workers=2)
    assert not (tmp_path/'sorted').exists()
    assert not (tmp_path/'sorted.building').exists()


def test_conversion_admission_precedes_full_kline_read(tmp_path,monkeypatch):
    import tools.streaming_selection as streaming
    inputs(tmp_path/'cache');events=[]
    original=streaming.pq.read_table
    def read(*args,**kwargs):events.append('read');return original(*args,**kwargs)
    monkeypatch.setattr(streaming.pq,'read_table',read)
    streaming.prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6,gate=lambda:events.append('gate'))
    assert events[0]=='gate'


def test_previous_block_released_before_pressure_wait(tmp_path):
    import weakref
    from tools.streaming_selection import prepare_columns,read_blocks
    source=inputs(tmp_path/'cache')
    folder=prepare_columns(tmp_path/'cache',tmp_path/'sorted',hours=6)
    previous=[]
    def gate():
        if previous:assert previous[-1]() is None
    blocks=read_blocks(folder,source.columns,gate=gate)
    first=next(blocks);previous.append(weakref.ref(first));del first
    second=next(blocks);del second
    blocks.close()
