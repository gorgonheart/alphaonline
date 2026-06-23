# -*- coding: utf-8 -*-
"""
选币策略 | 邢不行 | 2023分享会
author: 邢不行
微信: xbx6660
"""
import numpy as np
import pandas as pd


def signal(*args):
    df = args[0]
    n = int(args[1])
    factor_name = args[2]

    df['route_1'] = 2 * (df['high'] - df['low']) + (df['open'] - df['close'])
    df['route_2'] = 2 * (df['high'] - df['low']) + (df['close'] - df['open'])
    df.loc[df['route_1'] > df['route_2'], '盘中最短路径'] = df['route_2']
    df.loc[df['route_1'] <= df['route_2'], '盘中最短路径'] = df['route_1']
    df['最短路径_标准化'] = df['盘中最短路径'] / df['open']
    df['流动溢价'] = df['quote_volume'] / df['最短路径_标准化']

    df[factor_name] = df['流动溢价'].rolling(n, min_periods=2).std()

    del df['route_1']
    del df['route_2']
    del df['盘中最短路径']
    del df['最短路径_标准化']
    del df['流动溢价']

    return df


def signal2(*args):
    df = args[0]
    n = int(args[1])

    route1 = 2 * (df['high'] - df['low']) + (df['open'] - df['close'])
    route2 = 2 * (df['high'] - df['low']) + (df['close'] - df['open'])
    df.loc[route1 > route2, '盘中最短路径'] = route2
    df.loc[route1 <= route2, '盘中最短路径'] = route1
    shortcut = np.where(route1 > route2, route2, route1)
    shortcut_std = pd.Series(shortcut) / df['open']
    ilq = df['quote_volume'] / shortcut_std

    return ilq.rolling(n, min_periods=2).std()


def signal_multi_params(df, param_list) -> dict:
    ilq = df['quote_volume'] / ((2 * (df['high'] - df['low']) - abs(df['open'] - df['close'])) / df['open'])

    ret = dict()
    for param in param_list:
        n = int(param)
        ret[str(param)] = ilq.rolling(n, min_periods=2).std()
    return ret
