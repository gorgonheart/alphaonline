"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import pandas as pd


def dynamic_leverage_for_dataframe(equity: pd.DataFrame, *args) -> pd.DataFrame:
    """
    根据资金曲线，动态调整杠杆
    :param equity: 资金曲线
    :param args: 其他参数
    :return: 返回包含 leverage 的数据
    """

    # ===== 获取策略参数
    n = float(args[0])

    # ===== 计算指标
    use_ratio = (equity['long_pos_value'] + equity['short_pos_value']) / equity['equity']

    # 默认n倍杠杆
    leverage = pd.Series(n, index=equity.index)

    # 如果n倍杠杆后总开仓比例超过1，则缩小杠杆，使得总开仓比例刚好等于1
    use_ratio *= leverage
    con = use_ratio > 1
    leverage.loc[con] = leverage.loc[con] * 1 / use_ratio.loc[con]

    return leverage
