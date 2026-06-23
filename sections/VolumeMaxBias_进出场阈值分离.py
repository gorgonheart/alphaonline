"""
VolumeMaxBias_进出场阈值分离 截面因子

进出场阈值分离选币模式：
- 基于 VolumeMaxBias 因子的横截面排名百分比进行选币
- 入场阈值和出场阈值不同，实现更稳定的持仓换手
- 出几个就补几个，保持持仓数量稳定
- 当市场币种数量增多导致目标持仓增加时，额外补入新币

参数: (n, entry_threshold, exit_threshold, start_date)
- n: VolumeMaxBias 因子的滚动窗口
- entry_threshold: 入场阈值（排名百分比），如 0.1 表示排名前 10% 可入选
- exit_threshold: 出场阈值（排名百分比），如 0.9 表示排名超过 90% 才淘汰
- start_date: 策略开始日期

排名方式：VolumeMaxBias 升序排名（值越小排名越靠前，排名百分比越低）

用法示例（config.py 中的 filter_list）:
    ('VolumeMaxBias_进出场阈值分离', (720, 0.1, 0.9, '2021-01-01'), 'val:==1'),
"""
import numpy as np
import pandas as pd
import polars as pl


def _parse_params(params):
    if isinstance(params, (tuple, list)):
        n = int(params[0])
        entry_threshold = float(params[1])
        exit_threshold = float(params[2])
        start_date = str(params[3]) if len(params) > 3 else '2021-01-01'
    else:
        n = int(params)
        entry_threshold = 0.1
        exit_threshold = 0.9
        start_date = '2021-01-01'
    return n, entry_threshold, exit_threshold, start_date


def _calc_selection(df, base_col, entry_threshold, exit_threshold):
    """
    在每个时间截面上，基于 base_col 的排名百分比做进出场阈值分离选币。
    返回一个与 df 等长的 0/1 Series（保持原始索引）。

    df 可以包含现货+合约重复行，内部会去重排名后映射回所有行。
    """
    result = pd.Series(0, index=df.index, dtype=np.int32)

    prev_selected = set()

    for t, grp in df.groupby('candle_begin_time', sort=True):
        valid = grp[grp[base_col].notna()]
        # 去重：同一 symbol 只保留一行用于排名（优先合约 is_spot=0）
        dedup = valid.sort_values('is_spot').drop_duplicates(subset=['symbol'], keep='first')
        market_size = len(dedup)

        if market_size == 0:
            continue

        rank = dedup[base_col].rank(ascending=True, method='first')
        rank_pct = rank / market_size
        rp_dict = dict(zip(dedup['symbol'].values, rank_pct.values))

        target_num = max(int(market_size * entry_threshold), 1)

        if len(prev_selected) == 0:
            selected = {s for s, rp in rp_dict.items() if rp <= entry_threshold}
        else:
            current = set(dedup['symbol'].values)
            still_in = prev_selected & current

            to_exit = {s for s in still_in if rp_dict.get(s, 1.0) > exit_threshold}
            remaining = still_in - to_exit

            num_to_add = max(len(to_exit), target_num - len(remaining))
            num_to_add = max(num_to_add, 0)

            cands = [(s, rp) for s, rp in rp_dict.items()
                     if s not in remaining and rp <= entry_threshold]
            cands.sort(key=lambda x: x[1])

            new_in = {s for s, _ in cands[:num_to_add]}
            selected = remaining | new_in

        # 标记：grp 中所有 symbol 在 selected 里的行都标 1
        mask = grp['symbol'].isin(selected)
        result.loc[mask[mask].index] = 1

        prev_selected = selected

    return result


def signal(*args):
    df = args[0]
    params = args[1]
    factor_name = args[2]

    n, entry_threshold, exit_threshold, start_date = _parse_params(params)
    base_col = f'VolumeMaxBias_{n}'

    df[factor_name] = _calc_selection(df, base_col, entry_threshold, exit_threshold)
    return df


def get_factor_list(params):
    n, _, _, _ = _parse_params(params)
    return [
        ('VolumeMaxBias', n)
    ]


def signal_polars(lf: pl.LazyFrame, params: tuple, factor_name: str) -> pl.LazyFrame:
    """
    Polars 版本：collect 到 pandas 计算后，只添加因子列，保持原始行数和行顺序。
    """
    n, entry_threshold, exit_threshold, start_date = _parse_params(params)
    base_col = f'VolumeMaxBias_{n}'

    df = lf.collect().to_pandas()
    sel = _calc_selection(df, base_col, entry_threshold, exit_threshold)

    return lf.collect().with_columns(
        pl.Series(name=factor_name, values=sel.values)
    ).lazy()
