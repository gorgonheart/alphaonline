"""Research control: preserve 111 mathematics, isolate all input mutations.

因子数学定义（完全保留原版 `factors/111.py` 公式与首根覆盖规则，仅隔离对输入 `df` 的就地改写）：
1. 典型价格与首根对齐（仅在局部变量副本上修改 index 0，绝不污染上游传入的 `df`）：
   - `tp = (close + high + low) / 3`
   - 若行数 >= 2，令 `tp[0] = tp[1]`、`qvolume[0] = qvolume[1]`、`takervolume[0] = takervolume[1]`
2. 成交额相对强度与波动包络（窗口 `n`，`min_periods=n`）：
   - `qv_ma = Mean(qvolume, n, min_periods=n)`
   - `qv_rel = qvolume / qv_ma`
   - `high_env = Mean(qv_rel, n, min_periods=n)`
   - `low_env = Mean(|qv_rel - 1|, n, min_periods=n)`
   - `max_env = max(high_env, low_env)`，`cs = max_env / (1 + |max_env|)`
3. 主动买入占比极值（窗口 `n`，`min_periods=1`）：
   - `vol_sum = Sum(qvolume, n, min_periods=1)`
   - `buy_sum = Sum(taker_buy_quote_asset_volume, n, min_periods=1)`
   - `buy_ratio = buy_sum / vol_sum`
   - `tmax = max(|buy_ratio|, |buy_ratio - 1|)`
4. 价格偏离度复合动量及平滑输出：
   - `ma = Mean(tp, n, min_periods=n)`，`tp_rel = tp / ma - 1`
   - `mtm = tp_rel * cs * tmax * |tp_rel|`
   - `factor_111 = Mean(mtm, n, min_periods=1)`

性能实现：
   底层调用 `core.utils.fast_rolling.factor_111_param`（`@njit(cache=True)` Kahan 补偿滑窗核，
   内置连续 0 成交量退窗残差重算与 `+-inf` 传播保护），与原版 pandas 实现误差 <= 1.5e-14。
"""
import numpy as np
import pandas as pd
from core.utils.fast_rolling import factor_111_param


def signal_multi_params(df, param_list) -> dict:
    """计算多参数 `111_输入隔离` 因子，保证输入 DataFrame 零副作用。"""
    close = df['close'].to_numpy(dtype=np.float64, copy=False)
    high = df['high'].to_numpy(dtype=np.float64, copy=False)
    low = df['low'].to_numpy(dtype=np.float64, copy=False)
    typical_price = (close + high + low) / 3.0

    # 仅拷贝会被首行覆盖的成交额与主动买入额列，避免对整张 K 线表做 deepcopy
    quote_volume = df['quote_volume'].to_numpy(dtype=np.float64, copy=True)
    taker_buy_quote_volume = df['taker_buy_quote_asset_volume'].to_numpy(dtype=np.float64, copy=True)
    if typical_price.size >= 2:
        typical_price[0] = typical_price[1]
        quote_volume[0] = quote_volume[1]
        taker_buy_quote_volume[0] = taker_buy_quote_volume[1]

    idx = df.index
    ret = {}
    for param in param_list:
        window = int(param)
        factor_values = factor_111_param(typical_price, quote_volume, taker_buy_quote_volume, window)
        ret[str(param)] = pd.Series(factor_values, index=idx, dtype=np.float64)
    return ret
