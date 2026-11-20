"""
邢不行｜策略分享会
选币策略框架𝓟𝓻𝓸

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行

截面因子：动态回撤阈值（`当前回撤_{n}` 的截面 Z-Score）
-------------------------------------------------------------------------
核心计算定义（每个 `candle_begin_time` 截面独立，无跨期状态依赖）：
    1. 在同一时刻横截面上对 `当前回撤_{n}` 计算均值 `mean` 与样本标准差 `std`（`ddof=1`，忽略 NaN）；
    2. 计算标准化分值 `z = (当前回撤_{n} - mean) / std`；
    3. 将结果中的 NaN/null 填充为 `0`（若出现 `+-inf` 则保持与 pandas 行为一致）。
"""
import polars as pl


# 已对拍：可独立按声明的依赖列计算，不修改其他截面的输入。
shared_input_safe = True


def get_state_input_columns(params):
    return ['candle_begin_time', f'当前回撤_{params}']


def signal_block(frame, params, factor_name, state=None):
    """Each timestamp is a complete market; no history or state is required."""
    return signal_polars(frame.lazy(), params, factor_name).collect(), None


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['回撤Mean'] = df.groupby('candle_begin_time')[f'当前回撤_{n}'].transform('mean')
    df['回撤Std'] = df.groupby('candle_begin_time')[f'当前回撤_{n}'].transform('std')
    df['Zscore'] = (df[f'当前回撤_{n}'] - df['回撤Mean']) / df['回撤Std']
    df['Zscore'].fillna(0, inplace=True)
    df[factor_name] = df['Zscore']

    return df


def get_factor_list(n):
    return [
        ('当前回撤', n),
    ]


def signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame:
    """Polars 版本：当前回撤 的截面 z-score。

    与 pandas signal 严格等价：按 candle_begin_time 求 mean/std（ddof=1），
    z=(x-mean)/std，再 fillna(0)。入口用 fill_nan(None) 把浮点 nan 归一为 null，
    使 mean/std 的 skipna 语义与 pandas transform('mean'|'std') 一致。
    """
    n = params
    col = f'当前回撤_{n}'
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    lf = lf.with_columns([
        pl.col('_dd').mean().over('candle_begin_time').alias('_mean'),
        pl.col('_dd').std().over('candle_begin_time').alias('_std'),
    ])
    lf = lf.with_columns(
        ((pl.col('_dd') - pl.col('_mean')) / pl.col('_std')).alias('_z')
    )
    # fillna(0)：nan/null→0（inf 若出现则保留，与 pandas 一致）
    lf = lf.with_columns(pl.col('_z').fill_nan(0).fill_null(0).alias(factor_name))
    lf = lf.drop(['_dd', '_mean', '_std', '_z'])
    return lf
