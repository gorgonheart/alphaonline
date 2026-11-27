"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from tqdm import tqdm

from config import utc_offset, bmac_data_path, bmac_exginfo_path, stable_symbol, exchange_data_paths
from core.model.backtest_config import BacktestConfig
from core.utils.exchange_hub import get_dummy_client
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path, get_folder_path


def check_flags(run_time, flag_paths: List[Path]):
    # 检查 flag 是否都已经 ready
    while True:
        if all(flag_path.exists() for flag_path in flag_paths):
            time.sleep(3)
            return True

        # 当前时间是否超过run_time
        if datetime.now() > run_time + timedelta(minutes=4):
            # 如果当前时间超过run_time半小时，表示已经错过当前run_time的下单时间，可能数据中心更新数据失败，没有生成flag文件
            break
        time.sleep(1)

    return False


def check_bmac_update_flag(run_time, exchanges=None):
    """
    检查数据中心是否完成本轮更新
    :param run_time: 本轮运行时间
    :param exchanges: 需要等待的交易所列表，不传时只检查当前 config 的数据中心
    """
    minute = run_time.minute
    ts = int(run_time.timestamp())

    # 配置了 exchange_data_paths 的交易所用各自的数据中心，其余用当前 config 的数据中心
    data_paths = {Path(exchange_data_paths[exchange]) / bmac_data_path.name if exchange in exchange_data_paths
                  else bmac_data_path for exchange in exchanges or []} or {bmac_data_path}
    flag_paths = []
    for data_path in data_paths:
        spot_ready_file_path = data_path / f'{minute}m' / f"spot_dict_{ts}.ready"
        swap_ready_file_path = data_path / f'{minute}m' / f"swap_dict_{ts}.ready"
        logger.debug(f'🛂 spot_ready_file={spot_ready_file_path}')
        logger.debug(f'🛂 swap_ready_file={swap_ready_file_path}')
        flag_paths.extend([spot_ready_file_path, swap_ready_file_path])
    return check_flags(run_time, flag_paths)


def check_bmac_pivot_flag(run_time):
    minute = run_time.minute
    ts = int(run_time.timestamp())
    logger.debug(f'🌐 {run_time}需要的pivot数据准备中...')
    spot_ready_file_path = bmac_data_path / f'{minute}m' / f"market_pivot_spot_{ts}.ready"
    swap_ready_file_path = bmac_data_path / f'{minute}m' / f"market_pivot_swap_{ts}.ready"
    # logger.debug(f'pivot_spot_ready_file={spot_ready_file_path}')
    # logger.debug(f'pivot_swap_ready_file={swap_ready_file_path}')
    return check_flags(run_time, [spot_ready_file_path, swap_ready_file_path])


def read_bmac_market_pivot(hour_offset, symbol_type):
    market_piovt_dict = {}
    for file_path in (bmac_data_path / hour_offset).rglob(f'market_pivot_{symbol_type}_*.pkl'):
        for key, value in pd.read_pickle(file_path).items():
            if key not in market_piovt_dict:
                market_piovt_dict[key] = []
            market_piovt_dict[key].append(value)

    for key, df_list in market_piovt_dict.items():
        market_piovt_dict[key] = pd.concat(market_piovt_dict[key], copy=False).sort_index()

    return market_piovt_dict


def snapshot_bmac_exginfo():
    """
    将数据中心的 exginfo_spot.pkl / exginfo_swap.pkl 快照到本地 cache 目录。

    在 run_by_account 开头调用一次，用于规避数据中心重写 pkl 时可能出现的
    读写 race（EOFError: Ran out of input）。任一文件快照失败时打 warning
    并继续，由 read_bmac_exginfo 的数据中心兜底路径接手。
    """
    cache_dir = get_folder_path('data', 'cache', as_path_type=True)
    for symbol_type in ('spot', 'swap'):
        src = bmac_exginfo_path / f'exginfo_{symbol_type}.pkl'
        dst = cache_dir / f'exginfo_{symbol_type}.pkl'
        try:
            shutil.copy(src, dst)
        except Exception as e:
            logger.warning(f'快照 exginfo_{symbol_type}.pkl 失败: {e}')


