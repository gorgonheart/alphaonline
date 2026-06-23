def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['mean'] = df['close'].rolling(n).mean()
    df['std'] = df['close'].rolling(n).std(ddof=0)
    df['lower'] = df['mean'] - 2 * df['std']
    df[factor_name] = df['close'] - df['lower']

    return df