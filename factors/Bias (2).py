def signal(*args):
    # Bias
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['ma'] = df['close'].rolling(n, min_periods=1).mean()
    df[factor_name] = (df['close'] / df['ma'] - 1)

    del df['ma']

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    close = df['close']
    for param in param_list:
        ma = close.rolling(param, min_periods=1).mean()
        ret[param] = close / ma - 1
    return ret
