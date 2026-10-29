"""Two additive momentum terms, with causal, per-parameter anomaly isolation.

param: n, or (n, location_window, warmup_bars, cooldown_hours).
Defaults: (n, 4368, 167, 168). A detected/invalid bar resets BOTH terms.
The detector keeps only accepted observations in its n-bar reference window;
its current-observation-inclusive z score and 4-bar range definition are kept.
Requires numpy, pandas, numba (already used by the backtest framework).
"""
from numbers import Integral

import numpy as np
import pandas as pd
from numba import njit

# 预计算缺失时须先用生产者完整历史计算，不能在 Arrow 尾窗重新初始化状态。
requires_full_history = True


def _params(param):
    values = (param, 4368, 167, 168) if isinstance(param, Integral) else tuple(param)
    if len(values) != 4 or any(not isinstance(x, Integral) for x in values):
        raise ValueError('Use integer n or (n, location_window, warmup_bars, cooldown_hours)')
    n, location_window, warmup, cooldown = map(int, values)
    if n < warmup or warmup < 2 or location_window < 1 or cooldown < 0:
        raise ValueError('Require n >= warmup >= 2, location_window >= 1, cooldown >= 0')
    return n, location_window, warmup, cooldown


@njit(cache=False)
def _clean_anomalies(high, low, close, n):
    """Sequential rolling sample variance; rejected values never enter history.

    The window counts rows, including missing/rejected rows, like pandas rolling.
    Accepted reference history is retained across an event, but the four-bar
    raw-price window must start AFTER the event. No earlier output is rewritten.
    """
    length = len(high)
    anomaly = np.zeros(length, dtype=np.bool_)
    invalid = np.zeros(length, dtype=np.bool_)
    ring = np.full(n, np.nan)
    count = 0
    mean = 0.0
    m2 = 0.0
    last_reset = 0  # The first supplied listing candle is ignored.
    for i in range(length):
        slot = i % n
        previous = ring[slot]
        ring[slot] = np.nan
        if np.isfinite(previous):
            if count == 1:
                count, mean, m2 = 0, 0.0, 0.0
            else:
                next_mean = (count * mean - previous) / (count - 1)
                m2 = max(0.0, m2 - (previous - mean) * (previous - next_mean))
                mean = next_mean
                count -= 1
        if i == 0:
            continue
        if (not np.isfinite(high[i]) or not np.isfinite(low[i])
                or not np.isfinite(close[i]) or low[i] <= 0
                or close[i] <= 0 or high[i] < low[i]):
            invalid[i] = True
            last_reset = i
            continue
        if i - last_reset < 4:
            continue
        high_min, low_max = high[i], low[i]
        for j in range(i - 3, i):
            high_min = min(high_min, high[j])
            low_max = max(low_max, low[j])
        value = (high_min - low_max) / low_max
        if not np.isfinite(value):
            invalid[i] = True
            last_reset = i
            continue
        next_count = count + 1
        delta = value - mean
        next_mean = mean + delta / next_count
        next_m2 = max(0.0, m2 + delta * (value - next_mean))
        z = 0.0
        if next_count > 1 and next_m2 > 0:
            z = abs(value - next_mean) / np.sqrt(next_m2 / (next_count - 1))
        if z > 25.0 and (value > 0.2 or value < -0.5):
            anomaly[i] = True
            last_reset = i
            continue
        ring[slot] = value
        count, mean, m2 = next_count, next_mean, next_m2
    return anomaly, invalid


def _inputs(df):
    high = df['high'].astype(float).reset_index(drop=True).copy()
    low = df['low'].astype(float).reset_index(drop=True).copy()
    close = df['close'].astype(float).reset_index(drop=True).copy()
    if len(df):
        high.iloc[0] = low.iloc[0] = close.iloc[0] = np.nan
    times = None
    if 'candle_begin_time' in df:
        times = pd.to_datetime(df['candle_begin_time']).reset_index(drop=True)
        if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
            raise ValueError('Momentum requires unique increasing timestamps')
    return high, low, close, times


def _calculate(df, param, inputs):
    n, location_window, warmup, cooldown = _params(param)
    high, low, close, times = inputs
    tp = (high + low + close) / 3.0
    anomaly, invalid = _clean_anomalies(high.to_numpy(), low.to_numpy(), close.to_numpy(), n)
    reset = pd.Series(anomaly | invalid)
    trend = pd.Series(np.nan, index=pd.RangeIndex(len(df)), dtype=float)
    location = trend.copy()
    counts = pd.Series(0, index=trend.index, dtype='int64')
    start = 0
    for end in np.append(np.flatnonzero(reset.to_numpy()), len(df)):
        price = tp.iloc[start:end]
        if len(price):
            delta = price - price.ewm(span=n, adjust=False).mean()
            bottom = delta.rolling(n, min_periods=warmup).min()
            top = delta.rolling(n, min_periods=warmup).max()
            trend.iloc[start:end] = ((delta - bottom) / (top - bottom).replace(0, np.nan)).to_numpy()
            # Both the four-bar smoother and the long price-location window reset.
            h = high.iloc[start:end].rolling(4).min()
            l = low.iloc[start:end].rolling(4).max()
            ceiling = h.rolling(location_window, min_periods=1).max()
            floor = l.rolling(location_window, min_periods=1).min()
            location.iloc[start:end] = ((price - floor) / (ceiling - floor).replace(0, np.nan)).to_numpy()
            counts.iloc[start:end] = price.notna().cumsum().to_numpy()
        start = end + 1
    if times is None:
        blocked = reset.rolling(cooldown + 1, min_periods=1).max().astype(bool)
    else:
        last = times.where(reset).ffill()
        blocked = (times - last) <= pd.Timedelta(hours=cooldown)
    value = (trend + location).mask(blocked | (counts < warmup))
    result = pd.DataFrame({'trend': trend, 'location': location, 'anomaly': anomaly,
                           'invalid': invalid, 'blocked': blocked, 'normal_count': counts,
                           'signal': value.replace([np.inf, -np.inf], np.nan)})
    result.index = df.index
    return result


def diagnostics(df, param):
    """Expose the two terms and event/recovery state for research, never positions."""
    return _calculate(df, param, _inputs(df))


def signal_multi_params(df, param_list):
    inputs = _inputs(df)
    return {str(param): _calculate(df, param, inputs)['signal'] for param in param_list}


def signal(*args):
    """Compatibility with child frameworks that only expose the single-param API."""
    df, param, factor_name = args[:3]
    df[factor_name] = signal_multi_params(df, [param])[str(param)].to_numpy()
    return df
