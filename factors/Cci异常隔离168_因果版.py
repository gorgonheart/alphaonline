"""Versioned causal repair of Cci按异常0401修正; leave the legacy factor intact.

因子数学定义（因果异常隔离版 CCI）：
1. 典型价格与双 K 线波幅率（首根置 NaN）：
   - `tp_t = (high_t + low_t + close_t) / 3`（其中 `t = 0` 时 `high_0 = low_0 = tp_0 = NaN`）
   - `rolling_high_2 = max(high_{t-1}, high_t)`，`rolling_low_2 = min(low_{t-1}, low_t)`
   - `pctchange_t = (rolling_high_2 - rolling_low_2) / rolling_low_2`
2. 异常跳变点检测（窗口 `n`，`min_periods=1`）：
   - `pct_ma = Mean(pctchange, n)`，`pct_std = Std(pctchange, n, ddof=1)`
   - 当 `|(pctchange - pct_ma) / pct_std| > 25` 且 `|pctchange| > 0.2` 时，标记 `anomaly_t = True`
3. 按异常点分段独立计算 CCI（彻底隔离跨异常点的价格跳变污染）：
   - 以所有 `anomaly_t == True` 的位置为断点，将序列切分为若干连续正常区间 `[start, end)`；
   - 在每个区间内部独立计算：
     `ma = Mean(tp[start:end], n)`
     `md = Mean(|tp[start:end] - ma|, n) * 0.015`
     `cci = (tp[start:end] - ma) / md`
4. 168 小时因果冷却期掩码（无未来函数泄露）：
   - 有时间戳列 `candle_begin_time` 时：自最近一次历史异常时刻 `last_anomaly_time` 起，
     满足 `0 <= (t - last_anomaly_time) <= 168h` 的行全部置为 `NaN`；
   - 无时间戳列时：自异常点起向后连续 169 根 K 线置为 `NaN`。

性能实现：
   调用 `core.utils.fast_rolling.cci_causal_168_full`（`@njit(cache=True)` Welford 滑窗均值标准差
   + Kahan 分段滑窗均值），与纯 pandas 参考实现误差 <= 2e-10 且 NaN 掩码 100% 一致。
"""
import numpy as np
import pandas as pd
from core.utils.fast_rolling import cci_causal_168_full


def _extract_validated_timestamps_ns(df: pd.DataFrame) -> tuple[np.ndarray, bool]:
    """提取并校验严格单调递增的时间戳纳秒数组（缺失 candle_begin_time 时返回空数组）。"""
    if 'candle_begin_time' not in df:
        return np.empty(0, dtype=np.int64), False

    col = df['candle_begin_time']
    if np.issubdtype(col.dtype, np.datetime64):
        times_ns = col.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)
        nat_ns = np.int64(np.datetime64('NaT', 'ns').view('int64'))
        if (times_ns == nat_ns).any() or (times_ns.size > 1 and (times_ns[1:] <= times_ns[:-1]).any()):
            raise ValueError('CCI requires unique, increasing candle_begin_time values')
        return times_ns, True

    times = pd.to_datetime(col).reset_index(drop=True)
    if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError('CCI requires unique, increasing candle_begin_time values')
    return times.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False), True


def signal_multi_params(df, param_list) -> dict:
    """多参数计算 `Cci异常隔离168_因果版`，返回 RangeIndex 对齐的 Series 字典。"""
    high = df['high'].to_numpy(dtype=np.float64, copy=False)
    low = df['low'].to_numpy(dtype=np.float64, copy=False)
    close = df['close'].to_numpy(dtype=np.float64, copy=False)
    times_ns, has_times = _extract_validated_timestamps_ns(df)

    ret = {}
    idx = pd.RangeIndex(len(df))
    for param in param_list:
        window = int(param)
        if window <= 0:
            raise ValueError('CCI window must be positive')
        factor_arr = cci_causal_168_full(high, low, close, times_ns, has_times, window)
        ret[str(param)] = pd.Series(factor_arr, index=idx, dtype=np.float64)
    return ret
