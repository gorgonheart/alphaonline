def signal(*args):
    # Bias
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['ma'] = df['close'].rolling(n, min_periods=1).mean()
    df[factor_name] = (df['close'] / df['ma'] - 1)

    del df['ma']

    return df
