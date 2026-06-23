"""
邢不行™️ 策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import numpy as np
import pandas as pd

from core.model.strategy_config import PosStrategyConfig


def cal_one_ratio(factor_info, equity_dfs: [pd.DataFrame]) -> pd.DataFrame:
    """
    计算选币仓位结果，只接受两个参数，1. 因子配置信息，2. 资金曲线们
    :param factor_info: 因子配置信息
    :param equity_dfs: 资金曲线，是一个df的列表，里面包含配置中要求计算好的因子
    :return: 返回轮动因子计算得到的仓位比，比如：
                         0    1
    candle_begin_time
    2021-01-01 00:00:00  1.0  0.0
    2021-01-01 06:00:00  1.0  0.0
    2021-01-01 12:00:00  1.0  0.0
    2021-01-01 18:00:00  1.0  0.0
    2021-01-02 00:00:00  1.0  0.0
    ...                  ...  ...
    2024-07-23 06:00:00  0.0  1.0
    2024-07-23 12:00:00  0.0  1.0
    2024-07-23 18:00:00  0.0  1.0

    # 说明：
    可以在这里尽情施展你的组合才华，输入中包含策略的对象，以及我们准备好的资金曲线list
    """
    # 构建时间轴
    begin_times = equity_dfs[0]['candle_begin_time']

    # 取出对应因子名
    factor_name = factor_info[0] + '_' + str(factor_info[2])

    # 生成因子df
    factors = pd.DataFrame(index=begin_times, columns=range(len(equity_dfs)))  # 构建存储因子的df

    # 获取每个策略的因子值
    for idx, df in enumerate(equity_dfs):
        factors[idx] = df[factor_name].values

    # 对因子进行排名
    factor_rank = factors.rank(axis=1, ascending=factor_info[1], method='min')

    # 初始化资金比例rot_ratio
    rot_ratio = np.zeros_like(factor_rank)

    # 找到每行最大值/最小值的出现位置，并设置为1
    for col in factor_rank.columns:
        rot_ratio[factor_rank[col] == factor_rank.min(axis=1), col] = 1

    # 修正多个资金曲线排名相同的情况，当其中某N个资金曲线的排名相同时，平分权重
    ratio_sum = rot_ratio.sum(axis=1)
    for i in range(rot_ratio.shape[1]):
        rot_ratio[:, i] /= ratio_sum

    # 将结果转换回 DataFrame
    rot_ratio = pd.DataFrame(rot_ratio, columns=range(len(equity_dfs)), index=begin_times)

    return rot_ratio


def calc_ratio(equity_dfs: [pd.DataFrame], stg_conf: PosStrategyConfig) -> pd.DataFrame:
    """
    计算选币仓位结果，只接受两个参数，1. 资金曲线们，2. 策略配置
    :param equity_dfs: 资金曲线，是一个df的列表，里面包含配置中要求计算好的因子
    :param stg_conf: 策略配置
    :return: 返回仓位比，比如：
                         0    1
    candle_begin_time
    2021-01-01 00:00:00  1.0  0.0
    2021-01-01 06:00:00  1.0  0.0
    2021-01-01 12:00:00  1.0  0.0
    2021-01-01 18:00:00  1.0  0.0
    2021-01-02 00:00:00  1.0  0.0
    ...                  ...  ...
    2024-07-23 06:00:00  0.0  1.0
    2024-07-23 12:00:00  0.0  1.0
    2024-07-23 18:00:00  0.0  1.0

    # 说明：
    可以在这里尽情施展你的组合才华，输入中包含策略的对象，以及我们准备好的资金曲线list
    """
    # 取出初始资金分配比例
    cap_ratios_list = stg_conf.params['cap_ratios']

    # 取出factor_list
    factor_list = stg_conf.params['factor_list']

    # 处理轮动因子列表为空的特殊情况
    if not factor_list:
        print('轮动因子列表为空，程序将退出。')
        exit() # 维持原版行为

    # 根据用户需求，轮动选择应用于前两个策略
    num_strategies_for_rotation = 2 
    # 注意: 这个值目前是硬编码的。如果需要更灵活的配置，
    # 例如通过 stg_conf.params['num_rotated_strategies'] 来指定，则需要修改此处。

    if len(equity_dfs) < num_strategies_for_rotation:
        print(f"错误: 提供的资金曲线数量 ({len(equity_dfs)}) 不足以支持指定的 {num_strategies_for_rotation} 个轮动策略。")
        exit() # 错误情况，退出
    
    # 提取用于轮动的资金曲线子集
    equity_dfs_for_rotation = equity_dfs[:num_strategies_for_rotation]

    # 构建针对轮动子集的总选择信号 DataFrame
    all_rot_ratio_for_subset = pd.DataFrame()

    # 循环处理每个轮动因子，计算选择信号
    for i, factor_info_item in enumerate(factor_list):
        print(f'正在为前 {num_strategies_for_rotation} 个策略处理轮动因子：{factor_info_item}')
        
        # cal_one_ratio 接收轮动子集的资金曲线
        rot_ratio_subset = cal_one_ratio(factor_info_item, equity_dfs_for_rotation) 
        
        if i == 0:
            all_rot_ratio_for_subset = rot_ratio_subset.copy()
        else:
            # 累加不同因子的选择信号
            all_rot_ratio_for_subset = all_rot_ratio_for_subset.add(rot_ratio_subset, fill_value=0)

    # 如果有多个轮动因子，对累加的信号进行平均，以确保选择权重合理
    all_rot_ratio_for_subset = all_rot_ratio_for_subset.apply(lambda x: x / len(factor_list), axis=1)

    # 初始化最终的资金分配比例 DataFrame
    final_cap_ratios = pd.DataFrame(index=equity_dfs[0]['candle_begin_time'], columns=range(len(cap_ratios_list)))
    
    initial_ratios_rotated_part = np.array(cap_ratios_list[:num_strategies_for_rotation])
    initial_ratios_fixed_part = np.array(cap_ratios_list[num_strategies_for_rotation:])
    
    for t in range(len(all_rot_ratio_for_subset)):
        current_selection_signals = all_rot_ratio_for_subset.iloc[t].values
        
        modified_rotated_part = initial_ratios_rotated_part * current_selection_signals
        
        final_row_list = list(modified_rotated_part) + list(initial_ratios_fixed_part)
        final_cap_ratios.iloc[t] = final_row_list
        
    # ===== 特殊时段处理开始 =====
    # 根据配置参数中的workday_hour设置，将对应时间的数据乘以0.8，降低仓位
    
    # 将索引转换为datetime类型（如果还不是的话）
    final_cap_ratios.index = pd.to_datetime(final_cap_ratios.index)
    
    # 提取小时信息（UTC时间）
    utc_hours = final_cap_ratios.index.hour
    
    # 提取星期信息（UTC时间），注意：0=Monday, 6=Sunday
    utc_weekdays = final_cap_ratios.index.weekday
    
    # 从策略配置中获取workday_hour参数
    workday_hour_config = stg_conf.params.get('workday_hour', {})
    target_weekday = workday_hour_config.get('weekday', 5)  # 默认周六
    target_hour = workday_hour_config.get('hour', 22)       # 默认22点（UTC时间）
    
    # 创建时间筛选条件
    special_time_mask = (
        (utc_weekdays == target_weekday) & 
        (utc_hours == target_hour)
    )
    
    # 对符合条件的行应用0.8倍率
    final_cap_ratios.loc[special_time_mask, :] = final_cap_ratios.loc[special_time_mask, :] * 0.8
    
    print(f"特殊时段处理完成：目标星期{target_weekday}，目标小时{target_hour}（UTC时间），{special_time_mask.sum()} 个时间点被乘以0.8")
    # ===== 特殊时段处理结束 =====
    
    return final_cap_ratios


# final_cap_ratios 数据结构示例：
"""
final_cap_ratios 是一个DataFrame，结构如下：

                         0      1      2      3
 candle_begin_time
 2021-01-01 00:00:00   0.5    0.3    0.2    0.0  <- UTC时间周日0点，北京时间周日8点，正常
 2021-01-01 01:00:00   0.4    0.3    0.2    0.1  <- UTC时间周日1点，北京时间周日9点，正常
 2021-01-02 22:00:00   0.4    0.24   0.16   0.2  <- UTC时间周六22点，北京时间周日6点，乘以0.8
 2021-01-02 23:00:00   0.32   0.3    0.2    0.18 <- UTC时间周六23点，北京时间周日7点，乘以0.8
 2021-01-03 00:00:00   0.5    0.3    0.2    0.0  <- UTC时间周日0点，北京时间周日8点，正常
 2021-01-03 12:00:00   0.6    0.4    0.0    0.0
 
说明：
- 行索引是 candle_begin_time（UTC时间）
- 列索引是策略编号（0, 1, 2, 3...）
- 每行的值是该时间点各策略的资金分配比例，总和为1.0
- 仅在周六日北京时间6-8点（UTC时间周五六22:00-24:00和周六日0:00-2:00）的数据会被乘以0.8
"""