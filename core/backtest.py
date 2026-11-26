"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import gc
import logging
import multiprocessing
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager

import pandas as pd

from config import backtest_path, utc_offset
from core.equity import calc_equity
from core.model.backtest_config import BacktestConfig
from core.model.backtest_config import BacktestConfigFactory
from core.model.timing_signal import TimingSignal
from core.select_coin import calc_factors, select_coins, concat_select_results, transfer_spot_to_swap, \
    agg_multi_strategy_ratio, calc_cross_sections
from core.utils.commons import set_snapshot_time, get_snapshot_time
from core.utils.exchange_hub import apply_bstocks_black_list
from core.utils.functions import save_performance_df_csv, load_realtime_data
from core.utils.log_kit import logger, divider
from core.utils.misc_kit import mem_save_exe


def step3_calc_factors(conf: BacktestConfig):
    """
    计算因子
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'因子计算...')
    calc_factors(conf)
    logger.ok(f'完成计算因子，花费时间：{time.time() - s_time:.2f}秒')


def step4_select_coins(conf: BacktestConfig):
    """
    选币
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'时序因子计算...')
    calc_factors(conf)
    logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.2f}秒')

    # 计算截面因子
    s_time = time.time()
    logger.info('截面因子计算...')
    calc_cross_sections(conf)
    logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.2f}秒')


def save_latest_period(conf, select_results, max_candle_time):
    # 选币结果的最新时间
    latest_result_list = []
    for stg in conf.strategy_list:
        latest_result_list.append(
            select_results[
                (select_results['strategy'] == stg.name) &
                (select_results['candle_begin_time'] > (max_candle_time - pd.to_timedelta(stg.hold_period)))
                ].copy())
    pd.concat(latest_result_list, ignore_index=True, copy=False).to_pickle(conf.get_result_folder() / '最新持仓周期选币结果.pkl')


def step5_aggregate_select_results(conf: BacktestConfig, max_candle_time, save_final_result=False):
    from core.utils.factor_data import read_vmstat_swap
    t0 = time.perf_counter()
    _, swap_out_start = read_vmstat_swap()
    logger.info(f'整理{conf.name}选币结果...')
    # 整理选币结果
    t_concat = time.perf_counter()
    concat_select_results(conf)  # 合并多个策略的选币结果
    concat_s = time.perf_counter() - t_concat

    t_transfer = time.perf_counter()
    select_results = transfer_spot_to_swap(conf)  # 把现货币对换成合约来节省手续费，如果是纯多或者合约的话，就直接跳过
    save_latest_period(conf, select_results, max_candle_time)
    logger.debug(f'💾 {conf.name}选币结果df大小：'
                   f'{select_results.memory_usage(deep=True).sum() / 1024 / 1024 / 1024:.4f} G')
    if save_final_result:
        # 存储最终的选币结果
        select_results.to_pickle(backtest_path / 'final_select_results.pkl')
    transfer_s = time.perf_counter() - t_transfer

    # 聚合大杂烩中多策略的权重，以及多offset选币的权重聚合
    s_time = time.perf_counter()
    logger.debug(f'🔃 开始{conf.name}权重聚合...')
    df_spot_ratio, df_swap_ratio = agg_multi_strategy_ratio(conf, select_results, max_candle_time)
    agg_s = time.perf_counter() - s_time

    # 始终输出ratios
    t_save = time.perf_counter()
    df_spot_ratio.to_pickle(conf.get_result_folder() / 'df_spot_ratio.pkl')
    df_swap_ratio.to_pickle(conf.get_result_folder() / 'df_swap_ratio.pkl')
    save_s = time.perf_counter() - t_save

    total_s = time.perf_counter() - t0
    _, swap_out_end = read_vmstat_swap()
    logger.ok(f'完成{conf.name}权重聚合，花费时间： {agg_s + save_s:.3f}秒')
    logger.info(
        f'AGG_PROFILE conf={conf.name} rows={len(select_results)} concat_s={concat_s:.3f} '
        f'transfer_s={transfer_s:.3f} agg_s={agg_s:.3f} save_s={save_s:.3f} '
        f'total_s={total_s:.3f} swap_out_pages={max(0, swap_out_end - swap_out_start)}'
    )
    return df_spot_ratio, df_swap_ratio


