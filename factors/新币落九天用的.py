"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

import numpy as np
import pandas as pd

def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]
    # 将 candle_begin_time 转换为 pandas Series
    candle_series = pd.to_datetime(pd.Series(df['candle_begin_time']))

    # 计算每一行与第一行的时间差
    time_deltas = candle_series - candle_series.iloc[0]

    # 将时间差转换为小时数
    total_hours = time_deltas.dt.total_seconds() / 3600

    cond = (df['symbol_swap'] == '') | (df['symbol_spot'] == '')
    df[factor_name] = total_hours
    df.loc[cond, factor_name] = np.nan


    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    candle_series = pd.to_datetime(pd.Series(df['candle_begin_time']))
    time_deltas = candle_series - candle_series.iloc[0]
    total_hours = time_deltas.dt.total_seconds() / 3600
    cond = (df['symbol_swap'] == '') | (df['symbol_spot'] == '')
    values = total_hours.mask(cond.fillna(False))
    for param in param_list:
        ret[param] = values
    return ret
