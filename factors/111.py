import numpy as np


# https://bbs.quantclass.cn/thread/56619
#  def signal(df, n, factor_name):

#     df['TP'] = (df['close'] + df['high'] + df['low']) / 3

#     # 计算典型价格的n周期简单移动平均值。
#     # 移动平均能够帮助我们识别长期趋势，平滑短期波动。
#     df['ma'] = df['TP'].rolling(n).mean()

#     # 计算加权高点和加权低点。
#     # 这两个指标通过评估成交量与其过去平均值的相对变化，反映市场参与者的积极性变化。
#     df['H'] = (df['quote_volume'] / df['quote_volume'].rolling(n).mean()).rolling(n).mean()
#     df['L'] = abs(df['quote_volume'] / df['quote_volume'].rolling(n).mean() - 1).rolling(n).mean()

#     # 计算压缩比例因子，用于量化市场压力的方向和大小。
#     df['Max'] = np.where(df['H'] > df['L'], df['H'], df['L'])
#     df['cs'] = df['Max'] / (1 + abs(df['Max']))

#     # 计算市场主导买卖力量的比率。
#     # 主动买入量与成交量的比率反映了市场是被买方还是卖方主导。
#     df['volume1'] = df['quote_volume'].rolling(n, min_periods=1).sum()
#     df['buy_volume'] = df['taker_buy_quote_asset_volume'].rolling(n, min_periods=1).sum()
#     df['taker_1'] = abs((df['buy_volume'] / df['volume1']))
#     df['taker_2'] = abs((df['buy_volume'] / df['volume1'] - 1))
#     df['TMax'] = np.where(df['taker_1'] > df['taker_2'], df['taker_1'], df['taker_2'])

#     # 结合动量概念和市场主导力量，计算最终因子值。
#     # 这一步骤通过综合考量价格动量、市场压力和主导买卖力量，构建出最终的因子值。
#     df['mtm'] = (df['TP'] / df['ma'] - 1) * df['cs'] * df['TMax'] * abs(df['TP'] / df['ma'] - 1)
#     df[factor_name] = df['mtm'].rolling(n, min_periods=1).mean()

#     # 清理临时计算列，以保留最终所需的因子值。
#     del df['TP'], df['ma'], df['H'], df['L'], df['Max'], df['cs'], df['taker_1'], df['taker_2'], df['TMax'], df[
#         'volume1'], df['buy_volume'], df['mtm']

#     return df


def signal_multi_params(df, param_list) -> dict:
    # 计算典型价格，它是高、低、收盘价的简单平均。
    # 这一步的目的是减少市场波动对单个价格敏感度的影响，提供一个更为稳定的价格指标。
    tp = (df['close'] + df['high'] + df['low']) / 3
    qvolume = df['quote_volume']
    takervolume = df['taker_buy_quote_asset_volume']
    if (len(tp) >= 2):
        tp.iloc[0] = tp.iloc[1]
        qvolume.iloc[0] = qvolume.iloc[1]
        takervolume.iloc[0] = takervolume.iloc[1]


  
    ret = dict()
    for param in param_list:
        n = int(param)

        # 计算典型价格的n周期简单移动平均值。
        # 移动平均能够帮助我们识别长期趋势，平滑短期波动。
        ma = tp.rolling(n).mean()

        # 计算加权高点和加权低点。
        # 这两个指标通过评估成交量与其过去平均值的相对变化，反映市场参与者的积极性变化。
        high = (qvolume / qvolume.rolling(n).mean()).rolling(n).mean()
        low = abs(qvolume / qvolume.rolling(n).mean() - 1).rolling(n).mean()

        # 计算压缩比例因子，用于量化市场压力的方向和大小。
        max = np.where(high > low, high, low)
        cs = max / (1 + abs(max))


        # 计算市场主导买卖力量的比率。
        # 主动买入量与成交量的比率反映了市场是被买方还是卖方主导。
        volume1 = qvolume.rolling(n, min_periods=1).sum()
        buy_volume = takervolume.rolling(n, min_periods=1).sum()
        taker_1 = abs((buy_volume /volume1))
        taker_2 = abs((buy_volume / volume1 - 1))
        TMax = np.where(taker_1 > taker_2, taker_1, taker_2)

        # 结合动量概念和市场主导力量，计算最终因子值。
        # 这一步骤通过综合考量价格动量、市场压力和主导买卖力量，构建出最终的因子值。
        mtm = (tp / ma - 1) * cs * TMax * abs(tp / ma - 1)
        ret[str(param)] = mtm.rolling(n, min_periods=1).mean()


    return ret
        