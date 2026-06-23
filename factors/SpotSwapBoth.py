import numpy as np

#SpotSwap因子代码
def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df[factor_name] = np.where((df['symbol_spot'] != "") & (df['symbol_swap'] != ""), 1, 0)

    return df