_STEP6_FORK_MARKET = None
_STEP6_WORKER_MARKET = None
_ROUND_MARKET_HANDOFF_ACTIVE = False
_ROUND_MARKET_PIVOTS = None
_ROUND_MODEL_STATE_PRELOAD_PATH = None


@contextmanager
def step6_market_handoff(*, model_state_preload_path=None):
    """Scoped one-shot handoff so Step 6 preloaded pivots can be reused by Step 8 MODEL
    within the same live round, guaranteeing memory release on exit."""
    global _ROUND_MARKET_HANDOFF_ACTIVE, _ROUND_MARKET_PIVOTS, _ROUND_MODEL_STATE_PRELOAD_PATH
    prev_active = _ROUND_MARKET_HANDOFF_ACTIVE
    prev_pivots = _ROUND_MARKET_PIVOTS
    prev_preload_path = _ROUND_MODEL_STATE_PRELOAD_PATH
    _ROUND_MARKET_HANDOFF_ACTIVE = True
    _ROUND_MARKET_PIVOTS = None
    _ROUND_MODEL_STATE_PRELOAD_PATH = model_state_preload_path
    try:
        yield
    finally:
        had_pivots = _ROUND_MARKET_PIVOTS is not None
        _ROUND_MARKET_HANDOFF_ACTIVE = prev_active
        _ROUND_MARKET_PIVOTS = prev_pivots
        _ROUND_MODEL_STATE_PRELOAD_PATH = prev_preload_path
        from tools.local_risk_model import clear_preloaded_model_state
        clear_preloaded_model_state()
        if had_pivots:
            gc.collect()
            _trim_process_heap()


def _publish_round_market_pivots(hour_offset: str, sig: tuple, pivots: dict) -> None:
    global _ROUND_MARKET_PIVOTS
    if _ROUND_MARKET_HANDOFF_ACTIVE and pivots is not None:
        _ROUND_MARKET_PIVOTS = (hour_offset, sig, pivots)


def consume_round_market_pivots(hour_offset: str):
    """Pop preloaded read-only market pivots once if offset & file signature still match."""
    global _ROUND_MARKET_PIVOTS
    cached = _ROUND_MARKET_PIVOTS
    if cached is None or cached[0] != hour_offset:
        return None
    _ROUND_MARKET_PIVOTS = None
    saved_offset, saved_sig, pivots = cached
    if not saved_sig or _bmac_market_file_sig(saved_offset) != saved_sig:
        return None
    return pivots


def _trim_process_heap():
    """Return freed glibc heap arenas to the OS on Linux after dropping shared Pivot frames."""
    if sys.platform != 'linux':
        return
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass


def _bmac_market_file_sig(hour_offset: str) -> tuple:
    """Lightweight (path, mtime_ns, size) signature matching datatools pivot & exginfo inputs."""
    from core.utils import datatools

    sig = []
    offset_dir = datatools.bmac_data_path / hour_offset
    if offset_dir.exists():
        for symbol_type in ('spot', 'swap'):
            for fp in sorted(offset_dir.rglob(f'market_pivot_{symbol_type}_*.pkl')):
                try:
                    st = fp.stat()
                    sig.append((str(fp), st.st_mtime_ns, st.st_size))
                except OSError:
                    pass
    cache_dir = datatools.get_folder_path('data', 'cache', as_path_type=True)
    for symbol_type in ('spot', 'swap'):
        for base in (cache_dir, datatools.bmac_exginfo_path):
            fp = base / f'exginfo_{symbol_type}.pkl'
            if fp.exists():
                try:
                    st = fp.stat()
                    sig.append((str(fp), st.st_mtime_ns, st.st_size))
                except OSError:
                    pass
                break
    return tuple(sig)


def _load_step6_market_inputs(hour_offset: str) -> dict:
    from core.utils.datatools import read_bmac_market_pivot, read_bmac_exginfo

    return {
        'pivots': {k: read_bmac_market_pivot(hour_offset, k) for k in ('spot', 'swap')},
        'units': {k: read_bmac_exginfo(k) for k in ('spot', 'swap')},
    }


