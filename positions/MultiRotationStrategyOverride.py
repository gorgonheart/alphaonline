import pandas as pd
import numpy as np
import os

from core.model.strategy_config import PosStrategyConfig


def calc_factor_weights(df, factor_config, select_num):
    """计算该轮动因子的权重"""
    factor_name = f"{factor_config[0]}_{factor_config[2]}"  # 原始格式

    # 组内排名计算
    df['rank'] = df.groupby('candle_begin_time')[factor_name].rank(ascending=factor_config[1], method='min')

    # 筛选每组排名靠前的策略并计算权重
    selected = df[df['rank'] <= select_num].copy()
    selected['count'] = selected.groupby('candle_begin_time').transform('size')
    selected['weight'] = 1 / selected['count']  # 组内平均

    return selected


def cal_offset_weight(final_weights, offset_list, group_data, factor_list, select_num):
    # 初始化当前offset的权重
    group_weights = pd.DataFrame(0, index=final_weights.index, columns=final_weights.columns)

    for _offset in offset_list:
        offset_data = group_data[group_data['offset'] == _offset]
        if offset_data.empty:
            continue

        # 处理每个因子
        weighted_data = []
        for _factor_config in factor_list:
            _weighted_data = calc_factor_weights(offset_data, _factor_config, select_num)
            _weighted_data['weight'] /= len(factor_list)
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

            # 累加到当前组的权重
            group_weights = group_weights.add(pivot_weights, fill_value=0)

    # 平均不同因子的权重
    if len(offset_list) > 0:
        group_weights /= len(offset_list)

    return group_weights


def get_stg_use_ratio(stg_conf, begin_times):
    print(f"策略列表长度: {len(stg_conf.strategy_cfg_list)}")
    use_ratio = pd.DataFrame(index=begin_times).reset_index().rename(columns={'index': 'candle_begin_time'})
    for idx, cfg in enumerate(stg_conf.strategy_cfg_list):
        print(f"策略{idx}: {cfg.name}， {cfg.original_name}")

        path = cfg.get_result_folder() / '资金曲线_再择时.csv'
        path2 = cfg.get_result_folder() / '资金曲线.csv'
        if os.path.exists(path):
            equity_df = pd.read_csv(path, encoding='utf-8-sig', parse_dates=['candle_begin_time'])
        else:
            equity_df = pd.read_csv(path2, encoding='utf-8-sig', parse_dates=['candle_begin_time'])
        equity_df[f'use_ratio_{idx}'] = (equity_df['long_pos_value'] + equity_df['short_pos_value']) / equity_df['equity']
        use_ratio = pd.merge(use_ratio, equity_df[['candle_begin_time', f'use_ratio_{idx}']], on='candle_begin_time', how='left')
    use_ratio.set_index('candle_begin_time', inplace=True)

    return use_ratio


def cal_use_empty_ratio(group_weights, use_ratio):
    cols = group_weights.columns
    group_weights['use_ratio'] = 0
    for col in cols:
        group_weights['use_ratio'] += group_weights[col] * use_ratio[f'use_ratio_{col}']
    group_weights['empty_ratio'] = np.maximum(group_weights['use_ratio'], 1) - group_weights['use_ratio']
    group_weights['empty_ratio'] = np.where(group_weights['empty_ratio'] < 0.03, 0, group_weights['empty_ratio'])

    return group_weights


def calc_ratio(equity_dfs: list[pd.DataFrame], stg_conf: PosStrategyConfig) -> pd.DataFrame:
    # ===== 参数提取与验证 =====
    config = stg_conf.params
    factor_list = config['factor_list']
    rotation_period = config['rotation_period']
    offset_list = config['offset_list']
    rotation_group = config['rotation_group']
    all_strategy_names = []
    for k, v in rotation_group.items():
        if set(v['strategy_names']) not in all_strategy_names:
            all_strategy_names.append(set(v['strategy_names']))
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

    # ===== 数据预处理 =====
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

    # 预处理所有轮动组的权重
    group_weights_dict = {}
    for _group_strategy in all_strategy_names:
        print(f'正在处理轮动组：{_group_strategy}')
        group_data = all_equity[all_equity['name'].isin(_group_strategy)]
        if group_data.empty:
            continue

        # 计算轮动组的各策略权重
        _group_weights = cal_offset_weight(final_weights, offset_list, group_data, factor_list, select_num)
        group_weights_dict[str(_group_strategy)] = _group_weights

    # 获取每个策略的资金使用率
    use_ratio = get_stg_use_ratio(stg_conf, begin_times)

    # 处理填充逻辑
    for _group, _group_info in rotation_group.items():
        group_weights = group_weights_dict[str(set(_group_info['strategy_names']))].copy()

        # 处理填充逻辑
        override = _group_info.get('override', {})
        if override:
            _type = override.get('type', 'empty')
            _group_names = override.get('group_names', [])
            _group_ratio = override.get('group_ratio', [1 / len(_group_names)] * len(_group_names) if len(_group_names) > 0 else [])

            if _type == 'empty':
                # 计算开仓和空仓比例
                group_weights = cal_use_empty_ratio(group_weights, use_ratio)

                # 空仓填充其他策略
                for idx, _group_name in enumerate(_group_names):
                    _group_weights = group_weights_dict[str(set(rotation_group[_group_name]['strategy_names']))].copy()
                    _group_weights *= _group_ratio[idx]
                    _group_weights = _group_weights.multiply(group_weights['empty_ratio'], axis=0)
                    group_weights = group_weights.add(_group_weights, fill_value=0)

            elif _type == 'combine':
                # 以combine方式填充其他策略
                for idx, _group_name in enumerate(_group_names):
                    _group_weights = group_weights_dict[str(set(rotation_group[_group_name]['strategy_names']))].copy()
                    _group_weights *= _group_ratio[idx]
                    group_weights = group_weights.add(_group_weights, fill_value=0)

                # 计算开仓和空仓比例
                group_weights = cal_use_empty_ratio(group_weights, use_ratio)

                # 处理资金使用率超过1的情况
                con = group_weights['use_ratio'] > 1
                group_weights.loc[con, :] = group_weights.loc[con, :].div(group_weights.loc[con, 'use_ratio'].values, axis=0)

            # 删除掉use_ratio和empty_ratio
            group_weights.drop(['use_ratio', 'empty_ratio'], axis=1, inplace=True)

        # 应用资金比例
        group_weights *= _group_info['cap_ratio']
        # 累加到最终权重
        final_weights = final_weights.add(group_weights, fill_value=0)

    return final_weights