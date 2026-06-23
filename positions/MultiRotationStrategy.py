import pandas as pd

from core.model.strategy_config import PosStrategyConfig


def calc_ratio(equity_dfs: list[pd.DataFrame], stg_conf: PosStrategyConfig) -> pd.DataFrame:
    # ===== 参数提取与验证 =====
    config = stg_conf.params
    factor_list = config['factor_list']
    rotation_period = config['rotation_period']
    offset_list = config['offset_list']
    rotation_group = config['rotation_group']
    # 筛选掉不符合持仓周期的offset
    all_offset_list = list(range(0, int(rotation_period[:-1])))
    offset_list = [_offset for _offset in offset_list if _offset in all_offset_list]
    select_num = config.get('select_num', 1)  # 默认为1
    
    # 验证必要参数
    if (not factor_list) or (not offset_list):
        raise ValueError("参数配置错误: factor_list和offset_list不能为空")
    if stg_conf.hold_period != '1H':
        raise ValueError("参数配置错误: hold_period必须设置为'1H'")
    if rotation_period.endswith(('D', 'd')):
        raise ValueError("参数配置错误: rotation_period不能是日线级别")

    # ===== 数据预处理 - 修复添加group列 =====
    # 获取所有资金曲线的时间索引
    all_times = sorted(set().union(*[set(df['candle_begin_time']) for df in equity_dfs]))

    for idx, df in enumerate(equity_dfs):
        df['symbol'] = idx
        strategy_cfg = stg_conf.strategy_cfg_list[idx]
        df['name'] = strategy_cfg.original_name

        # 确保有所有时间点的数据
        df = df.set_index('candle_begin_time')
        df = df.reindex(all_times)
        df = df.reset_index().sort_values('candle_begin_time')
        equity_dfs[idx] = df
    
    # 合并所有资金曲线
    all_equity = pd.concat(equity_dfs)
    all_equity = all_equity.sort_values('candle_begin_time')

    # ===== 计算轮动时间偏移 =====
    period_num = int(rotation_period[:-1])
    base_seconds = 3600  # 小时级别固定3600秒
    
    # 统一参考时间点
    tz_aware = all_equity['candle_begin_time'].dt.tz is not None
    ref_point = pd.Timestamp('2017-01-01', tz='UTC') if tz_aware else pd.Timestamp('2017-01-01')

    # 计算时间差和偏移量
    time_diff = all_equity['candle_begin_time'] - ref_point
    time_diff_seconds = time_diff.dt.total_seconds()
    all_equity['offset'] = (time_diff_seconds / base_seconds % period_num).astype(int)
    
    # ===== 核心权重计算 =====
    # 创建空权重矩阵
    begin_times = all_equity['candle_begin_time'].unique()
    symbol_count = len(equity_dfs)
    final_weights = pd.DataFrame(0, index=pd.DatetimeIndex(sorted(begin_times)),
                                columns=range(symbol_count),
                                dtype=float)

    # 因子权重计算函数
    def calc_factor_weights(df, factor_config):
        factor_name = f"{factor_config[0]}_{factor_config[2]}"  # 原始格式
        
        # 组内排名计算
        df['rank'] = df.groupby('candle_begin_time')[factor_name].rank(ascending=factor_config[1], method='min')

        # 筛选每组排名靠前的策略
        selected = df[df['rank'] <= select_num].copy()

        # 计算每组选择的数量
        selected['count'] = selected.groupby('candle_begin_time').transform('size')

        # 计算权重
        selected['weight'] = 1 / selected['count']  # 组内平均

        return selected

    # 主处理流程：遍历所有偏移值和因子
    for offset_val in offset_list:
        offset_data = all_equity[all_equity['offset'] == offset_val]
        if offset_data.empty:
            continue

        # 初始化当前offset的权重
        offset_weights = pd.DataFrame(0, index=final_weights.index, columns=final_weights.columns)
        
        # 处理每个因子
        for factor_config in factor_list:

            # 处理每个组
            weighted_data = []
            for _group, _group_info in rotation_group.items():
                group_data = offset_data[offset_data['name'].isin(_group_info['strategy_names'])]
                if group_data.empty:
                    continue

                # 计算权重
                _weighted_data = calc_factor_weights(group_data, factor_config)
                _weighted_data['weight'] *= _group_info['cap_ratio']
                weighted_data.append(_weighted_data)

            # 转换为权重矩阵
            if len(weighted_data):
                weighted_data = pd.concat(weighted_data)
                weighted_data = weighted_data.sort_values(['candle_begin_time', 'symbol'], ascending=True)
                pivot_weights = weighted_data.pivot_table(
                    index='candle_begin_time',
                    columns='symbol',
                    values='weight',
                    aggfunc='sum'
                )
                pivot_weights = pivot_weights.fillna(0)

                # 填充到完整时间序列
                pivot_weights = pivot_weights.reindex(final_weights.index)

                # 向前填充权重
                pivot_weights = pivot_weights.fillna(method='ffill')

                # 累加到当前offset的权重
                offset_weights = offset_weights.add(pivot_weights)
        
        # 平均不同因子的权重
        if len(factor_list) > 0:
            offset_weights /= len(factor_list)

        # 累加到最终权重
        final_weights = final_weights.add(offset_weights)
    
    # 平均不同偏移值的权重
    if offset_list:  # 防止除零错误
        final_weights /= len(offset_list)

    # 最后确保向前填充所有空值
    average_columns = final_weights.notna().sum()[final_weights.notna().sum() > 0].index
    final_weights[average_columns] = final_weights[average_columns].fillna(1 / len(average_columns))
    final_weights = final_weights.fillna(method='ffill').fillna(0)
    final_weights = final_weights.div(final_weights.sum(axis=1), axis=0)

    return final_weights