import pandas as pd

def signal(*args):
    df = args[0]
    n = args[1]  
    factor_name = args[2]

    # 获取币种的首次出现时间
    first_appear = df['candle_begin_time'].min()

    # 计算当前时间与首次出现时间的小时差
    df[factor_name] = (df['candle_begin_time'] - first_appear).dt.total_seconds() / 3600

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    first_appear = df['candle_begin_time'].min()
    hours = (df['candle_begin_time'] - first_appear).dt.total_seconds() / 3600
    for param in param_list:
        ret[param] = hours
    return ret
