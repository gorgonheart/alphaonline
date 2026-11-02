"""HG 回撤保护 × Bolling 择时，附带乘数变化限速的单文件实现。

只依赖 numpy、pandas 和标准库，不依赖 signals 或研究目录中的其他文件。
合并来源：HysteresisGuard、HysteresisGuardRateLimit、
HysteresisBollingMix1022，以及原 HGCappedBolling1022 支持的六类 Bolling。
Bolling1/2/multi 算法来源：邢不行策略分享会，保留原作者版权；
仅供个人学习使用，未经授权不得用于商业用途。

处理顺序：同一条未择时资金曲线 → HG 与 Bolling → 组合 → 乘数限速。
必须输入未择时账户，避免空仓后账户不再变化而无法恢复的反馈问题。
返回值为 [0, 1] 的计划仓位乘数；不在本文件中移位，由框架决定执行时点。
限速约束乘数变化，不约束实际换手、成交金额或行情引起的仓位漂移。

参数顺序保持原接口：
    hg_params: [滚动峰值窗口, 退出回撤阈值, 恢复回撤阈值, 连续确认根数]
    boll_name: Bolling2multi / Bolling2 / Bolling1 / BollingBandLong /
               BollingBandShort / Bolling2MinVol
    boll_params: 传给对应 Bolling 的位置参数列表；多窗口须嵌套一层。
    mode: product=HG*B；blend=strength*HG+(1-strength)*B；
          soft=B*(strength+(1-strength)*HG)；hg_only / boll_only 为单信号。
          product 不使用 strength 调节权重，strength=0 仍保留 HG。
    strength: [0, 1]，所有 mode 均检查该参数。
    max_delta: 每根乘数最大变化，(0, 1]；限速起始乘数为 0。
    order: final=组合后限速；hg_first=仅 HG 限速，Bolling 可绕过最终限速；
           both=HG 与最终组合分别限速。

当前 SIMPLE_H48 四饼用法：
    dynamic_leverage(equity, [672, 0.1, 0.05, 6], 'Bolling2multi',
                     [[504, 672, 840, 1008]], 'product', 0.0, 0.25, 'final')

本文件放在 positions 便于集中维护，但接口属于 re_timing 信号，不是
父仓管 calc_ratio。配置如需使用，可先调用本文件的 register()，再沿用
re_timing={'name': 'HGCappedBolling1022', 'params': [...]}。
"""

import math
import sys

import numpy as np
import pandas as pd


ALLOWED = frozenset({
    'Bolling2multi',
    'Bolling2',
    'Bolling1',
    'BollingBandLong',
    'BollingBandShort',
    'Bolling2MinVol',
})


# ============================================================================
# 1. HG：滚动回撤 + 连续确认 + 恢复滞回
# ============================================================================
def _hysteresis_guard(
    equity: pd.Series,
    peak_window,
    threshold,
    recovery,
    confirm_bars,
) -> pd.Series:
    """初始开启；连续深回撤后关闭，连续恢复至较浅回撤后重新开启。"""
    window = int(peak_window)
    threshold = float(threshold)
    recovery = float(recovery)
    confirm = int(confirm_bars)
    if window <= 0 or not 0 < recovery < threshold < 1 or confirm <= 0:
        raise ValueError('HysteresisGuard expects 0 < recovery < threshold < 1')

    values = equity.astype(float)
    peak = values.rolling(window, min_periods=1).max()
    drawdown = values / peak - 1.0

    state = 1.0
    below = 0
    above = 0
    output = []
    for value in drawdown:
        if state > 0:
            below = below + 1 if value <= -threshold else 0
            if below >= confirm:
                state = 0.0
                below = 0
        else:
            above = above + 1 if value >= -recovery else 0
            if above >= confirm:
                state = 1.0
                above = 0
        output.append(state)
    return pd.Series(output, index=equity.index)


# ============================================================================
# 2. Bolling 家族：保留原有预热、严格穿越和窗口平均语义
# ============================================================================
def _bolling_regime(equity: pd.Series, window, *, enter_at_lower=False) -> pd.Series:
    """Bolling2 上穿均线进入；Bolling1 上穿下轨进入；均下穿下轨退出。"""
    window = int(window)
    middle = equity.rolling(window, min_periods=1).mean()
    std = equity.rolling(window, min_periods=1).std()
    zscore = (equity - middle) / std
    width = zscore.abs().rolling(window, min_periods=1).mean()
    lower = middle - width * std

    entry_line = lower if enter_at_lower else middle
    enter = (equity.shift() < entry_line.shift()) & (equity > entry_line)
    exit_ = (equity.shift() > lower.shift()) & (equity < lower)
    leverage = pd.Series(np.nan, index=equity.index)
    leverage.loc[enter] = 1
    leverage.loc[exit_] = 0
    return leverage.ffill().fillna(0)


def _bolling_multi(equity: pd.Series, windows) -> pd.Series:
    """多个 Bolling2 窗口的等权平均；保留原接口对 list/标量的处理。"""
    if not isinstance(windows, list):
        windows = [windows]
    signals = [_bolling_regime(equity, window) for window in windows]
    return pd.concat(signals, axis=1).mean(axis=1)


def _bolling_band(equity: pd.Series, window, *, short=False) -> pd.Series:
    """固定 1.5 倍标准差；Long 过滤下轨外，Short 过滤上轨外。"""
    window = int(window)
    middle = equity.rolling(window=window, min_periods=window).mean()
    std = equity.rolling(window=window, min_periods=window).std()
    upper = middle + 1.5 * std
    lower = middle - 1.5 * std

    leverage = pd.Series(0.0, index=equity.index)
    leverage.loc[equity > upper] = 0 if short else 1
    leverage.loc[equity < lower] = 1 if short else 0
    leverage.loc[(equity >= lower) & (equity <= upper)] = 1
    # 原版将前 window 根全部置零，包含首根完整滚动窗口。
    leverage.iloc[:window] = 0.0
    return leverage


