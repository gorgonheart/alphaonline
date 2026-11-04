"""Full-history initialization orchestration, independent of the trading flow.

Reuse core mathematics and the original snapshot contract. Temporary factors
have one layout; final child/S3/MODEL states are written to the private staging
snapshot. The existing worker validates and atomically installs that snapshot.
"""
from pathlib import Path
import gc

import pandas as pd
import polars as pl
from tools.streaming_selection import select_blocks as selections
from tools.streaming_factors import section_blocks as sections


def _isolated(function,*args):
    """One process per large-table stage, joined before the next allocation.

    Arguments/results contain block paths and configuration, never full panels.
    This bounds native allocator retention and closes mmap readers on Windows;
    it does not recycle a process for each strategy or each time block.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as pool:
        result=pool.submit(function,*args).result()
    return result


def supported(factory,block_hours):
    # Custom/entry_exit strategies have cross-block behavior the standard rank
    # selector cannot infer. They retain the existing initialization entry.
    import config
    if config.shared_factor_pipeline:
        from core.backtest import supports_factor_blocks
        return block_hours>0 and supports_factor_blocks(factory)
    return block_hours>0 and all(not s.use_custom_func and s.select_mode!='entry_exit'
               for c in factory.config_list for s in c.strategy_list)


def simulate_child(conf,run_time,ratios,market):
    """Original state0 → retiming → state1, no intermediate curve CSV read."""
    from core.equity import calc_equity
    from core.backtest import calc_timing_leverages
    from core.model.timing_signal import TimingSignal
    retained=[]
    equity=calc_equity(conf,run_time,state_name='state0',ratio_inputs=ratios,
                       market_inputs=market,ratio_outputs=retained)
    timing=None
    if isinstance(conf.timing,TimingSignal):
        timing=calc_timing_leverages(conf,equity)
        equity=calc_equity(conf,run_time,leverage=timing*conf.leverage,state_name='state1',
                           ratio_inputs=ratios,market_inputs=market)
        timing=pd.Series(timing.to_numpy(),index=pd.DatetimeIndex(equity.candle_begin_time),name='动态杠杆')
    return dict(equity=equity,ratios=tuple(retained),timing=timing)


def _swap_mapping(store):
    """Own the small price/mapping table before deleting mapped factor files."""
    from tools.robust40.resources import phase
    from core.utils.factor_data import temporary_gc
    # Arrow→pandas can retain zero-copy mmap buffers. Only this six-column
    # subset survives selection; copying it lets the full factor store close.
    # Keep conversion-created cyclic mmap owners young. Scanning unrelated old
    # objects can fault in a swapped-out heap at this last-consumer boundary.
    with temporary_gc(phase('initialization.mapping_gc')):
        with phase('initialization.mapping_read'):
            rows=store.read_history(['candle_begin_time','symbol','is_spot','symbol_spot','close','next_close'],
                predicate=(pl.col('is_spot')==0)&(pl.col('symbol_spot')!=''))
        try:
            with phase('initialization.mapping_convert'):
                result=rows.to_pandas().copy(deep=True)
        finally:
            del rows
    return result


def run(mef,run_time,minute):
    """Return memory inputs for the MODEL; persistent files are final states."""
    import config
    from core import select_coin as select
    from core.utils.exchange_hub import apply_bstocks_black_list
    from core.utils.commons import get_snapshot_time,set_snapshot_time
    from core.utils.datatools import read_bmac_market_pivot,read_bmac_exginfo
    from tools.initialization_data import FactorBlocks
    from tools.streaming_factors import factor_blocks
    from tools.robust40.initialization_config import INITIALIZATION,MODEL_JOB_NUM
    from tools.robust40.resources import phase
    gate=getattr(config,'compute_gate',None)
    configs=mef.factory.config_list
    conf=mef.factory.generate_all_factor_config()
    if get_snapshot_time(conf.name):raise ValueError('Initialization requires an empty private snapshot')
    if not supported(mef.factory,INITIALIZATION['selection_block_hours']):
        raise ValueError('Initialization block pipeline requires positive blocks and standard selection')
    apply_bstocks_black_list(conf)
    for child in configs:child.save()
    cache=Path(select.get_file_path('data','cache','init_blocks',as_path_type=True))
    if config.shared_factor_pipeline:
        from core.backtest import prepare_factor_selection
        store, paths=prepare_factor_selection(mef.factory,run_time,directory=cache)
        selected=cache/'selected'
    else:
        # IPC is the selected production layout. Other FactorBlocks backends exist
        # only for offline experiments, not as additional deployment modes.
        store=FactorBlocks(cache,hours=INITIALIZATION['selection_block_hours'],minute=minute)
        with phase('initialization.direct_factors'):factor_blocks(conf,run_time,store)
        with phase('initialization.sections'):
            store.sections,store.section_columns=_isolated(sections,conf,store)
        gc.collect()
        selected=cache.parent/'init_selections'
        with phase('initialization.selection'):
            paths=_isolated(selections,configs,store,selected,min(2,MODEL_JOB_NUM))
    with phase('initialization.release_factors'):
        if gate:gate()
        # Conversion needs only swap mapping/price, not the full factor panel.
        market_rows=_swap_mapping(store)
        with phase('initialization.scratch_cleanup'):
            store.close()
    with phase('initialization.market_inputs'):
        if gate:gate()
        market=dict(pivots={k:read_bmac_market_pivot(f'{minute}m',k) for k in ('spot','swap')},
                    units={k:read_bmac_exginfo(k) for k in ('spot','swap')})
    end=run_time-pd.Timedelta(hours=1+config.utc_offset)
    results={};timing={}
    for i,child in enumerate(configs):
        with phase(f'initialization.aggregate_simulate:{i}'):
            if gate:gate()
            frames=[]
            for j in range(len(child.strategy_list)):
                parts=paths[i][j]
                if parts:
                    frames.append(pl.concat([pl.read_ipc(p,memory_map=False) for p in parts]).to_pandas())
                    for p in parts:p.unlink()
            merged=select.concat_select_results(child,frames=frames,persist_result=False)
            del frames
            converted=select.transfer_spot_to_swap(child,selection=merged,market=market_rows)
            del merged
            ratios=select.agg_multi_strategy_ratio(child,converted,end)
            del converted
            value=simulate_child(child,run_time,ratios,market)
            del ratios
            results[child.name]=value
            if value['timing'] is not None:timing[child.name]=value['timing']
    del market_rows
    selected.rmdir()
    cache.rmdir()
    set_snapshot_time(conf.name,max(v['equity'].candle_begin_time.max() for v in results.values()))
    with phase('initialization.equity_inputs'):
        mef.process_equities(run_time,f'{minute}m',inputs=results)
    return dict(pivots=market['pivots'],timing=timing)
