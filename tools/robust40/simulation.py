"""Frozen research virtual-account arithmetic, ported from the supplied 2.3.0 backtest.
Original framework copyright: 邢不行. No exchange, configuration or file I/O.
"""
import numpy as np
import numba as nb
from numba.experimental import jitclass

@jitclass
class Simulator:
    equity: float
    fee_rate: float
    min_order_limit: float
    lot_sizes: nb.float64[:]
    lots: nb.int64[:]
    target_lots: nb.int64[:]
    last_prices: nb.float64[:]
    has_last_prices: bool

    def __init__(self, init_capital, lot_sizes, fee_rate, init_lots, min_order_limit):
        """
        初始化
        :param init_capital: 初始资金
        :param lot_sizes: 每个币种的最小下单量
        :param fee_rate: 手续费率
        :param init_lots: 初始持仓
        :param min_order_limit: 最小下单金额
        """
        self.equity = init_capital
        self.fee_rate = fee_rate
        self.min_order_limit = min_order_limit
        n = len(lot_sizes)
        self.lot_sizes = np.zeros(n, dtype=np.float64)
        self.lot_sizes[:] = lot_sizes
        self.last_prices = np.zeros(n, dtype=np.float64)
        self.has_last_prices = False
        self.lots = np.zeros(n, dtype=np.int64)
        self.lots[:] = init_lots
        self.target_lots = np.zeros(n, dtype=np.int64)
        self.target_lots[:] = init_lots

    def set_target_lots(self, target_lots):
        self.target_lots[:] = target_lots

    def adjust_capital(self, delta):
        """真金白银出入金：直接增减账户现金权益（USDT）。"""
        self.equity += delta

    def fill_last_prices(self, prices):
        mask = np.logical_not(np.isnan(prices))
        self.last_prices[mask] = prices[mask]
        self.has_last_prices = True

    def settle_equity(self, prices):
        """
        结算当前账户权益
        :param prices: 当前价格
        :return:
        """
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(prices)))
        equity_delta = np.sum((prices[mask] - self.last_prices[mask]) * self.lot_sizes[mask] * self.lots[mask])
        self.equity += equity_delta

    def on_open(self, open_prices, funding_rates, mark_prices):
        """
        模拟: K 线开盘 -> K 线收盘时刻
        :param open_prices: 开盘价
        :param funding_rates: 资金费
        :param mark_prices: 计算资金费的标记价格（目前就用开盘价来）
        :return:
        """
        if not self.has_last_prices:
            self.fill_last_prices(open_prices)
        self.settle_equity(open_prices)
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(mark_prices)))
        pos_val = notional_value = self.lot_sizes[mask] * self.lots[mask] * mark_prices[mask]
        funding_fee = np.sum(notional_value * funding_rates[mask])
        self.equity -= funding_fee
        self.fill_last_prices(open_prices)
        return (self.equity, funding_fee, pos_val)

    def on_execution(self, exec_prices):
        """
        模拟: K 线开盘时刻 -> 调仓时刻
        :param exec_prices:  执行价格
        :return:            调仓后的账户权益、调仓后的仓位名义价值
        """
        if not self.has_last_prices:
            self.fill_last_prices(exec_prices)
        self.settle_equity(exec_prices)
        delta = self.target_lots - self.lots
        mask = np.logical_and(delta != 0, np.logical_not(np.isnan(exec_prices)))
        turnover = np.zeros(len(self.lot_sizes), dtype=np.float64)
        turnover[mask] = np.abs(delta[mask]) * self.lot_sizes[mask] * exec_prices[mask]
        mask = np.logical_and(mask, turnover >= self.min_order_limit)
        turnover_total = turnover[mask].sum()
        if np.isnan(turnover_total):
            raise RuntimeError('Turnover is nan')
        fee = turnover_total * self.fee_rate
        self.equity -= fee
        self.lots[mask] = self.target_lots[mask]
        self.fill_last_prices(exec_prices)
        return (self.equity, turnover_total, fee)

    def on_close(self, close_prices):
        """
        模拟: K 线收盘 -> K 线收盘时刻
        :param close_prices: 收盘价
        :return:           收盘后的账户权益
        """
        if not self.has_last_prices:
            self.fill_last_prices(close_prices)
        self.settle_equity(close_prices)
        self.fill_last_prices(close_prices)
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(close_prices)))
        pos_val = self.lot_sizes[mask] * self.lots[mask] * close_prices[mask]
        return (self.equity, pos_val)

