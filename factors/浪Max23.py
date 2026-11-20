"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import numpy as np


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['振幅'] = (df['high'] - df['low']) / df['open']
    df['振幅均值'] = df['振幅'].rolling(n, min_periods=1).mean()
    df['Q1'] = df['振幅均值'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

    df['VMean'] = df['volume'].rolling(n, min_periods=1).mean()
    df['Q2'] = df['VMean'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

    df[factor_name] = df[['Q1', 'Q2']].max(axis=1)

    return df


def signal_multi_params(df, param_list) -> dict:
    """多参数振幅均值分位数与成交量均值分位数双通道极值（浪Max23）计算。

    数学定义：
        1. 振幅均值分位数：Q1_n(t) = RankPct_n(Mean_n((high - low) / open))
        2. 成交量均值分位数：Q2_n(t) = RankPct_n(Mean_n(volume))
        3. 因子值：浪Max23_n(t) = max(Q1_n(t), Q2_n(t))
    """
    import pandas as pd
    from core.utils.fast_rolling import rolling_rank_pct
    amplitude = (df['high'] - df['low']) / df['open']
    idx = df.index

    ret = dict()
    for param in param_list:
        n = int(param)
        amp_mean = amplitude.rolling(n, min_periods=1).mean()
        amp_mean_q = rolling_rank_pct(amp_mean.to_numpy(dtype=np.float64, copy=False), n)
        vol_mean = df['volume'].rolling(n, min_periods=1).mean()
        vol_mean_q = rolling_rank_pct(vol_mean.to_numpy(dtype=np.float64, copy=False), n)
        ret[str(param)] = pd.Series(np.maximum(amp_mean_q, vol_mean_q), index=idx)
    return ret
