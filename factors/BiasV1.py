# -*- coding: utf-8 -*-
"""
中性策略框架 | 邢不行 | 2024分享会
author: 邢不行
微信: xbx6660
"""
import numpy as np
import pandas as pd
# import numba as nb


# @nb.jit(nopython=True)
def get_idx(x):
    return x.argmax()


def signal(*args):
    df = args[0]
    n = int(args[1])
    factor_name = args[2]

    source_cls = df.columns.tolist()

    df = df.dropna(axis=0, subset=['close']).reset_index(drop=True)

    add_index = np.where(df.index >= n, df.index - n + 1, 0)
    hm_index = df['high'].rolling(n, min_periods=1).apply(lambda x: get_idx(x), raw=True)
    hm_index += add_index
    distance = df.index - hm_index + 1

    tp = (df['close'] + df['high'] + df['low']) / 3
    ma = tp.rolling(n, min_periods=1).mean()
    bias = tp / ma

    df[factor_name] = bias / distance

    df.drop(columns=list(set(df.columns.values).difference(set(source_cls + [factor_name]))), inplace=True)

    return df


def signal_multi_params(df, param_list) -> dict:
    """多参数时间衰减加权典型价格乖离率（BiasV1）计算。

    数学定义（保留旧版先 `dropna(subset=['close']).reset_index(drop=True)` 的索引约定）：
        1. 典型价格乖离率：tp_t = (close_t + high_t + low_t) / 3，bias_t = tp_t / Mean_n(tp)
        2. 距离滚动最高价的 K 线根数：
           - `high_argmax_offset` 为窗口 `[max(0, t-n+1) : t]` 内首个最高价（或首个 NaN）的相对下标
           - `distance_t = t - (window_start + high_argmax_offset) + 1`
        3. 因子值：BiasV1_n(t) = bias_t / distance_t

    性能优化：
        用 `fast_rolling.rolling_argmax_first`（$O(N)$ 单调队列）替代 `rolling(n).apply(argmax)` 的
        Python 逐行回调（原每币耗时 ~15ms 降至 <0.2ms）。
    """
    from core.utils.fast_rolling import rolling_argmax_first
    ret = dict()
    if not param_list:
        return ret
    valid_close_mask = df['close'].notna()
    close = df['close'].loc[valid_close_mask].reset_index(drop=True)
    high = df['high'].loc[valid_close_mask].reset_index(drop=True)
    low = df['low'].loc[valid_close_mask].reset_index(drop=True)
    typical_price = (close + high + low) / 3
    high_arr = high.to_numpy(dtype=np.float64, copy=False)
    bar_index_arr = np.arange(len(close), dtype=np.float64)
    for param in param_list:
        n = int(param)
        window_start_idx = np.where(bar_index_arr >= n, bar_index_arr - n + 1.0, 0.0)
        high_peak_abs_idx = rolling_argmax_first(high_arr, n) + window_start_idx
        bars_since_high_peak = bar_index_arr - high_peak_abs_idx + 1.0
        tp_ma = typical_price.rolling(n, min_periods=1).mean()
        ret[param] = (typical_price / tp_ma) / bars_since_high_peak
    return ret
