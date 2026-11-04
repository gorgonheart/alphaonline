"""Shared complete-market blocks for standard cross-sectional selection.

select_blocks consumes the new unified layout without another sorted cache.
run_pool retains the earlier Parquet-to-IPC entry for initialization fallback.
Each complete time block is shared by the whole pool; never rank separate
symbol shards. The original core function supplies all mathematics and offsets.
"""
from contextlib import ExitStack, nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import gc
import os
import shutil

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def prepare_columns(cache, destination, *, hours, factor_columns=None, gate=None, workers=1):
    """Sort row-aligned columns once; LZ4 IPC batches share complete time ranges.

    Only the bounded in-flight factor columns are materialized. The stable Kline
    permutation is reused for every column, including null and NaN positions.
    This is disposable initialization cache, not upstream Arrow or snapshot data.
    """
    cache, destination = Path(cache), Path(destination)
    if hours <= 0:
        raise ValueError('Positive time block size required')
    if destination.exists():
        return destination
    sources = ([cache/f'factor_{name}.parquet' for name in factor_columns]
               if factor_columns is not None else sorted(
                   p for p in cache.glob('factor_*.parquet') if not p.name.startswith('factor_full_')))
    kline_path = cache/'all_factors_kline.parquet'
    count = pq.read_metadata(kline_path).num_rows
    if not count or any(pq.read_metadata(p).num_rows != count for p in sources):
        raise ValueError('Selection factor row count differs from common Kline rows')
    if gate:
        gate()
    temporary = destination.with_name(destination.name+'.building')
    temporary.mkdir(exist_ok=False)
    try:
        kline = pq.read_table(kline_path, use_threads=False).combine_chunks()
        times = kline['candle_begin_time']
        if times.null_count:
            raise ValueError('Missing selection timestamp')
        nanos = pc.cast(pc.cast(times, pa.timestamp('ns')), pa.int64()).to_numpy()
        order = np.argsort(nanos, kind='stable')
        keys = nanos[order]//(hours*3600*10**9)
        bounds = np.r_[0, np.flatnonzero(keys[1:] != keys[:-1])+1, count]
        del keys, nanos, times
        order = pa.array(order)
        options = pa.ipc.IpcWriteOptions(compression='lz4', use_threads=False)

        def write(table, path):
            table = table.take(order).combine_chunks()
            with pa.OSFile(str(path), 'wb') as sink, pa.ipc.new_file(sink, table.schema, options=options) as writer:
                for start, end in zip(bounds[:-1], bounds[1:]):
                    writer.write_batch(table.slice(int(start), int(end-start)).to_batches()[0])

        write(kline, temporary/'kline.ipc')
        del kline
        def convert(path):
            table = pq.read_table(path, use_threads=False)
            if len(table.column_names) != 1:
                raise ValueError('Selection factors must be single-column files')
            write(table, temporary/path.with_suffix('.ipc').name)
            del table
        if workers>1:
            from tools.bounded_pool import iter_results
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for _ in iter_results(executor,convert,((p,) for p in sources),workers,before_submit=gate):
                    pass
        else:
            for path in sources:
                if gate:
                    gate()
                convert(path)
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return destination


def read_blocks(directory, columns, workers=1, gate=None):
    """Read each requested column batch once, preserving common row alignment."""
    with ExitStack() as stack:
        def open_reader(path):
            return pa.ipc.open_file(stack.enter_context(pa.memory_map(str(path), 'r')))
        base = open_reader(Path(directory)/'kline.ipc')
        others = [open_reader(Path(directory)/f'factor_{name}.ipc')
                  for name in columns if name not in base.schema.names]
        if any(r.num_record_batches != base.num_record_batches for r in others):
            raise ValueError('Selection column batch count differs')
        readers = stack.enter_context(ThreadPoolExecutor(max_workers=min(workers,len(others)))) if workers>1 and others else None
        for i in range(base.num_record_batches):
            if gate:
                gate()
            with _phase(f'selection.load_pool:{i}'):
                table = pa.Table.from_batches([base.get_batch(i)])
                batches = readers.map(lambda reader:reader.get_batch(i),others) if readers else (r.get_batch(i) for r in others)
                for batch in batches:
                    if batch.num_rows != len(table):
                        raise ValueError('Selection column batch rows differ')
                    table = table.append_column(batch.schema.field(0), batch.column(0))
                frame = pl.from_arrow(table.select(columns), rechunk=False)
            yield frame
            del frame, table  # Release the old block before allocating the next.


