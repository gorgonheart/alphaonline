"""
邢不行｜策略分享会
选币策略框架𝓟𝓻𝓸

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行

截面因子：TopKTo2K（前期强势进入前 10%，当前回落至 21% 名次之外的“高位回落”标志）
-------------------------------------------------------------------------
核心计算定义（仅作用于合约样本 `is_spot != 1`，现货样本恒为 null/NaN）：
    1. 每根 K 线横截面内，对基础因子 `base_col = f"{base_factor}_{base_window}"` 做降序排名
       （`method='min'`），记截面内合约币种总数为 `M`；
    2. 强势门槛：`top_k = max(ceil(M * 0.1), 1)`，当 `rank <= top_k` 时标记 `is_top_now = 1`；
    3. 滚动记忆：单币种过去 `n` 根 K 线内若至少出现一次 `is_top_now == 1`，记 `was_top_n = True`；
    4. 回落门槛：`bottom_k = max(ceil(M * 0.21), 1)`（沿用研究 2.1 退出系数 `0.1 * 2.1 = 0.21`），
       当前排名 `rank >= bottom_k` 时记 `in_bottom_now = True`；
    5. 输出：`was_top_n & in_bottom_now`（0/1 整数），表示“近 n 小时曾跻身前 10%，当前已回落至前 21% 以外”。

分块状态续算说明：
    - `get_state_input_columns(params)` 声明最小依赖列集（仅 4 列），避免读取无关 K 线字段；
    - `signal_block` 仅在跨块状态中保留每币种最近 `n` 根 K 线的 `_is_top_now` 尾窗，
      不持有全历史价格表。
"""
import numpy as np
import pandas as pd
import polars as pl


# 已对拍：可独立按声明的依赖列计算，不修改其他截面的输入。
shared_input_safe = True


def _historical_pandas_signal(*args):
    df = args[0]

    params = args[1]  # params = (n, base_factor, base_window)
    factor_name = args[2]

    # 解包
    n = params[0]
    base_factor = params[1]
    base_window = params[2]
    # 进入前阈值：固定0.1（前10%）
    top_raw = 0.1

    # 基础因子列名
    base_col = f'{base_factor}_{base_window}'

    con = df['is_spot'] != 1

    # 1) 横截面排名
    df.loc[con, 'rank'] = df.loc[con].groupby('candle_begin_time')[base_col] \
        .rank(ascending=False, method='min')

    # 2) 进入过前 top_raw（百分比或绝对名次）
    if top_raw < 1:
        df.loc[con, 'market_size'] = df.loc[con].groupby('candle_begin_time')['symbol'].transform('size')
        df.loc[con, 'top_k_dyn'] = np.ceil(df.loc[con, 'market_size'] * top_raw).clip(lower=1)
    else:
        const_top = max(int(np.ceil(top_raw)), 1)
        df.loc[con, 'top_k_dyn'] = const_top

    df.loc[con, 'is_top_pct_now'] = (df.loc[con, 'rank'] <= df.loc[con, 'top_k_dyn']).astype(int)

    # 过去 n 根K线内是否曾进入过前 top_raw
    # is_top_pct_now 是 0/1，因此 rolling(max) 等价于 rolling(sum)>0，可用累计和替代滚动计算
    is_top = df.loc[con, 'is_top_pct_now']
    sym = df.loc[con, 'symbol']
    cs = is_top.groupby(sym).cumsum()
    cs_n = cs.groupby(sym).shift(n).fillna(0)
    df.loc[con, 'was_top_n'] = (cs - cs_n).gt(0)

    # 3) 退出前阈值的2倍（同样支持比例/绝对名次）
    bottom_raw = top_raw * 2
    if bottom_raw < 1:
        df.loc[con, 'bottom_rank_min'] = np.ceil(df.loc[con, 'market_size'] * bottom_raw).clip(lower=1)
    else:
        const_bottom = max(int(np.ceil(bottom_raw)), 1)
        df.loc[con, 'bottom_rank_min'] = const_bottom
    df.loc[con, 'in_bottom_now'] = (df.loc[con, 'rank'] >= df.loc[con, 'bottom_rank_min'])

    # 4) 因子
    df.loc[con, factor_name] = (df.loc[con, 'was_top_n'] & df.loc[con, 'in_bottom_now']).astype(int)

    # 清理
    tmp = [
        'rank', 'market_size', 'top_k_dyn', 'is_top_pct_now',
        'was_top_n', 'bottom_rank_min', 'in_bottom_now'
    ]
    df.drop(columns=[c for c in tmp if c in df.columns], inplace=True)

    return df


def signal(*args):
    """Both entry points use the research 2.1 exit boundary and market grouping."""
    df, params, factor_name = args[:3]
    result = signal_polars(pl.from_pandas(df).lazy(), params, factor_name).collect()
    df[factor_name] = result.get_column(factor_name).to_numpy()
    return df


def get_factor_list(params):
    base_factor = params[1]
    base_window = params[2]
    return [
        (base_factor, base_window)
    ]


