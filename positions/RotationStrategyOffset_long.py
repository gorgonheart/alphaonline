"""
邢不行™️ 策略分享会
仓位管理框架（长短期组合版）

版权所有 ©️ 邢不行
微信: xbx6660
"""
import pandas as pd
from .RotationStrategyOffset import calc_ratio as rotation_calc_ratio
from .FixedRatioStrategy import calc_ratio as fixed_calc_ratio
from core.model.strategy_config import PosStrategyConfig


def calc_ratio(equity_dfs: [pd.DataFrame], stg_conf: PosStrategyConfig) -> pd.DataFrame:
    # 分割数据集
    # first_two = [df.iloc[:, :2] for df in equity_dfs]
    # remaining = [df.iloc[:, 2:] for df in equity_dfs]

    first_two = equity_dfs[:2]
    remaining = equity_dfs[2:]

    # 计算轮动部分
    all_rot_ratio = rotation_calc_ratio(first_two, stg_conf)
    
    # 添加权重调整
    all_rot_ratio = all_rot_ratio * 0.4

    # 计算固定比例部分
    cap_weights = fixed_calc_ratio(remaining, stg_conf)
    all_rot_ratio = all_rot_ratio * 0.7


    # 合并结果（保持原有权重）
    combined_ratio = pd.concat([
        all_rot_ratio,
        cap_weights
    ], axis=1)

    # 保持原始列顺序
    return combined_ratio.reindex(columns=range(len(equity_dfs[0].columns)))