def _get_worker_step6_market_inputs(hour_offset: str) -> dict:
    """Return read-only market_inputs shared across strategies within one Step 6 pool.

    On fork (Linux): reuses parent-preloaded _STEP6_FORK_MARKET (1 total disk read, CoW pages).
    On spawn (Windows/non-fork): bounded 1-entry per-worker cache keyed by (hour_offset, file_sig).
    Callers (calc_equity) wrap inner pivot dicts in fresh outer dict shells and never mutate DataFrames.
    """
    global _STEP6_WORKER_MARKET
    fork_shared = _STEP6_FORK_MARKET
    if fork_shared is not None and fork_shared[0] == hour_offset:
        return fork_shared[1]
    sig = _bmac_market_file_sig(hour_offset)
    if sig and _STEP6_WORKER_MARKET is not None and _STEP6_WORKER_MARKET[0] == (hour_offset, sig):
        return _STEP6_WORKER_MARKET[1]
    market_inputs = _load_step6_market_inputs(hour_offset)
    _STEP6_WORKER_MARKET = ((hour_offset, sig), market_inputs) if sig else None
    return market_inputs


def step6_simulate_performance(conf: BacktestConfig, run_time, *, market_inputs=None):
    logger.info(f'{conf.name} 开始模拟交易...')
    has_timing_signal = isinstance(conf.timing, TimingSignal)
    if market_inputs is None and has_timing_signal:
        # Only this child's state0/state1 share inputs; no cross-round/global cache.
        offset = f'{run_time.minute}m'
        market_inputs = _load_step6_market_inputs(offset)
    account_df = calc_equity(conf, run_time, state_name='state0', market_inputs=market_inputs)

    save_performance_df_csv(conf, 资金曲线=account_df)

    if has_timing_signal:
        account_df = simu_timing(conf, run_time, account_df=account_df, market_inputs=market_inputs)

    return account_df


def simu_timing(conf: BacktestConfig, run_time, *, account_df=None, market_inputs=None):
    s_time = time.time()
    logger.info(f'{conf.get_fullname(as_folder_name=True)} 资金曲线择时，生成动态杠杆')
    state0_equity_path = conf.get_snapshot_folder() / 'account_state0.pkl'
    if not state0_equity_path.exists():
        logger.warning(f'{state0_equity_path} 不存在，请检查')
        logger.critical(f'{conf.name} 历史选币为空，可能是过滤条件过于严格，或者选中的币种被拉黑，请检查配置。程序退出...AA')
        exit()
    if account_df is None:
        account_df = pd.read_pickle(state0_equity_path)

    leverages = calc_timing_leverages(conf, account_df)
    logger.debug(f'⏰ 完成再择时杠杆计算，已花费时间{time.time() - s_time:.3f}秒')

    account_df = calc_equity(conf, run_time, leverage=leverages * conf.leverage, state_name='state1',
                             market_inputs=market_inputs)
    save_performance_df_csv(conf, 资金曲线_再择时=account_df, 再择时动态杠杆=pd.DataFrame({
        'candle_begin_time': account_df['candle_begin_time'],
        '动态杠杆': leverages
    }))

    return account_df


def calc_timing_leverages(conf, account_df):
    """Shared retiming mathematics; caller owns reporting and second simulation."""

    # 检查哪个动态杠杆方法有实现，优先使用 get_dynamic_leverage_for_dataframe
    if conf.timing.impl_flags.get('dynamic_leverage_for_dataframe', False):
        leverages = conf.timing.get_dynamic_leverage_for_dataframe(account_df)
    elif conf.timing.impl_flags.get('dynamic_leverage', False):
        leverages = conf.timing.get_dynamic_leverage(account_df['equity'])
    else:
        raise NotImplementedError(f'择时信号 {conf.timing.name} 必须实现 dynamic_leverage 或 dynamic_leverage_for_dataframe 方法之一')
    return leverages


def _wait_for_compute_window():
    import config
    if gate := getattr(config, 'compute_gate', None):
        gate()


def _step4_worker(conf, multi_process):
    """Step 4 并行 worker（直接运行，PPE 退出后内存自动释放，等价于 mem_save_exe 效果）"""
    from core.utils.log_kit import logger
    _wait_for_compute_window()
    logger.info(f'{conf.name}的{len(conf.strategy_list)}个子策略选币')
    select_coins(conf, multi_process=multi_process)


def _step5_worker(conf, max_candle_time, has_snapshot):
    """Step 5 并行 worker（直接运行，PPE 退出后内存自动释放，等价于 mem_save_exe 效果）"""
    from core.utils.log_kit import logger
    _wait_for_compute_window()
    conf.has_snapshot = has_snapshot
    logger.debug(f"[{conf.name}] 聚合{len(conf.strategy_list)}个子策略")
    step5_aggregate_select_results(conf, max_candle_time, save_final_result=True)


