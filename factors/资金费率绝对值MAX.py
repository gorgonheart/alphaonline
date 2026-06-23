#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd

def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    # 将 funding_fee 中所有的 0 替换为 NA 并用前向填充
    df['funding_fee'] = df['funding_fee'].replace(0, pd.NA).fillna(method='ffill')
    # df['funding_fee'] = df['funding_fee'].replace(0, pd.NA).ffill()

    # 确保 'funding_fee' 列是数值类型
    df['funding_fee'] = pd.to_numeric(df['funding_fee'], errors='coerce')

    # df['close'] = df['funding_fee']

    # 对补全后的资金费率进行后续处理
    df['fr'] = df['funding_fee']
    df[factor_name] = df['fr'].rolling(n, min_periods=1).max().abs()

    return df
