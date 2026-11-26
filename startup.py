"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import gc
import os.path
import platform
import random
import shutil
import subprocess
import sys
import time
import traceback
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import is_debug, error_webhook_url, debug_flag, clean_start, snapshot_path, is_simulate, statistics_config
from core.account_group import is_account_group, load_group, run_group
from core.account_manager import init_system, load_multi_accounts, load_account_units, read_hour_offset_from_config, \
    read_exchange_from_config
from core.utils.commons import sleep_until_run_time, remedy_until_run_time, get_debug_run_time
from core.utils.datatools import check_bmac_update_flag, resolve_bmac_data_path
from core.utils.dingding import send_wechat_work_msg
from core.utils.functions import refresh_diff_time
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_file_path, PROJECT_ROOT
from core.version import version_prompt
from tools.statistics_runtime import StatisticsTasks

# ====================================================================================================
# ** 脚本运行前配置 **
# 主要是解决各种各样奇怪的问题们
# ====================================================================================================
# region 脚本运行前准备
warnings.filterwarnings('ignore')  # 过滤一下warnings，不要吓到老实人

# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)

# 解决pm2在win系统下的乱码问题
os_type = platform.system()
if os_type == 'Windows':  # win执行
    # noinspection PyUnresolvedReferences
    sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
# endregion


# ====================================================================================================
# ** 本脚本的全局变量 **
# ====================================================================================================
# 构建线程池，给统计脚本异步执行
stat_executor = StatisticsTasks(statistics_config['max_workers'], logger.warning)
python_exec = sys.executable  # 获取当前环境下的python解释器，也是给统计脚本用的
from tools.robust40.server import Coordinator, clean_startup, enabled
from tools.robust40.initialization_config import PREWARM_LEAD_SECONDS
server_initializer = Coordinator(PROJECT_ROOT)


# ====================================================================================================
# ** 重要的函数区域 **
# 负责我们保温杯系统平顺执行的核心组件
# ====================================================================================================
def run_statistics_async(run_time, random_time, account_target):
    """
    异步执行统计：单账户统计自身；账户组逐个统计成员，再发送组汇总
    """
    # debug 模式不统计
    if not statistics_config['enabled'] or (is_debug and is_simulate == 'debug'):
        return
    logger.info(f'sleep 一下保证统计脚本在 offset 下单之后开始计算数据···')
    time.sleep(random_time if random_time else 120)  # 提前随机多久，就休息多久，默认2分钟

    account_path = Path(account_target)
    script = get_file_path("core", "utils", f"statistics{'_debug' if is_debug else ''}.py")
    member_paths = list(load_group(account_path)['paths'].values()) if is_account_group(account_path) else [account_path]
    for member_path in member_paths:
        env = {**os.environ, 'X3S_TRADING_ACCOUNT': str(member_path), 'PYTHONPATH': PROJECT_ROOT}
        subprocess.run(
            [sys.executable, script, f'{int(run_time.timestamp())}', member_path.stem],
            check=True, env=env,
        )

    if is_account_group(account_path) and not is_debug:
        subprocess.run(
            [sys.executable, get_file_path('core', 'utils', 'group_statistics.py'),
             f'{int(run_time.timestamp())}', str(account_path)],
            check=True, env={**os.environ, 'PYTHONPATH': PROJECT_ROOT},
        )


def get_unit_info(unit: Path) -> tuple[str, set[str], list[Path]]:
    """
    调度单元的 hour_offset、用到的交易所及成员账户列表（不执行账户文件）
    :return: (hour_offset, {交易所}, [成员路径])
    """
    if is_account_group(unit):
        group = load_group(unit)
        members = list(group['paths'].values())
        return group['hour_offset'], {read_exchange_from_config(path) for path in members}, members
    return read_hour_offset_from_config(unit), {read_exchange_from_config(unit)}, [unit]


def _dispatch_unit_member(account_path: Path, run_time, *extra_args):
    """统一调度账户组成员（含 MODEL server_init=True 与传统账户）"""
    market = resolve_bmac_data_path(read_exchange_from_config(account_path))
    command, worker_env = server_initializer.command(account_path, run_time, market, *extra_args)
    if command is None:
        return
    env = dict(os.environ if worker_env is None else worker_env)
    env['X3S_TRADING_ACCOUNT'] = str(account_path)
    env['PYTHONPATH'] = PROJECT_ROOT
    result = subprocess.run(command, env=env, check=False)
    server_initializer.handle_result(account_path, run_time, market, command, env, result.returncode)


