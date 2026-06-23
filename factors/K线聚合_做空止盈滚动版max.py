#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np

np.set_printoptions(suppress=True)
pd.set_option('display.width', 1000)  # 设置字符显示宽度
pd.set_option('display.max_rows', None)  # 设置显示最大行
pd.set_option('display.max_columns', 1000)

pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
def signal(*args):
    df = args[0]
    n  = args[1]
    factor_name = args[2]

    df['n_hour_close'] = df['close'].rolling(n).min()

    # 计算当前收盘价与过去n小时内的收盘最低价之间的差价，并与收盘价形成比值
    df['n_hour_low_low'] = (df['close']-df['n_hour_close']) / df['close']
    df[factor_name] = df['n_hour_low_low'].rolling(n).max()

    # 删除临时列
    df = df.drop(columns=['n_hour_low_low'])
    return df
