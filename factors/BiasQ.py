"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""


# def signal(*args):
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]

#     df['bias'] = df['close'] / df['close'].rolling(n, min_periods=1).mean() - 1

#     df[factor_name] = df['bias'].rolling(n, min_periods=1).rank(ascending=True, pct=True)

#     return df


def signal_multi_params(df, param_list) -> dict:
    """多参数收盘价乖离率滚动分位数（BiasQ）计算。

    数学定义：
        1. 乖离率：bias_n(t) = close_t / Mean(close[t-n+1 : t], min_periods=1) - 1
        2. 滚动分位数：BiasQ_n(t) = RankPct(bias_n[t-n+1 : t], ascending=True, pct=True)
    """
    import numpy as np
    import pandas as pd
    from core.utils.fast_rolling import rolling_rank_pct
    idx = df.index
    ret = dict()
    for param in param_list:
        n = int(param)
        bias = df['close'] / df['close'].rolling(n, min_periods=1).mean() - 1
        rank_pct_arr = rolling_rank_pct(bias.to_numpy(dtype=np.float64, copy=False), n)
        ret[str(param)] = pd.Series(rank_pct_arr, index=idx)
    return ret