def _bolling_min_vol(equity: pd.Series, window, target_vol) -> pd.Series:
    """Bolling2 与年化波动率目标两项取小值；波动率按小时数据年化。"""
    window = int(window)
    target = float(target_vol)
    if window <= 1 or target <= 0:
        raise ValueError('Bolling2MinVol expects window > 1 and target > 0')

    bolling = _bolling_regime(equity, window)
    returns = equity.astype(float).pct_change()
    min_periods = min(window, max(24, window // 4))
    annualized_vol = (
        returns.rolling(window, min_periods=min_periods).std() * np.sqrt(24 * 365)
    )
    vol_target = (target / annualized_vol.replace(0, np.nan)).clip(lower=0.0, upper=1.0)
    return (
        pd.concat([bolling, vol_target], axis=1)
        .min(axis=1)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )


def _bolling_signal(equity: pd.Series, name: str, params) -> pd.Series:
    """显式选择内置算法，无动态导入和外部策略依赖。"""
    if name == 'Bolling2multi':
        return _bolling_multi(equity, params[0])
    if name == 'Bolling2':
        return _bolling_regime(equity, params[0])
    if name == 'Bolling1':
        return _bolling_regime(equity, params[0], enter_at_lower=True)
    if name == 'BollingBandLong':
        return _bolling_band(equity, params[0])
    if name == 'BollingBandShort':
        return _bolling_band(equity, params[0], short=True)
    if name == 'Bolling2MinVol':
        return _bolling_min_vol(equity, params[0], params[1])
    raise ValueError('unsupported Bolling family')


# ============================================================================
# 3. 乘数组合与变化限速
# ============================================================================
def compose(h: pd.Series, b: pd.Series, mode: str, strength) -> pd.Series:
    """组合 HG(h) 与 Bolling(b)；模式及计算次序保持原接口。"""
    strength = float(strength)
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('strength must be finite in [0,1]')
    if mode == 'blend':
        return strength * h + (1 - strength) * b
    if mode == 'soft':
        return b * (strength + (1 - strength) * h)
    if mode == 'product':
        return h * b
    if mode == 'hg_only':
        return h
    if mode == 'boll_only':
        return b
    raise ValueError('unknown composition mode')


def limit_leverage(desired: pd.Series, max_delta_leverage: float) -> pd.Series:
    """从零乘数开始，每根向最新目标最多移动 delta；不排队追赶旧目标。"""
    if isinstance(max_delta_leverage, (bool, np.bool_)):
        raise ValueError('max_delta_leverage must be numeric in (0, 1]')
    delta = float(max_delta_leverage)
    if not math.isfinite(delta) or not 0 < delta <= 1:
        raise ValueError('max_delta_leverage must be finite in (0, 1]')
    if not isinstance(desired, pd.Series):
        raise TypeError('desired must be an indexed pandas Series')
    values = desired.to_numpy(dtype=float)
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('desired leverage must be finite in [0, 1]')
    if delta == 1:
        return pd.Series(values.copy(), index=desired.index)

    limited = np.empty(len(values), dtype=float)
    previous = 0.0
    for i, target in enumerate(values):
        change = target - previous
        if abs(change) <= delta:
            previous = target
        else:
            previous += delta if change > 0 else -delta
        previous = min(1.0, max(0.0, previous))
        limited[i] = previous
    return pd.Series(limited, index=desired.index)


def combine_signals(
    h: pd.Series,
    b: pd.Series,
    mode: str,
    strength,
    max_delta=0.25,
    order='final',
) -> pd.Series:
    """校验对齐信号，并按 order 将限速放在 HG、组合后或两处。"""
    if order not in {'final', 'hg_first', 'both'}:
        raise ValueError('order must be final, hg_first or both')
    if not isinstance(h, pd.Series) or not isinstance(b, pd.Series) or not h.index.equals(b.index):
        raise ValueError('aligned HG and Bolling Series required')
    if (
        not np.isfinite(h).all()
        or not np.isfinite(b).all()
        or not h.between(0, 1).all()
        or not b.between(0, 1).all()
    ):
        raise ValueError('member signals must be finite in [0,1]')

    hg_signal = limit_leverage(h, max_delta) if order in {'hg_first', 'both'} else h
    desired = compose(hg_signal, b, mode, strength)
    if order in {'final', 'both'}:
        return limit_leverage(desired, max_delta)
    return desired


# ============================================================================
# 4. 对外接口：保持原参数顺序；可选接入本项目 SignalHub
# ============================================================================
def dynamic_leverage(
    equity: pd.Series,
    hg_params,
    boll_name,
    boll_params,
    mode,
    strength,
    max_delta=0.25,
    order='final',
) -> pd.Series:
    """基于同一条未择时资金曲线计算 HG、Bolling 和最终计划乘数。"""
    if boll_name not in ALLOWED:
        raise ValueError('unsupported Bolling family')
    h = _hysteresis_guard(equity, *hg_params)
    b = _bolling_signal(equity.copy(), boll_name, boll_params)
    return combine_signals(h, b, mode, strength, max_delta, order)


def register() -> None:
    """显式注册为 signals.HGCappedBolling1022；单纯导入本文件无注册副作用。"""
    from core.utils.signal_hub import SignalHub

    name = 'HGCappedBolling1022'
    sys.modules[f'signals.{name}'] = sys.modules[__name__]
    SignalHub._signal_cache.pop(name, None)