def run_pool(configs, multi_process=False, silent=False):
    """Time block outside / all strategies inside: no repeated pool-wide reads."""
    import config
    from core import select_coin as select
    gate = getattr(config, 'compute_gate', None)
    supported = []
    for conf in configs:
        if any(s.use_custom_func or s.select_mode == 'entry_exit' for s in conf.strategy_list):
            if gate:
                gate()
            select.select_coin_with_conf(conf, multi_process=multi_process, silent=silent)
        else:
            supported.append(conf)
    if not supported:
        return
    columns = list(dict.fromkeys(c for conf in supported for s in conf.strategy_list for c in s.factor_columns))
    cache = Path(select.get_file_path('data','cache','all_factors_kline.parquet',as_path_type=True)).parent
    hours = config.selection_block_hours
    read_workers = max(1,getattr(config,'job_num',1))
    with _phase('selection.column_conversion'):
        directory = prepare_columns(cache,cache/f'selection_columns_{hours}h',hours=hours,
                                    factor_columns=columns,gate=gate,workers=read_workers)
    jobs = [(conf, s, []) for conf in supported for s in conf.strategy_list]
    # Reuse existing MODEL limits; native Polars/BLAS threads remain one.
    workers = max(1,min(getattr(config,'select_coin_inner_threads',1),len(jobs)))
    select._GLOBAL_FACTOR_LF = None
    try:
        with ExitStack() as stack, pl.StringCache():
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=workers)) if workers>1 else None
            blocks = read_blocks(directory, list(dict.fromkeys([*select.KLINE_COLS, *columns])),workers=read_workers,gate=gate)
            number = 0
            while True:
                try:
                    data = next(blocks)
                except StopIteration:
                    break
                def calculate(index):
                    conf,strategy,_ = jobs[index]
                    return index,select.process_strategy_thread(strategy,conf.get_result_folder(),silent,conf.unified_time,
                                                                factor_source=data.lazy(),return_result=True)
                with _phase(f'selection.compute_pool:{number}'):
                    results = executor.map(calculate,range(len(jobs))) if executor else map(calculate,range(len(jobs)))
                    for index,result in results:
                        if result is not None and len(result):
                            jobs[index][2].append(result)
                        del result
                del data
                gc.collect()
                number += 1
            for conf, strategy, chunks in jobs:
                result = pl.concat(chunks).to_pandas() if chunks else pd.DataFrame(columns=select.SELECT_RES_COLS)
                target = conf.get_result_folder()/f'{strategy.get_fullname(as_folder_name=True)}.pkl'
                temporary = target.with_suffix('.pkl.tmp')
                result.to_pickle(temporary)
                os.replace(temporary,target)
                chunks.clear()
                del result
    finally:
        select._GLOBAL_FACTOR_LF = None
    # All selections are committed and readers closed. Aggregation still needs
    # the original Parquet files, but never these disposable sorted columns.
    # Keep the cache on selection/write failure so recovery can reuse it.
    with _phase('selection.release_columns'):
        shutil.rmtree(directory)


def _phase(name):
    import config
    callback=getattr(config,'compute_phase',None)
    return callback(name) if callback else nullcontext()



def run(conf, multi_process=False, silent=False):
    """Standalone/offline single-combination entry; framework uses run_pool."""
    return run_pool([conf],multi_process=multi_process,silent=silent)


def select_blocks(configs,store,directory,workers):
    """Complete time block → strategy tasks → bounded per-strategy output.

No 43×all-block list of results remains resident. Small selection partitions
are consumed once by each child aggregator, then removed. They are necessary
spill, not a second factor representation.
"""
    import config
    from core import select_coin as select
    directory=Path(directory);directory.mkdir(exist_ok=False)
    jobs=[(i,j,conf,s) for i,conf in enumerate(configs) for j,s in enumerate(conf.strategy_list)]
    columns=list(dict.fromkeys([*select.KLINE_COLS,*[c for _,_,_,s in jobs for c in s.factor_columns]]))
    paths={i:{j:[] for j in range(len(conf.strategy_list))} for i,conf in enumerate(configs)}
    gate=getattr(config,'compute_gate',None)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number,frame in enumerate(store.iter_blocks(columns,sort_time=False,before_read=gate)):
            # One canonical order for all strategies; filtering/projection keeps
            # it. The stage process owns this materialized block and exits
            # before the parent loads aggregation/simulation inputs.
            frame=frame.sort(['candle_begin_time','symbol'])
            def compute(job):
                i,j,conf,strategy=job
                result=select.process_strategy_thread(strategy,conf.get_result_folder(),False,conf.unified_time,
                                                       factor_source=frame.lazy(),return_result=True,source_sorted=True)
                if result is None or not len(result):return i,j,None
                target=directory/f'{i}-{j}-{number}.ipc'
                result.write_ipc(target,compression='lz4')
                return i,j,target
            for i,j,path in pool.map(compute,jobs):
                if path is not None:paths[i][j].append(path)
            del frame
            # Polars lazy callbacks may participate in Python reference cycles.
            # Match the original shared selector's block-boundary collection;
            # waiting for a generation-2 GC retains earlier large blocks.
            gc.collect()
    return paths
