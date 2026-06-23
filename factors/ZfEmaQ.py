def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]


    df['振幅'] = (df['high'] - df['low']) / df['open']
    df['振幅均值'] = df['振幅'].ewm(span=n, adjust=False).mean()

    df[factor_name] = df['振幅均值'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

    return df


def signal_multi_params(df, param_list) -> dict:
    """
    使用同因子多参数聚合计算，可以有效提升回测、实盘 cal_factor 的速度，
    相对于 signal 大概提升3倍左右
    :param df: k线数据的dataframe
    :param param_list: 参数列表
    """
    zf = (df['high'] - df['low']) / df['open']



    ret = dict()
    for param in param_list:
        n = int(param)
        zf_mean = zf.ewm(span=n, adjust=False).mean()
        ret[str(param)] = zf_mean.rolling(n, min_periods=1).rank(ascending=True, pct=True)
    return ret