def run_loop() -> datetime | None:
    """
    无限循环这个函数♾️
    :return: 成功执行的话，返回本次运行的时间，否则是None
    """
    divider('本次循环开始', '=')

    # ====================================================================================================
    # 0. 调试相关配置区域
    # ====================================================================================================
    random_time = random.randint(1 * 60, 2 * 60)  # 不建议使用负数。小时级别提前时间可以设置短一点

    _load_units = globals().get('load_account_units', load_multi_accounts)
    _is_group = globals().get('is_account_group', lambda p: False)
    _load_group = globals().get('load_group', None)
    _run_group = globals().get('run_group', None)
    _get_unit_info = globals().get('get_unit_info', None)
    _resolve_market = globals().get('resolve_bmac_data_path', None)
    _read_exchange = globals().get('read_exchange_from_config', None)
    _dispatch_member = globals().get('_dispatch_unit_member', None)
    _send_err = globals().get('send_wechat_work_msg', None)
    _err_hook = globals().get('error_webhook_url', '')

    raw_units = _load_units()
    if not raw_units:
        logger.critical('没有检测到可用的账户配置文件，程序退出...')
        exit(2)

    debug_round = is_debug and is_simulate == 'debug'
    if not debug_round:
        run_time = sleep_until_run_time('5m', if_sleep=True, cheat_seconds=random_time)  # 每五分钟运行一次

    offsets = {}
    unit_exchanges = {}
    prepare_accounts = []
    for unit in raw_units:
        if _is_group(unit) and _get_unit_info is not None:
            try:
                offset, exgs, members = _get_unit_info(unit)
            except Exception as exc:
                msg = f'[{unit.name}] 配置有误，跳过：{exc}'
                logger.error(msg)
                if _send_err is not None:
                    _send_err(msg, _err_hook)
                continue
        elif hasattr(unit, 'is_dir') and unit.is_dir() and not _is_group(unit):
            logger.warning(f'目录 {unit.name} 下没有 _manage.py，不是账户组，跳过')
            continue
        else:
            offset = read_hour_offset_from_config(unit)
            exgs = {_read_exchange(unit)} if _read_exchange is not None else set()
            members = [unit]

        try:
            minute = int(offset.removesuffix('m'))
            if not offset.endswith('m') or not 0 <= minute < 60:
                raise ValueError('hour_offset 应为 0m 至 59m')
            offsets[unit] = minute
            unit_exchanges[unit] = exgs
            prepare_accounts.extend(members)
        except (OSError, ValueError) as exc:
            logger.error(f'跳过账户 {unit.stem}：无法读取有效 hour_offset，{exc}')

    if debug_round:
        if not offsets:
            return None
        run_time = get_debug_run_time(f'{next(iter(offsets.values()))}m')

    # ====================================================================================================
    # 2. 等待数据中心完成数据更新
    # ====================================================================================================
    if not is_debug:
        from config import bmac_data_path
        server_initializer.prepare(prepare_accounts, run_time, bmac_data_path)
    due_units = [unit for unit, minute in offsets.items() if minute == run_time.minute]
    if not due_units:
        logger.debug(f'{run_time} 无匹配账户，跳过行情等待')
        remedy_until_run_time(run_time)
        return None

    _is_model_enabled = globals().get('enabled', lambda p: False)
    _prewarm_lead = globals().get('PREWARM_LEAD_SECONDS', 10)
    if (not debug_round and len(due_units) == 1 and not _is_group(due_units[0])
            and _is_model_enabled(due_units[0]) and hasattr(server_initializer, 'launch_prewarmed_trade')):
        unit = due_units[0]
        wait_s = max(0.0, run_time.timestamp() - float(_prewarm_lead) - time.time())
        if wait_s > 0:
            logger.info(f'{unit.stem}: 预热等待至 run_time - {_prewarm_lead:g}s（{wait_s:.1f}s）...')
            time.sleep(wait_s)
        os.environ["X3S_TRADING_ACCOUNT"] = str(unit)
        os.environ['PYTHONPATH'] = PROJECT_ROOT
        from config import bmac_data_path
        market = _resolve_market(_read_exchange(unit)) if (_read_exchange is not None and _resolve_market is not None) else bmac_data_path
        launched = server_initializer.launch_prewarmed_trade(unit, run_time, market)
        if launched is not None:
            proc, command, worker_env = launched
            rc = proc.wait()
            if server_initializer.handle_result(unit, run_time, market, command, worker_env, rc):
                stat_executor.submit(unit.stem, run_statistics_async, run_time, random_time, unit)
            gc.collect()
            divider('本次循环结束，23秒后进入下一次循环', '=')
            print('\n\n')
            time.sleep(23)
            remedy_until_run_time(run_time)
            return run_time

    exchanges = sorted(set().union(*(unit_exchanges[u] for u in due_units)))
    logger.info(f'检查数据中心 {exchanges or ["default"]}，{run_time}，本轮单元：{", ".join(u.stem for u in due_units)}')
    # 检查数据中心数据是否更新（兼容单参 mock 测试）
    is_data_ready = check_bmac_update_flag(run_time, exchanges) if _read_exchange is not None else check_bmac_update_flag(run_time)
    if not is_data_ready:  # 没有更新好，跳过当前下单
        logger.warning(f'数据中心数据未更新，跳过当前，{run_time}，[调试模式{debug_flag}]')
        logger.debug('ℹ️ 也可以确认一下数据中心的`enabled_hour_offsets`，确认你需要的offsets都被启用了。')
        logger.debug('如果你没有配置过数据中心的这个配置，那请无视上面的这条提示。')
        return None
    logger.ok(f'数据中心数据更新完成，准备选币下单，[调试模式{debug_flag}]')

    # ====================================================================================================
    # 3. 针对每一个单账户或账户组，进行因子计算、选币、下单
    # ====================================================================================================
    """!!! 核心逻辑在这里 !!!"""
    for unit in due_units:
        if _is_group(unit) and _load_group is not None and _run_group is not None:
            _run_group(_load_group(unit), run_time, dispatcher=_dispatch_member)
            stat_executor.submit(unit.name, run_statistics_async, run_time, random_time, unit)
            gc.collect()
            continue

        os.environ["X3S_TRADING_ACCOUNT"] = str(unit)
        os.environ['PYTHONPATH'] = PROJECT_ROOT
        from config import bmac_data_path
        market = _resolve_market(_read_exchange(unit)) if (_read_exchange is not None and _resolve_market is not None) else bmac_data_path
        command, worker_env = server_initializer.command(unit, run_time, market)
        if command is None:
            continue
        result = subprocess.run(command, env=worker_env, check=False)
        if not server_initializer.handle_result(unit, run_time, market, command, worker_env, result.returncode):
            continue
        stat_executor.submit(unit.stem, run_statistics_async, run_time, random_time, unit)
        gc.collect()  # 强制垃圾回收

    # 本次循环结束
    divider('本次循环结束，23秒后进入下一次循环', '=')
    print('\n\n')
    time.sleep(23)
    # 每次运行完发送

    remedy_until_run_time(run_time)  # 休息休息～～～
    return run_time


