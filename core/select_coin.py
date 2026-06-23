"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import gc
import os
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import polars as pl
from tqdm import tqdm

from config import job_num, factor_col_limit, clean_start, use_streaming
from core.factor import calc_factor_vals
from core.model.backtest_config import BacktestConfig, StrategyConfig
from core.utils.commons import get_snapshot_time
from core.utils.factor_hub import FactorHub
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path

warnings.filterwarnings('ignore')
# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)

# 计算完因子之后，保留的字段
KLINE_COLS = ['candle_begin_time', 'symbol', 'is_spot', 'close', 'next_close', 'symbol_spot', 'symbol_swap', '是否交易']
# 计算完选币之后，保留的字段
SELECT_RES_COLS = [*KLINE_COLS, 'strategy', 'cap_weight', '方向', 'offset', 'target_alloc_ratio', 'order_first']
# 完整kline数据保存的路径
ALL_KLINE_PATH_TUPLE = ('data', 'cache', 'all_factors_kline.parquet')
ALL_KLINE_FULL_PATH_TUPLE = ('data', 'cache', 'all_factors_kline_full.parquet')

# 全局 LazyFrame，用于多线程共享数据（零拷贝列投影）
_GLOBAL_FACTOR_LF: pl.LazyFrame = None


# ======================================================================================
# 因子计算相关函数
# - calc_factors_by_symbol: 计算单个币种的因子池
# - calc_factors: 计算因子池
# ======================================================================================

def trans_period_for_day(df, date_col='candle_begin_time', factor_dict=None):
    """
    将数据周期转换为指定的1D周期
    :param df: 原始数据
    :param date_col: 日期列
    :param factor_dict: 转换规则
    :return:
    """
    df.set_index(date_col, inplace=True)
    # 必备字段
    agg_dict = {
        'symbol': 'first',
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'quote_volume': 'sum',
        'trade_num': 'sum',
        'taker_buy_base_asset_volume': 'sum',
        'taker_buy_quote_asset_volume': 'sum',
        'is_spot': 'last',
        # 'has_swap': 'last',
        'symbol_swap': 'last',
        'symbol_spot': 'last',
        'funding_fee': 'sum',
        'next_avg_price': 'last',
        '是否交易': 'last',
    }

    if factor_dict:
        agg_dict = dict(agg_dict, **factor_dict)
    df = df.resample('1D').agg(agg_dict)
    df.reset_index(inplace=True)

    return df


# region 因子计算相关函数
def calc_factors_by_candle(candle_df, conf: BacktestConfig, factor_col_name_list) -> pd.DataFrame:
    """
    针对单一比对，计算所有因子的数值
    :param candle_df: 一个币种的k线数据 dataframe
    :param conf: 回测配置
    :param factor_col_name_list: 需要计算的因子列
    :return: 包含所有因子的 dataframe(目前是包含k线数据的）
    """
    # 遍历每个因子，计算每个因子的数据
    factor_series_dict = {}
    for factor_name, param_list in conf.factor_params_dict.items():
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if factor.is_cross:
            continue

        # 筛选一下需要计算的因子
        factor_param_list = []
        for param in param_list:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name in factor_col_name_list:
                factor_param_list.append(param)
        if len(factor_param_list) == 0:
            continue  # 当该因子不需要计算的时候直接返回

        factor_series_dict.update(calc_factor_vals(candle_df, factor_name, factor_param_list))

    # 保证多进程下列顺序稳定，避免 Polars concat 列名不一致
    ordered_factor_series = {}
    for col_name in factor_col_name_list:
        if col_name in factor_series_dict:
            ordered_factor_series[col_name] = factor_series_dict[col_name]
    if len(ordered_factor_series) != len(factor_series_dict):
        extra_keys = [k for k in factor_series_dict.keys() if k not in ordered_factor_series]
        for col_name in sorted(extra_keys):
            ordered_factor_series[col_name] = factor_series_dict[col_name]

    # 将结果 DataFrame 与原始 DataFrame 合并
    kline_with_factor_dict = {
        'candle_begin_time': candle_df['candle_begin_time'].values,
        'symbol': candle_df['symbol'].values,
        'is_spot': candle_df['is_spot'].values,
        'close': candle_df['close'].values,
        # 'has_swap': candle_df['has_swap'],
        # 'next_avg_price': candle_df['next_avg_price'].values,
        'next_close': candle_df['close'].shift(-1).values,  # 后面周期排除需要用
        # 'next_funding_fee': candle_df['funding_fee'].shift(-1).values,
        'symbol_spot': candle_df['symbol_spot'].astype(str).values,
        'symbol_swap': candle_df['symbol_swap'].astype(str).values,
        **ordered_factor_series,
        '是否交易': candle_df['是否交易'].values,
    }

    kline_with_factor_df = pd.DataFrame(kline_with_factor_dict, copy=False)
    kline_with_factor_df.sort_values(by='candle_begin_time', inplace=True)

    # 抛弃一开始的一段k线，保留后面的数据
    first_candle_time = candle_df.iloc[0]['first_candle_time'] + pd.to_timedelta(f'{conf.min_kline_num}h')

    # # 调整 symbol_spot 和 symbol_swap
    # for col in ['symbol_spot', 'symbol_swap']:
    #     symbol_start_time = candle_df[
    #         (candle_df[col] != '') & (candle_df[col].shift(1) == '') & (~candle_df[col].shift(1).isna())
    #         ]['candle_begin_time']
    #     if not symbol_start_time.empty:
    #         condition = pd.Series(False, index=kline_with_factor_df.index)
    #         for symbol_time in symbol_start_time:
    #             _cond1 = kline_with_factor_df['candle_begin_time'] >= symbol_time
    #             _cond2 = kline_with_factor_df['candle_begin_time'] <= symbol_time + pd.to_timedelta(
    #                 f'{conf.min_kline_num}h')
    #             condition |= (_cond1 & _cond2)
    #         kline_with_factor_df.loc[condition, col] = ''
    #     kline_with_factor_df[col] = kline_with_factor_df[col].astype('category')

    # 需要对数据进行裁切
    kline_with_factor_df = kline_with_factor_df[kline_with_factor_df['candle_begin_time'] >= first_candle_time]

    # 下架币/拆分币，去掉最后一个周期不全的数据
    # if kline_with_factor_df['candle_begin_time'].max() < pd.to_datetime(conf.end_date):
    #     _temp_time = kline_with_factor_df['candle_begin_time'] + pd.Timedelta(conf.max_hold_period)
    #     _del_time = kline_with_factor_df[kline_with_factor_df.loc[_temp_time.index, 'next_close'].isna()][
    #         'candle_begin_time']
    #     kline_with_factor_df = kline_with_factor_df[
    #         kline_with_factor_df['candle_begin_time'] <= _del_time.min() - pd.Timedelta(conf.max_hold_period)]

    snapshot_time = get_snapshot_time(conf.name)
    # 只保留最近的数据
    if snapshot_time and not conf.has_section_factor:
        cut_off_time = (
                pd.to_datetime(snapshot_time).replace(tzinfo=None)
                - max(pd.Timedelta(conf.max_hold_period),
                      pd.Timedelta(days=conf.lookback_days),
                      pd.Timedelta(hours=conf.incremental_lookback_hours))
        )
        kline_with_factor_df = kline_with_factor_df[kline_with_factor_df['candle_begin_time'] >= cut_off_time]

    # 只保留需要的字段
    return kline_with_factor_df


def process_candle_df(candle_df: pd.DataFrame, conf: BacktestConfig, factor_col_name_list: List[str], idx: int):
    """
    # 针对每一个币种的k线数据，按照策略循环计算因子信息
    :param candle_df: 单个币种的数据
    :param conf: backtest config
    :param factor_col_name_list:    因子列表，可以用于动态判断当前需要计算的因子列。
                                    当 factor_col_name_list ≠ conf.factor_col_name_list 时，说明需要节省一点内存
    :param idx: 索引
    :return: 带有因子数值的数据
    """
    # ==== 数据预处理 ====
    factor_dict = {'first_candle_time': 'first', 'last_candle_time': 'last'}
    for strategy in conf.strategy_list:
        symbol = candle_df['symbol'].iloc[-1]
        candle_df, _factor_dict, _ = strategy.after_merge_index(candle_df, symbol, factor_dict, {})
        factor_dict.update(_factor_dict)

    # 计算平均开盘价格
    candle_df['next_avg_price'] = candle_df[conf.avg_price_col].shift(-1)  # 用于后面计算当周期涨跌幅

    # 转换成日线数据  跟回测保持一致
    if conf.is_day_period:
        candle_df = trans_period_for_day(candle_df, factor_dict=factor_dict)

    # ==== 计算因子 ====
    # 清理掉头部参与日线转换的填充数据
    candle_df.dropna(subset=['symbol'], inplace=True)
    candle_df.reset_index(drop=True, inplace=True)
    # 针对单个币种的K线数据计算
    # 返回带有因子数值的K线数据
    factor_df = calc_factors_by_candle(candle_df, conf, factor_col_name_list)

    return idx, factor_df


