"""Versioned causal repair of Cci按异常0401修正; leave the legacy factor intact."""
import numpy as np
import pandas as pd
from core.utils.fast_rolling import cci_causal_168_full


def signal_multi_params(df, param_list) -> dict:
    high = df['high'].to_numpy(dtype=np.float64, copy=False)
    low = df['low'].to_numpy(dtype=np.float64, copy=False)
    close = df['close'].to_numpy(dtype=np.float64, copy=False)
    length = len(df)

    has_times = 'candle_begin_time' in df
    if has_times:
        col = df['candle_begin_time']
        if np.issubdtype(col.dtype, np.datetime64):
            times_ns = col.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)
            nat = np.int64(np.datetime64('NaT', 'ns').view('int64'))
            if (times_ns == nat).any() or (times_ns.size > 1 and (times_ns[1:] <= times_ns[:-1]).any()):
                raise ValueError('CCI requires unique, increasing candle_begin_time values')
        else:
            times = pd.to_datetime(col).reset_index(drop=True)
            if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
                raise ValueError('CCI requires unique, increasing candle_begin_time values')
            times_ns = times.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)
    else:
        times_ns = np.empty(0, dtype=np.int64)

    ret = {}
    idx = pd.RangeIndex(length)
    for param in param_list:
        n = int(param)
        if n <= 0:
            raise ValueError('CCI window must be positive')
        factor_arr = cci_causal_168_full(high, low, close, times_ns, has_times, n)
        ret[str(param)] = pd.Series(factor_arr, index=idx, dtype=np.float64)
    return ret
