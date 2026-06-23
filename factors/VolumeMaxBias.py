"""
VolumeMaxBias 因子
当前小时成交量 / 过去N周期最大成交量
"""


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df[factor_name] = df['volume'] / df['volume'].rolling(n, min_periods=1).max()

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    for param in param_list:
        n = int(param)
        ret[str(param)] = df['volume'] / df['volume'].rolling(n, min_periods=1).max()
    return ret
