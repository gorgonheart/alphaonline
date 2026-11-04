
import numpy as np
import pandas as pd

def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    # 将参数n和symbol都转换为不带横杠的格式进行比较
    n_normalized = n.replace('-', '')
    symbol_normalized = df['symbol'].str.replace('-', '', regex=False)

    df[factor_name] = np.where(symbol_normalized == n_normalized, 1, np.nan)

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    if not param_list:
        return ret
    symbol_normalized = df['symbol'].str.replace('-', '', regex=False)
    for param in param_list:
        n_normalized = param.replace('-', '')
        ret[param] = pd.Series(np.where(symbol_normalized == n_normalized, 1, np.nan), index=df.index)
    return ret

#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
