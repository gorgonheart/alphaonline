#!/usr/bin/python3
# -*- coding: utf-8 -*-
import numpy as np
def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['tp'] = (df['high'] + df['low'] + df['close']) / 3
    cond = (df['symbol_swap'] == '') | (df['symbol_spot'] == '')
    df.loc[cond, 'tp'] = np.nan

    df['ma'] = df['tp'].rolling(window=n, min_periods=1).mean()
    df['md'] = abs(df['tp'] - df['ma']).rolling(window=n, min_periods=1).mean()

    df[factor_name] = (df['tp'] - df['ma']) / (df['md'] * 0.015)

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    tp = (df['high'] + df['low'] + df['close']) / 3
    cond = (df['symbol_swap'] == '') | (df['symbol_spot'] == '')
    tp = tp.mask(cond.fillna(False))
    for param in param_list:
        ma = tp.rolling(window=param, min_periods=1).mean()
        md = abs(tp - ma).rolling(window=param, min_periods=1).mean()
        ret[param] = (tp - ma) / (md * 0.015)
    return ret
