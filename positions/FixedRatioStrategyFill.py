"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import numpy as np
import pandas as pd

from core.model.strategy_config import PosStrategyConfig


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
    # 取出对应参数
    cap_ratios = stg_conf.params['cap_ratios']  # 默认资金分配
    pos_limit = stg_conf.params['pos_limit']
    monitor_strategy = pos_limit['monitor_strategy']  # 监控的策略
    alt_ratios = pos_limit['alt_ratios']  # 备选资金分配比例

    monitor_times = None

    print(f"策略列表长度: {len(stg_conf.strategy_cfg_list)}")
    for i, cfg in enumerate(stg_conf.strategy_cfg_list):
        print(f"策略{i}: {cfg.name}， {cfg.original_name}")

    # 找到需要监控的策略
    for index, cfg in enumerate(stg_conf.strategy_cfg_list):
        if cfg.original_name == monitor_strategy:
            equity_df = pd.read_csv(cfg.get_result_folder() / '资金曲线.csv', encoding='utf-8-sig', parse_dates=['candle_begin_time'])
            cond_empty_long = (equity_df['symbol_long_num'] == 0)
            cond_empty_short = (equity_df['symbol_short_num'] == 0)
            monitor_times = equity_df.loc[cond_empty_long & cond_empty_short, 'candle_begin_time']
            break

    # 判断资金占比的参数数量是否与资金曲线的数量一致
    if len(cap_ratios) != len(equity_dfs) or len(alt_ratios) != len(equity_dfs):
        print('资金比例数量与资金曲线数量不同，退出...')
        exit()

    # 构建时间轴
    begin_times = equity_dfs[0]['candle_begin_time']

    # 创建一个全 0 的 NDArray
    cap_weights = np.zeros([len(begin_times), len(equity_dfs)])

    # 分配默认权重
    for i in range(len(cap_ratios)):
        cap_weights[:, i] = cap_ratios[i]

    # 如果监控策略空仓，则应用替代配置
    if monitor_times is not None:
        # 创建时间匹配掩码（将时间轴与监控策略空仓时间对齐）
        mask = begin_times.isin(monitor_times)
        # 在空仓时应用替代权重
        for i in range(len(alt_ratios)):
            cap_weights[mask, i] = alt_ratios[i]

    # 将结果转换回 DataFrame
    cap_weights = pd.DataFrame(cap_weights, columns=range(cap_weights.shape[1]), index=begin_times)

    return cap_weights