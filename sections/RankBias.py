"""
邢不行｜策略分享会
选币策略框架𝓟𝓻𝓸

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import polars as pl


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['rank'] = df.groupby('candle_begin_time')[f'QuoteVolumeMean_{n}'].rank(ascending=True, method='min')
    df['rank_mean'] = df.groupby('symbol')['rank'].transform(lambda x: x.rolling(n, min_periods=1).mean())
    df['rank_bias'] = df['rank'] / df['rank_mean']

    df[factor_name] = df['rank_bias']

    return df


def get_factor_list(n):
    return [
        ('QuoteVolumeMean', n)
    ]


def signal_polars(lf: pl.LazyFrame, params: tuple, factor_name: str) -> pl.LazyFrame:
    """
    Polars 版本的 RankBias 截面因子计算
    排名偏离度 = 当前排名 / 排名的滚动均值

    :param lf: 全量 K 线数据 (LazyFrame)
    :param params: n (窗口期)
    :param factor_name: 因子列名
    :return: 添加了因子列的 LazyFrame
    """
    n = params
    base_col = f'QuoteVolumeMean_{n}'

    # 1) 横截面排名（按时间分组，升序排名）
    lf = lf.with_columns([
        pl.col(base_col)
        .rank(method='min', descending=False)
        .over('candle_begin_time')
        .alias('_rank'),
    ])

    # 2) 排名的滚动均值（按 symbol 分组，确保按时间顺序）
    lf = lf.with_columns([
        pl.col('_rank')
        .rolling_mean(window_size=n, min_periods=1)
        .over('symbol', order_by='candle_begin_time')
        .alias('_rank_mean'),
    ])

    # 3) 计算偏离度
    lf = lf.with_columns([
        (pl.col('_rank') / pl.col('_rank_mean')).alias(factor_name),
    ])

    # 4) 清理临时列
    lf = lf.drop(['_rank', '_rank_mean'])

    return lf
