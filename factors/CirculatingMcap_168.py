"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import pandas as pd

import numpy as np
extra_data_dict = {
    'coin-cap': ['circulating_supply']
}


def signal(*args):


    df = args[0]
    n = args[1]
    
    if(len(df)<168):
        df[factor_name] = np.nan
           
    factor_name = args[2]

    df['circulating_mcap'] = df['circulating_supply'] * df['close']

    df[factor_name] = df['circulating_mcap']

    return df
