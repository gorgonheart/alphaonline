import pandas as pd
import numpy as np
def signal(*args):
    df = args[0]
    n = args[1]  
    factor_name = args[2]

    # 转换时间为 pandas Series
    candle_series = pd.to_datetime(pd.Series(df['candle_begin_time']))
    # 满足有合约有现货的条件
    cond = (df['symbol_swap'] != '') & (df['symbol_spot'] != '')

    # 获取币种的首次出现时间
    if cond.any():
        first_idx = cond[cond].index[0]
        first_time = candle_series.iloc[first_idx]
        # 计算每一行到首次满足条件的时间差 （小时）
        hours_since_first = (candle_series - first_time).dt.total_seconds() / 3600
        # 只有满足条件的行才赋值，其他为0
        df[factor_name] = np.where(cond, hours_since_first, np.nan)
    else:
        df[factor_name] = np.nan
    
    return df


def signal_multi_params(df, param_list) -> dict:
    """保留旧版以标签作为 iloc 位置的行为，规范输入应为 RangeIndex。"""
    ret = dict()
    if not param_list:
        return ret
    if len(df) == 0:
        empty = pd.Series(dtype=np.float64, index=df.index)
        for param in param_list:
            ret[param] = empty
        return ret
    swap_vals = df['symbol_swap'].to_numpy(copy=False)
    spot_vals = df['symbol_spot'].to_numpy(copy=False)
    cond = (swap_vals != '') & (spot_vals != '')
    if cond.any():
        first_pos = int(np.argmax(cond))
        col = df['candle_begin_time']
        if np.issubdtype(col.dtype, np.datetime64) and isinstance(df.index, pd.RangeIndex) and df.index.start == 0 and df.index.step == 1:
            ts_ns = col.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)
            hours_since_first = (ts_ns - ts_ns[first_pos]) / 3.6e12
        else:
            candle_series = pd.to_datetime(pd.Series(col))
            first_idx = df.index[first_pos]
            first_time = candle_series.iloc[first_idx]
            hours_since_first = ((candle_series - first_time).dt.total_seconds() / 3600).to_numpy(dtype=np.float64, copy=False)
        values = pd.Series(np.where(cond, hours_since_first, np.nan), index=df.index, dtype=np.float64)
    else:
        values = pd.Series(np.nan, index=df.index, dtype=np.float64)
    for param in param_list:
        ret[param] = values
    return ret