def read_bmac_exginfo(symbol_type: str) -> pd.DataFrame:
    """
    读取 exginfo DataFrame，优先读本地 cache 快照，失败则降级读数据中心。

    :param symbol_type: 'spot' 或 'swap'
    :return: exginfo DataFrame（保持数据中心原始结构，调用方自行 set_index）
    """
    cache_dir = get_folder_path('data', 'cache', as_path_type=True)
    local_path = cache_dir / f'exginfo_{symbol_type}.pkl'
    source_path = bmac_exginfo_path / f'exginfo_{symbol_type}.pkl'

    if local_path.exists():
        try:
            return pd.read_pickle(local_path)
        except Exception as e:
            logger.warning(f'读取本地 exginfo 快照失败（{local_path.name}），降级读数据中心: {e}')

    return pd.read_pickle(source_path)


def align_spot_swap_mapping(df, column_name, n):
    """
    处理spot和swap的映射关系
    :param df: 原始k线数据
    :param column_name: 需要处理的列
    :param n: 需要调整映射的周期数量
    :return: 调整好的k线数据
    """
    # 创建新组标识列
    df['is_new_group'] = (df[column_name].ne('') & df[column_name].shift().eq('')).astype(int)
    # 累积求和生成组号
    df['group'] = df['is_new_group'].cumsum()
    # 将空字符串对应的组号设为NaN
    df.loc[df[column_name].eq(''), 'group'] = np.nan
    # 通过 groupby 添加 grp_seq
    df['grp_seq'] = df.groupby('group').cumcount()
    # 过滤条件并修改前 n 行
    df.loc[df['grp_seq'] < n, column_name] = ''

    # 删除辅助列
    df.drop(columns=['is_new_group', 'group', 'grp_seq'], inplace=True)

    return df


def read_and_merge_bmac_data(conf: BacktestConfig, symbol_type: str, df: pd.DataFrame, run_time):
    """
    读取k线数据，并且合并三方数据
    :param conf:  账户配置
    :param symbol_type: 币种类型
    :param df:          k线数据
    :param run_time:   实盘运行时间
    :return:
    """
    if df is None or df.empty:
        return None, None
    symbol = df['symbol'].iloc[-1]  # 获取币种名称
    if '-' in symbol:  # OKX 的原生 instId（如 USDC-USDT / BTC-USDT-SWAP），取基础币种判断
        if symbol.split('-')[0] in stable_symbol:  # 稳定币不参与选币
            return symbol, None
    else:
        if symbol.endswith('USDT') and symbol[:-4] in stable_symbol:  # 稳定币不参与选币
            return symbol, None
        if symbol.endswith('USDC') and symbol[:-4] in stable_symbol:  # 稳定币不参与选币
            return symbol, None
    if symbol in conf.black_list:  # 黑名单币种直接跳过
        return symbol, None
    if conf.white_list and symbol not in conf.white_list:  # 不是白名单的币种跳过
        return symbol, None

    # TODO: 优化数据结构，节省掉这里的排序和去重操作
    df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)  # 去重保留最新的数据
    df.sort_values('candle_begin_time', inplace=True)  # 通过candle_begin_time排序
    df.dropna(subset=['symbol'], inplace=True)

    df['first_candle_time'] = df['first_candle_time'].dt.tz_localize(None)
    df['last_candle_time'] = df['last_candle_time'].dt.tz_localize(None)
    df['candle_begin_time'] = df['candle_begin_time'].dt.tz_localize(None)
    df = df[df['candle_begin_time'] + pd.Timedelta(hours=utc_offset) < run_time]  # 根据run_time过滤一下时间

    # 不能踢掉历史数据不够的情况
    if df.empty or df.shape[0] < conf.min_kline_num:
        return symbol, None

    # 需要处理对于重复上下架的币种
    df = align_spot_swap_mapping(df, f"symbol_{'spot' if symbol_type == 'swap' else 'swap'}", conf.min_kline_num)

    df = df[-conf.get_kline_num():]  # 根据config配置，控制内存中币种的数据，可以节约内存，加快计算速度

    df['symbol_swap'].fillna(value='', inplace=True)
    df['symbol_spot'].fillna(value='', inplace=True)

    # 重置索引并且返回
    return symbol, df.reset_index(drop=True)


