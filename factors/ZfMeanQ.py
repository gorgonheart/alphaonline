import pandas as pd
import numpy as np


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['振幅'] = (df['high'] - df['low']) / df['open']
    df['振幅均值'] = df['振幅'].rolling(n, min_periods=1).mean()

    df[factor_name] = df['振幅均值'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    amplitude = (df['high'] - df['low']) / df['open']
    for param in param_list:
        mean = amplitude.rolling(param, min_periods=1).mean()
        ret[param] = mean.rolling(param, min_periods=1).rank(ascending=True, pct=True)
    return ret
