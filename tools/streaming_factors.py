"""Full Arrow initialization: bounded symbol batches, original factor mathematics.

Only an empty-snapshot initialization opts in. factor_blocks/section_blocks
feed the unified block store directly; prepare_inputs/calc_factors retain the
previous PKL/Parquet fallback. Both preserve original symbol/row order and reuse
the factor pool. Normal4050 trading keeps its original framework entry.
"""
import gc
import multiprocessing
import os
import time
from contextlib import ExitStack

import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq


def iter_direct_candles(conf, run_time):
    """Same universe/preprocessing as prepare_inputs, without a PKL handoff."""
    import config
    from core.utils import datatools as data
    from core.utils.functions import is_trade_symbol
    from core.utils.arrow_reader import arrow_snapshot_path
    from tools.initialization_data import read_market
    required=[f'{name}_{p}' for name,params in conf.precompute_factor_configs.items() for p in params]
    for kind in ('swap','spot'):
        if {kind,'mix'}.isdisjoint(conf.select_scope_set) and {kind}.isdisjoint(conf.order_first_set):continue
        allowed=set(data.get_dummy_client().get_market_info(kind).get('symbol_list',[]))
        for symbol,frame in read_market(arrow_snapshot_path(config.bmac_data_path,kind,run_time),
                                       require_full=True,required_columns=required):
            if (allowed and symbol not in allowed) or not is_trade_symbol(symbol,conf.black_list,conf.white_list):continue
            _,frame=data.read_and_merge_bmac_data(conf,kind,frame,run_time,align_fn=data.align_spot_swap_mapping_fast)
            if frame is not None and not frame.empty:yield frame


def iter_candle_descriptors(conf,run_time):
    """Small immutable tasks: path/batch/market, never a full-market frame."""
    import json
    import config
    from core.utils import datatools as data
    from core.utils.functions import is_trade_symbol
    from core.utils.arrow_reader import arrow_snapshot_path,ArrowReadError
    required={f'{name}_{p}' for name,params in conf.precompute_factor_configs.items() for p in params}
    for kind in ('swap','spot'):
        if {kind,'mix'}.isdisjoint(conf.select_scope_set) and {kind}.isdisjoint(conf.order_first_set):continue
        path=arrow_snapshot_path(config.bmac_data_path,kind,run_time)
        allowed=set(data.get_dummy_client().get_market_info(kind).get('symbol_list',[]))
        with pa.memory_map(str(path),'r') as source:
            reader=pa.ipc.open_file(source)
            if (reader.schema.metadata or {}).get(b'full_history')!=b'1':raise ArrowReadError('Full history Arrow required')
            if required-set(reader.schema.names):raise ArrowReadError('Missing precomputed columns')
            symbols=json.loads(reader.schema.metadata[b'symbols'])
            if len(symbols)!=reader.num_record_batches:raise ArrowReadError('Arrow symbol count differs')
            for index,symbol in enumerate(symbols):
                if (not allowed or symbol in allowed) and is_trade_symbol(symbol,conf.black_list,conf.white_list):
                    yield str(path),index,kind


def process_descriptor(descriptor,conf,columns,index,run_time):
    """Executed in the existing factor worker; preprocessing math is unchanged."""
    from core.utils import datatools as data
    from core.select_coin import process_candle_df_return_polars
    path,batch,kind=descriptor
    with pa.memory_map(path,'r') as source:
        frame=pa.ipc.open_file(source).get_batch(batch).to_pandas(use_threads=False)
    _,frame=data.read_and_merge_bmac_data(conf,kind,frame,run_time,align_fn=data.align_spot_swap_mapping_fast)
    if frame is None or frame.empty:return index,pl.DataFrame()
    return process_candle_df_return_polars(frame,conf,columns,index)


