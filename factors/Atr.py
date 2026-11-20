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
    """多参数去量纲平均真实波幅（Normalized ATR）计算。

    数学定义：
        1. 真实波幅（True Range）：
           TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
           （首根 K 线因 Close_{t-1} 为 NaN，按 skipna 语义退化为 High_0 - Low_0）
        2. 滚动均幅与中轨：
           ATR_n(t) = Mean(TR[t-n+1 : t], min_periods=1)
           Middle_n(t) = Mean(Close[t-n+1 : t], min_periods=1)
        3. 去量纲化：
           Factor_n(t) = ATR_n(t) / (Middle_n(t) + 1e-8)

    性能优化：
        用 IEEE-754 `np.fmax` 向量化逐元素取最大值替代 `pd.concat([c1, c2, c3], axis=1).max(axis=1)`，
        避免构造二维临时 DataFrame 及行向归约开销，同时 100% 保持 skipna 与全 NaN 传播语义。
    """
    import numpy as np

    ret = dict()
    if not param_list:
        return ret

    close = df['close']
    high_arr = df['high'].to_numpy(dtype=np.float64, copy=False)
    low_arr = df['low'].to_numpy(dtype=np.float64, copy=False)
    prev_close_arr = close.shift(1).to_numpy(dtype=np.float64, copy=False)

    hl_range = high_arr - low_arr
    high_prev_gap = np.abs(high_arr - prev_close_arr)
    low_prev_gap = np.abs(low_arr - prev_close_arr)
    true_range = pd.Series(np.fmax(hl_range, np.fmax(high_prev_gap, low_prev_gap)), index=df.index, copy=False)

    for param in param_list:
        atr = true_range.rolling(param, min_periods=1).mean()
        middle = close.rolling(param, min_periods=1).mean()
        ret[param] = atr / (middle + eps)
    return ret