def _step6_worker(conf, run_time):
    """Step 6 并行 worker（模块级函数，Windows multiprocessing 可 pickle）"""
    from core.utils.log_kit import logger
    _wait_for_compute_window()
    logger.debug(f"🔃 聚合{conf.name}的{len(conf.strategy_list)}个子策略，并计算资金曲线...")
    market_inputs = _get_worker_step6_market_inputs(f'{run_time.minute}m')
    return step6_simulate_performance(conf, run_time, market_inputs=market_inputs)


# ====================================================================================================
# ** 回测主程序 **
# 1. 准备工作
# 2. 读取数据
# 3. 计算因子
# 4. 选币
# 5. 整理选币数据
# 6. 添加下一个每一个周期需要卖出的币的信息
# 7. 计算资金曲线
# ====================================================================================================
def supports_factor_blocks(factory):
    """Custom callbacks and entry/exit strategies retain their full-history path."""
    from core.utils.factor_hub import FactorHub
    if any(s.use_custom_func or s.select_mode == 'entry_exit'
           for conf in factory.config_list for s in conf.strategy_list):
        return False
    # An arbitrary signal_polars may mutate base columns or differ from signal.
    # Only reviewed sections opt into independent projection/dispatch.
    names = {name for conf in factory.config_list for name in getattr(conf, 'section_params_dict', {})}
    return all(getattr(FactorHub.get_by_name(name), 'shared_input_safe', False) for name in names)


def _run_block_stage(function, *args, _polars_threads=1, **kwargs):
    """Release native allocations at stage boundaries; never fork Polars threads."""
    import multiprocessing
    import os
    # Native pools initialize during imports, before a pool initializer can run.
    # This coordinator is serial; restore its environment even when a stage fails.
    names = ('POLARS_MAX_THREADS', 'PMT_STAGE_POLARS_THREADS', 'OMP_NUM_THREADS',
             'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS')
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = str(max(1, int(_polars_threads))) if name in names[:2] else '1'
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
            return pool.submit(function, *args, **kwargs).result()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _produce_factor_store(conf, run_time, store):
    calc_factors(conf, run_time=run_time, store=store)
    return store


def prepare_factor_selection(factory, run_time, *, directory):
    """Common initialization/continuation/replay front half, with caller-owned spill.

    Input history is preserved for factors/sections. Only selection is cropped
    by the original snapshot/holding/lookback rule. Returned factors remain alive
    for the final spot/swap mapping consumer; the caller must close the store.
    """
    from pathlib import Path
    import config
    from core.utils.factor_data import FactorBlocks, compute_phase
    from core.select_coin import select_factor_blocks
    if not supports_factor_blocks(factory):
        raise ValueError('Shared factor selection requires standard strategies')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    conf = factory.generate_all_factor_config()
    apply_bstocks_black_list(conf)
    store = FactorBlocks(directory/'factors', hours=getattr(config, 'selection_block_hours', 2016),
                         minute=run_time.minute)
    phase = getattr(config, 'compute_phase', None) or compute_phase
    try:
        with phase('initialization.direct_factors'):
            store = _run_block_stage(_produce_factor_store, conf, run_time, store)
        budget = max(1, config.job_num)
        gate = getattr(config, 'compute_gate', None)
        section_threads = budget if gate is None else min(2, budget)
        with phase('initialization.sections'):
            store.sections, store.section_columns = _run_block_stage(calc_cross_sections, conf, store=store,
                _polars_threads=section_threads)
        snapshot = get_snapshot_time(conf.name)
        start = None if not snapshot else pd.to_datetime(snapshot).replace(tzinfo=None) - max(
            pd.Timedelta(conf.max_hold_period), pd.Timedelta(days=conf.lookback_days),
            pd.Timedelta(hours=conf.incremental_lookback_hours))
        store.selection_start = start
        # One strategy at a time; its Polars operators use the common CPU budget.
        # Concurrent Python tasks share one native pool and increase live panels.
        with phase('initialization.selection'):
            paths = _run_block_stage(select_factor_blocks, factory.config_list, store,
                directory/'selected', workers=1, start=start, _polars_threads=budget)
        return store, paths
    except BaseException:
        # This store owns only scratch created here, not any public input/state.
        store.close()
        raise


