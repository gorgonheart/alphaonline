"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import os
import numpy as np
import pandas as pd


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['tp'] = (df['high'] + df['low'] + df['close']) / 3
    df['min'] = df['tp'].rolling(n, min_periods=1).min()
    df['max'] = df['tp'].rolling(n, min_periods=1).max()
    df['minmax'] = (df['tp'] - df['min']) / (df['max'] - df['min'])

    df[factor_name] = df['minmax'] - 0.5

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    tp = (df['high'] + df['low'] + df['close']) / 3
    for param in param_list:
        minimum = tp.rolling(param, min_periods=1).min()
        maximum = tp.rolling(param, min_periods=1).max()
        ret[param] = (tp - minimum) / (maximum - minimum) - 0.5
    return ret
