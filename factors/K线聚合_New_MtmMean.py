#!/usr/bin/python3
# -*- coding: utf-8 -*-

import numpy  as np
import pandas as pd
# import talib as ta
# from utils.diff import add_diff, eps


def signal(*args):
    # MtmMean 指标
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['涨跌幅'] = df['close'].pct_change(1)
    df['涨跌幅'] = np.where((df['涨跌幅']) > 0.2, -df['涨跌幅'], df['涨跌幅']) #反转

    df['4小时涨跌幅'] = df['涨跌幅'].rolling(window=4, min_periods=1).sum() #  f0.35: 886(18.4 ) f0.32:828
    f = 0.35
    df['4小时涨跌幅'] = np.where(df['4小时涨跌幅'] > f, f, df['4小时涨跌幅']) #f:0.2  559.4/ 14.75   f:0.35:559.4/14.75
    df['4小时涨跌幅'] = np.where(df['4小时涨跌幅'] < -f, -f, df['4小时涨跌幅'])
    df['该小时涨跌幅'] = df['4小时涨跌幅']/4

    df['mtm_sum'] = df['该小时涨跌幅'].rolling(window=n, min_periods=1).sum() #f0.4：182.768(8.86)

    df[factor_name]  =  df['mtm_sum'].rolling(window=n, min_periods=1).mean()

    del df['该小时涨跌幅'], df['4小时涨跌幅'], df['mtm_sum'], df['涨跌幅']
    return df
