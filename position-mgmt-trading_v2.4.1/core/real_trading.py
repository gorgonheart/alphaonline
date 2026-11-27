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
from pathlib import Path

import pandas as pd

from config import is_debug, runtime_folder, simulator_config, is_simulate, snapshot_path, lookback_save_interval_hours
from core.equity import calc_equity
from core.model.account_config import AccountConfig
from core.model.backtest_config import MultiEquityBacktestConfig, BacktestConfig
from core.position import calc_target_position
from core.utils.datatools import snapshot_bmac_exginfo
from core.utils.functions import refresh_diff_time, save_symbol_order, save_final_select_results, save_position_results, \
    save_performance_df_csv, save_order_history
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_folder_path
from core.utils.runtime_data import save_runtime_data_archive
from core.utils.statis_func import save_position_snapshot


DEBUG_INITIAL_USDT = 1_0000  # 调试模式的模拟初始资金


# ====================================================================================================
# ** 流程函数 **
# 主进程 -> run_loop -> run_by_account
# 调度逻辑在 run_loop 函数中
# 核心逻辑在 run_by_account 函数中，分为两段：
# - calc_by_account：选币、仓管，算出最终权重（不撤单、不下单）
# - trade_by_account：按最终权重计算目标仓位并下单
# 账户组的成员在两段之间插入组合层计算（见 core/account_group.py），单账户直接顺序执行
# ====================================================================================================
def is_archive_round(run_time) -> bool:
    """本轮是否归档实盘数据"""
    return (run_time.hour % lookback_save_interval_hours == 0) if lookback_save_interval_hours != 0 else False


def run_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time):
    if not calc_by_account(acct_conf, me_conf, run_time):
        return
    trade_by_account(acct_conf, me_conf, run_time)

    # ===归集本轮实盘运行数据
    if is_debug:
        return
    if is_archive_round(run_time):
        save_runtime_data_archive(acct_conf, me_conf, run_time)
    else:
        logger.info(f'[{acct_conf.name}] 本轮跳过实盘数据归档')