# 写入单个因子文件的辅助函数（Polars DataFrame 版本）
def write_factor_file_polars(args):
    col_name, file_path, pl_df = args
    file_path.unlink(missing_ok=True)
    pl_df.write_parquet(file_path)


def process_candle_df_return_polars(candle_df: pd.DataFrame, conf: BacktestConfig, factor_col_name_list: List[str],
                                    idx: int):
    """process_candle_df 的包装：在子进程内完成 pd→pl 转换。
    转换工作分散到多个子进程并行完成，主进程 concat 接近零开销。"""
    idx, factor_df = process_candle_df(candle_df, conf, factor_col_name_list, idx)
    if len(factor_df) == 0:
        return idx, pl.DataFrame()
    return idx, pl.from_pandas(factor_df)


def calc_factors(conf: BacktestConfig):
    """
    选币因子计算，考虑到大因子回测的场景，我们引入chunk的概念，会把所有factor切成多分，然后分别计算
    :param conf:       账户信息
    :return:
    """
    # ====================================================================================================
    # 1. ** k线数据整理及参数准备 **
    # - is_use_spot: True的时候，使用现货数据和合约数据;
    # - False的时候，只使用合约数据。所以这个情况更简单
    # ====================================================================================================
    # hold_period的作用是计算完因子之后，
    # 获取最近 hold_period 个小时内的数据信息，
    # 同时用于offset字段计算使用
    # ====================================================================================================
    # 2. ** 因子计算 **
    # 遍历每个币种，计算相关因子数据
    # ====================================================================================================
    t_load_start = time.time()
    candle_df_list = pd.read_pickle(get_file_path('data', 'cache', 'all_candle_df_list.pkl'))
    t_load_pickle = time.time() - t_load_start
    factor_col_count = len(conf.factor_col_name_list)
    shards = range(0, factor_col_count, factor_col_limit)

    logger.debug(f'''* 总共计算因子个数：{factor_col_count} 个
* 单次计算因子个数：{factor_col_limit} 个，(需分成{len(shards)}组计算)
* 需要计算币种数量：{len(candle_df_list)} 个''')

    logger.debug(f'📊 数据加载: 读取 pickle {t_load_pickle:.2f}s')

    # 清理 cache 的缓存
    all_kline_parquet = get_file_path(*ALL_KLINE_PATH_TUPLE, as_path_type=True)
    all_kline_parquet.unlink(missing_ok=True)

    all_kline_full_parquet = get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True)
    all_kline_full_parquet.unlink(missing_ok=True)

    for shard_index in shards:
        logger.info(f'因子分片计算中，进度：{int(shard_index / factor_col_limit) + 1}/{len(shards)}')
        factor_col_name_list = conf.factor_col_name_list[shard_index:shard_index + factor_col_limit]

        all_factor_pl_list = [pl.DataFrame()] * len(candle_df_list)
        t_mp_start = time.time()
        with ProcessPoolExecutor(max_workers=job_num) as executor:
            futures = [executor.submit(
                process_candle_df_return_polars, candle_df, conf, factor_col_name_list, candle_idx
            ) for candle_idx, candle_df in enumerate(candle_df_list)]

            for future in tqdm(as_completed(futures), total=len(candle_df_list), desc='🧮 时序因子计算'):
                idx, factor_pl = future.result()
                all_factor_pl_list[idx] = factor_pl
        t_multiprocess = time.time() - t_mp_start

        # ====================================================================================================
        # 3. ** 合并因子结果 **
        # 转成 polars DataFrame 减少后续转换时间
        # ====================================================================================================
        t_concat_start = time.time()
        all_factors_pl = pl.concat([df for df in all_factor_pl_list if len(df) > 0])
        del all_factor_pl_list

        t_concat = time.time() - t_concat_start

        # ====================================================================================================
        # 4. ** 因子结果分片存储 (全程 Polars) **
        # 分片存储计算结果，节省内存占用，提高选币效率
        # - 将合并好的df，分成2个部分：k线和因子列
        # - k线数据存储为一个parquet，每一列因子存储为一个parquet，在选币时候按需读入合并成df
        # ====================================================================================================
        logger.debug('💾 分片存储因子结果...')

        t_save_start = time.time()

        # 保留最小 k 线时间
        snapshot_time = get_snapshot_time(conf.name)
        # 只保留最近的数据
        if snapshot_time:
            min_candle_time = (
                    pd.to_datetime(snapshot_time).replace(tzinfo=None)
                    - max(pd.Timedelta(conf.max_hold_period),
                          pd.Timedelta(days=conf.lookback_days),
                          pd.Timedelta(hours=conf.incremental_lookback_hours))
            )
        else:
            min_candle_time = all_factors_pl['candle_begin_time'].to_pandas().min()

        # 选币需要的k线
        if not all_kline_parquet.exists():
            # 存储裁切时间的数据（Polars 排序 + 过滤）
            all_kline_pl = all_factors_pl.select(KLINE_COLS)
            all_kline_pl = all_kline_pl.filter(
                pl.col('candle_begin_time') >= pl.lit(pd.to_datetime(min_candle_time))
            )
            all_kline_pl.write_parquet(all_kline_parquet)

        if not all_kline_full_parquet.exists() and conf.has_section_factor:
            # 存储不裁切的全量数据
            all_kline_full_pl = all_factors_pl.select(KLINE_COLS)
            all_kline_full_pl.write_parquet(all_kline_full_parquet)

        # 针对每一个因子进行存储
        # 重要：必须和 all_kline_df 使用相同的排序，确保 horizontal concat 时行顺序一致
        cut_factors_pl = all_factors_pl.filter(
            pl.col('candle_begin_time') >= pl.lit(pd.to_datetime(min_candle_time))
        )

        write_tasks = []
        all_columns = all_factors_pl.columns
        for factor_col_name in factor_col_name_list:
            # 截面因子数据不在这里计算，不存在这个列名
            if factor_col_name not in all_columns:
                continue

            # 裁切后的因子文件
            factor_parquet = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet', as_path_type=True)
            write_tasks.append((factor_col_name, factor_parquet, cut_factors_pl.select(factor_col_name)))

            # 全量因子文件（用于截面因子）
            if conf.has_section_factor and factor_col_name in conf.section_depend_factor_col_name_list:
                factor_full_parquet = get_file_path('data', 'cache', f'factor_full_{factor_col_name}.parquet', as_path_type=True)
                write_tasks.append((factor_col_name, factor_full_parquet, all_factors_pl.select(factor_col_name)))

        # 并行写入（IO 密集型任务使用线程池）
        if write_tasks:
            with ThreadPoolExecutor(max_workers=job_num) as executor:
                executor.map(write_factor_file_polars, write_tasks)
            del write_tasks
            gc.collect()

        t_save = time.time() - t_save_start

        logger.debug(f'''🚀 PyArrow + Polars 优化时间统计:
  - 多进程因子计算: {t_multiprocess:.2f}s
  - Arrow concat + Polars: {t_concat:.2f}s
  - 分片保存 (排序+写入): {t_save:.2f}s
  - 总计: {t_multiprocess + t_concat + t_save:.2f}s''')

        del all_factors_pl, cut_factors_pl

        gc.collect()


def process_factor_df(factor_col_name):
    # 准备所有时序因子数据
    factor_path = get_file_path('data', 'cache', f'factor_full_{factor_col_name}.parquet', as_path_type=True)
    if not factor_path.exists():
        return factor_col_name, pl.DataFrame()

    return factor_col_name, pl.read_parquet(factor_path)


