def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df[factor_name] = abs(df['close'].pct_change()).rolling(n, min_periods=1).max()
    df.fillna({factor_name: 0}, inplace=True)

    return df