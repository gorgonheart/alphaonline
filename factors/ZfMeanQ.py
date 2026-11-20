import pandas as pd
import numpy as np


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['振幅'] = (df['high'] - df['low']) / df['open']
    df['振幅均值'] = df['振幅'].rolling(n, min_periods=1).mean()

    df[factor_name] = df['振幅均值'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

    return df


def signal_multi_params(df, param_list) -> dict:
    """多参数振幅均值滚动分位数（ZfMeanQ）计算。

    数学定义：
        1. 单根 K 线振幅：amplitude_t = (high_t - low_t) / open_t
        2. 滚动振幅均值：amp_mean_n(t) = Mean(amplitude[t-n+1 : t], min_periods=1)
        3. 滚动百分比排名：ZfMeanQ_n(t) = RankPct(amp_mean_n[t-n+1 : t], ascending=True, pct=True)

    性能优化：
        使用 `fast_rolling.rolling_rank_pct`（坐标压缩 + 树状数组 O(N log N) 滑窗秩）
        替代 pandas `rolling(n).rank()` 的 O(N * n) 逐窗重排，数值逐位等价。
    """
    from core.utils.fast_rolling import rolling_rank_pct
    ret = dict()
    if not param_list:
        return ret
    amplitude = (df['high'] - df['low']) / df['open']
    idx = df.index
    for param in param_list:
        amp_mean = amplitude.rolling(param, min_periods=1).mean()
        rank_pct_arr = rolling_rank_pct(amp_mean.to_numpy(dtype=np.float64, copy=False), param)
        ret[param] = pd.Series(rank_pct_arr, index=idx)
    return ret
