import numpy as np
import pandas as pd

#SpotSwap因子代码
def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df[factor_name] = np.where((df['symbol_spot'] != "") & (df['symbol_swap'] != ""), 1, 0)

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    values = pd.Series(np.where((df['symbol_spot'] != '') & (df['symbol_swap'] != ''), 1, 0), index=df.index)
    for param in param_list:
        ret[param] = values
    return ret