def factor_blocks(conf,run_time,store,*,dispatch='descriptor'):
    """Bounded full-symbol tasks → final downstream partitions, no input files.

One reusable pool; a fixed shard size independent of worker count preserves
input row order. The caller owns the store and its last-consumer cleanup.
"""
    from itertools import islice
    import config
    import core.select_coin as select
    from tools.bounded_pool import iter_results
    if select.get_snapshot_time(conf.name):raise ValueError('Direct factors require fresh initialization')
    gate=getattr(config,'compute_gate',None)
    size=config.factor_symbol_batch_size
    if dispatch not in ('parent','descriptor'):raise ValueError('Unknown input dispatch')
    source=iter_direct_candles(conf,run_time) if dispatch=='parent' else iter_candle_descriptors(conf,run_time)
    started=time.perf_counter();number=0
    with select.ProcessPoolExecutor(max_workers=select.job_num,mp_context=multiprocessing.get_context('spawn')) as pool:
        while True:
            if gate:gate()
            candles=list(islice(source,size))
            if not candles:break
            results=[None]*len(candles)
            function=select.process_candle_df_return_polars if dispatch=='parent' else process_descriptor
            args=((df,conf,conf.factor_col_name_list,i) if dispatch=='parent' else
                  (df,conf,conf.factor_col_name_list,i,run_time) for i,df in enumerate(candles))
            for i,result in iter_results(pool,function,args,select.job_num):results[i]=result
            del candles,args
            nonempty=[r for r in results if len(r)]
            if nonempty:store.append(pl.concat(nonempty),number)
            del results,nonempty,result
            number+=1
            select.logger.info(f'INIT_DATA 因子直连：{number}批，{store.rows}行，含读取/传输/分区保存{time.perf_counter()-started:.3f}s')
    if not store.rows:raise ValueError('Direct factor output is empty')


def prepare_inputs(conf, run_time):
    import config
    from core.utils import datatools as data
    from core.utils.functions import is_trade_symbol
    from core.utils.arrow_reader import arrow_snapshot_path, iter_arrow_market
    from core.utils.path_kit import get_file_path
    from core.utils.log_kit import logger

    directory = get_file_path('data', 'cache', 'candle_batches', as_path_type=True)
    # A fresh workspace is required: never append to a partially prepared input.
    directory.mkdir(exist_ok=False)
    size = getattr(config, 'factor_symbol_batch_size', 16)
    if size < 1:
        raise ValueError('factor_symbol_batch_size must be positive')
    gate = getattr(config, 'compute_gate', None)
    required = [f'{name}_{p}' for name, params in conf.precompute_factor_configs.items() for p in params]
    symbols, batch, count, rows = set(), [], 0, 0
    started = time.perf_counter()
    if gate:
        gate()
    for kind in ('swap', 'spot'):
        if {kind, 'mix'}.isdisjoint(conf.select_scope_set) and {kind}.isdisjoint(conf.order_first_set):
            continue
        allowed = set(data.get_dummy_client().get_market_info(kind).get('symbol_list', []))
        path = arrow_snapshot_path(config.bmac_data_path, kind, run_time)
        for symbol, frame in iter_arrow_market(path, require_full=True, required_columns=required):
            if (allowed and symbol not in allowed) or not is_trade_symbol(symbol, conf.black_list, conf.white_list):
                continue
            symbol, frame = data.read_and_merge_bmac_data(conf, kind, frame, run_time,
                                                        align_fn=data.align_spot_swap_mapping_fast)
            if frame is None or frame.empty:
                continue
            symbols.add(symbol)
            rows += len(frame)
            batch.append(frame)
            if len(batch) == size:
                pd.to_pickle(batch, directory/f'{count:06d}.pkl')
                count += 1
                batch.clear()
                del frame
                if gate:
                    gate()
    if batch:
        pd.to_pickle(batch, directory/f'{count:06d}.pkl')
        count += 1
    if not count:
        raise ValueError('Full Arrow initialization contains no usable candles')
    logger.info(f'FULL_ARROW 输入：{count}批，{rows}行；长因子补算0，耗时{time.perf_counter()-started:.3f}s')
    return tuple(symbols)


