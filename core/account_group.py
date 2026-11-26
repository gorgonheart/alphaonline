"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import json
import math
import traceback
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

import pandas as pd

from config import error_webhook_url, runtime_folder
from core.account_manager import run_account_exec, read_hour_offset_from_config
from core.utils.dingding import send_wechat_work_msg
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_folder_path

# ====================================================================================================
# ** 实盘账户组 **
# accounts 下的一个子目录 = 一个账户组，目录内 _manage.py 配置目标资金比例，其余 .py 是成员（格式与单账户一致）
# 每轮流程（对照回测 docs/账户级组合回测蓝图.md，规则见 docs/实盘账户组设计.md）：
# 1. 计算轮：逐成员子进程选币、仓管，保存最终权重和账户权益 E_i 到交接目录，不撤单、不下单
# 2. 组合层：k_i = min(ΣE × w_i / E_i, max_scale)，偏离超过阈值时发送划转提醒（实盘不自动划转）
# 3. 下单轮：逐成员子进程按 杠杆 × k_i 计算目标仓位并下单
# ====================================================================================================
MANAGE_FILE = '_manage.py'
MAX_SCALE_LIMIT = 2  # 账户层动态杠杆 k 的上限不允许超过 2
FLOAT_EPS = 1e-12
THRESHOLD_MODES = ('absolute', 'relative')  # 划转阈值口径：偏离的百分点 / 偏离占目标比例的比例（与回测一致）


# ====================================================================================================
# ** 组配置 **
# ====================================================================================================
def is_account_group(path: Path) -> bool:
    return path.is_dir() and (path / MANAGE_FILE).is_file()


def load_group(group_path: Path) -> dict:
    """
    读取并检查账户组配置
    :param group_path: 账户组目录
    :return: dict(name, members{成员名: 目标比例}, paths{成员名: 配置路径}, threshold, threshold_mode, max_scale,
                  webhook 组汇总机器人, transfer_webhook 划转提醒/整组跳过机器人, hour_offset)
    """
    manage_path = group_path / MANAGE_FILE
    spec = spec_from_file_location('account_group_manage', manage_path)
    manage = module_from_spec(spec)
    spec.loader.exec_module(manage)

    members = dict(getattr(manage, 'members', {}))
    threshold = float(getattr(manage, 'rebalance_threshold', 0.05))
    threshold_mode = getattr(manage, 'rebalance_threshold_mode', 'absolute')
    max_scale = float(getattr(manage, 'max_scale', MAX_SCALE_LIMIT))

    # ====================================================================================================
    # 1. 检查组配置
    # ====================================================================================================
    if not members:
        raise ValueError(f'{manage_path} 未配置 members')
    if any(not math.isfinite(w) or w <= 0 for w in members.values()):
        raise ValueError(f'{manage_path} 的目标比例必须为正数：{members}')
    if abs(sum(members.values()) - 1) > 1e-9:
        raise ValueError(f'{manage_path} 的目标比例合计必须为 1，当前为 {sum(members.values())}')
    if not 0 < threshold < 1:
        raise ValueError(f'{manage_path} 的 rebalance_threshold 必须在 (0, 1) 之间')
    if threshold_mode not in THRESHOLD_MODES:
        raise ValueError(f"{manage_path} 的 rebalance_threshold_mode 只能是 'absolute' 或 'relative'：{threshold_mode!r}")
    if not 1 <= max_scale <= MAX_SCALE_LIMIT:
        raise ValueError(f'{manage_path} 的 max_scale 必须在 [1, {MAX_SCALE_LIMIT}] 之间')

    # ====================================================================================================
    # 2. 检查成员：目录内的成员文件与 members 完全一致，hour_offset 组内一致
    # ====================================================================================================
    paths = {p.stem: p for p in sorted(group_path.glob('*.py')) if not p.stem.startswith('_')}
    if set(paths) != set(members):
        raise ValueError(f'{manage_path} 的 members {sorted(members)} 与目录内的成员文件 {sorted(paths)} 不一致')

    hour_offsets = {member_id: read_hour_offset_from_config(path) for member_id, path in paths.items()}
    if len(set(hour_offsets.values())) != 1:
        raise ValueError(f'账户组 {group_path.name} 的成员 hour_offset 必须一致：{hour_offsets}')

    return dict(
        name=group_path.name,
        members=members,
        paths=paths,
        threshold=threshold,
        threshold_mode=threshold_mode,
        max_scale=max_scale,
        webhook=getattr(manage, 'wechat_webhook_url', '') or error_webhook_url,
        transfer_webhook=getattr(manage, 'transfer_webhook_url', '') or error_webhook_url,
        hour_offset=next(iter(hour_offsets.values())),
    )


# ====================================================================================================
# ** 交接数据 **
# 计算轮和下单轮在不同的子进程中，通过 data/<账户名>/组交接/ 交接
# ====================================================================================================
def get_handoff_folder(account_name: str) -> Path:
    return get_folder_path('data', account_name, '组交接', as_path_type=True)


def save_handoff(account_name: str, run_time, equity: float):
    """计算轮结束时调用：保存最终权重和账户权益"""
    folder = get_handoff_folder(account_name)
    for name in ('df_spot_ratio.pkl', 'df_swap_ratio.pkl'):
        pd.read_pickle(runtime_folder / name).to_pickle(folder / name)
    with open(folder / 'handoff.json', 'w', encoding='utf-8') as f:
        json.dump(dict(run_time=int(run_time.timestamp()), equity=float(equity)), f)