def load_all_factors(conf: BacktestConfig):
    all_kline_full_parquet = get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True)
    factor_df = pl.read_parquet(all_kline_full_parquet)

    # 保留最小 k 线时间
    snapshot_time = get_snapshot_time(conf.name)
    # 只保留最近的数据
    if snapshot_time:
        min_candle_time = (
                pd.to_datetime(snapshot_time).replace(tzinfo=None)
                - max(pd.Timedelta(conf.max_hold_period),
                      pd.Timedelta(days=conf.lookback_days),
                      pd.Timedelta(hours=conf.incremental_lookback_hours))
        )
    else:
        min_candle_time = factor_df['candle_begin_time'].to_pandas().min()

    # 准备所有时序因子数据
    factor_list = conf.section_depend_factor_col_name_list
    for factor_col_name in tqdm(factor_list, total=len(factor_list), desc='✂️ 裁切时序因子数据'):
        factor_col_name, kline_with_factor_df = process_factor_df(factor_col_name)
        if not kline_with_factor_df.is_empty():
            factor_df = factor_df.with_columns(kline_with_factor_df.get_column(factor_col_name))

    return factor_df.to_pandas(), min_candle_time


def calc_cross_sections(conf: BacktestConfig):
    """
    截面因子计算，自动选择 Polars 或 Pandas 路径
    :param conf:       账户信息
    :return:
    """
    section_params_dict = conf.section_params_dict
    # 如果没有配置截面因子，那么直接跳过后续
    if not section_params_dict:
        logger.info(f'未检查到截面因子配置，跳过计算截面因子步骤。')
        return

    # 检查是否所有截面因子都有 Polars 实现
    all_polars = all(
        hasattr(FactorHub.get_by_name(fn), 'signal_polars')
        for fn in section_params_dict.keys()
    )

    if all_polars:
        # Polars 路径
        calc_cross_sections_polars(conf)
    else:
        # 原有 Pandas 路径
        calc_cross_sections_pandas(conf)


def calc_cross_sections_polars(conf: BacktestConfig):
    """
    Polars 版本的截面因子计算
    :param conf:       账户信息
    :return:
    """
    t_start = time.time()
    section_params_dict = conf.section_params_dict

    # 保留最小 k 线时间
    snapshot_time = get_snapshot_time(conf.name)
    if snapshot_time:
        min_candle_time = pd.to_datetime(snapshot_time).replace(tzinfo=None) - max(pd.Timedelta(conf.max_hold_period), pd.Timedelta(days=conf.lookback_days))
    else:
        min_candle_time = None

    # 加载全量 K线数据（保持原始排序，与时序因子 parquet 一致）
    all_kline_full_parquet = get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True)
    lf = pl.scan_parquet(all_kline_full_parquet)

    # 加载依赖的时序因子（批量读取，单次 with_columns）
    factor_series_list = []
    for factor_col_name in conf.section_depend_factor_col_name_list:
        factor_full_parquet = get_file_path('data', 'cache', f'factor_full_{factor_col_name}.parquet', as_path_type=True)
        if factor_full_parquet.exists():
            factor_series = pl.read_parquet(factor_full_parquet, columns=[factor_col_name]).to_series()
            factor_series_list.append(factor_series)

    # 一次性添加所有因子列
    if factor_series_list:
        lf = lf.with_columns(factor_series_list)

    # 提前 collect 成内存 DataFrame：chunk 循环内 df_base.lazy() 的根节点是内存，不再重复扫描磁盘
    df_base = lf.collect()
    del lf

    t_load = time.time()
    logger.debug(f'📊 截面因子 - 数据加载+collect base: {t_load - t_start:.2f}s')

    import config
    chunk_size = int(getattr(config, 'cross_section_chunk_size', 0) or 0)

    # 汇总所有需要计算的因子参数，跨因子分片
    all_factor_params = []
    for factor_name, param_list in section_params_dict.items():
        for param in param_list:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name in conf.factor_col_name_list:
                all_factor_params.append((factor_name, param))

    # 分片计算，避免一次性累积过多列
    chunks = [all_factor_params] if chunk_size <= 0 else [
        all_factor_params[i:i + chunk_size]
        for i in range(0, len(all_factor_params), chunk_size)
    ]

    for chunk in chunks:
        # df_base.lazy() 根节点是内存 DataFrame，collect 只执行截面算子，无磁盘 I/O
        lf_chunk = df_base.lazy()
        t_chunk_start = time.time()
        for factor_name, param in tqdm(chunk, desc='🧮 截面因子计算 (Polars)'):
            factor = FactorHub.get_by_name(factor_name)
            factor_col_name = f'{factor_name}_{param}'
            lf_chunk = factor.signal_polars(lf_chunk, param, factor_col_name)

        t_calc = time.time()
        logger.debug(f'📊 截面因子 - 因子计算: {t_calc - t_chunk_start:.2f}s')

        # collect 一次获取当前分片结果
        df_chunk = lf_chunk.collect()
        t_collect = time.time()
        logger.debug(f'📊 截面因子 - Collect: {t_collect - t_calc:.2f}s')

        # 时间范围裁切一次，chunk 内所有 factor 复用，避免重复 filter
        cut_df_base = (
            df_chunk.filter(pl.col('candle_begin_time') >= pl.lit(pd.to_datetime(min_candle_time)))
            if min_candle_time is not None else df_chunk
        )

        # 构建写入任务（与时序因子 calc_factors 保持一致）
        write_tasks = []
        for factor_name, param in chunk:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name not in df_chunk.columns:
                continue
            factor_parquet = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet', as_path_type=True)
            write_tasks.append((factor_col_name, factor_parquet, cut_df_base.select([factor_col_name])))

        # 并行写入（I/O 密集型，线程池）
        if write_tasks:
            with ThreadPoolExecutor(max_workers=job_num) as executor:
                executor.map(write_factor_file_polars, write_tasks)

        t_save = time.time()
        logger.debug(f'📊 截面因子 - 保存: {t_save - t_collect:.2f}s')

        del df_chunk, cut_df_base, write_tasks
        gc.collect()

    del df_base
    logger.debug(f'📊 截面因子 - 总计: {time.time() - t_start:.2f}s')


def calc_cross_sections_pandas(conf: BacktestConfig):
    """
    原有 Pandas 版本的截面因子计算
    :param conf:       账户信息
    :return:
    """
    section_params_dict = conf.section_params_dict

    # 加载面板数据
    factor_df, min_candle_time = load_all_factors(conf)

    # 遍历截面因子，调用截面因子计算方法
    for factor_name, param_list in section_params_dict.items():
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if not factor.is_cross:
            continue

        # 筛选一下需要计算的因子
        factor_param_list = []
        section_param_list = []
        for param in param_list:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name in conf.factor_col_name_list:
                factor_param_list.append(param)
                section_param_list.extend(factor.get_factor_list(param))
        if len(factor_param_list) == 0:
            continue  # 当该因子不需要计算的时候直接返回

        # 截面因子依赖的时序因子列
        section_col_name_list = list(set(f'{f}_{n}' for f, n in set(section_param_list)))

        # 对截面因子按照时间进行分段计算
        base_cols = KLINE_COLS + section_col_name_list
        legacy_candle_df = factor_df[base_cols].copy()  # 如果是老的因子计算逻辑，单独拿出来一份数据
        for param in tqdm(factor_param_list, total=len(factor_param_list), desc=f'🧮 截面因子计算 {factor_name}'):
            factor_col_name = f'{factor_name}_{param}'
            legacy_candle_df = factor.signal(legacy_candle_df, param, factor_col_name)

            # 对数据进行裁切并保存
            cross_factor_df = legacy_candle_df[['candle_begin_time', factor_col_name]]
            cross_factor_df = cross_factor_df[cross_factor_df['candle_begin_time'] >= min_candle_time]
            factor_parquet = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet', as_path_type=True)
            factor_parquet.unlink(missing_ok=True)  # 动态清理掉cache的缓存
            pl.from_pandas(cross_factor_df[[factor_col_name]]).write_parquet(factor_parquet)
            del cross_factor_df
            # 丢弃刚生成的因子列，避免列持续增长
            legacy_candle_df = legacy_candle_df[base_cols]

            gc.collect()
        del legacy_candle_df
    del factor_df
    gc.collect()


# endregion


