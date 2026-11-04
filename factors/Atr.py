import pandas as pd

eps = 1e-8


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    """
    N=20
    TR=MAX(HIGH-LOW,ABS(HIGH-REF(CLOSE,1)),ABS(LOW-REF(CLOSE,1)))
    ATR=MA(TR,N)
    MIDDLE=MA(CLOSE,N)
    """
    df['c1'] = df['high'] - df['low']  # HIGH-LOW
    df['c2'] = abs(df['high'] - df['close'].shift(1))  # ABS(HIGH-REF(CLOSE,1)
    df['c3'] = abs(df['low'] - df['close'].shift(1))  # ABS(LOW-REF(CLOSE,1))
    df['TR'] = df[['c1', 'c2', 'c3']].max(
        axis=1)  # TR=MAX(HIGH-LOW,ABS(HIGH-REF(CLOSE,1)),ABS(LOW-REF(CLOSE,1)))
    df['_ATR'] = df['TR'].rolling(n, min_periods=1).mean()  # ATR=MA(TR,N)
    df['middle'] = df['close'].rolling(n, min_periods=1).mean()  # MIDDLE=MA(CLOSE,N)
    # ATR指标去量纲
    df[factor_name] = df['_ATR'] / (df['middle'] + eps)

    del df['c1']
    del df['c2']
    del df['c3']
    del df['TR']
    del df['_ATR']
    del df['middle']

    return df


def get_parameter():
    param_list = []
    n_list = [3, 5, 7, 8, 14, 16, 21, 24, 30, 55]
    for n in n_list:
        param_list.append(n)

    return param_list


def signal_multi_params(df, param_list) -> dict:
    """共享 TR，保留原窗口、首根与 NaN 语义，不写入行情。"""
    ret = dict()
    if not param_list:
        return ret
    close = df['close']
    previous_close = close.shift(1)
    tr = pd.concat([df['high'] - df['low'],
                    abs(df['high'] - previous_close),
                    abs(df['low'] - previous_close)], axis=1).max(axis=1)
    for param in param_list:
        atr = tr.rolling(param, min_periods=1).mean()
        middle = close.rolling(param, min_periods=1).mean()
        ret[param] = atr / (middle + eps)
    return ret
