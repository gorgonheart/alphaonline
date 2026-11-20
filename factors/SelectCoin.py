
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
    """多参数指定币种筛选指示因子（命中目标币种为 1.0，否则为 NaN）。

    业务规则：
        将参数 `param`（如 `'BTC-USDT'`）与 K 线 `symbol` 列统一去除 `'-'` 后比对。
    性能优化：
        单币种 K 线表（`symbol` 列全表恒定）仅做 1 次标量字符串标准化与常数填充，
        避免对 5,000 行重复执行 `.str.replace('-', '')`；若遇混排多币种表则自动回退逐行比对。
    """
    ret = dict()
    if not param_list:
        return ret
    n_rows = len(df)
    if n_rows == 0:
        empty = pd.Series(dtype=np.float64, index=df.index)
        for param in param_list:
            ret[param] = empty
        return ret

    sym_col = df['symbol']
    sym_arr = sym_col.to_numpy(copy=False)
    first_sym = sym_arr[0]
    if isinstance(first_sym, str) and bool((sym_arr == first_sym).all()):
        symbol_norm = first_sym.replace('-', '')
        ones_series = None
        nan_series = pd.Series(np.nan, index=df.index, dtype=np.float64)
        for param in param_list:
            target_norm = param.replace('-', '')
            if symbol_norm == target_norm:
                if ones_series is None:
                    ones_series = pd.Series(1.0, index=df.index, dtype=np.float64)
                ret[param] = ones_series
            else:
                ret[param] = nan_series
        return ret

    symbol_normalized = sym_col.str.replace('-', '', regex=False)
    for param in param_list:
        target_norm = param.replace('-', '')
        ret[param] = pd.Series(np.where(symbol_normalized == target_norm, 1, np.nan), index=df.index)
    return ret

#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