# ======================================================================================
# 选币相关函数
# - calc_select_factor_rank: 计算因子排序
# - select_long_and_short_coin: 选做多和做空的币种
# - select_coins_by_strategy: 根据策略选币
# - select_coins: 选币，循环策略调用 `select_coins_by_strategy`
# ======================================================================================
# region 选币相关函数
def calc_select_factor_rank(lf: pl.LazyFrame, factor_column: str = '因子', ascending: bool = True) -> pl.LazyFrame:
    """
    计算因子排名 (LazyFrame 版本)
    :param lf:              原数据 (Polars LazyFrame)
    :param factor_column:   需要计算排名的因子名称
    :param ascending:       计算排名顺序，True：从小到大排序；False：从大到小排序
    :return:                计算排名后的 LazyFrame
    """
    descending = not ascending

    lf = lf.with_columns([
        pl.col(factor_column).rank(method='min', descending=descending).over('candle_begin_time').alias('rank'),
        pl.len().over('candle_begin_time').alias('总币数')
    ]).with_columns([
        pl.col('rank').max().over('candle_begin_time').alias('rank_max')
    ])

    lf = lf.sort(['candle_begin_time', 'rank'])

    return lf


def select_long_and_short_coin(strategy: StrategyConfig, long_lf: pl.LazyFrame, short_lf: pl.LazyFrame) -> pl.LazyFrame:
    """
    选币，添加多空资金权重后，对于无权重的情况，减少选币次数 (LazyFrame 版本)
    """
    # 做多选币
    if strategy.long_cap_weight > 0:
        long_lf = calc_select_factor_rank(long_lf, factor_column=strategy.long_factor, ascending=True)
        long_lf = select_by_coin_num_polars(
            long_lf,
            strategy.long_select_coin_num,
            strategy.select_inclusive,
            max_limit=strategy.long_select_coin_num_max
        )
        long_lf = long_lf.with_columns([
            pl.lit(1).alias('方向'),
            (pl.lit(1.0) / pl.len().over('candle_begin_time')).alias('target_alloc_ratio')
        ])
    else:
        long_lf = long_lf.filter(pl.lit(False))

    # 做空选币
    if strategy.short_cap_weight > 0:
        short_lf = calc_select_factor_rank(short_lf, factor_column=strategy.short_factor, ascending=False)

        if strategy.short_select_coin_num == 'long_nums':
            long_select_num = long_lf.group_by('candle_begin_time').agg(
                pl.col('symbol').count().alias('多头数量')
            )
            short_lf = short_lf.join(long_select_num, on='candle_begin_time', how='left')
            short_lf = short_lf.filter(pl.col('rank') <= pl.col('多头数量'))
            short_lf = short_lf.drop('多头数量')
        else:
            short_lf = select_by_coin_num_polars(
                short_lf,
                strategy.short_select_coin_num,
                strategy.select_inclusive,
                min_limit=strategy.short_select_coin_num_min
            )

        short_lf = short_lf.with_columns([
            pl.lit(-1).alias('方向'),
            (pl.lit(1.0) / pl.len().over('candle_begin_time')).alias('target_alloc_ratio')
        ])
    else:
        short_lf = short_lf.filter(pl.lit(False))

    lf = pl.concat([long_lf, short_lf], how='diagonal')
    lf = lf.sort(['candle_begin_time', '方向'], descending=[False, True])
    lf = lf.drop(['总币数', 'rank_max'])

    return lf


def select_by_coin_num_polars(
    lf: pl.LazyFrame,
    coin_num,
    select_inclusive,
    max_limit=None,
    min_limit=None
) -> pl.LazyFrame:
    """
    根据选币数量进行选币 (LazyFrame 版本)
    """
    select_range = coin_num if isinstance(coin_num, (tuple, list)) else (None, coin_num)
    inclusive = select_inclusive if isinstance(select_inclusive, (tuple, list)) else (select_inclusive, select_inclusive)

    conditions = []

    def apply_limits(expr, raw_num):
        if int(raw_num) == 0:
            if max_limit is not None:
                expr = pl.when(expr > max_limit).then(max_limit).otherwise(expr)
            if min_limit is not None:
                expr = pl.when(expr < min_limit).then(min_limit).otherwise(expr)
            return expr

        value = raw_num
        if max_limit is not None:
            value = min(value, max_limit)
        if min_limit is not None:
            value = max(value, min_limit)
        return pl.lit(value)

    # 左边界条件
    if select_range[0] is not None:
        left_num = select_range[0]
        if int(left_num) == 0:
            select_num_expr = pl.col('总币数') * left_num
        else:
            select_num_expr = pl.lit(left_num)
        select_num_expr = apply_limits(select_num_expr, left_num)

        if inclusive[0] != 'right':
            conditions.append(pl.col('rank') >= select_num_expr)
        else:
            conditions.append(pl.col('rank') > select_num_expr)

    # 右边界条件
    if select_range[1] is not None:
        right_num = select_range[1]
        if int(right_num) == 0:
            select_num_expr = pl.col('总币数') * right_num
        else:
            select_num_expr = pl.lit(right_num)
        select_num_expr = apply_limits(select_num_expr, right_num)

        if inclusive[1] != 'left':
            conditions.append(pl.col('rank') <= select_num_expr)
        else:
            conditions.append(pl.col('rank') < select_num_expr)

    if conditions:
        combined_condition = conditions[0]
        for cond in conditions[1:]:
            combined_condition = combined_condition & cond
        lf = lf.filter(combined_condition)

    return lf


def select_coins_by_strategy(factor_lf: pl.LazyFrame, stg_conf: StrategyConfig) -> pl.LazyFrame:
    """
    针对每一个策略，进行选币 (LazyFrame 版本)
    """
    # 4.1 数据预处理
    pass

    # 4.2 计算目标选币因子
    s = time.time()
    if stg_conf.use_custom_func:
        factor_df = factor_lf.collect()
        factor_pd = factor_df.to_pandas()
        result_pd = stg_conf.calc_select_factor(factor_pd)
        prev_cols = factor_df.columns
        new_cols = list(set(result_pd.columns) - set(prev_cols))
        if new_cols:
            new_cols_df = pl.from_pandas(result_pd[new_cols])
            factor_df = pl.concat([factor_df, new_cols_df], how='horizontal')
        factor_lf = factor_df.lazy()
    else:
        factor_lf = stg_conf.calc_select_factor_polars_lazy(factor_lf)
    logger.debug(f'[{stg_conf.name}] 选币因子计算耗时：{time.time() - s:.2f}s')

    # 4.3 前置过滤筛选
    s = time.time()
    if stg_conf.use_custom_func:
        factor_df = factor_lf.collect()
        factor_pd = factor_df.to_pandas()
        long_pd, short_pd = stg_conf.filter_before_select(factor_pd)
        long_lf = pl.from_pandas(long_pd).lazy()
        short_lf = pl.from_pandas(short_pd).lazy()
    else:
        long_lf, short_lf = stg_conf.filter_before_select_polars_lazy(factor_lf)
    short_lf = short_lf.filter(pl.col('symbol_swap') != '')
    logger.debug(f'[{stg_conf.name}] 前置过滤耗时：{time.time() - s:.2f}s')

    # 4.4 根据选币因子进行选币
    s = time.time()
    result_lf = select_long_and_short_coin(stg_conf, long_lf, short_lf)
    logger.debug(f'[{stg_conf.name}] 多空选币耗时：{time.time() - s:.2f}s')

    # 4.5 后置过滤筛选
    if stg_conf.use_custom_func:
        result_df = result_lf.collect()
        factor_pd = result_df.to_pandas()
        factor_pd = stg_conf.filter_after_select(factor_pd)
        result_lf = pl.from_pandas(factor_pd).lazy()
    else:
        result_lf = stg_conf.filter_after_select_polars_lazy(result_lf)
    logger.debug(f'[{stg_conf.name}] 后置过滤耗时：{time.time() - s:.2f}s')

    # 4.6 根据多空比调整币种的权重
    long_ratio = stg_conf.long_cap_weight / (stg_conf.long_cap_weight + stg_conf.short_cap_weight)
    result_lf = result_lf.with_columns([
        pl.when(pl.col('方向') == 1)
        .then(pl.col('target_alloc_ratio') * long_ratio)
        .otherwise(pl.col('target_alloc_ratio') * (1 - long_ratio))
        .alias('target_alloc_ratio')
    ])
    result_lf = result_lf.filter(pl.col('target_alloc_ratio').abs() > 1e-9)

    return result_lf.select([*KLINE_COLS, '方向', 'target_alloc_ratio'])


