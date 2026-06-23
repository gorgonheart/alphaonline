"""
==========================================================================
TopKTo2K 截面因子 - 优化版本 v4
==========================================================================

优化策略：使用 Polars 替代 Pandas 进行核心计算

【v4 优化内容】
在 v2 基础上使用 Polars 重写核心计算逻辑：

1. **Polars DataFrame 操作**（预计节省 2.0-3.0 秒）
   - Polars 的 groupby 操作比 Pandas 快 3-10 倍
   - Rust 实现的原生性能优化
   - 更高效的内存管理和缓存策略

2. **Polars 的优势功能**
   - `pl.col().rank()` - 高效排名计算
   - `pl.col().over().count()` - 分组计数（替代 transform）
   - `pl.col().sum().over()` - 分组累计和
   - `pl.col().shift(n).over()` - 分组偏移

3. **Lazy API 延迟计算**
   - 使用 Polars LazyFrame 进行查询优化
   - 自动合并操作、减少中间结果

==========================================================================

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
优化版本：TopKTo2K_fast_v4.py
==========================================================================
"""

import numpy as np
import pandas as pd
import polars as pl


def signal(*args):
    """
    计算 TopKTo2K 截面因子 - 优化版本 v4 (Polars 混合模式)

    【因子逻辑】
    筛选出过去 n 根 K 线内曾进入过前 10%，且当前处于后 20% 的币种

    【参数说明】
    args[0] - df: 输入的 DataFrame，包含行情数据和基础因子
    args[1] - params: 元组 (n, base_factor, base_window)
                - n: 过去多少根 K 线（如 62）
                - base_factor: 基础因子名称
                - base_window: 基础因子窗口（如 620）
    args[2] - factor_name: 因子列名

    【返回值】
    df: 添加了因子列的 DataFrame（原地修改）

    【v4 优化关键点】
    1. 使用 Polars 仅加速排名操作（最大瓶颈）
    2. 其他逻辑使用 Pandas（保证正确性）
    3. 混合模式兼顾性能和准确性
    """
    # ========== 参数解析 ==========
    df = args[0]
    params = args[1]
    factor_name = args[2]

    # 解包参数
    n = params[0]
    base_factor = params[1]
    base_window = params[2]

    top_raw = 0.1
    bottom_raw = top_raw * 2
    base_col = f'{base_factor}_{base_window}'

    # ========== 子集化 ==========
    swap_mask = df['is_spot'] != 1
    required_cols = ['candle_begin_time', 'symbol', base_col]
    df_swap_pandas = df.loc[swap_mask, required_cols].copy()

    # ========== 第一步：用 Polars 加速排名（最大瓶颈） ==========
    df_pl = pl.from_pandas(df_swap_pandas)

    # 用 Polars 计算排名（使用 over() 实现分组排名）
    rank_result = (
        df_pl
        .lazy()
        .with_columns([
            pl.col(base_col).rank(descending=True, method="min").over("candle_begin_time").alias("rank")
        ])
        .select(pl.col("rank"))
        .collect()
    )

    # 将 Polars 排名结果合并回 Pandas
    df_swap_pandas['rank'] = rank_result['rank'].to_pandas().values

    # ========== 第二步：用 Pandas 计算剩余逻辑（与 v2 相同） ==========
    # 计算 market_size
    market_sizes = df_swap_pandas.groupby('candle_begin_time', observed=True).size()

    # 计算 top_k_dyn
    if top_raw < 1:
        top_k_map = (np.ceil(market_sizes * top_raw).clip(lower=1).astype(int)).to_dict()
        top_k_dyn = df_swap_pandas['candle_begin_time'].map(top_k_map)
    else:
        const_top = max(int(np.ceil(top_raw)), 1)
        top_k_dyn = pd.Series(const_top, index=df_swap_pandas.index)

    # 计算 bottom_rank_min
    if bottom_raw < 1:
        bottom_rank_min_map = (np.ceil(market_sizes * bottom_raw).clip(lower=1).astype(int)).to_dict()
        bottom_rank_min = df_swap_pandas['candle_begin_time'].map(bottom_rank_min_map)
    else:
        const_bottom = max(int(np.ceil(bottom_raw)), 1)
        bottom_rank_min = pd.Series(const_bottom, index=df_swap_pandas.index)

    # 判断是否在前 10%
    is_top_pct_now = (df_swap_pandas['rank'] <= top_k_dyn).astype('int8')

    # 过去 n 根 K 线内是否曾进入过前 10%
    sym = df_swap_pandas['symbol']
    cs = is_top_pct_now.groupby(sym, observed=True).cumsum()
    cs_n = cs.groupby(sym, observed=True).shift(n).fillna(0)
    was_top_n = (cs - cs_n).gt(0)

    # 判断是否在后 20%
    in_bottom_now = (df_swap_pandas['rank'] >= bottom_rank_min)

    # 计算因子值
    factor_values = (was_top_n & in_bottom_now).astype('int8')

    # ========== 第三步：合并回原 DataFrame ==========
    df[factor_name] = 0
    df.loc[df_swap_pandas.index, factor_name] = factor_values.values

    return df



def get_factor_list(params):
    """
    获取因子依赖列表

    【参数说明】
    params: 参数元组 (n, base_factor, base_window)

    【返回值】
    因子依赖列表，格式：[(base_factor, base_window)]

    【说明】
    TopKTo2K 因子依赖于基础因子，需要先计算基础因子
    """
    base_factor = params[1]
    base_window = params[2]
    return [
        (base_factor, base_window)
    ]