def get_state_input_columns(params):
    """声明截面分块计算的最小依赖列集（避免读入 close/next_close/symbol_spot 等无关 K 线列）。"""
    return ['candle_begin_time', 'symbol', 'is_spot', f'{params[1]}_{params[2]}']


def _rank_block(lf, params):
    n = params[0]
    base_factor = params[1]
    base_window = params[2]
    top_raw = 0.1  # 进入前阈值：前 10%

    base_col = f'{base_factor}_{base_window}'

    # 只处理合约数据（is_spot != 1）
    # 使用 filter partition 策略：仅在合约数据中进行排名计算
    # 注意：ranking 和 market_size 必须只在合约币种中计算

    # 1) 横截面排名（按时间分组，对 base_col 降序排名）
    # 使用 filter().over() 模式：只对 is_spot != 1 的数据进行分组排名
    lf = lf.with_columns([
        pl.when(pl.col('is_spot') != 1)
        .then(
            pl.col(base_col)
            .rank(method='min', descending=True)
            .over(
                pl.col('candle_begin_time'),
                pl.col('is_spot') != 1  # 分区内排名
            )
        )
        .otherwise(pl.lit(None))
        .alias('_rank'),
    ])

    # 2) 市场规模（每个时间点的合约币种数量）
    # 需要单独计算合约币种的数量
    lf = lf.with_columns([
        pl.when(pl.col('is_spot') != 1)
        .then(
            (pl.col('is_spot') != 1).cast(pl.Int32).sum().over('candle_begin_time')
        )
        .otherwise(pl.lit(None))
        .alias('_market_size'),
    ])

    # 3) 动态 top_k 阈值
    lf = lf.with_columns([
        (pl.col('_market_size') * top_raw).ceil().clip(lower_bound=1).alias('_top_k'),
    ])

    # 4) 当前是否在 top_k 内
    lf = lf.with_columns([
        (pl.col('_rank') <= pl.col('_top_k')).cast(pl.Int32).alias('_is_top_now'),
    ])

    return lf


def _finish_window(lf, params, factor_name):
    n = params[0]
    top_raw = 0.1
    # 5) 过去 n 根 K 线内是否曾进入过前 top_raw
    # 使用 cumsum 和 shift 实现滚动窗口
    lf = lf.with_columns([
        pl.col('_is_top_now')
        .fill_null(0)
        .cum_sum()
        .over(['symbol', 'is_spot'])
        .alias('_cumsum'),
    ])

    lf = lf.with_columns([
        pl.col('_cumsum')
        .shift(n)
        .over(['symbol', 'is_spot'])
        .fill_null(0)
        .alias('_cumsum_n'),
    ])

    lf = lf.with_columns([
        (pl.col('_cumsum') - pl.col('_cumsum_n')).gt(0).alias('_was_top_n'),
    ])

    # 6) 退出前阈值的 2 倍
    bottom_raw = top_raw * 2.1
    lf = lf.with_columns([
        (pl.col('_market_size') * bottom_raw).ceil().clip(lower_bound=1).alias('_bottom_k'),
    ])

    lf = lf.with_columns([
        (pl.col('_rank') >= pl.col('_bottom_k')).alias('_in_bottom_now'),
    ])

    # 7) 最终因子：曾在 top 且现在在 bottom
    # 只对合约数据计算，现货数据设为 null
    lf = lf.with_columns([
        pl.when(pl.col('is_spot') != 1)
        .then((pl.col('_was_top_n') & pl.col('_in_bottom_now')).cast(pl.Int32))
        .otherwise(pl.lit(None))
        .alias(factor_name),
    ])

    # 8) 清理临时列
    tmp_cols = ['_rank', '_market_size', '_top_k', '_is_top_now',
                '_cumsum', '_cumsum_n', '_was_top_n', '_bottom_k', '_in_bottom_now']
    lf = lf.drop([c for c in tmp_cols if c in lf.collect_schema().names()])

    return lf


def signal_polars(lf: pl.LazyFrame, params: tuple, factor_name: str) -> pl.LazyFrame:
    """Original whole-history entry; shared expressions also serve block callers."""
    return _finish_window(_rank_block(lf, params), params, factor_name)


def signal_block(frame, params, factor_name, state=None):
    """Carry only n prior observations per symbol/market, not full price history.

    Rank the complete current cross sections before prepending prior indicators.
    A missing hour does not advance the rolling window; an actual null row does.
    """
    columns = ['symbol', 'is_spot', '_rank', '_market_size', '_is_top_now']
    ranked = _rank_block(frame.lazy(), params).select(columns).collect()
    combined = ranked if state is None else pl.concat([state['tail'], ranked])
    output = _finish_window(combined.lazy(), params, factor_name).select(factor_name).collect().tail(len(frame))
    n = params[0]
    tail = combined.group_by(['symbol', 'is_spot'], maintain_order=True).tail(n) if n else combined.head(0)
    return frame.with_columns(output[factor_name]), {'tail': tail}
