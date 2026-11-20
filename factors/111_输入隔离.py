"""Research control: preserve 111 mathematics, isolate all input mutations."""
import numpy as np
import pandas as pd
from core.utils.fast_rolling import factor_111_param


def signal_multi_params(df, param_list) -> dict:
    """Exact mathematics of legacy factors.111 without deep-copying or mutating df."""
    close = df['close'].to_numpy(dtype=np.float64, copy=False)
    high = df['high'].to_numpy(dtype=np.float64, copy=False)
    low = df['low'].to_numpy(dtype=np.float64, copy=False)
    tp = (close + high + low) / 3.0
    qvolume = df['quote_volume'].to_numpy(dtype=np.float64, copy=True)
    takervolume = df['taker_buy_quote_asset_volume'].to_numpy(dtype=np.float64, copy=True)
    if tp.size >= 2:
        tp[0] = tp[1]
        qvolume[0] = qvolume[1]
        takervolume[0] = takervolume[1]

    idx = df.index
    ret = {}
    for param in param_list:
        n = int(param)
        ret[str(param)] = pd.Series(factor_111_param(tp, qvolume, takervolume, n), index=idx, dtype=np.float64)
    return ret
