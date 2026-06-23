"""
邢不行｜策略分享会
选币策略框架𝓟𝓻𝓸

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import numpy as np
import pandas as pd
from numba import njit


@njit
def compute_holdings_numba_pct(times, symbols, rank_pcts, n_symbols, prev_holdings,
                               select_pct=0.10, threshold_pct=0.9, active_start_time_id=0, _d=1):
    """
    纯增量更新版 numba 计算函

    参数:
        times:         时间ID数组（整数编码，相同时间点值相同，按时间升序排列）
        symbols:       币种ID数组
        rank_pcts:     百分比排名数组 (0.0-1.0)
        n_symbols:     symbol_to_id 映射的总数量
        prev_holdings: 初始持仓数组，长度 n_symbols，持仓为 >= 0，未持仓为 -1.0
        select_pct:    选币百分比
        threshold_pct: 出场阈值
        active_start_time_id: 从该时间ID开始做增量更新，之前的时间只填充缓冲区

    返回:
        result: 持仓标记数组（0/1）
    """
    n_rows = len(times)
    result = np.zeros(n_rows, dtype=np.int32)
    current_holdings = prev_holdings.copy()

    # 环形缓冲区
    _d = _d % 11
    _buf_size = _d if _d > 0 else 1
    _rank = np.full((_buf_size, n_symbols), -1.0, dtype=np.float64)
    hist_ptr = 0

    current_time = times[0]
    start_idx = 0

    max_coins = 5000
    temp_indices = np.empty(max_coins, dtype=np.int32)
    temp_rank_pcts = np.empty(max_coins, dtype=np.float64)

    for i in range(n_rows + 1):
        if i == n_rows or (i > 0 and times[i] != current_time):
            end_idx = i
            n_current = end_idx - start_idx

            # 仅对 active 期（>= active_start_time_id）执行持仓增量更新
            if current_time >= active_start_time_id:
                keep_symbols = np.zeros(n_symbols, dtype=np.int32)
                keep_count = 0

                # 第一遍：找满足保留条件的上期持仓币种（出场用当期排名）
                for j in range(n_current):
                    idx = start_idx + j
                    symbol_id = symbols[idx]
                    if current_holdings[symbol_id] >= 0 and rank_pcts[idx] <= threshold_pct:
                        keep_symbols[symbol_id] = 1
                        keep_count += 1

                target_count = int(n_current * select_pct)
                if target_count < 1:
                    target_count = 1
                need_count = target_count - keep_count

                # 清空持仓，准备写入本期结果
                current_holdings[:] = -1.0

                # 第二遍：标记保留币种
                for j in range(n_current):
                    idx = start_idx + j
                    symbol_id = symbols[idx]
                    if keep_symbols[symbol_id] == 1:
                        result[idx] = 1
                        current_holdings[symbol_id] = rank_pcts[idx]

                # 第三遍：补充新币种
                if need_count > 0:
                    available_count = 0
                    for j in range(n_current):
                        idx = start_idx + j
                        symbol_id = symbols[idx]
                        if keep_symbols[symbol_id] == 0:
                            if _d == 0:
                                _r = rank_pcts[idx]
                            else:
                                _r = _rank[hist_ptr, symbol_id]
                            if _r >= 0:
                                temp_indices[available_count] = j
                                temp_rank_pcts[available_count] = _r
                                available_count += 1

                    if available_count > 0:
                        valid_indices = temp_indices[:available_count]
                        valid_rank_pcts = temp_rank_pcts[:available_count]
                        sorted_order = np.argsort(valid_rank_pcts)
                        select_count = min(need_count, available_count)
                        for k in range(select_count):
                            j = valid_indices[sorted_order[k]]
                            idx = start_idx + j
                            result[idx] = 1
                            symbol_id = symbols[idx]
                            current_holdings[symbol_id] = rank_pcts[idx]

            # 无论 warmup 还是 active，都记录当期 rank_pct 到环形缓冲区
            _rank[hist_ptr, :] = -1.0
            for j in range(n_current):
                idx = start_idx + j
                _rank[hist_ptr, symbols[idx]] = rank_pcts[idx]
            hist_ptr = (hist_ptr + 1) % _buf_size

            if i < n_rows:
                current_time = times[i]
                start_idx = i

    return result


def signal(*args):
    df = args[0]
    n = args[1][0]
    select_pct = args[1][1]  # 选币百分比，如0.10表示前10%
    threshold_pct = args[1][2]  # 阈值，超过这个阈值即平仓
    start_date = args[1][3]  # 开始时间，为模型第一次运行时正常选币的时间点
    factor_name = args[2]
    df_prev = args[3] if len(args) > 3 else None

    if df_prev is None:
        # 判断全量更新时K线数量是否足够；如果全量更新时足够，增量更新时也足够
        time_threshold = pd.to_datetime(start_date) - pd.Timedelta(hours=n)
        if df['candle_begin_time'].min() > time_threshold:
            _ = (df['candle_begin_time'].min() - time_threshold).total_seconds() / 3600
            raise ValueError(f'K线数量不足，请补充K线，保证最早的K线在{time_threshold}之前，需补充{_}H的K线')

    # 动态构建因子列名
    base_factor_name = f'VolumeMaxBias_{n}'

    # 检查因子是否存在
    if base_factor_name not in df.columns:
        print(f"警告: 因子 {base_factor_name} 不存在于数据中")
        df[factor_name] = 0
        return df

    # 保存索引
    original_index = df.index.copy()

    # 确定时间范围，一次性过滤 + 计算 rank_pct
    start_timestamp = pd.Timestamp(start_date)
    time_mask = df['candle_begin_time'] >= start_timestamp

    mask = (
            df[base_factor_name].notna()
            & time_mask
            & (df['是否交易'] == 1)
    )
    df_work = df[mask].copy()

    # 过滤：去掉现货&合约域的现货
    con = (df_work['symbol_spot'] != '') & (df_work['symbol_swap'] != '') & (df_work['is_spot'] == 1)
    df_work = df_work[~con]
    # # 过滤：去掉现货&合约域的合约
    # con = (df_work['symbol_spot'] != '') & (df_work['symbol_swap'] != '') & (df_work['is_spot'] == 0)
    # df_work = df_work[~con]

    if len(df_work) == 0:
        df.index = original_index
        return df

    # 按时间排序，确保 numba 函数时间顺序正确
    df_work = df_work.sort_values('candle_begin_time')

    # 一次性计算所有截面的 rank_pct
    df_work['rank_pct'] = df_work.groupby('candle_begin_time', sort=False)[base_factor_name].rank(
        ascending=True, method='min', pct=True
    )

    # 构建 symbol 编码和初始持仓数组
    if df_prev is not None:
        all_symbols = sorted(list(set(df_work['symbol'].tolist()) | set(df_prev['symbol'].tolist())))
    else:
        all_symbols = sorted(list(df_work['symbol'].unique()))
    symbol_to_id = {s: i for i, s in enumerate(all_symbols)}
    n_symbols = len(all_symbols)

    # 初始持仓：-1.0 表示未持仓，>= 0 表示持仓
    prev_holdings = np.full(n_symbols, -1.0, dtype=np.float64)

    # 增量更新的配置，回测不用管，只在实盘有效
    warmup_cutoff = None

    if df_prev is not None:  # 增量更新分支，只在实盘有效，回测不用管
        # 取 df_prev 中"因子值有效"的最末时刻，避免缓存被污染后 last_time 漂到 NaN 截面
        notna_mask = df_prev[factor_name].notna()
        if notna_mask.any():
            last_time = df_prev.loc[notna_mask, 'candle_begin_time'].max()
            prev_latest = df_prev[df_prev['candle_begin_time'] == last_time]
            held_symbols = prev_latest.loc[prev_latest[factor_name] == 1, 'symbol']
            for symbol in held_symbols:
                prev_holdings[symbol_to_id[symbol]] = 0

            # 把 df_prev 的历史因子值合并回 df：merge 会把 df 的 index 重置成 RangeIndex，
            # 而后面 df.loc[df_work.index, ...] 用的还是 merge 前的 label，所以必须立刻把
            # 原 index 还回去。how='left' 保证行顺序不变，长度也不变。
            prev_cols = ['candle_begin_time', 'symbol', 'is_spot', factor_name]
            df = pd.merge(
                df,
                df_prev[prev_cols],
                how='left',
                on=['candle_begin_time', 'symbol', 'is_spot'],
            )
            df.index = original_index

            warmup_cutoff = last_time
            # 多取 24 小时让 numba 自己重建 _rank（不需要 df_prev 携带 rank_pct）
            df_work = df_work[df_work['candle_begin_time'] >= last_time - pd.Timedelta(hours=24)]
        else:
            df_prev = None  # 缓存完全无有效因子值，退化到首次运行逻辑
    if df_prev is None:
        # 从第一个时间截面按 rank_pct 升序取前 int(n_current * select_pct) 只
        first_time = df_work['candle_begin_time'].min()
        df_first = df_work[df_work['candle_begin_time'] == first_time]
        n_current = len(df_first)
        target_count = max(1, int(n_current * select_pct))
        first_selected = df_first.sort_values('rank_pct').head(target_count)
        for symbol, rank_pct in zip(first_selected['symbol'], first_selected['rank_pct']):
            prev_holdings[symbol_to_id[symbol]] = rank_pct
        # 写回第一期结果，numba 只处理后续期
        df.loc[first_selected.index, factor_name] = 1
        df_work = df_work[df_work['candle_begin_time'] > first_time]

    # 构建 numba 输入数组
    if len(df_work) > 0:
        unique_times = sorted(df_work['candle_begin_time'].unique())
        time_to_id = {t: i for i, t in enumerate(unique_times)}

        # 计算 active 起始时间ID（warmup 期间只填充缓冲区，不做持仓更新）
        active_start_time_id = 0
        if warmup_cutoff is not None:
            for t_id, t in enumerate(unique_times):
                if t > warmup_cutoff:
                    active_start_time_id = t_id
                    break
            else:
                active_start_time_id = len(unique_times)

        times_arr = df_work['candle_begin_time'].map(time_to_id).values.astype(np.int64)
        symbols_arr = df_work['symbol'].map(symbol_to_id).values.astype(np.int32)
        rank_pcts_arr = df_work['rank_pct'].values.astype(np.float64)

        # numba 加速计算，增量更新函数
        result = compute_holdings_numba_pct(
            times_arr, symbols_arr, rank_pcts_arr, n_symbols,
            prev_holdings, select_pct, threshold_pct, active_start_time_id, n
        )

        # 仅对 active 期（last_time 之后）的行赋值回 df
        if warmup_cutoff is not None:
            active_mask = df_work['candle_begin_time'] > warmup_cutoff
            df_active = df_work[active_mask]
            result_active = result[active_mask.values]
            df.loc[df_active.index, factor_name] = result_active
        else:
            df.loc[df_work.index, factor_name] = result

    # 恢复原始索引
    df.index = original_index

    return df


def get_factor_list(n):
    return [
        ('VolumeMaxBias', n[0]),
    ]
