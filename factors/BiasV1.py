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
    """仅取公式所需列；兼容旧版丢弃缺 close 行及重置索引的行为。"""
    ret = dict()
    if not param_list:
        return ret
    prices = df.loc[df['close'].notna(), ['close', 'high', 'low']].reset_index(drop=True)
    tp = (prices['close'] + prices['high'] + prices['low']) / 3
    for param in param_list:
        n = int(param)
        add_index = np.where(prices.index >= n, prices.index - n + 1, 0)
        hm_index = prices['high'].rolling(n, min_periods=1).apply(lambda x: get_idx(x), raw=True)
        hm_index += add_index
        distance = prices.index - hm_index + 1
        ma = tp.rolling(n, min_periods=1).mean()
        ret[param] = (tp / ma) / distance
    return ret
