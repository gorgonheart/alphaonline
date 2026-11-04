"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
extra_data_dict = {
    'coin-cap': ['circulating_supply']
}


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['circulating_mcap'] = df['circulating_supply'] * df['close']

    df[factor_name] = df['circulating_mcap']

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    circulating_mcap = df['circulating_supply'] * df['close']
    for param in param_list:
        ret[param] = circulating_mcap
    return ret
