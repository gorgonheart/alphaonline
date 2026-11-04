# -*- coding: utf-8 -*-
"""
轮动策略框架 | 邢不行 | 2024分享会
author: 邢不行
微信: xbx6660
"""
import pandas as pd
import numpy as np

eps = 1e-8


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]
    n = int(n/3)
    df['ma1'] = df['close'].rolling(n, min_periods=1).mean()
    df['ma2'] = df['close'].rolling(n*3, min_periods=1).mean()
    df[factor_name] = df['ma1'] - df['ma2']

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    close = df['close']
    for param in param_list:
        n = int(param / 3)
        ma1 = close.rolling(n, min_periods=1).mean()
        ma2 = close.rolling(n * 3, min_periods=1).mean()
        ret[param] = ma1 - ma2
    return ret
