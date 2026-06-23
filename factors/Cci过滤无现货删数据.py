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

# def signal_multi_params(df, param_list) -> dict:
#     ret = dict()

#     tp = (df['high'] + df['low'] + df['close']) / 3
#     for param in param_list:
#         n = int(param)

#         ma = tp.rolling(window=n, min_periods=1).mean()
#         md = abs(tp - ma).rolling(window=n, min_periods=1).mean()*0.015


#         ret[str(param)] = (tp - ma) / md
#     return ret