def check_accounts():
    refresh_diff_time()  # 刷新与交易所的时差
    if is_simulate == 'dry_run':
        logger.info('🔵 [干跑模式] 跳过杠杆、持仓模式及保证金模式修改')
        return
    for account_profile_file in load_multi_accounts():
        account, _ = init_system(
            str(account_profile_file) if len(account_profile_file.parts) > 1 else account_profile_file.stem
        )

        logger.debug(f'🔵 {account.name} 检查杠杆、持仓模式、保证金模式...')
        account.bn.reset_max_leverage()  # 检查杠杆
        account.bn.set_single_side_position()  # 检查并且设置持仓模式：单向持仓
        account.bn.set_multi_assets_margin()  # 检查联合保证金模式
        time.sleep(2)  # 多账户之间，停顿一下
        logger.ok('自检完成')


def prepare_startup_accounts(run_time=None):
    """启动完成时立即检查 MODEL 账户快照并同步上游 .arrow-demand 状态，不等首次 5m 睡眠。"""
    from config import bmac_data_path
    now = datetime.now() if run_time is None else run_time
    server_initializer.prepare(load_multi_accounts(), now, bmac_data_path)


if __name__ == '__main__':
    version_prompt()
    if is_debug:
        logger.debug('🟠' * 16 + ' [调试模式] ' + '🟠' * 16)
    else:
        logger.debug('🟢' * 16 + ' [正式模式] ' + '🟢' * 16)
    logger.info(f'系统初始化，稍等...')
    time.sleep(1)

    # ====================================================================================================
    # 运行前自检和准备
    # ====================================================================================================
    # 显式启用的 MODEL 账户：先验证并保留成对状态，再执行原有清理。
    from tools.robust40.warmup import prepare_startup, restore_startup
    warmup_plans = prepare_startup(load_multi_accounts(), PROJECT_ROOT, clean_start) if not is_debug else []
    if clean_start:
        clean_startup(load_multi_accounts(), PROJECT_ROOT, snapshot_path)
    else:
        logger.critical('!!!!! 注意，你选择了允许缓存的条件下重启，可能会有灾难性后果 !!!!!')
        logger.warning('如果你对此不确定，请立即退出，并删除 `data -> snapshot` 文件夹，再重启。')
        logger.debug('给你7s时间确认...')
        time.sleep(7)

    restore_startup(warmup_plans)

    # 如果不是debug模式准备账户设置
    if not is_debug:
        snapshot_path.mkdir(parents=True, exist_ok=True)
        check_accounts()
        prepare_startup_accounts()
        time.sleep(1)
    else:
        logger.debug('🐞 调试模式 - 跳过账户检查和准备')

    # ====================================================================================================
    # 实盘主程序，电不停我不停
    # ====================================================================================================
    while True:
        try:
            run_loop()
        except Exception as err:
            msg = '系统出错，10s之后重新运行，出错原因: ' + str(err)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            send_wechat_work_msg(msg, error_webhook_url)
            time.sleep(12)  # 休息十一秒钟，再冲
        finally:
            if is_debug and is_simulate == 'debug':
                break
