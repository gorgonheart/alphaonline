"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import config
from core.mef_manager import init_mef_system
from core.model.account_config import AccountConfig
from core.model.backtest_config import MultiEquityBacktestConfig
from core.utils.log_kit import logger
from core.utils.path_kit import get_folder_path, get_file_path, PROJECT_ROOT

account_config_dict = {}


# ====================================================================================================
# ** 系统config初始化相关 **
# ===================================================================================================
def init_system(env_account: str = None) -> tuple[AccountConfig, MultiEquityBacktestConfig]:
    if env_account:
        os.environ["X3S_TRADING_ACCOUNT"] = env_account
        importlib.reload(config)

    mef = init_mef_system(env_account)

    from core.utils.exchange_hub import normalize_symbol_filter_list

    base_config = dict(
        get_kline_num=config.data_config['get_kline_num'],
        # 以下配置和回测过程同步
        rebalance_mode=getattr(config, 'rebalance_mode', None),  # rebalance类型
        min_kline_num=config.data_config['min_kline_num'],
        leverage=config.leverage,
        black_list=normalize_symbol_filter_list(config.black_list),  # 拉黑名单
        white_list=normalize_symbol_filter_list(config.white_list),  # 只交易名单
        is_pure_long=mef.is_pure_long,  # 纯多模式设置
        use_private_instruments=config.is_simulate == 'none',  # 正式模式使用OKX账户实际可交易列表
    )
    config_dict = {**base_config, **config.account_config}

    cfg: AccountConfig = AccountConfig(config.account_name, **config_dict)
    cfg.use_spot = mef.use_spot

    # 初始化交易所的配置
    _apikey, _secret = cfg.api_key, cfg.secret
    if _apikey and _secret:
        # 更新交易所基础配置
        config.exchange_basic_config['apiKey'], config.exchange_basic_config['secret'] = _apikey, _secret

        # 初始化交易所相关接口
        cfg.init_exchange(config.exchange_basic_config)

    # 把成功初始化的对象放入到返回结果
    return cfg, mef


def get_usable_account_config_dict() -> tuple[AccountConfig, MultiEquityBacktestConfig]:
    from core.utils.functions import update_accounts_info

    acct_conf, mef = init_system()

    if config.is_debug:
        logger.debug('🐞 调试模式 - 不更新账户信息')
        return acct_conf, mef
    else:
        config_dict = update_accounts_info({config.account_name: acct_conf})

        # 判断是否没能成功读取任一账户
        if not len(config_dict.keys()):  # 如果update_account_info数据为空，表示更新账户信息失败
            logger.warning(f'所有账户更新信息失败')

        return config_dict[config.account_name]


def load_multi_accounts():
    """
    读取 accounts 文件夹下的所有账户配置文件，包含账户组内的成员
    下划线开头的文件、下划线开头的账户组目录都会忽略；账户名（文件名）全局不允许重复
    """
    root = get_folder_path('accounts', as_path_type=True)
    account_files = [
        f for f in sorted(root.rglob('*.py')) if
        f.is_file() and not f.stem.startswith('_') and not f.relative_to(root).parts[0].startswith('_')]

    # 账户名对应 data/<账户名>/ 目录，重名会互相覆盖
    names = [f.stem for f in account_files]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f'账户文件名重复：{duplicates}，账户名（含账户组成员）必须全局唯一')
    return account_files


def load_account_units():
    """
    读取本轮的调度单元：accounts 下一级的账户文件（单账户）和子目录（账户组），下划线开头的忽略
    """
    root = get_folder_path('accounts', as_path_type=True)
    load_multi_accounts()  # 提前检查账户重名
    return [
        path for path in sorted(root.iterdir()) if
        not path.name.startswith('_') and (path.is_dir() or path.suffix == '.py')]


def read_config_value(config_file_path: Path, key: str, default: str) -> str:
    """不执行账户文件，按 `'key': 'value'` 的格式读取字符串配置，忽略注释行"""
    pattern = rf"""^[^#\n]*['"]{key}['"]\s*:\s*['"]([^'"]+)['"]"""
    match = re.search(pattern, config_file_path.read_text(encoding='utf-8', errors='ignore'), re.MULTILINE)
    return match.group(1) if match else default


def read_hour_offset_from_config(config_file_path: Path) -> str:
    return read_config_value(config_file_path, 'hour_offset', '0m')


def read_exchange_from_config(config_file_path: Path) -> str:
    return read_config_value(config_file_path, 'exchange_name', config.exchange_name).lower()


def run_account_exec(account_path: Path, run_time, *args):
    """
    在独立子进程中执行一个账户（core/account_exec.py），账户之间的 config、缓存互不影响
    :param account_path: 账户配置文件
    :param run_time: 本轮运行时间
    :param args: 额外参数，如 --stage calc
    """
    env = {**os.environ, 'X3S_TRADING_ACCOUNT': str(account_path), 'PYTHONPATH': PROJECT_ROOT}
    subprocess.run(
        [sys.executable, get_file_path('core', 'account_exec.py'), f'{int(run_time.timestamp())}', *args],
        check=True, env=env
    )
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