def calc_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time) -> bool:
    """
    选币、仓管、聚合、仓位限制，最终权重保存到 runtime_folder
    :return: 是否完成计算（不是本账户的 offset 时返回 False）
    """
    # ====================================================================================================
    # 0. 调试相关配置区域
    # ====================================================================================================
    # 和交易所对一下表
    refresh_diff_time()

    # 根据配置的hour offset控制本轮是否下单
    if run_time.minute != acct_conf.hour_offset_minute:
        logger.warning(f'[{acct_conf.name}] 当前不是需要下单的时间，跳过...')
        return False
    else:
        print(acct_conf)
        logger.warning(f'[{acct_conf.name}] 启动...')

    # ====================================================================================================
    # 归档频率控制：非归档轮次将 lookback_days 置零，降低实盘增量运算开销
    # ====================================================================================================
    should_save_archive = is_archive_round(run_time)
    if should_save_archive:
        logger.info(f'[{acct_conf.name}] 本轮为归档轮次 (间隔={lookback_save_interval_hours}h)，保留原 lookback_days')
    else:
        for conf in me_conf.factory.config_list:
            conf.lookback_days = 0
        logger.info(f'[{acct_conf.name}] 本轮非归档轮次 (间隔={lookback_save_interval_hours}h)，已将 lookback_days 置 0')

    # 清理一下运行过程中的缓存数据
    runtime_cache_path = get_folder_path('data', 'runtime', as_path_type=True)
    if runtime_cache_path.exists():
        shutil.rmtree(runtime_cache_path, ignore_errors=True)
    runtime_cache_path.mkdir(parents=True, exist_ok=True)

    cache_path = get_folder_path('data', 'cache', as_path_type=True)
    if cache_path.exists():
        shutil.rmtree(cache_path, ignore_errors=True)
    cache_path.mkdir(parents=True, exist_ok=True)

    # 快照数据中心 exginfo 到本地 cache，规避数据中心重写 pkl 时的 race
    snapshot_bmac_exginfo()

    # 更新一下交易所的行情数据，获取最新币种信息，放置在缓存中
    logger.info(f'[{acct_conf.name}] 更新市场数据...')
    acct_conf.bn.fetch_market_info('swap')
    acct_conf.bn.fetch_market_info('spot')

    # =====保存持仓快照（在下单前执行，确保能捕获到所有持仓币种）
    save_position_snapshot(acct_conf, run_time)

    # ====================================================================================================
    # ** 1. 初始化 **
    # 根据 config.py 中的配置，初始化回测
    # ====================================================================================================
    # 记录一下时间戳
    r_time = time.time()

    # ====================================================================================================
    # ** 2. 子策略回测 **
    # 运行子策略回测，计算每一个子策略的资金曲线
    # 💡小技巧：如果你仓位管理的子策略不变化，调试的时候可以注释这个步骤，可以加快调试的速度
    # ====================================================================================================
    me_conf.backtest_strategies(run_time)

    # ====================================================================================================
    # ** 3. 整理子策略的资金曲线 **
    # 获取所有子策略的资金曲线信息，并且针对仓位管理策略做周期转换，并计算因子
    # ====================================================================================================
    me_conf.process_equities(run_time, hour_offset=acct_conf.hour_offset)

    # ====================================================================================================
    # ** 4. 计算仓位比例 **
    # 仓位管理策略接入，计算每一个时间周期中，子策略应该持仓的资金比例
    # ====================================================================================================
    divider('计算仓位比例', sep='-')
    s_time = time.time()
    pos_ratio = me_conf.calc_ratios()
    logger.ok(f'完成计算仓位比例，花费时间：{time.time() - s_time:.3f}秒')

    # ====================================================================================================
    # ** 5. 聚合选币结果 **
    # 根据子策略的资金比例，重新聚合成一个选币结果，及对应周期内币种的资金分配
    # ====================================================================================================
    df_spot_ratio, df_swap_ratio = me_conf.agg_pos_ratio(pos_ratio)
    # ====================================================================================================
    # ** 5.1. 应用仓位限制 **
    # 处理超过rate_limit的权重，将超出部分分配给fill_strategy的选币结果
    # ====================================================================================================
    df_spot_ratio, df_swap_ratio = me_conf.apply_position_limits(df_spot_ratio, df_swap_ratio)
    logger.ok(f'完成仓位限制，已花费时间{time.time() - s_time:.3f}秒')

    df_spot_ratio.to_pickle(runtime_folder / 'df_spot_ratio.pkl')
    df_swap_ratio.to_pickle(runtime_folder / 'df_swap_ratio.pkl')

    df_swap_ratio.iloc[-1][df_swap_ratio.iloc[-1] > 0].to_csv(runtime_folder / f'swap_ratio_{run_time.strftime("%Y%m%d_%H%M")}.csv')
    df_spot_ratio.iloc[-1][df_spot_ratio.iloc[-1] > 0].to_csv(runtime_folder / f'spot_ratio_{run_time.strftime("%Y%m%d_%H%M")}.csv')
    logger.ok(f'[{acct_conf.name}] 完成权重计算，花费时间：{time.time() - r_time:.2f}s')
    return True