@nb.njit(cache=True)
def compute_capital_flow(equity, flow_val, mode):
    """
    根据本期收盘权益与规则载荷，算出实际出入金 C（钱加权口径，不维护份额/NAV）。
    :param equity: 本期收盘账户权益 E_i（注资前）
    :param flow_val: 规则载荷（mode==0 绝对 USDT / mode==1 占权益比例(小数) / mode==2 自动识别）
    :param mode: -1 关闭 / 0 绝对 USDT / 1 占权益比例(小数,如0.1=10%) / 2 自动识别
        （mode==2：|flow_val|<=100 按百分比(flow_val%,如20=20%)，|flow_val|>100 按绝对 USDT。
         因最小下单金额限制，<100 USDT 的绝对出入金意义不大，故把 0-100 区间复用为百分比比例）
    :return: C 实际出入金
    """
    C = 0.0
    if mode < 0 or equity < 0.0:
        return C
    if mode == 0:
        C = flow_val
    elif mode == 1:
        if equity > 0.0:
            C = flow_val * equity
    elif abs(flow_val) <= 100.0:
        if equity > 0.0:
            C = flow_val / 100.0 * equity
    else:
        C = flow_val
    if C < 0.0 and equity + C < 0.0:
        C = -equity
    return C

@nb.jit(nopython=True, boundscheck=True)
def start_simulation(init_capital, leverages, spot_lot_sizes, swap_lot_sizes, spot_c_rate, swap_c_rate, spot_min_order_limit, swap_min_order_limit, min_margin_rate, spot_ratio, swap_ratio, spot_open_p, spot_close_p, spot_vwap1m_p, swap_open_p, swap_close_p, swap_vwap1m_p, funding_rates, pos_calc, require_rebalance, capital_flow_arr, capital_flow_mode, resume_state=None):
    """
    模拟交易
    :param init_capital: 初始资金
    :param leverages: 杠杆
    :param spot_lot_sizes: spot 现货的最小下单量
    :param swap_lot_sizes: swap 合约的最小下单量
    :param spot_c_rate: spot 现货的手续费率
    :param swap_c_rate: swap 合约的手续费率
    :param spot_min_order_limit: spot 现货最小下单金额
    :param swap_min_order_limit: swap 合约最小下单金额
    :param min_margin_rate: 维持保证金率
    :param spot_ratio: spot 的仓位透视表 (numpy 矩阵)
    :param swap_ratio: swap 的仓位透视表 (numpy 矩阵)
    :param spot_open_p: spot 的开仓价格透视表 (numpy 矩阵)
    :param spot_close_p: spot 的平仓价格透视表 (numpy 矩阵)
    :param spot_vwap1m_p: spot 的 vwap1m 价格透视表 (numpy 矩阵)
    :param swap_open_p: swap 的开仓价格透视表 (numpy 矩阵)
    :param swap_close_p: swap 的平仓价格透视表 (numpy 矩阵)
    :param swap_vwap1m_p: swap 的 vwap1m 价格透视表 (numpy 矩阵)
    :param funding_rates: swap 的 funding rate 透视表 (numpy 矩阵)
    :param pos_calc: 仓位计算
    :param require_rebalance: 是否需要调仓
    :return:
    """
    n_bars = spot_ratio.shape[0]
    n_syms_spot = spot_ratio.shape[1]
    n_syms_swap = swap_ratio.shape[1]
    start_lots_spot = np.zeros(n_syms_spot, dtype=np.int64)
    start_lots_swap = np.zeros(n_syms_swap, dtype=np.int64)
    funding_rates_spot = np.zeros(n_syms_spot, dtype=np.float64)
    turnovers = np.zeros(n_bars, dtype=np.float64)
    fees = np.zeros(n_bars, dtype=np.float64)
    equities = np.zeros(n_bars, dtype=np.float64)
    funding_fees = np.zeros(n_bars, dtype=np.float64)
    margin_rates = np.zeros(n_bars, dtype=np.float64)
    long_pos_values = np.zeros(n_bars, dtype=np.float64)
    short_pos_values = np.zeros(n_bars, dtype=np.float64)
    capital_flows = np.zeros(n_bars, dtype=np.float64)
    sim_spot = Simulator(init_capital, spot_lot_sizes, spot_c_rate, start_lots_spot, spot_min_order_limit)
    sim_swap = Simulator(0, swap_lot_sizes, swap_c_rate, start_lots_swap, swap_min_order_limit)
    if resume_state is not None:
        ss, su = resume_state
        sim_spot.equity = ss[0]
        sim_spot.lots[:] = ss[1]
        sim_spot.target_lots[:] = ss[2]
        sim_spot.last_prices[:] = ss[3]
        sim_spot.has_last_prices = ss[4]
        sim_swap.equity = su[0]
        sim_swap.lots[:] = su[1]
        sim_swap.target_lots[:] = su[2]
        sim_swap.last_prices[:] = su[3]
        sim_swap.has_last_prices = su[4]
    for i in range(n_bars):
        '1. 模拟开盘on_open'
        equity_spot, _, pos_value_spot = sim_spot.on_open(spot_open_p[i], funding_rates_spot, spot_open_p[i])
        equity_swap, funding_fee, pos_value_swap = sim_swap.on_open(swap_open_p[i], funding_rates[i], swap_open_p[i])
        position_val = np.sum(np.abs(pos_value_spot)) + np.sum(np.abs(pos_value_swap))
        if position_val < 1e-08:
            margin_rate = 10000.0
        else:
            margin_rate = (equity_spot + equity_swap) / float(position_val)
        if margin_rate < min_margin_rate:
            margin_rates[i] = margin_rate
            break
        '2. 模拟开仓on_execution'
        equity_spot, turnover_spot, fee_spot = sim_spot.on_execution(spot_vwap1m_p[i])
        equity_swap, turnover_swap, fee_swap = sim_swap.on_execution(swap_vwap1m_p[i])
        '3. 模拟K线结束on_close'
        equity_spot_close, pos_value_spot_close = sim_spot.on_close(spot_close_p[i])
        equity_swap_close, pos_value_swap_close = sim_swap.on_close(swap_close_p[i])
        long_pos_value = np.sum(pos_value_spot_close[pos_value_spot_close > 0]) + np.sum(pos_value_swap_close[pos_value_swap_close > 0])
        short_pos_value = -(np.sum(pos_value_spot_close[pos_value_spot_close < 0]) + np.sum(pos_value_swap_close[pos_value_swap_close < 0]))
        funding_fees[i] = funding_fee
        equities[i] = equity_spot_close + equity_swap_close
        turnovers[i] = turnover_spot + turnover_swap
        fees[i] = fee_spot + fee_swap
        margin_rates[i] = margin_rate
        long_pos_values[i] = long_pos_value
        short_pos_values[i] = short_pos_value
        E_i = equity_spot_close + equity_swap_close
        C = compute_capital_flow(E_i, capital_flow_arr[i], capital_flow_mode)
        if C != 0.0:
            sim_spot.adjust_capital(C)
        capital_flows[i] = C
        equity_leveraged = (equity_spot_close + equity_swap_close + C) * leverages[i]
        '4. 计算目标持仓'
        if require_rebalance[i] == 1:
            target_lots_spot, target_lots_swap = pos_calc.calc_lots(equity_leveraged, spot_close_p[i], sim_spot.lots, spot_ratio[i], swap_close_p[i], sim_swap.lots, swap_ratio[i])
            sim_spot.set_target_lots(target_lots_spot)
            sim_swap.set_target_lots(target_lots_swap)
    state = ((sim_spot.equity, sim_spot.lots.copy(), sim_spot.target_lots.copy(), sim_spot.last_prices.copy(), sim_spot.has_last_prices),
             (sim_swap.equity, sim_swap.lots.copy(), sim_swap.target_lots.copy(), sim_swap.last_prices.copy(), sim_swap.has_last_prices))
    return (equities, turnovers, fees, funding_fees, margin_rates, long_pos_values, short_pos_values, capital_flows, state)