def load_bmac_data(symbol_type, run_time, conf: BacktestConfig):
    """
    加载数据
    :param symbol_type: 数据类型
    :param run_time:  实盘的运行时间
    :param conf:  账户配置
    :return:
    """
    hour_offset = f'{run_time.minute}m'
    file_list = get_file_path(bmac_data_path, hour_offset, as_path_type=True).rglob(f'{symbol_type}_dict_batch*.pkl')

    # 获取当前可交易的币种列表，用于过滤已下架或不可交易或传统资产的币种
    symbol_list = get_dummy_client().get_market_info(symbol_type).get('symbol_list', [])

    results = dict()
    for file_path in file_list:
        data_dict = pd.read_pickle(file_path)

        for _df in tqdm(data_dict.values(), desc=f'💿 {file_path.stem}数据'):
            # 使用 market_info 的 symbol_list 过滤不可交易的币种
            if symbol_list and _df is not None and not _df.empty:
                symbol = _df['symbol'].iloc[-1]
                if symbol not in symbol_list:
                    continue

            symbol, df_candle = read_and_merge_bmac_data(conf, symbol_type, _df, run_time)
            if df_candle is not None:
                results[symbol] = df_candle

    return results


# ===============================================================================================================
# 额外数据源
# ===============================================================================================================
def merge_data(df: pd.DataFrame, data_name: str, save_cols: List[str], symbol: str = '') -> dict[str, pd.Series]:
    """
    导入数据，最终只返回带有同index的数据
    :param df: （只读）原始的行情数据，主要是对齐数据用的
    :param data_name: 数据中心中的数据英文名
    :param save_cols: 需要保存的列
    :param symbol: 币种
    :return: 合并后的数据
    """
    import core.data_bridge as db
    from config import data_source_dict

    func_name, file_path = data_source_dict[data_name]

    if hasattr(db, func_name):
        extra_df: pd.DataFrame = getattr(db, func_name)(file_path, df, save_cols, symbol)
    else:
        print(f'⚠️ 未实现数据源：{data_name}')
        return {col: pd.Series([np.nan] * len(df)) for col in save_cols}

    if extra_df is None or extra_df.empty:
        return {col: pd.Series([np.nan] * len(df)) for col in save_cols}

    return {col: extra_df[col] for col in save_cols}


def check_cfg():
    """
    检查 data_source_dict 配置
    检查加载数据源函数是否存在
    检查数据源文件是否存在
    :return:
    """
    import core.data_bridge as db
    from config import data_source_dict
    for key, value in data_source_dict.items():
        func_name, file_path = value
        if not hasattr(db, func_name):
            raise Exception(f"【{key}】加载数据源方法未实现：{func_name}")

        if not (file_path and Path(file_path).exists()):
            raise Exception(f"【{key}】数据源文件不存在：{file_path}")

    print('✅ data_source_dict 配置检查通过')


def check_factor(factors: list):
    """
    检查因子中的配置
    检查是否有 extra_data_dict
    检查 extra_data_dict 中的数据源是否在 data_source_dict 中

    因子中的外部数据使用案例:

    extra_data_dict = {
        'coin-cap': ['circulating_supply']
    }

    :param factors:
    :return:
    """
    from core.utils.factor_hub import FactorHub
    for factor_name in factors:
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if not (hasattr(factor, 'extra_data_dict') and factor.extra_data_dict):
            raise Exception(f"未找到【{factor_name}】因子中 extra_data_dict 配置")

        for data_source in factor.extra_data_dict.keys():
            from config import data_source_dict
            if data_source not in data_source_dict:
                raise Exception(f"未找到 extra_data_dict 配置的数据源：{data_source}")

    print(f'✅ {factors} 因子配置检查通过')
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
