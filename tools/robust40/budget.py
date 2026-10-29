"""Explicit equity convention; existing accounts retain their reserve policy."""
import math


def execution_budget(total, leverage, buy_bnb, has_spot, has_swap, swap_net, mode='live_reserve'):
    if mode == 'research':
        if not math.isfinite(total) or total <= 0:raise ValueError('Invalid account equity')
        return total, leverage, total*leverage
    if mode != 'live_reserve':raise ValueError('Unknown execution equity convention')
    equity = total/1.001
    if has_spot and (not has_swap or swap_net >= 0):
        leverage *= .95 if buy_bnb/equity > .001 else .97
    return equity, leverage, equity*leverage