def trade_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time,
                     ratio_folder: Path = runtime_folder, scale: float = 1.0):
    """
    按最终权重计算目标仓位并下单
    :param acct_conf: 账户配置
    :param me_conf: 仓管配置
    :param run_time: 运行时间
    :param ratio_folder: 最终权重目录，账户组成员从交接目录读取
    :param scale: 账户层动态杠杆 k，单账户为 1
    """
    r_time = time.time()
    df_spot_ratio = pd.read_pickle(ratio_folder / 'df_spot_ratio.pkl')
    df_swap_ratio = pd.read_pickle(ratio_folder / 'df_swap_ratio.pkl')

    # 撤销所有币种挂单
    if is_debug:
        logger.debug(f'🐞 调试模式 - 跳过撤单\n')
    else:
        acct_conf.bn.cancel_all_swap_orders()  # 合约撤单
        acct_conf.bn.cancel_all_spot_orders()  # 现货撤单

    # ====================================================================================================
    # ** 6. 计算目标仓位 **
    # 根据目标选币权重，计算下单量
    # ====================================================================================================
    divider('开始计算目标仓位', sep='-')
    s_time = time.time()
    if is_debug:
        acct_conf.swap_equity = 0
        acct_conf.spot_equity = DEBUG_INITIAL_USDT
        logger.warning(
            f'[🐞调试] 账户名称: {acct_conf.name}, 模拟现货资金：{DEBUG_INITIAL_USDT}，模拟合约资金：0')

    position_results = calc_target_position(acct_conf, df_spot_ratio, df_swap_ratio, is_debug=is_debug, scale=scale)
    logger.info(f'目标仓位：{position_results[position_results["目标持仓量"] != 0]}\n')
    logger.ok(f'完成计算目标仓位，花费时间： {time.time() - s_time:.3f}秒')

    # ====================================================================================================
    # 6.1 按目标仓位精调页面杠杆（避免 binance 「目标 notional 超过当前杠杆档容量」拒单）
    # ====================================================================================================
    if not is_debug:
        divider('按目标仓位精调页面杠杆', sep='-')
        s_time = time.time()
        swap_targets = position_results[
            (position_results['symbol_type'] == 'swap') & (position_results['目标持仓量'] != 0)
        ]
        if not swap_targets.empty:
            target_notional_dict = (
                swap_targets['目标持仓量'].abs() * swap_targets['price']
            ).set_axis(swap_targets['symbol']).to_dict()
            acct_conf.bn.adjust_leverage_by_target_notional(target_notional_dict)
            logger.info(f'精调页面杠杆操作结束')
        else:
            logger.info('目标仓位中无 swap 行，跳过页面杠杆精调')
        logger.ok(f'完成按目标仓位精调页面杠杆，花费时间： {time.time() - s_time:.3f}秒')

    # ====================================================================================================
    # 7. 计算下单信息
    # ====================================================================================================
    divider('计算下单信息', sep='-')
    symbol_order = acct_conf.calc_order_amount(position_results)
    logger.info(f'下单信息：{symbol_order}\n')

    logger.ok(f'{acct_conf.name}目标仓位计算消耗时间：{time.time() - r_time:.2f}s，准备下单...')
    # 调试模式，打印下单信息之后即可退出程序
    if is_debug:
        if is_simulate == 'simulate':
            snapshot_bmac_exginfo()  # 账户组下单轮时 cache 可能已被其他成员覆盖，重新快照当前交易所的 exginfo
            sim_conf = BacktestConfig(acct_conf.name, **simulator_config)
            account_df = calc_equity(sim_conf, run_time, acct_conf.leverage * scale, account_name=acct_conf.name,
                                     ratio_folder=ratio_folder)
            save_performance_df_csv(sim_conf, 资金曲线=account_df)
            shutil.rmtree(snapshot_path, ignore_errors=True)
        return

    # ====================================================================================================
    # 8. 下单
    # ====================================================================================================
    spot_sell_success, spot_sell_failures = acct_conf.proceed_spot_order(
        symbol_order, df_spot_ratio.empty, is_only_sell=True,
        run_time=run_time
    )
    swap_success, swap_failures = acct_conf.proceed_swap_order(symbol_order, run_time)
    spot_buy_success, spot_buy_failures = acct_conf.proceed_spot_order(
        symbol_order, df_spot_ratio.empty, is_only_sell=False,
        run_time=run_time
    )

    # ====================================================================================================
    # 9. 资金归集
    # ====================================================================================================
    acct_conf.bn.collect_asset()

    # 基础稳定币调整
    acct_conf.bn.adjust_base_margin()

    # ====================================================================================================
    # 10. 记录下单数据
    # ====================================================================================================
    save_position_results(position_results, run_time, acct_conf.name)

    # ===保存选币数据(平均一天 2G 的数据，最多保留 3 天)
    save_final_select_results(run_time, me_conf, acct_conf.name, max_file_limit=24 * 3)

    # ===保存下单数据
    save_symbol_order(symbol_order, run_time, acct_conf.name)

    # ===保存成交回执（最近 1h 成交额统计的数据底座）
    save_order_history(symbol_order, run_time, acct_conf.name,
                       spot_sell_success + spot_buy_success, swap_success)
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
