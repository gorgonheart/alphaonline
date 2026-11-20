"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

# 这里把LUNA拉黑，因为回撤阈值空头中有一些参数可能会因为LUNA导致纯空资金曲线爆仓，影响后续的总策略资金曲线
LUNA_SYMBOLS = {'LUNA-USDT', 'LUNAUSDT'}


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['vwap'] = df['quote_volume'] / df['volume']
    df['当前回撤'] = df['vwap'] / df['vwap'].rolling(n, min_periods=1).max() - 1

    df[factor_name] = df['当前回撤']

    # 在这里把LUNA拉黑了，否则纯空头策略的资金曲线可能会爆仓，影响后续的总策略资金曲线
    df.loc[df['symbol'].isin(LUNA_SYMBOLS), factor_name] = float('nan')

    return df


def signal_multi_params(df, param_list) -> dict:
    """多参数成交均价（VWAP）滚动最高点回撤计算。

    数学定义：
        1. 单根 K 线成交均价：VWAP_t = quote_volume_t / volume_t
        2. 滚动最大回撤（负值或 0）：
           当前回撤_n(t) = VWAP_t / max(VWAP[t-n+1 : t], min_periods=1) - 1
        3. 风控黑名单保护：
           若币种属于 `LUNA_SYMBOLS`（`LUNA-USDT` / `LUNAUSDT`），全序列置为 NaN。

    性能优化：
        单币种 DataFrame 仅在首元素上做 1 次 `symbol in LUNA_SYMBOLS` 标量集合判断，
        避免每币对 5,000 行构造字符串哈希表；混排多币种输入自动回退逐行 `.isin()` 掩码。
    """
    import numpy as np
    import pandas as pd

    ret = dict()
    if not param_list:
        return ret

    n_rows = len(df)
    if n_rows == 0:
        empty = pd.Series(dtype=np.float64, index=df.index)
        for param in param_list:
            ret[str(param)] = empty
        return ret

    sym_col = df['symbol']
    sym_arr = sym_col.to_numpy(copy=False)
    first_sym = sym_arr[0]
    is_uniform_symbol = isinstance(first_sym, str) and bool((sym_arr == first_sym).all())

    if is_uniform_symbol and first_sym in LUNA_SYMBOLS:
        nan_series = pd.Series(np.nan, index=df.index, dtype=np.float64)
        for param in param_list:
            ret[str(param)] = nan_series
        return ret

    vwap = df['quote_volume'] / df['volume']
    luna_mask = None if is_uniform_symbol else sym_col.isin(LUNA_SYMBOLS)

    for param in param_list:
        n = int(param)
        drawdown = vwap / vwap.rolling(n, min_periods=1).max() - 1
        ret[str(param)] = drawdown if luna_mask is None else drawdown.mask(luna_mask)
    return ret
