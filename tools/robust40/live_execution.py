"""MODEL-only live execution adapter; the framework has no model dependency."""
import numpy as np

from .rebalance import RebRobust40


class LiveRebRobust40:
    use_full_equity = True

    def __init__(self, spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio=.4, execution_cap=2.45):
        self.calculator = RebRobust40(np.asarray(spot_lot_sizes, dtype=float),
                                     np.asarray(swap_lot_sizes, dtype=float),
                                     min_order_usdt_ratio, execution_cap)

    def calc_lots(self, equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios):
        for prices, held, target in ((spot_prices, spot_lots, spot_ratios),
                                     (swap_prices, swap_lots, swap_ratios)):
            active = (held != 0) | (target != 0)
            if np.any(active & (~np.isfinite(prices) | (prices <= 0))):
                raise ValueError('Missing active instrument price for MODEL_ROBUST40')
        return self.calculator.calc_lots(equity, spot_prices, spot_lots, spot_ratios,
                                         swap_prices, swap_lots, swap_ratios)


def register_execution():
    """Add one named extension; never replace built-in modes or another plugin."""
    from core import rebalance
    existing = getattr(rebalance, 'RebRobust40', None)
    if existing is not None and existing is not LiveRebRobust40:
        raise ValueError('RebRobust40 execution name is already registered')
    rebalance.RebRobust40 = LiveRebRobust40