def load_handoff_equity(account_name: str, run_time) -> float | None:
    """读取本轮计算轮保存的账户权益；文件缺失或不是本轮的返回 None"""
    path = get_handoff_folder(account_name) / 'handoff.json'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        handoff = json.load(f)
    if handoff['run_time'] != int(run_time.timestamp()):
        return None
    return handoff['equity']


# ====================================================================================================
# ** 组合层计算 **
# ====================================================================================================
def calc_scales(equities: dict[str, float], weights: dict[str, float], max_scale: float) -> dict[str, float]:
    """
    账户层动态杠杆 k_i = min(ΣE × w_i / E_i, max_scale)，即回测中的 scale
    :param equities: {成员名: 权益 E_i}
    :param weights: {成员名: 目标比例 w_i}
    :param max_scale: k 的上限
    """
    total_equity = sum(equities.values())
    return {member_id: min(total_equity * weights[member_id] / equity, max_scale)
            for member_id, equity in equities.items()}


def build_transfer_msg(group: dict, equities: dict[str, float], scales: dict[str, float]) -> str | None:
    """
    任一成员资金占比偏离目标超过阈值（严格大于）时，生成划转提醒；未超过返回 None
    偏离：absolute 为 |p - w|，relative 为 |p - w| / w（与回测 calc_transfers 一致）
    建议划转金额 = ΣE × w_i − E_i，正为转入、负为转出
    """
    total_equity = sum(equities.values())
    weights = group['members']
    relative = group['threshold_mode'] == 'relative'
    deviations = {m: abs(equities[m] / total_equity - weights[m]) / (weights[m] if relative else 1) for m in equities}
    if max(deviations.values()) <= group['threshold'] + FLOAT_EPS:
        return None

    threshold_desc = f'目标比例的 {group["threshold"]:.0%}' if relative else f'{group["threshold"]:.2%}'
    msg = f'【账户组划转提醒】{group["name"]}\n'
    msg += f'组合权益：{total_equity:,.2f} USDT，偏离阈值：{threshold_desc}\n\n'
    for member_id, equity in equities.items():
        amount = total_equity * weights[member_id] - equity
        msg += f'{member_id}\n'
        msg += f'  权益 {equity:,.2f}，占比 {equity / total_equity:.2%}（目标 {weights[member_id]:.2%}）\n'
        msg += f'  建议{"转入" if amount > 0 else "转出"} {abs(amount):,.2f} USDT\n'
        msg += f'  动态杠杆 k={scales[member_id]:.4f}{"（已触顶）" if scales[member_id] >= group["max_scale"] else ""}\n'
    msg += '\nk > 1 的账户现货部分不放大，划转前组合多头会低于目标'
    return msg


# ====================================================================================================
# ** 账户组主流程 **
# ====================================================================================================
def run_group(group: dict, run_time, *, dispatcher=None):
    """
    执行一个账户组：计算轮 -> 组合层 -> 下单轮
    任一成员计算失败，整组跳过、不下单并发送通知
    """
    name = group['name']
    dispatch = dispatcher or run_account_exec

    # ====================================================================================================
    # 1. 计算轮：逐成员选币、仓管，保存交接数据
    # 子进程内的异常由 account_exec 捕获并通知，这里只看交接数据是否是本轮的
    # ====================================================================================================
    divider(f'账户组 {name}：计算轮', '-')
    for member_id, path in group['paths'].items():
        try:
            dispatch(path, run_time, '--stage', 'calc')
        except Exception as err:
            logger.error(f'[{name}/{member_id}] 计算轮出错：{err}')
            logger.debug(traceback.format_exc())

    equities = {member_id: load_handoff_equity(member_id, run_time) for member_id in group['members']}
    failed = [member_id for member_id, equity in equities.items() if equity is None or equity <= 0]
    if failed:
        msg = f'【账户组跳过】{name} {run_time}\n成员 {failed} 计算失败或权益小于等于 0，整组本轮不下单'
        logger.error(msg)
        send_wechat_work_msg(msg, group['transfer_webhook'])
        return

    # ====================================================================================================
    # 2. 组合层：计算动态杠杆，偏离超过阈值时提醒划转（每轮都发）
    # ====================================================================================================
    scales = calc_scales(equities, group['members'], group['max_scale'])
    total_equity = sum(equities.values())
    for member_id, equity in equities.items():
        logger.info(f'[{name}/{member_id}] 权益 {equity:,.2f}，占比 {equity / total_equity:.2%}'
                    f'（目标 {group["members"][member_id]:.2%}），动态杠杆 k={scales[member_id]:.4f}')

    transfer_msg = build_transfer_msg(group, equities, scales)
    if transfer_msg:
        logger.warning(transfer_msg)
        send_wechat_work_msg(transfer_msg, group['transfer_webhook'])

    # ====================================================================================================
    # 3. 下单轮：逐成员按 杠杆 × k 下单；已下单的成员无法回滚，单个失败不影响其他成员
    # ====================================================================================================
    divider(f'账户组 {name}：下单轮', '-')
    for member_id, path in group['paths'].items():
        try:
            dispatch(path, run_time, '--stage', 'trade', '--scale', repr(scales[member_id]))
        except Exception as err:
            msg = f'[{name}/{member_id}] 下单轮出错：{err}'
            logger.error(msg)
            logger.debug(traceback.format_exc())
            send_wechat_work_msg(msg, group['transfer_webhook'])
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
