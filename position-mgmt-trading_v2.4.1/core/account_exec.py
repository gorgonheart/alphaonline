"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import argparse
import traceback
import warnings
from datetime import datetime

import pandas as pd

from config import is_debug, error_webhook_url
from core.account_group import save_handoff, get_handoff_folder
from core.account_manager import init_system
from core.real_trading import run_by_account, calc_by_account, trade_by_account, is_archive_round, DEBUG_INITIAL_USDT
from core.utils.runtime_data import save_runtime_data_archive
from core.utils.dingding import send_wechat_work_msg
from core.utils.functions import refresh_diff_time
from core.utils.log_kit import logger, divider

# ====================================================================================================
# ** 脚本运行前配置 **
# 主要是解决各种各样奇怪的问题们
# ====================================================================================================
warnings.filterwarnings('ignore')  # 过滤一下warnings，不要吓到老实人

# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)


def parse_args():
    """
    命令行参数：
    - 单账户：account_exec.py <时间戳>
    - 账户组成员：account_exec.py <时间戳> --stage calc / account_exec.py <时间戳> --stage trade --scale <k>
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('timestamp', type=int, help='本轮运行时间戳')
    parser.add_argument('--stage', choices=['all', 'calc', 'trade'], default='all',
                        help='all：单账户完整流程；calc：账户组计算轮；trade：账户组下单轮')
    parser.add_argument('--scale', type=float, default=1.0, help='账户层动态杠杆 k，仅 trade 使用')
    return parser.parse_args()


def prepare_account(account_config) -> bool:
    """更新、检查账户信息"""
    if is_debug:
        logger.debug('🐞调试模式 - 不更新账户信息')
        return True
    account_config.bn.reset_max_leverage()  # 重置页面杠杆（新币可能会没有被设置过）
    is_ready = account_config.update_account_info(is_operate=True)
    if is_ready is None or not is_ready:
        logger.info(f'{account_config.name} 更新没有准备好，跳过当前账户')
        return False
    return True


def run_calc_stage(account_config, me_conf, run_time):
    """账户组计算轮：选币、仓管，保存交接数据；下单轮会覆盖共享缓存，所以归档在这里做"""
    if not prepare_account(account_config) or not calc_by_account(account_config, me_conf, run_time):
        return
    equity = DEBUG_INITIAL_USDT if is_debug else account_config.spot_equity + account_config.swap_equity
    save_handoff(account_config.name, run_time, equity)
    logger.ok(f'[{account_config.name}] 已保存交接数据，权益 {equity:,.2f} USDT')

    if not is_debug and is_archive_round(run_time):
        save_runtime_data_archive(account_config, me_conf, run_time)


def run_trade_stage(account_config, me_conf, run_time, scale: float):
    """账户组下单轮：读取交接权重，按 杠杆 × k 下单"""
    # 下单轮是新的进程，重新对表并获取最新币种信息（计算轮获取的只在计算轮进程中）
    refresh_diff_time()
    account_config.bn.fetch_market_info('swap')
    account_config.bn.fetch_market_info('spot')
    trade_by_account(account_config, me_conf, run_time,
                     ratio_folder=get_handoff_folder(account_config.name), scale=scale)


def main() -> datetime | None:
    # ====================================================================================================
    # 1. 启动时间
    # ====================================================================================================
    logger.info('准备中...')
    args = parse_args()
    run_time = datetime.fromtimestamp(args.timestamp)
    logger.info(f'启动时间：{run_time}，阶段：{args.stage}')

    # ====================================================================================================
    # 2. 加载账户
    # ====================================================================================================
    account_config, me_conf = init_system()
    divider(f'🚀 [{account_config.name}] 开始', '+')

    # ====================================================================================================
    # 3. 执行
    # !!! 核心逻辑在这里 !!!
    # ====================================================================================================
    if args.stage == 'calc':
        run_calc_stage(account_config, me_conf, run_time)
    elif args.stage == 'trade':
        run_trade_stage(account_config, me_conf, run_time, args.scale)
    elif prepare_account(account_config):
        run_by_account(account_config, me_conf, run_time)

    divider(f'🏁 [{account_config.name}] 完成', '+')

    return run_time


if __name__ == '__main__':
    try:
        main()
    except Exception as err:
        msg = '系统出错，10s之后重新运行，出错原因: ' + str(err)
        logger.error(msg)
        logger.debug(traceback.format_exc())
        send_wechat_work_msg(msg, error_webhook_url)
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