def persist_block_selections(configs, paths):
    """Retain original per-strategy reporting/aggregation files and no factor copy."""
    import polars as pl
    from core.select_coin import SELECT_RES_COLS
    for i, conf in enumerate(configs):
        for j, strategy in enumerate(conf.strategy_list):
            parts = paths[i][j]
            result = (pl.concat([pl.read_ipc(p, memory_map=False) for p in parts]).to_pandas()
                      if parts else pd.DataFrame(columns=SELECT_RES_COLS))
            result.to_pickle(conf.get_result_folder()/f'{strategy.get_fullname(as_folder_name=True)}.pkl')
            del result
            for path in parts:
                path.unlink()


def _prepare_shared_reports(factory, run_time):
    from core.select_coin import KLINE_COLS, ALL_KLINE_PATH_TUPLE, get_file_path
    from core.utils.factor_data import temporary_gc
    store, paths = prepare_factor_selection(factory, run_time,
        directory=get_file_path('data', 'cache', 'shared_compute', as_path_type=True))
    try:
        persist_block_selections(factory.config_list, paths)
        # Original consumers still need prices/mappings and their report columns.
        # There is no second copy of factor columns or whole candle input.
        with temporary_gc():
            rows = store.read_history(KLINE_COLS, start=getattr(store, 'selection_start', None))
            try:
                rows.write_parquet(get_file_path(*ALL_KLINE_PATH_TUPLE))
            finally:
                del rows
    finally:
        store.close()
    (store.directory.parent/'selected').rmdir()
    store.directory.parent.rmdir()


