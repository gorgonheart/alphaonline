"""
邢不行™️ 策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import pandas as pd

extra_data_dict = {
    'coin-btc': ['btc_close']
}


def signal(*args):
    """计算与BTC指数的相关性"""
    df = args[0]
    n = args[1]
    factor_name = args[2]

    # 使用 extra_data_dict 获取的 btc_close 计算指数涨跌幅
    if 'btc_close' in df.columns:
        df['指数涨跌幅'] = df['btc_close'].pct_change()
    else:
        df['指数涨跌幅'] = 0
    df['指数涨跌幅'].fillna(0, inplace=True)

    indicator1 = df['close'].pct_change()
    indicator2 = df['指数涨跌幅']
    corr = indicator1.rolling(n, min_periods=1).corr(indicator2)

    df[factor_name] = corr

    return df


def signal_multi_params(df, param_list) -> dict:
    """多参数与 BTC 指数涨跌幅滚动皮尔逊相关系数计算。

    数学定义：
        1. 基准收益率：r_btc(t) = pct_change(btc_close_t)，缺失值填 0（若无 btc_close 列则全为 0）
        2. 币种收益率：r_coin(t) = pct_change(close_t)
        3. 滚动相关性：CorrBTC_n(t) = Corr(r_coin[t-n+1 : t], r_btc[t-n+1 : t])，min_periods=1, ddof=1

    性能与等价保障：
        调用 `rolling_corr_pandas_exact` 预对齐一次双序列有效掩码与乘积数组，直接驱动
        pandas 底层 Cython 窗口算子，与 `indicator1.rolling(n, min_periods=1).corr(indicator2)`
        达到 0.0 逐位浮点与 NaN 边界完全一致。
    """
    from core.utils.fast_rolling import rolling_corr_pandas_exact

    if not param_list:
        return {}
    if 'btc_close' in df.columns:
        btc_return = df['btc_close'].pct_change().fillna(0)
    else:
        btc_return = pd.Series(0.0, index=df.index)
    coin_return = df['close'].pct_change()
    return rolling_corr_pandas_exact(coin_return, btc_return, param_list)