def calc_factors(conf):
    import config
    import core.select_coin as select
    from tools.bounded_pool import iter_results

    if select.get_snapshot_time(conf.name):
        raise ValueError('Batched full Arrow factors require a fresh initialization workspace')
    directory = select.get_file_path('data', 'cache', 'candle_batches', as_path_type=True)
    files = sorted(directory.glob('*.pkl'))
    if not files:
        raise ValueError('Missing prepared full Arrow candle batches')
    gate = getattr(config, 'compute_gate', None)
    started = time.perf_counter()
    rows = 0
    pending = pl.DataFrame()
    # Same row groups as the framework's Polars output. Independent symbol
    # batch boundaries must not create misaligned column chunks at selection.
    row_group_rows = 122880
    writers, targets = {}, {}
    # All writers close before publication; failures never expose final Parquets.
    with ExitStack() as stack:
        pool = stack.enter_context(select.ProcessPoolExecutor(
            max_workers=select.job_num, mp_context=multiprocessing.get_context('spawn')))

        def write(name, frame):
            table = frame.to_arrow()
            if name not in writers:
                target = select.get_file_path('data', 'cache', name, as_path_type=True)
                temp = target.with_suffix('.parquet.tmp')
                targets[name] = (temp, target)
                # Match native Polars encoding: high-cardinality float factors
                # use plain pages, avoiding large decode dictionaries downstream.
                dictionaries = [field.name for field in table.schema if not pa.types.is_floating(field.type)]
                writers[name] = stack.enter_context(pq.ParquetWriter(
                    temp, table.schema, compression='zstd', use_dictionary=dictionaries))
            writers[name].write_table(table)

        def write_frame(frame):
            write(select.ALL_KLINE_PATH_TUPLE[-1], frame.select(select.KLINE_COLS))
            if conf.has_section_factor:
                write(select.ALL_KLINE_FULL_PATH_TUPLE[-1], frame.select(select.KLINE_COLS))
            for column in conf.factor_col_name_list:
                if column not in frame.columns:
                    continue
                write(f'factor_{column}.parquet', frame.select(column))
                if conf.has_section_factor and column in conf.section_depend_factor_col_name_list:
                    write(f'factor_full_{column}.parquet', frame.select(column))

        for index, path in enumerate(files):
            # Previous batch has drained and been released before a busy wait.
            if gate:
                gate()
            candles = pd.read_pickle(path)
            results = [None]*len(candles)
            arguments = ((df, conf, conf.factor_col_name_list, i) for i, df in enumerate(candles))
            for i, result in iter_results(pool, select.process_candle_df_return_polars, arguments,
                                          max_pending=select.job_num):
                results[i] = result
            del candles, arguments
            nonempty = [value for value in results if len(value)]
            if nonempty:
                frame = pl.concat(nonempty)
                rows += len(frame)
                pending = pl.concat([pending, frame]) if len(pending) else frame
                while len(pending) >= row_group_rows:
                    write_frame(pending.head(row_group_rows))
                    pending = pending.slice(row_group_rows)
                del frame
            del results, nonempty, result
            # These are disposable intermediates in this fresh workspace. The
            # immutable Arrow remains available to rebuild a failed attempt.
            path.unlink()
            gc.collect()
            select.logger.info(f'FULL_ARROW 时序因子：{index+1}/{len(files)}批，累计{rows}行，'
                               f'耗时{time.perf_counter()-started:.3f}s')
        if len(pending):
            write_frame(pending)
    if not rows:
        raise ValueError('Full Arrow factor output is empty')
    for temp, target in targets.values():
        os.replace(temp, target)


def section_blocks(conf,store):
    """Compute each section on full history of ONLY its dependency columns.

TopK has cross-time rolling dependencies; splitting it blindly by time changes
its result. S3's existing state/checkpoint callback remains the single oracle.
Results are stored together per time block, without duplicate full factor files.
"""
    import config
    from core.select_coin import KLINE_COLS
    from core.utils.factor_hub import FactorHub
    from core.utils.section_state import run_section
    outputs=[];rows=None;gate=getattr(config,'compute_gate',None)
    for name,parameters in conf.section_params_dict.items():
        factor=FactorHub.get_by_name(name)
        for params in parameters:
            column=f'{name}_{params}'
            if column not in conf.factor_col_name_list:continue
            if gate:gate()
            dependencies=[f'{n}_{p}' for n,p in factor.get_factor_list(params)]
            needed=list(dict.fromkeys(['_row',*KLINE_COLS,*dependencies]))
            frame=store.read_history(needed)
            if rows is None:rows=frame.select('_row')
            if hasattr(factor,'signal_polars'):
                result=run_section(conf,factor,frame.lazy(),params,column,polars=True).select(column).collect()
            else:
                result=pl.from_pandas(run_section(conf,factor,frame.to_pandas(),params,column)[[column]])
            outputs.append(result[column])
            del frame,result
    if outputs:store.add_sections(rows.with_columns(outputs))
    return store.sections,getattr(store,'section_columns',[])