def preload_all_factor_data(strategy_list: List[StrategyConfig]) -> None:
    """
    预加载所有策略需要的因子数据，合并为全局 LazyFrame（用于多线程模式）
    """
    global _GLOBAL_FACTOR_LF

    all_factor_columns = set()
    for stg_conf in strategy_list:
        all_factor_columns.update(stg_conf.factor_columns)

    logger.info(f'预加载数据: K线 + {len(all_factor_columns)} 个因子列')

    merged_df = pl.read_parquet(get_file_path(*ALL_KLINE_PATH_TUPLE))
    for factor_col_name in all_factor_columns:
        factor_path = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet')
        factor_col_df = pl.read_parquet(factor_path)
        merged_df = pl.concat([merged_df, factor_col_df], how='horizontal')

    logger.info(f'预加载完成: 合并后 {merged_df.shape}')
    _GLOBAL_FACTOR_LF = merged_df.lazy()


def process_strategy_thread(stg_conf: StrategyConfig, result_folder: Path, is_silent=False, unified_time='2017-01-01'):
    """
    多线程版本的策略处理函数（使用全局 LazyFrame 零拷贝列投影）
    """
    import logging
    global _GLOBAL_FACTOR_LF

    if is_silent:
        logger.setLevel(logging.WARNING)
    s = time.time()
    strategy_name = stg_conf.name
    logger.debug(f'[{stg_conf.name}] 开始选币...')

    required_cols = [*KLINE_COLS, *stg_conf.factor_columns]
    select_scope = stg_conf.select_scope
    order_first = stg_conf.order_first

    filters = [pl.col('是否交易') == 1]

    if select_scope == 'spot':
        filters.append(pl.col('is_spot') == 1)
    elif select_scope == 'swap':
        filters.append(pl.col('is_spot') == 0)
    else:  # mix 混合
        both_not_null = (pl.col('symbol_spot') != '') & (pl.col('symbol_swap') != '')
        order_first_symbol = pl.col('is_spot') == (1 if order_first == 'spot' else 0)
        filters.append(~both_not_null | order_first_symbol)

    for col in stg_conf.factor_columns:
        filters.append(pl.col(col).is_not_null())
    filters.append(pl.col('symbol').is_not_null())

    factor_lf = (_GLOBAL_FACTOR_LF
                 .select(required_cols)
                 .filter(pl.all_horizontal(filters))
                 .sort(['candle_begin_time', 'symbol']))

    logger.debug(f'[{stg_conf.name}] 选币数据准备完成，消耗时间：{time.time() - s:.2f}s')

    result_lf = select_coins_by_strategy(factor_lf, stg_conf)
    result_df = result_lf.collect(engine="streaming" if use_streaming else "auto")

    stg_select_result = result_folder / f'{stg_conf.get_fullname(as_folder_name=True)}.pkl'

    if result_df.is_empty():
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        logger.setLevel(logging.DEBUG)
        return

    cal_offset_base_seconds = 3600 * 24 if stg_conf.is_day_period else 3600
    reference_date = pl.lit(pd.to_datetime(unified_time))
    result_df = result_df.with_columns([
        ((pl.col('candle_begin_time') - reference_date).dt.total_seconds() / cal_offset_base_seconds)
        .mod(stg_conf.period_num)
        .cast(pl.Int8)
        .alias('_offset_temp')
    ])
    result_df = result_df.with_columns([
        ((pl.col('_offset_temp') + 1 + stg_conf.period_num) % stg_conf.period_num)
        .cast(pl.Int8)
        .alias('offset')
    ])
    result_df = result_df.drop('_offset_temp')
    result_df = result_df.filter(pl.col('offset').is_in(stg_conf.offset_list))

    if result_df.is_empty():
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        logger.setLevel(logging.DEBUG)
        return

    result_df = result_df.with_columns([
        pl.col('方向').cast(pl.Int8),
        pl.col('offset').cast(pl.Int8),
    ])

    result_df = result_df.with_columns([
        pl.lit(strategy_name).alias('strategy'),
        pl.lit(stg_conf.cap_weight).cast(pl.Float64).alias('cap_weight'),
    ])

    result_df = result_df.with_columns([
        (pl.col('target_alloc_ratio') * pl.col('cap_weight') / len(stg_conf.offset_list) * pl.col('方向'))
        .cast(pl.Float64)
        .alias('target_alloc_ratio')
    ])

    result_df = result_df.with_columns([
        pl.lit(order_first).alias('order_first')
    ])

    result_df = result_df.with_columns([
        pl.col('symbol_spot').cast(pl.Categorical),
        pl.col('symbol_swap').cast(pl.Categorical),
        pl.col('strategy').cast(pl.Categorical),
        pl.col('order_first').cast(pl.Categorical),
    ])

    result_df.select(SELECT_RES_COLS).to_pandas().to_pickle(stg_select_result)

    logger.debug(f'[{strategy_name}] 耗时: {(time.time() - s):.2f}s')
    gc.collect()
    logger.setLevel(logging.DEBUG)


def process_strategy(stg_conf: StrategyConfig, result_folder: Path, is_silent=False, unified_time='2017-01-01'):
    """
    单策略处理函数（独立加载数据，用于多进程回退场景）
    """
    import logging
    if is_silent:
        logger.setLevel(logging.WARNING)
    s = time.time()
    strategy_name = stg_conf.name
    logger.debug(f'[{stg_conf.name}] 开始选币...')

    factor_df = pl.read_parquet(get_file_path(*ALL_KLINE_PATH_TUPLE))
    for factor_col_name in stg_conf.factor_columns:
        factor_path = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet')
        factor_col_df = pl.read_parquet(factor_path)
        factor_df = pl.concat([factor_df, factor_col_df], how='horizontal')

    factor_df = factor_df.filter(pl.col('是否交易') == 1)

    select_scope = stg_conf.select_scope
    order_first = stg_conf.order_first
    if select_scope == 'spot':
        condition = pl.col('is_spot') == 1
    elif select_scope == 'swap':
        condition = pl.col('is_spot') == 0
    else:  # mix 混合
        both_not_null = (pl.col('symbol_spot') != '') & (pl.col('symbol_swap') != '')
        order_first_symbol = pl.col('is_spot') == (1 if order_first == 'spot' else 0)
        condition = ~both_not_null | order_first_symbol
    factor_df = factor_df.filter(condition)

    factor_df = factor_df.drop_nulls(subset=stg_conf.factor_columns)
    factor_df = factor_df.drop_nulls(subset=['symbol'])

    factor_df = factor_df.sort(['candle_begin_time', 'symbol'])

    logger.debug(f'[{stg_conf.name}] 选币数据准备完成，消耗时间：{time.time() - s:.2f}s')

    factor_lf = factor_df.lazy()
    del factor_df

    result_lf = select_coins_by_strategy(factor_lf, stg_conf)
    result_df = result_lf.collect(engine="streaming" if use_streaming else "auto")

    stg_select_result = result_folder / f'{stg_conf.get_fullname(as_folder_name=True)}.pkl'

    if result_df.is_empty():
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        return

    cal_offset_base_seconds = 3600 * 24 if stg_conf.is_day_period else 3600
    reference_date = pl.lit(pd.to_datetime(unified_time))

    result_df = result_df.with_columns([
        ((pl.col('candle_begin_time') - reference_date).dt.total_seconds() / cal_offset_base_seconds)
        .mod(stg_conf.period_num)
        .cast(pl.Int8)
        .alias('_offset_temp')
    ])
    result_df = result_df.with_columns([
        ((pl.col('_offset_temp') + 1 + stg_conf.period_num) % stg_conf.period_num)
        .cast(pl.Int8)
        .alias('offset')
    ])
    result_df = result_df.drop('_offset_temp')
    result_df = result_df.filter(pl.col('offset').is_in(stg_conf.offset_list))

    if result_df.is_empty():
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        logger.setLevel(logging.DEBUG)
        return

    result_df = result_df.with_columns([
        pl.col('方向').cast(pl.Int8),
        pl.col('offset').cast(pl.Int8),
    ])

    result_df = result_df.with_columns([
        pl.lit(strategy_name).alias('strategy'),
        pl.lit(stg_conf.cap_weight).cast(pl.Float64).alias('cap_weight'),
    ])

    result_df = result_df.with_columns([
        (pl.col('target_alloc_ratio') * pl.col('cap_weight') / len(stg_conf.offset_list) * pl.col('方向'))
        .cast(pl.Float64)
        .alias('target_alloc_ratio')
    ])

    result_df = result_df.with_columns([
        pl.lit(order_first).alias('order_first')
    ])

    result_df = result_df.with_columns([
        pl.col('symbol_spot').cast(pl.Categorical),
        pl.col('symbol_swap').cast(pl.Categorical),
        pl.col('strategy').cast(pl.Categorical),
        pl.col('order_first').cast(pl.Categorical),
    ])

    result_df.select(SELECT_RES_COLS).to_pandas().to_pickle(stg_select_result)

    logger.debug(f'[{strategy_name}] 耗时: {(time.time() - s):.2f}s')
    gc.collect()
    logger.setLevel(logging.DEBUG)


