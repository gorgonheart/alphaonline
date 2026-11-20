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
    """
    使用同因子多参数聚合计算，可以有效提升回测、实盘 cal_factor 的速度，
    相对于 `signal` 大概提升3倍左右
    :param df: k线数据的dataframe
    :param param_list: 参数列表
    """
    import pandas as pd
    from core.utils.fast_rolling import rolling_rank_pct
    zf = (df['high'] - df['low']) / df['open']
    idx = df.index

    ret = dict()
    for param in param_list:
        n = int(param)
        ZfMean = zf.rolling(n, min_periods=1).mean()
        ZfMeanQ = rolling_rank_pct(ZfMean.to_numpy(dtype=np.float64, copy=False), n)
        VolumeMean = df['volume'].rolling(n, min_periods=1).mean()
        VolumeMeanQ = rolling_rank_pct(VolumeMean.to_numpy(dtype=np.float64, copy=False), n)
        ret[str(param)] = pd.Series(np.maximum(ZfMeanQ, VolumeMeanQ), index=idx)
    return ret
