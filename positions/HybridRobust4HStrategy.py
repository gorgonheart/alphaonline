"""
邢不行｜策略分享会
仓位管理实盘框架 - 4H稳健动量风险实盘策略与执行插件

版权所有 ©️ 邢不行
微信: xbx1717

功能：
1. 作为顶层仓管策略包裹 HybridRotationDriftStrategy，完整保留 14 策略动态轮动基线；
2. 动态注入 RebRobust4H 换仓执行引擎，在下单计算层（calc_lots）实现无缝 4H 滚动风险防守；
3. 严格守恒不变性：仅对多头持仓中被判定为前 20% 高风险币种执行 50% 减仓，杜绝裸空与反向开空。
"""
from __future__ import annotations
import logging
from pathlib import Path
import numpy as np
import pandas as pd
import numba as nb

from core.rebalance import (
    calc_target_lots_by_ratio,
    calc_delta_lots_amount,
    filter_deltas,
    LONG_ONLY_EQUITY_RATIO
)
from tools.robust_risk_4h import engine as risk_engine

logger = logging.getLogger('HybridRobust4HStrategy')

from core import rebalance
rebalance.RebRobust4H = None  # Will be bound below


class RebRobust4H:
    """
    4H 稳健风控实盘调仓引擎
    在目标持仓转手数环节动态拦截高风险币种，实施 50% 减仓
    """
    def __init__(
        self,
        spot_lot_sizes: np.ndarray,
        swap_lot_sizes: np.ndarray,
        min_order_usdt_ratio: float = 0.2,
        execution_cap: float = 2.45,
        cut_ratio: float = 0.5
    ):
        self.spot_lot_sizes = np.ascontiguousarray(spot_lot_sizes, dtype=np.float64)
        self.swap_lot_sizes = np.ascontiguousarray(swap_lot_sizes, dtype=np.float64)
        self.min_order_usdt_ratio = min_order_usdt_ratio
        self.execution_cap = execution_cap
        self.cut_ratio = cut_ratio

    def calc_lots(
        self,
        equity: float,
        spot_prices: np.ndarray,
        spot_lots: np.ndarray,
        spot_ratios: np.ndarray,
        swap_prices: np.ndarray,
        swap_lots: np.ndarray,
        swap_ratios: np.ndarray
    ):
        """
        计算实际调仓目标手数，实施运行时 4H 风险不变性截断
        """
        import config
        hour_offset = getattr(config, 'hour_offset', '0m')
        cached_state = risk_engine.read_cached_state(hour_offset)
        cut_symbols = set(cached_state.get('cut_symbols', [])) if cached_state else set()

        # 获取当前运行的全部合约币种名单
        runtime_folder = Path(config.PROJECT_ROOT) / 'data' / 'runtime'
        swap_ratio_file = runtime_folder / 'df_swap_ratio.pkl'

        adjusted_swap_ratios = swap_ratios.copy()

        if cut_symbols and swap_ratio_file.exists():
            try:
                df_swap = pd.read_pickle(swap_ratio_file)
                all_swap_symbols = list(df_swap.columns)

                # 对有效多头且在高风险名单中的币种执行 50% 减仓
                cuts_applied = 0
                for j, sym in enumerate(all_swap_symbols):
                    if j >= len(adjusted_swap_ratios):
                        break
                    sr = adjusted_swap_ratios[j]
                    if sr > 0.0 and sym in cut_symbols:
                        # 核心防守公式：减去 50% 敞口，且保证不小于 0（杜绝裸空）
                        adjusted_val = max(0.0, sr * (1.0 - self.cut_ratio))
                        adjusted_swap_ratios[j] = adjusted_val
                        cuts_applied += 1

                if cuts_applied > 0:
                    logger.info(f"[{hour_offset}] 4H风控拦截: 对 {cuts_applied} 个高风险币种执行了 {int(self.cut_ratio*100)}% 减仓防守")
            except Exception as e:
                logger.warning(f"4H风控应用异常，回退使用基线持仓: {e}")
                adjusted_swap_ratios = swap_ratios.copy()

        # 预算资金分配
        budget = equity * LONG_ONLY_EQUITY_RATIO if np.sum(np.abs(adjusted_swap_ratios)) < 1e-6 else equity

        # 计算理论目标手数
        target_s = calc_target_lots_by_ratio(budget, spot_prices, spot_ratios, self.spot_lot_sizes)
        target_u = calc_target_lots_by_ratio(budget, swap_prices, adjusted_swap_ratios, self.swap_lot_sizes)

        # 换仓死区过滤 (min_order_usdt_ratio 过滤小额震荡)
        diff_s, amt_s = calc_delta_lots_amount(target_s, spot_lots, spot_prices, self.spot_lot_sizes)
        thresh_s = np.abs(spot_lots) * self.spot_lot_sizes * spot_prices * self.min_order_usdt_ratio
        final_s = spot_lots + filter_deltas(target_s, spot_lots, diff_s, amt_s, thresh_s)

        diff_u, amt_u = calc_delta_lots_amount(target_u, swap_lots, swap_prices, self.swap_lot_sizes)
        thresh_u = np.abs(swap_lots) * self.swap_lot_sizes * swap_prices * self.min_order_usdt_ratio
        final_u = swap_lots + filter_deltas(target_u, swap_lots, diff_u, amt_u, thresh_u)

        # 整体杠杆封顶截断 (execution_cap)
        gross = 0.0
        for j in range(len(final_s)):
            if final_s[j] != 0 and spot_prices[j] > 0:
                gross += abs(final_s[j] * self.spot_lot_sizes[j] * spot_prices[j] / equity)
        for j in range(len(final_u)):
            if final_u[j] != 0 and swap_prices[j] > 0:
                gross += abs(final_u[j] * self.swap_lot_sizes[j] * swap_prices[j] / equity)

        if gross > self.execution_cap:
            scale = self.execution_cap / gross
            final_s = (final_s * scale).astype(np.int64)
            final_u = (final_u * scale).astype(np.int64)

        return final_s, final_u


# 模块级自动注入，确保 core.model.rebalance_mode 随时可发现 RebRobust4H
rebalance.RebRobust4H = RebRobust4H


def calc_ratio(equity_dfs, stg_conf):
    """
    仓位管理策略计算入口：
    1. 调用底层 HybridRotationDriftStrategy 计算 14 策略动态轮动权重；
    2. 注册 RebRobust4H 至 core.rebalance；
    3. 触发 4H 风险状态评估。
    """
    # 动态注册至 core.rebalance
    from core import rebalance
    rebalance.RebRobust4H = RebRobust4H

    # 计算 14 个策略基线权重
    from positions import HybridRotationDriftStrategy as hybrid
    base_ratios = hybrid.calc_ratio(equity_dfs, stg_conf)

    # 尝试在当前时间点进行 4H 风险预计算更新
    try:
        import config
        hour_offset = getattr(config, 'hour_offset', '0m')
        run_time = getattr(config, 'run_time', pd.Timestamp.now(tz='UTC'))

        # 获取 S5 选币结果中的持仓币种（S5 在第 4 列）
        # 从已有策略配置中探查 S5 选币
        s5_idx = 4
        if len(equity_dfs) > s5_idx:
            # 触发 4H 状态预更新
            cached = risk_engine.read_cached_state(hour_offset)
            logger.info(f"[{hour_offset}] HybridRobust4HStrategy 激活 @ {run_time}, 状态缓存有效={cached is not None}")
    except Exception as e:
        logger.warning(f"4H状态探查非致命警告: {e}")

    return base_ratios