def run_backtest_multi(factory: BacktestConfigFactory, run_time):
    # ====================================================================================================
    # 1. 准备工作
    # ====================================================================================================
    divider('准备启动', sep='-')
    iter_results_folder = factory.result_folder

    # 删除缓存
    shutil.rmtree(iter_results_folder, ignore_errors=True)
    iter_results_folder.mkdir(parents=True, exist_ok=True)

    conf_list = factory.config_list
    import config
    # 默认保留原4进程；资源受限的worker可统一覆盖选币、聚合和模拟上限。
    pool_workers = min(len(conf_list), getattr(config, 'backtest_job_num', 4))
    gate = getattr(config, 'compute_gate', None)
    for index, conf in enumerate(conf_list):
        logger.debug(f'ℹ️ 策略{index + 1}｜共{len(conf_list)}个')
        logger.debug(f'{conf.get_fullname()}')
        conf.save()
    logger.ok('策略池中需要回测的策略数：{}'.format(len(conf_list)))

    # 记录一下时间戳
    r_time = time.time()

    # ====================================================================================================
    # 2. 读取回测所需数据，并做简单的预处理
    # ====================================================================================================
    divider('读取数据', sep='-')
    s_time = time.time()
    conf_all = factory.generate_all_factor_config()

    # bStocks 黑名单显式并入：conf_all 会被 pickle 进子进程，进程内类级缓存不会跟着带过去，
    # 提前并入 black_list 才能保证子进程里的数据过滤/选币判断同样生效
    apply_bstocks_black_list(conf_all)

    if getattr(config, 'shared_factor_pipeline', False) and supports_factor_blocks(factory):
        _prepare_shared_reports(factory, run_time)
    else:
        # 读取数据
        # 针对现货策略和非现货策略读取的逻辑完全不同。
        # - 如果是纯合约模式，只需要读入 swap 数据并且合并即可
        # - 如果是现货模式，需要读入 spot 和 swap 数据并且合并，然后添加 tag
        if gate:gate()
        mem_save_exe(load_realtime_data, conf_all, run_time)  # 串行方式，完全等价
        logger.ok(f'完成读取数据中心数据，花费时间：{time.time() - s_time:.3f}秒')

        # ====================================================================================================
        # 3. 计算因子
        # ====================================================================================================
        divider('时序因子计算', sep='-')
        s_time = time.time()
        if gate:gate()
        mem_save_exe(calc_factors, conf_all)  # 执行时序因子计算
        logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

        # 计算截面因子
        s_time = time.time()
        divider(f'截面因子计算', sep='-')
        if gate:gate()
        mem_save_exe(calc_cross_sections, conf_all)  # 执行截面因子计算
        logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')


        # ====================================================================================================
        # 4. 选币
        # - 注意：选完之后，每一个策略的选币结果会被保存到硬盘
        # ====================================================================================================
        divider('选币', sep='-')
        s_time = time.time()
        logger.debug(f'注意：这个过程时间久，和包含的策略及子策略数量、选币数量有关...')
        # LPT（最长任务优先）：子策略多的 conf 先提交，减少尾部等待
        _multi_process = bool(get_snapshot_time(conf_all.name))
        _sorted4 = sorted(conf_list, key=lambda c: len(c.strategy_list), reverse=True)
        if pool_runner := getattr(config, 'selection_pool_runner', None):
            # 可选工具层共享输入；独立子进程退出后释放整池选币内存。
            mem_save_exe(pool_runner, _sorted4, multi_process=_multi_process)
        else:
            with ProcessPoolExecutor(max_workers=pool_workers) as executor:
                futures = {executor.submit(_step4_worker, conf, _multi_process): conf for conf in _sorted4}
                for f in as_completed(futures):
                    f.result()  # 捕获异常；任务完成即释放，不等整批

        logger.ok(f'完成选币，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 5. 针对选币结果进行聚合
    # ====================================================================================================
    divider('合并选币的资金权重', sep='-')
    logger.setLevel(logging.DEBUG)
    logger.debug(f'注意：主要和选币数量有关...')
    s_time = time.time()
    max_candle_time = run_time - pd.to_timedelta('1D' if conf_all.is_day_period else '1H') - pd.Timedelta(hours=utc_offset)
    # LPT 排序，子策略多的先做
    _has_snapshot = bool(get_snapshot_time(conf_all.name))
    _sorted5 = sorted(conf_list, key=lambda c: len(c.strategy_list), reverse=True)
    with ProcessPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(_step5_worker, conf, max_candle_time, _has_snapshot): conf for conf in _sorted5}
        for f in as_completed(futures):
            f.result()  # 捕获异常；PPE 退出后内存自动释放
    logger.ok(f'聚合策略资金权重完成，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 6. 回测模拟
    # ====================================================================================================
    logger.setLevel(logging.DEBUG)
    divider('回测模拟', sep='-')
    logger.info(f'时间会比较久，主要和选币数量有关...')
    s_time = time.time()

    # LPT 排序 + as_completed：长任务先提交，完成即收割，不等整批
    _sorted6 = sorted(conf_list, key=lambda c: len(c.strategy_list), reverse=True)
    equity_map = {}
    global _STEP6_FORK_MARKET
    _preload_fork = (
        len(_sorted6) > 1
        and multiprocessing.get_context().get_start_method() == 'fork'
    )
    _preload_sig = None
    try:
        if _preload_fork:
            _t_pre = time.perf_counter()
            _offset = f'{run_time.minute}m'
            _preload_sig = _bmac_market_file_sig(_offset)
            _STEP6_FORK_MARKET = (_offset, _load_step6_market_inputs(_offset))
            logger.info(
                f'STEP6_MARKET_PRELOAD offset={_offset} mode=fork '
                f'elapsed_s={time.perf_counter() - _t_pre:.3f}'
            )
        with ProcessPoolExecutor(max_workers=pool_workers) as executor:
            futures = {executor.submit(_step6_worker, conf, run_time): conf for conf in _sorted6}
            if _ROUND_MODEL_STATE_PRELOAD_PATH is not None:
                from tools.local_risk_model import start_model_state_preload
                start_model_state_preload(_ROUND_MODEL_STATE_PRELOAD_PATH)
            for f in as_completed(futures):
                conf = futures[f]
                equity_map[conf.name] = f.result()
    finally:
        if _STEP6_FORK_MARKET is not None:
            handed_off = False
            if _ROUND_MARKET_HANDOFF_ACTIVE and _preload_sig:
                _publish_round_market_pivots(
                    _STEP6_FORK_MARKET[0], _preload_sig, _STEP6_FORK_MARKET[1]['pivots']
                )
                handed_off = True
            _STEP6_FORK_MARKET = None
            if not handed_off:
                gc.collect()
                _trim_process_heap()
    equity_list = [equity_map[conf.name] for conf in conf_list]  # 恢复原始顺序

    snapshot_time = max([equity['candle_begin_time'].max() for equity in equity_list])

    set_snapshot_time(conf_all.name, snapshot_time)

    logger.ok(f'完成策略池所有策略模拟，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒，最后资金曲线时间：{snapshot_time}')

    return equity_list
