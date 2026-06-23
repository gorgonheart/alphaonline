# -*- coding: utf-8 -*-
"""
选币策略 | 邢不行 | 2023分享会
author: 邢不行
微信: xbx6660
"""
import numpy as np
import pandas as pd


# def signal(*args):
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]
#     df['bbi'] = (df['close'].rolling(n, min_periods=1).mean() + df['close']
#                  .rolling(2 * n, min_periods=1).mean() + df['close']
#                  .rolling(4 * n, min_periods=1).mean() + df['close']
#                  .rolling(6 * n, min_periods=1).mean()) / 4
#     df['bbi_bias'] = df['close'] / df['bbi']
#     ######################
#     df['route_1'] = 2 * (df['high'] - df['low']) + (df['open'] - df['close'])
#     df['route_2'] = 2 * (df['high'] - df['low']) + (df['close'] - df['open'])
#     df.loc[df['route_1'] > df['route_2'], '盘中最短路径'] = df['route_2'] / df['route_1']
#     df.loc[df['route_1'] <= df['route_2'], '盘中最短路径'] = df['route_1'] / df['route_2']
#     ##################
#     df['最短路径_标准化'] = df['盘中最短路径'] / df['open']
#     df['流动溢价'] = df['quote_volume'] / df['最短路径_标准化']
#     #######################
#     df['振幅'] = (df['high'] - df['low']) / df['open']
#     df['流动溢价2'] = df['quote_volume'] / df['振幅']



#     df['factor'] = df['流动溢价'] + df['流动溢价2']
#     df[factor_name] = (df['factor']).rolling(n, min_periods=1).std() * df['bbi_bias']
#     #################################################


#     del df['route_1'], df['bbi_bias'], df['bbi'], df['factor'], df['振幅']
#     del df['route_2']
#     del df['盘中最短路径']
#     del df['最短路径_标准化']
#     del df['流动溢价'],df['流动溢价2']

#     return df




def signal_multi_params(df, param_list) -> dict:

    zhenfu = (df['high'] - df['low']) / df['open']
    ilq2 = df['quote_volume'] / zhenfu
    
    ilq = df['quote_volume'] / ((2 * (df['high'] - df['low']) - abs(df['open'] - df['close'])) / df['open'])

    ilq_sum = ilq+ilq2 

    ret = dict()
    for param in param_list:
        n = int(param)
        bbi = (df['close'].rolling(n, min_periods=1).mean() + df['close']
                .rolling(2 * n, min_periods=1).mean() + df['close']
                .rolling(4 * n, min_periods=1).mean() + df['close']
                .rolling(6 * n, min_periods=1).mean()) / 4
        bbi_bias = df['close']/bbi

        factor = ilq_sum.rolling(n, min_periods=1).std() * bbi_bias


        ret[str(param)] = factor
    return ret

