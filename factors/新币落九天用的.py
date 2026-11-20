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
    if len(df) == 0:
        empty = pd.Series(dtype=np.float64, index=df.index)
        for param in param_list:
            ret[param] = empty
        return ret
    col = df['candle_begin_time']
    if np.issubdtype(col.dtype, np.datetime64):
        ts_ns = col.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)
        total_hours = (ts_ns - ts_ns[0]) / 3.6e12
    else:
        candle_series = pd.to_datetime(pd.Series(col))
        total_hours = ((candle_series - candle_series.iloc[0]).dt.total_seconds() / 3600).to_numpy(dtype=np.float64, copy=False)
    swap_vals = df['symbol_swap'].to_numpy(copy=False)
    spot_vals = df['symbol_spot'].to_numpy(copy=False)
    cond = (swap_vals == '') | (spot_vals == '')
    out = np.where(cond, np.nan, total_hours)
    values = pd.Series(out, index=df.index, dtype=np.float64)
    for param in param_list:
        ret[param] = values
    return ret
