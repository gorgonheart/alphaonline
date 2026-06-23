# -*- coding: utf-8 -*-
"""
中性策略框架 | 邢不行 | 2024分享会
author: 邢不行
微信: xbx6660
"""
import numpy as np
import pandas as pd


# def signal(*args):
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]

#     df['均价'] = (df['close'] + df['high'] + df['low']) / 3

#     df['涨跌幅'] = df['均价'].pct_change()
#     df['振幅'] = (df['high'] - df['low']) / df['open']
#     df['振幅'] = np.where(df['涨跌幅'] > 0, df['振幅'], 0)
#     df['振幅均值'] = df['振幅'].rolling(n, min_periods=1).mean()

#     df[factor_name] = df['振幅均值'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

#     return df

def signal_multi_params(df, param_list) -> dict:
    """
    使用同因子多参数聚合计算，可以有效提升回测、实盘 cal_factor 的速度，
    相对于 `signal` 大概提升3倍左右
    :param df: k线数据的dataframe
    :param param_list: 参数列表
    """
    ret = dict()

    tp = (df['close'] + df['high'] + df['low']) / 3

    pctchange = tp.pct_change()
    zf = (df['high'] - df['low']) / df['open']
    zf = np.where(pctchange > 0, zf, 0)

    zf_pds = pd.Series(zf)


    for param in param_list:
        n = int(param)
        
        zf_mean = zf_pds.rolling(n, min_periods=1).mean()

        ret[str(param)] = zf_mean.rolling(n, min_periods=1).rank(ascending=True, pct=True)
    return ret
