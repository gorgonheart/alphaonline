"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

# 这里把LUNA拉黑，因为回撤阈值空头中有一些参数可能会因为LUNA导致纯空资金曲线爆仓，影响后续的总策略资金曲线
LUNA_SYMBOLS = {'LUNA-USDT', 'LUNAUSDT'}


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['vwap'] = df['quote_volume'] / df['volume']
    df['当前回撤'] = df['vwap'] / df['vwap'].rolling(n, min_periods=1).max() - 1

    df[factor_name] = df['当前回撤']

    # 在这里把LUNA拉黑了，否则纯空头策略的资金曲线可能会爆仓，影响后续的总策略资金曲线
    df.loc[df['symbol'].isin(LUNA_SYMBOLS), factor_name] = float('nan')

    return df


def signal_multi_params(df, param_list) -> dict:
    """
    使用同因子多参数聚合计算，可以有效提升回测、实盘 cal_factor 的速度，
    相对于 `signal` 大概提升3倍左右
    :param df: k线数据的dataframe
    :param param_list: 参数列表
    """
    vwap = df['quote_volume'] / df['volume']

    ret = dict()
    luna_mask = df['symbol'].isin(LUNA_SYMBOLS)
    for param in param_list:
        n = int(param)
        ret[str(param)] = (vwap / vwap.rolling(n, min_periods=1).max() - 1).mask(luna_mask)
    return ret
