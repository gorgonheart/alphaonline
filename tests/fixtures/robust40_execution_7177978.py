# Frozen pre-change execution oracle from release 7177978. Do not regenerate.
import numpy as np
import numba as nb
from numba.experimental import jitclass
LONG_ONLY_EQUITY_RATIO=.97

@jitclass
class RebRobust40:
    spot_lot_sizes: nb.float64[:]
    swap_lot_sizes: nb.float64[:]
    min_order_usdt_ratio: float
    execution_cap: float

    def __init__(self, spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio=.4, execution_cap=2.45):
        self.spot_lot_sizes = spot_lot_sizes.copy()
        self.swap_lot_sizes = swap_lot_sizes.copy()
        self.min_order_usdt_ratio = min_order_usdt_ratio
        self.execution_cap = execution_cap

    def _filter(self, target, current, prices, sizes):
        difference, amount = calc_delta_lots_amount(target, current, prices, sizes)
        threshold = np.abs(current)*sizes*prices*self.min_order_usdt_ratio
        return current+filter_deltas(target, current, difference, amount, threshold)

    def calc_lots(self, equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios):
        if not np.isfinite(equity) or equity <= 0:
            raise ValueError('Invalid execution equity')
        for j in range(len(spot_lots)):
            if not np.isfinite(spot_ratios[j]) or not np.isfinite(self.spot_lot_sizes[j]) or self.spot_lot_sizes[j] <= 0:
                raise ValueError('Invalid spot target or lot size')
        for j in range(len(swap_lots)):
            if not np.isfinite(swap_ratios[j]) or not np.isfinite(self.swap_lot_sizes[j]) or self.swap_lot_sizes[j] <= 0:
                raise ValueError('Invalid swap target or lot size')
        budget = equity*LONG_ONLY_EQUITY_RATIO if np.sum(np.abs(swap_ratios)) < 1e-6 else equity
        s = calc_target_lots_by_ratio(budget, spot_prices, spot_ratios, self.spot_lot_sizes)
        u = calc_target_lots_by_ratio(budget, swap_prices, swap_ratios, self.swap_lot_sizes)
        s = self._filter(s, spot_lots, spot_prices, self.spot_lot_sizes)
        u = self._filter(u, swap_lots, swap_prices, self.swap_lot_sizes)
        gross = 0.
        for j in range(len(s)):
            if s[j] != 0:
                if not np.isfinite(spot_prices[j]) or spot_prices[j] <= 0:
                    raise ValueError('Invalid held spot price')
                gross += abs(s[j]*self.spot_lot_sizes[j]*spot_prices[j]/equity)
        for j in range(len(u)):
            if u[j] != 0:
                if not np.isfinite(swap_prices[j]) or swap_prices[j] <= 0:
                    raise ValueError('Invalid held swap price')
                gross += abs(u[j]*self.swap_lot_sizes[j]*swap_prices[j]/equity)
        if gross > self.execution_cap:
            s = (s*(self.execution_cap/gross)).astype(np.int64)
            u = (u*(self.execution_cap/gross)).astype(np.int64)
        return s, u

@nb.njit
def calc_target_lots_by_ratio(equity, prices, ratios, lot_sizes):
    """
    根据目标持仓比例，计算目标持仓手数
    """
    target_lots = np.zeros(len(lot_sizes), dtype=np.int64)
    target_equity = equity * ratios
    mask = np.logical_and(np.abs(target_equity) > 0.01, np.logical_and(prices != 0, lot_sizes != 0))
    target_lots[mask] = (target_equity[mask] / prices[mask] / lot_sizes[mask]).astype(np.int64)
    int64_max = np.iinfo(np.int64).max
    int64_min = np.iinfo(np.int64).min
    extreme_value_mask = (target_lots == int64_max) | (target_lots == int64_min)
    if np.any(extreme_value_mask):
        extreme_indices = np.where(extreme_value_mask)[0]
        print(f'警告：发现 {len(extreme_indices)} 个int64极值，已强制设置为0')
        target_lots[extreme_value_mask] = 0
    return target_lots

@nb.njit
def calc_delta_lots_amount(target_lots, current_lots, prices, lot_sizes):
    """
    计算调仓手数和金额
    """
    delta_lots = target_lots - current_lots
    delta_amount = np.zeros(len(lot_sizes), dtype=np.float64)
    mask = delta_lots != 0
    delta_amount[mask] = np.abs(delta_lots[mask]) * lot_sizes[mask] * prices[mask]
    return (delta_lots, delta_amount)

@nb.njit
def filter_deltas(target_lots, current_lots, delta_lots, delta_amount, min_order_limit):
    mask_builds = np.logical_and(current_lots == 0, target_lots != 0)
    mask_liquidations = np.logical_and(current_lots != 0, target_lots == 0)
    mask_bld_liq = np.logical_or(mask_builds, mask_liquidations)
    mask_invalid = np.logical_and(delta_amount < min_order_limit, np.logical_not(mask_bld_liq))
    delta_lots[mask_invalid] = 0
    return delta_lots