def select_coin_with_conf(conf: BacktestConfig, multi_process=True, silent=True):
    """
    ** 策略选币 (核心函数) **
    预加载 + 多线程选币

    :param conf: 回测配置
    :param multi_process: 是否启用多线程
    :param silent: 是否静默
    :return:
    """
    import logging
    global _GLOBAL_FACTOR_LF
    if silent:
        logger.setLevel(logging.WARNING)

    result_folder = conf.get_result_folder()
    # 预加载所有策略需要的数据到全局 LazyFrame
    preload_all_factor_data(conf.strategy_list)

    if not multi_process:
        try:
            for index, strategy in enumerate(conf.strategy_list):
                logger.debug(f'ℹ️ [{index + 1}/{len(conf.strategy_list)}] {conf.name}')
                process_strategy_thread(strategy, result_folder, False, conf.unified_time)
        finally:
            _GLOBAL_FACTOR_LF = None
            gc.collect()
        return

    # 多线程模式（推荐）：预加载数据 + 线程池共享（零拷贝列投影）
    try:
        with ThreadPoolExecutor(max_workers=min(max(job_num // 2, 1), 10)) as executor:
            futures = [
                executor.submit(process_strategy_thread, stg, result_folder, silent, conf.unified_time)
                for stg in conf.strategy_list
            ]

            for future in tqdm(as_completed(futures), total=len(conf.strategy_list), desc=f'🚀 {conf.name}'):
                try:
                    future.result()
                except Exception as e:
                    logger.exception(e)
                    exit(1)
    finally:
        _GLOBAL_FACTOR_LF = None
        gc.collect()

    logger.setLevel(logging.DEBUG)


# 选币数据整理 & 选币
def select_coins(conf: BacktestConfig, multi_process=False, silent=False):
    """
    ** 策略选币 **
    保持原有函数签名不变，内部调用 select_coin_with_conf

    :param conf: 回测配置
    :param multi_process: 是否启用多进程/多线程
    :param silent: 是否静默
    :return:
    """
    return select_coin_with_conf(conf, multi_process=multi_process, silent=silent)
# endregion


# ======================================================================================
# 选币结果聚合
# ======================================================================================
# region 选币结果聚合
def transfer_swap(select_coin, df_swap):
    """
    将现货中的数据替换成合约数据，主要替换：close
    :param select_coin:     选币数据
    :param df_swap:         合约数据
    :return:
    """
    trading_cols = ['symbol', 'is_spot', 'close', 'next_close']
    spot_line_index = select_coin[(select_coin['symbol_swap'] != '') & (select_coin['is_spot'] == 1)].index

    spot_select_coin = select_coin.loc[spot_line_index].copy()
    swap_select_coin = select_coin.loc[select_coin.index.difference(spot_line_index)].copy()
    # ['candle_begin_time', 'symbol_swap', 'strategy', 'cap_weight', '方向', 'offset', 'target_alloc_ratio']
    spot_select_coin = pd.merge(
        spot_select_coin, df_swap[['candle_begin_time', *trading_cols]],
        left_on=['candle_begin_time', 'symbol_swap'], right_on=['candle_begin_time', 'symbol'],
        how='left', suffixes=('', '_2'))

    # merge完成之后，可能因为有些合约数据上线不超过指定的时间（min_kline_num）,造成合并异常，需要按照原现货逻辑执行
    failed_merge_select_coin = spot_select_coin[spot_select_coin['close_2'].isna()][select_coin.columns].copy()

    spot_select_coin = spot_select_coin.dropna(subset=['close_2'], how='any')
    spot_select_coin['is_spot_2'] = spot_select_coin['is_spot_2'].astype(np.int8)

    spot_select_coin.drop(columns=trading_cols, inplace=True)
    rename_dict = {f'{trading_col}_2': trading_col for trading_col in trading_cols}
    spot_select_coin.rename(columns=rename_dict, inplace=True)

    # 将拆分的选币数据，合并回去
    select_coin = pd.concat([swap_select_coin, failed_merge_select_coin, spot_select_coin], axis=0, copy=False)
    select_coin.sort_values(['candle_begin_time', '方向'], inplace=True)

    return select_coin


def concat_select_results(conf: BacktestConfig) -> None:
    """
    聚合策略选币结果，形成综合选币结果
    :param conf:
    :return:
    """
    # 如果是纯多头现货模式，那么就不转换合约数据，只下现货单
    all_select_result_df_list = []  # 全量选币结果
    result_folder = conf.get_result_folder()
    select_result_path = result_folder / '选币结果.pkl'

    for strategy in conf.strategy_list:
        stg_select_result = result_folder / f'{strategy.get_fullname(as_folder_name=True)}.pkl'
        # 如果文件不存在，就跳过
        if not os.path.exists(stg_select_result):
            continue

        # 加载全部的选币结果
        result_result_df = pd.read_pickle(stg_select_result)
        all_select_result_df_list.append(result_result_df)

        # 删除该策略的选币结果，如果要保留可以注释
        if not conf.is_reserved('strategy'):
            stg_select_result.unlink()

    # 如果没有任何策略的选币结果，就直接返回
    if not all_select_result_df_list:
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(select_result_path)
        return None

    # 聚合选币结果
    all_select_result_df = pd.concat(all_select_result_df_list, ignore_index=True, copy=False)
    del all_select_result_df_list
    gc.collect()

    if all_select_result_df.empty:
        all_select_result_df.to_pickle(select_result_path)
        return None

    if not conf.has_snapshot:
        all_stg_select_first_time = all_select_result_df.groupby('strategy')['candle_begin_time'].first().max()
        all_select_result_df = all_select_result_df[all_select_result_df['candle_begin_time'] >= all_stg_select_first_time]
    all_select_result_df.to_pickle(select_result_path)
    if conf.lookback_days:
        all_select_result_df[
            all_select_result_df['candle_begin_time'] > all_select_result_df['candle_begin_time'].max() - pd.Timedelta(days=conf.lookback_days)
        ].to_csv(result_folder / '选币结果.csv', encoding='utf-8-sig', index=False)
    else:
        (result_folder / '选币结果.csv').unlink(missing_ok=True)

    # 保存历史选币数据
    snapshot_select_result_path = conf.get_snapshot_folder() / '选币结果.pkl'
    if not clean_start and snapshot_select_result_path.exists():
        local_select_result_df = pd.read_pickle(snapshot_select_result_path)
        min_select_time = all_select_result_df['candle_begin_time'].min()
        pd.concat(
            [local_select_result_df[local_select_result_df['candle_begin_time'] < min_select_time], all_select_result_df],
            ignore_index=True, copy=False
        ).to_pickle(snapshot_select_result_path)
    else:
        all_select_result_df.to_pickle(snapshot_select_result_path)

    return all_select_result_df


def transfer_spot_to_swap(conf: BacktestConfig) -> pd.DataFrame:
    """
    将现货策略的选币结果，转换成对应的合约策略的选币结果，来节省手续费
    """
    select_result_path = conf.get_result_folder() / '选币结果.pkl'
    if not select_result_path.exists():
        logger.warning('没有生成选币文件，直接返回')
        return pd.DataFrame(columns=SELECT_RES_COLS)
    all_select_result_df = pd.read_pickle(select_result_path)

    # 不是纯多，且是现货策略
    # 筛选一下选币结果，判断其中的 优先下单标记是什么
    cond1 = all_select_result_df['order_first'] == 'swap'  # 优先下单合约
    cond2 = all_select_result_df['is_spot'] == 1  # 当前币种是现货
    if not all_select_result_df[cond1 & cond2].empty:
        all_kline_df = pd.read_parquet(get_file_path(*ALL_KLINE_PATH_TUPLE))
        # 将含有现货的币种，替换掉其中close价格
        df_swap = all_kline_df[(all_kline_df['is_spot'] == 0) & (all_kline_df['symbol_spot'] != '')]
        no_transfer_df = all_select_result_df[~(cond1 & cond2)]
        all_select_result_df = transfer_swap(all_select_result_df[cond1 & cond2], df_swap)
        all_select_result_df = pd.concat([no_transfer_df, all_select_result_df], ignore_index=True)

    # 删除选币文件，如果要保留可以注释
    if not conf.is_reserved('select'):
        select_result_path.unlink()

    return all_select_result_df


def to_ratio_pivot(df_select: pd.DataFrame, candle_begin_times, columns) -> pd.DataFrame:
    # 转换为仓位比例，index 为时间，columns 为币种，values 为比例的求和
    df_ratio = df_select.pivot_table(
        index='candle_begin_time', columns=columns, values='target_alloc_ratio',
        fill_value=0, aggfunc='sum', observed=True
    )

    # 重新填充为完整的小时级别数据
    df_ratio = df_ratio.reindex(candle_begin_times, fill_value=0)
    return df_ratio


def trim_ratio_delists(df_ratio: pd.DataFrame, end_time: pd.Timestamp, market_dict: dict, trade_type: str):
    """
    ** 删除要下架的币 **
    当币种即将下架的时候，把后续的持仓调整为 0
    :param df_ratio: 仓位比例
    :param end_time: 回测结束时间
    :param market_dict: 所有币种的K线数据
    :param trade_type: spot or swap
    :return: 仓位调整后的比例
    """
    for symbol in df_ratio.columns:
        df_market = market_dict[symbol]
        if len(df_market) < 2:
            continue

        # 没有下架
        last_end_time = df_market['candle_begin_time'].iloc[-1]
        if last_end_time >= end_time:
            continue

        second_last_end_time = df_market['candle_begin_time'].iloc[-2]
        if (df_ratio.loc[second_last_end_time:, symbol].abs() > 1e-8).any():
            logger.warning(f'{trade_type} {symbol} 下架选币权重不为 0，清除 {second_last_end_time} 之后的权重')
            df_ratio.loc[second_last_end_time:, symbol] = 0

    return df_ratio


def agg_strategy_offsets(df_select: pd.DataFrame, stg_conf: StrategyConfig, max_candle_time):
    # 如果没有现货选币结果，就返回空
    if df_select.empty:
        return pd.DataFrame(columns=['candle_begin_time', 'symbol', 'target_alloc_ratio'])

    # 转换spot和swap的选币数据为透视表，以candle_begin_time为index，symbol为columns，values为target_alloc_ratio的sum
    # 注：多策略的相同周期的相同选币，会在这个步骤被聚合权重
    df_ratio = df_select.pivot(index='candle_begin_time', columns='symbol', values='target_alloc_ratio')

    # 构建candle_begin_time序列
    candle_begin_times = pd.date_range(df_select['candle_begin_time'].min(),
                                       max_candle_time,
                                       freq=stg_conf.period_type,
                                       inclusive='both')
    df_ratio = df_ratio.reindex(candle_begin_times, fill_value=0)

    # 多offset的权重聚合
    df_ratio = df_ratio.rolling(stg_conf.hold_period, min_periods=1).sum()

    # 恢复 candle_begin_time, symbol, target_alloc_ratio的df结构
    df_ratio = df_ratio.stack().reset_index(name='target_alloc_ratio')
    df_ratio.rename(columns={'level_0': 'candle_begin_time'}, inplace=True)

    return df_ratio


def agg_multi_strategy_ratio(conf: BacktestConfig, df_select: pd.DataFrame, max_candle_time):
    """
    聚合多offset、多策略选币结果中的target_alloc_ratio
    :param conf: 回测配置
    :param df_select: 选币结果
    :param max_candle_time: 回测最大时间
    :return: 聚合后的df_spot_ratio 和 df_swap_ratio。

    数据结构:
    - index_col为candle_begin_time，
    - columns为symbol，
    - values为target_alloc_ratio的聚合结果

    示例:
                    1000BONK-USDT	1000BTTC-USDT	1000FLOKI-USDT	1000LUNC-USDT	1000PEPE-USDT	1000RATS-USDT	1000SATS-USDT	1000SHIB-USDT	1000XEC-USDT	1INCH-USDT	AAVE-USDT	ACE-USDT	ADA-USDT	    AEVO-USDT   ...
    2021/1/1 00:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 01:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 02:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 03:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 04:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 05:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 06:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 07:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 08:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 09:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    """
    # ====================================================================================================
    # 1. 先针对每个策略的多offset进行聚合
    # ====================================================================================================
    df_spot_select_list = []
    df_swap_select_list = []

    # 如果是D的持仓周期，应该是当天的选币，第二天0点持仓。
    # 按照目前的逻辑，原来自带的begin time是0点
    if conf.is_day_period:
        df_select['candle_begin_time'] = df_select['candle_begin_time'] + pd.Timedelta(hours=23)

    for stg in conf.strategy_list:
        # 裁切当前策略的spot选币结果
        df_select_spot = df_select[(df_select['strategy'] == stg.name) & (df_select['is_spot'] == 1)]
        # 买入现货部分
        _spot_select_long = agg_strategy_offsets(df_select_spot[df_select_spot['方向'] == 1], stg, max_candle_time)
        df_spot_select_list.append(_spot_select_long)
        # 做空现货部分
        _spot_select_short = agg_strategy_offsets(df_select_spot[df_select_spot['方向'] == -1], stg, max_candle_time)
        df_spot_select_list.append(_spot_select_short)

        # 裁切当前策略的swap选币结果
        df_select_swap = df_select[(df_select['strategy'] == stg.name) & (df_select['is_spot'] == 0)]
        # 买入合约部分
        _swap_select_long = agg_strategy_offsets(df_select_swap[df_select_swap['方向'] == 1], stg, max_candle_time)
        df_swap_select_list.append(_swap_select_long)
        # 做空合约部分
        _swap_select_short = agg_strategy_offsets(df_select_swap[df_select_swap['方向'] == -1], stg, max_candle_time)
        df_swap_select_list.append(_swap_select_short)

    df_spot_select = pd.concat(df_spot_select_list, ignore_index=True)
    df_swap_select = pd.concat(df_swap_select_list, ignore_index=True)

    # ====================================================================================================
    # 2. 针对多策略进行聚合
    # ====================================================================================================
    # 构建candle_begin_time序列，不管是D还是H的持仓周期，都以H为准
    # 构建candle_begin_time序列
    start_date = min(
        df_spot_select['candle_begin_time'].min(),
        df_swap_select['candle_begin_time'].min(),
        key=lambda x: x if not pd.isnull(x) else pd.Timestamp.max
    )
    if pd.isna(start_date):
        start_date = max_candle_time - pd.to_timedelta(conf.max_hold_period)

    candle_begin_times = pd.date_range(start_date, max_candle_time, freq=conf.hold_period_type, inclusive='both')

    # 转换spot和swap的选币数据为透视表，以candle_begin_time为index，symbol为columns，values为target_alloc_ratio的sum
    # 注：多策略的相同周期的相同选币，会在这个步骤被聚合权重
    df_spot_ratio = to_ratio_pivot(df_spot_select, candle_begin_times, 'symbol')
    df_swap_ratio = to_ratio_pivot(df_swap_select, candle_begin_times, 'symbol')

    # # 针对下架币的处理
    # df_spot_ratio = trim_ratio_delists(df_spot_ratio, candle_begin_times.max(), spot_dict, 'spot')
    # df_swap_ratio = trim_ratio_delists(df_swap_ratio, candle_begin_times.max(), swap_dict, 'swap')
    logger.debug(f'现货权重: {df_spot_ratio.iloc[-1]}')
    logger.debug(f'合约权重: {df_swap_ratio.iloc[-1]}')

    return df_spot_ratio, df_swap_ratio


# ======================================================================================
# 备用算法入口：差分数组 + 累积和（从回测同步，后续可手动切换测试）
# ======================================================================================
def _accumulate_ratio_by_diff(df_select: pd.DataFrame, hold_steps: int, step_delta: pd.Timedelta,
                              candle_begin_times: pd.DatetimeIndex, symbol_col_map: dict,
                              out_matrix: np.ndarray) -> None:
    """
    使用差分数组在固定步长时间轴上累计目标仓位。

    Args:
        df_select: 单个策略、单个市场、单个方向的选币结果，只保留当前需要累计的记录。
        hold_steps: 当前策略的持仓持续步数；小时策略按小时数传入，日频策略按天数传入。
        step_delta: 聚合时间轴的步长，用于把时间戳映射成差分数组下标。
        candle_begin_times: 当前账户聚合后的完整时间轴，输出矩阵的行顺序必须与其一致。
        symbol_col_map: 币种到输出矩阵列号的映射。
        out_matrix: 原地累加的输出矩阵；函数会直接把本批次累计结果加到这里。

    Returns:
        None。结果直接写入 out_matrix，不额外返回 DataFrame。
    """
    if df_select.empty or hold_steps <= 0 or len(candle_begin_times) == 0:
        return

    subset_times = df_select['candle_begin_time']
    subset_min_time = subset_times.min()
    global_start_time = candle_begin_times[0]
    global_end_time = candle_begin_times[-1]

    # 关键修正：
    # 不能只算到“最后一次信号出现的时间”，否则最后一笔持仓会在尾部被提前截断。
    # 差分数组至少要延伸到“最后一次信号的完整持仓结束时刻”，再与全局时间轴求交集。
    subset_effective_end_time = subset_times.max() + (hold_steps - 1) * step_delta
    subset_effective_end_time = min(subset_effective_end_time, global_end_time)

    overlap_start = subset_min_time if subset_min_time > global_start_time else global_start_time
    overlap_end = subset_effective_end_time
    if overlap_start > overlap_end:
        return

    subset_len = int((subset_effective_end_time - subset_min_time) // step_delta) + 1
    if subset_len <= 0:
        return

    positions = ((subset_times - subset_min_time) // step_delta).to_numpy(dtype=np.int64, copy=False)
    symbols = df_select['symbol'].to_numpy()
    ratios = df_select['target_alloc_ratio'].to_numpy(dtype=np.float64, copy=False)

    local_start = int((overlap_start - subset_min_time) // step_delta)
    local_end = int((overlap_end - subset_min_time) // step_delta)
    global_start = int((overlap_start - global_start_time) // step_delta)
    global_end = global_start + (local_end - local_start)

    codes, uniques = pd.factorize(symbols, sort=False)
    for code_idx, sym in enumerate(uniques):
        col = symbol_col_map.get(sym)
        if col is None:
            continue
        mask = codes == code_idx
        if not mask.any():
            continue
        sym_pos = positions[mask]
        sym_ratios = ratios[mask]
        diff = np.zeros(subset_len + 1, dtype=np.float64)
        np.add.at(diff, sym_pos, sym_ratios)

        # 在固定步长时间轴上，持仓会从信号时刻持续 hold_steps 根 bar。
        # 因此差分的结束点应当落在 “起点 + hold_steps” 的下一格。
        end_pos = sym_pos + hold_steps
        end_pos = np.minimum(end_pos, subset_len)
        np.add.at(diff, end_pos, -sym_ratios)
        series = np.cumsum(diff[:-1])
        out_matrix[global_start:global_end + 1, col] += series[local_start:local_end + 1]


def agg_multi_strategy_ratio_v2(conf: BacktestConfig, df_select: pd.DataFrame, max_candle_time):
    """
    聚合多offset、多策略选币结果中的target_alloc_ratio（差分数组算法版本）
    备用入口，与 agg_multi_strategy_ratio 接口相同，可手动切换测试

    Args:
        conf: 回测配置，包含策略列表、持仓周期粒度以及最大持仓周期等信息。
        df_select: 原始选币结果，至少需要包含 strategy / is_spot / 方向 / target_alloc_ratio 等列。
        max_candle_time: 当前这轮聚合允许覆盖到的最后时间点。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]:
            聚合后的现货仓位透视表和合约仓位透视表；行索引为 candle_begin_time，列为 symbol。
    """
    keep_cols = ['candle_begin_time', 'symbol', 'strategy', 'is_spot', '方向', 'target_alloc_ratio']
    if df_select.empty:
        work_df = pd.DataFrame(columns=keep_cols)
    else:
        work_df = df_select.loc[:, keep_cols].copy()

        # 如果是 D 周期，当前框架约定“当天信号在当天 23 点之后进入持仓”。
        # 因此必须在构建全量时间轴之前先平移时间，否则索引起点会和原生实现不一致。
        if conf.is_day_period:
            work_df['candle_begin_time'] = work_df['candle_begin_time'] + pd.Timedelta(hours=23)

    strategy_names = [stg.name for stg in conf.strategy_list]
    if strategy_names:
        work_df = work_df[work_df['strategy'].isin(strategy_names)]
    else:
        work_df = work_df.iloc[0:0]

    start_date = work_df['candle_begin_time'].min() if not work_df.empty else pd.NaT
    if pd.isna(start_date):
        start_date = max_candle_time - pd.to_timedelta(conf.max_hold_period)

    # 与当前框架原生聚合保持一致：
    # 小时策略输出小时级时间轴，日频策略输出日级时间轴，避免 v2 产物与主逻辑索引语义不一致。
    candle_begin_times = pd.date_range(start_date, max_candle_time, freq=conf.hold_period_type, inclusive='both')

    if work_df.empty:
        df_spot_ratio = pd.DataFrame(index=candle_begin_times, columns=[])
        df_swap_ratio = pd.DataFrame(index=candle_begin_times, columns=[])
        df_spot_ratio.index.name = 'candle_begin_time'
        df_spot_ratio.columns.name = 'symbol'
        df_swap_ratio.index.name = 'candle_begin_time'
        df_swap_ratio.columns.name = 'symbol'
        return df_spot_ratio, df_swap_ratio

    if len(candle_begin_times) >= 2:
        step_delta = candle_begin_times[1] - candle_begin_times[0]
    else:
        step_delta = pd.Timedelta(days=1) if conf.hold_period_type == 'D' else pd.Timedelta(hours=1)

    # 预分配输出矩阵
    spot_symbols = sorted(work_df.loc[work_df['is_spot'] == 1, 'symbol'].unique())
    swap_symbols = sorted(work_df.loc[work_df['is_spot'] == 0, 'symbol'].unique())
    spot_symbol_to_col = {sym: idx for idx, sym in enumerate(spot_symbols)}
    swap_symbol_to_col = {sym: idx for idx, sym in enumerate(swap_symbols)}

    spot_ratio_matrix = np.zeros((len(candle_begin_times), len(spot_symbols)), dtype=np.float64)
    swap_ratio_matrix = np.zeros((len(candle_begin_times), len(swap_symbols)), dtype=np.float64)

    # 按策略聚合（差分数组 + 累积和）
    for stg_conf in conf.strategy_list:
        stg_df = work_df[work_df['strategy'] == stg_conf.name]
        if stg_df.empty:
            continue

        hold_delta = pd.to_timedelta(stg_conf.hold_period)
        hold_steps = int(hold_delta / step_delta)
        if hold_steps <= 0 or hold_steps * step_delta != hold_delta:
            continue

        df_select_spot = stg_df[stg_df['is_spot'] == 1]
        if not df_select_spot.empty:
            _accumulate_ratio_by_diff(
                df_select_spot[df_select_spot['方向'] == 1],
                hold_steps, step_delta, candle_begin_times, spot_symbol_to_col, spot_ratio_matrix
            )
            _accumulate_ratio_by_diff(
                df_select_spot[df_select_spot['方向'] == -1],
                hold_steps, step_delta, candle_begin_times, spot_symbol_to_col, spot_ratio_matrix
            )

        df_select_swap = stg_df[stg_df['is_spot'] == 0]
        if not df_select_swap.empty:
            _accumulate_ratio_by_diff(
                df_select_swap[df_select_swap['方向'] == 1],
                hold_steps, step_delta, candle_begin_times, swap_symbol_to_col, swap_ratio_matrix
            )
            _accumulate_ratio_by_diff(
                df_select_swap[df_select_swap['方向'] == -1],
                hold_steps, step_delta, candle_begin_times, swap_symbol_to_col, swap_ratio_matrix
            )

    eps = 1e-9
    spot_ratio_matrix[np.abs(spot_ratio_matrix) < eps] = 0
    swap_ratio_matrix[np.abs(swap_ratio_matrix) < eps] = 0

    df_spot_ratio = pd.DataFrame(spot_ratio_matrix, index=candle_begin_times, columns=spot_symbols, dtype=np.float64)
    df_swap_ratio = pd.DataFrame(swap_ratio_matrix, index=candle_begin_times, columns=swap_symbols, dtype=np.float64)
    df_spot_ratio.index.name = 'candle_begin_time'
    df_spot_ratio.columns.name = 'symbol'
    df_swap_ratio.index.name = 'candle_begin_time'
    df_swap_ratio.columns.name = 'symbol'

    return df_spot_ratio, df_swap_ratio
