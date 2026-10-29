"""Explicit adapters from the trading framework to the shared ROBUST40 engine."""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from .offline import canonical, rename_columns
from .runtime import utc_index, expected_bar, update_state


def _frame(frame):
    out = rename_columns(frame.copy())
    out.index = utc_index(out.index)
    return out


def build_context(equities, ratios, leverage, pivots, sizes, minute, simulator):
    """Normalize only representation; do not shift signals or fill missing retiming."""
    equities = [d.copy() for d in equities]
    for d in equities:d['candle_begin_time'] = utc_index(d.candle_begin_time)
    index = pd.DatetimeIndex(equities[0].candle_begin_time)
    if any(not index.equals(pd.DatetimeIndex(d.candle_begin_time)) for d in equities):
        raise ValueError('Child equity time axes differ')
    pairs = [];columns = set()
    for pair in ratios:
        item = {}
        for kind, frame in zip(('spot', 'swap'), pair):
            frame = _frame(frame)
            if not index.isin(frame.index).all():raise ValueError('Child ratios missing equity bars')
            frame = frame.loc[index]
            if frame.isna().any().any():raise ValueError('Invalid child ratios')
            item[kind] = frame;columns.update(kind+':'+s for s in frame.columns)
        pairs.append(item)
    columns = sorted(columns);mapping = {c: i for i, c in enumerate(columns)}
    matrices = []
    for pair in pairs:
        matrix = sparse.csr_matrix((len(index), len(columns)))
        for kind, frame in pair.items():
            coo = sparse.coo_matrix(frame.to_numpy())
            loc = np.array([mapping[kind+':'+s] for s in frame.columns], dtype=int)
            matrix += sparse.csr_matrix((coo.data, (coo.row, loc[coo.col])), shape=matrix.shape)
        matrices.append(matrix)
    leverage = leverage.copy();leverage.index = utc_index(leverage.index)
    n = len(index)
    sim = dict(init_capital=float(simulator['initial_usdt']), leverages=np.ones(n),
        spot_c_rate=simulator['spot_c_rate'], swap_c_rate=simulator['swap_c_rate'],
        spot_min_order_limit=float(simulator['spot_min_order_limit']), swap_min_order_limit=float(simulator['swap_min_order_limit']),
        min_margin_rate=simulator['margin_rate'], require_rebalance=np.ones(n, dtype=np.int8),
        capital_flow_arr=np.zeros(n), capital_flow_mode=-1)
    closes = []
    for kind in ('spot', 'swap'):
        symbols = [c.split(':', 1)[1] for c in columns if c.startswith(kind+':')]
        p = {k: _frame(v) for k, v in pivots[kind].items()}
        for key in ('open', 'close', 'vwap1m'):
            if not index.isin(p[key].index).all():raise ValueError('Market pivot is stale/incomplete')
            sim[kind+'_'+key+'_p'] = np.ascontiguousarray(p[key].reindex(index=index, columns=symbols).to_numpy())
        if kind == 'swap':
            sim['funding_rates'] = np.ascontiguousarray(p['funding_rate'].reindex(index=index, columns=symbols).fillna(0).to_numpy())
        sim[kind+'_lot_sizes'] = sizes[kind].reindex(symbols).to_numpy(dtype=float)
        if not np.isfinite(sim[kind+'_lot_sizes']).all() or (sim[kind+'_lot_sizes'] <= 0).any():
            raise ValueError('Missing instrument unit for virtual account')
        close = p['close'].reindex(columns=symbols).loc[:index[-1]]
        close.columns = [kind+':'+s for s in symbols];closes.append(close)
    return dict(index=index, minute=minute, columns=columns, matrices=matrices,
        leverage=leverage.reindex(index), equities=equities, simulation=sim,
        close=pd.concat(closes, axis=1).reindex(columns=columns))


def normalized(value):
    if isinstance(value, dict):return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, range)):return [normalized(v) for v in value]
    return value


def research_definitions(value):
    """Map explicitly versioned equivalent factors to their research names."""
    aliases = {'TopKTo2KRobust40': 'TopKTo2K',
               'VolumeMaxBiasRobust40': 'VolumeMaxBias_进出场阈值分离官方'}
    if isinstance(value, dict):return {k: research_definitions(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, range)):return [research_definitions(v) for v in value]
    return aliases.get(value, value) if isinstance(value, str) else value


def prepare(me_conf, account, run_time):
    from config import bmac_data_path, utc_offset, simulator_config
    from core.utils.datatools import read_bmac_market_pivot
    strategy = me_conf.strategy
    if strategy.hold_period != '1H' or strategy.rebalance_cap_step != 1 or strategy.symbol_ratio_limit:
        raise ValueError('ROBUST40 requires 1H, full parent change and its own exposure constraints')
    minute = account.hour_offset_minute
    expected = expected_bar(run_time, minute, utc_offset)
    if not strategy.params.get('enable_live_hooks', False) or account.rebalance_mode.mode != 'RebRobust40':
        raise ValueError('ROBUST40 execution configuration is inconsistent')
    if account.rebalance_mode.params != dict(min_order_usdt_ratio=.4, execution_cap=2.45) or account.leverage != 1:
        raise ValueError('ROBUST40 v1 requires frozen execution parameters and leverage=1')
    options = strategy.params['runtime']
    if options['minute'] != minute:raise ValueError('Runtime and account offsets differ')
    state = Path(options['state_path']);bootstrap = Path(options['bootstrap_path'])
    # State cannot live in folders that run_by_account clears every hour.
    from config import runtime_folder
    for cleared in [Path(runtime_folder), Path('data/cache'), Path('data/runtime')]:
        if state.resolve().is_relative_to(cleared.resolve()):raise ValueError('State path is in a cleared cache directory')
    saved = pd.read_pickle(state if state.exists() else bootstrap)
    def check(bundle):
        if normalized(bundle['params']) != normalized(strategy.params['parent']):
            raise ValueError('Parent configuration changed')
        if normalized(bundle['simulator_config']) != normalized(simulator_config):
            raise ValueError('Child simulation configuration differs from baseline')
        if len(me_conf.factory.config_list) != 14:raise ValueError('Expected 14 children')
        for conf, reference in zip(me_conf.factory.config_list, bundle['strategy_pool']):
            if research_definitions(conf.strategy_list_raw) != normalized(reference['strategy_list']):
                raise ValueError('Child strategy definition differs from MODEL_ROBUST40 baseline')
            timing = dict(name=conf.timing.name, params=conf.timing.params) if conf.timing else None
            if normalized(timing) != normalized(reference.get('re_timing')):
                raise ValueError('Child retiming definition differs from baseline')
    check(saved)
    index = utc_index(me_conf.equity_dfs[0].candle_begin_time)
    leverage = pd.DataFrame(1., index=index, columns=range(14))
    for i, conf in enumerate(me_conf.factory.config_list):
        if conf.timing:
            path = conf.get_result_folder()/'再择时动态杠杆.csv'
            value = pd.read_csv(path, index_col='candle_begin_time', parse_dates=True)['动态杠杆']
            value.index = utc_index(value.index);leverage[i] = value.reindex(index)
    pivots = {k: read_bmac_market_pivot(account.hour_offset, k) for k in ('spot', 'swap')}
    sizes = {}
    for kind in ('spot', 'swap'):
        cols = [c.split(':', 1)[1] for c in saved['context']['columns'] if c.startswith(kind+':')]
        frozen = pd.Series(saved['context']['simulation'][kind+'_lot_sizes'], index=cols)
        current = pd.Series({canonical(k): .1**v for k, v in account.bn.market_info[kind]['min_qty'].items()})
        sizes[kind] = frozen.combine_first(current)
    context = build_context(me_conf.equity_dfs, me_conf.ratio_dfs, leverage, pivots, sizes, minute, simulator_config)
    raw = {}
    for path in sorted((Path(bmac_data_path)/account.hour_offset).rglob('swap_dict_batch*.pkl')):
        for symbol, frame in pd.read_pickle(path).items():
            symbol = canonical(symbol)
            if symbol in raw:raise ValueError('Duplicate raw swap symbol')
            frame = frame.copy();frame['candle_begin_time'] = utc_index(frame.candle_begin_time)
            if 'first_candle_time' not in frame:
                raise ValueError('Raw swap history must retain first_candle_time')
            if 'first_candle_time' in frame:
                frame['first_candle_time'] = pd.to_datetime(frame.first_candle_time, utc=True).dt.tz_localize(None)
            raw[symbol] = frame.loc[frame.candle_begin_time <= expected]
    if not raw:raise ValueError('No complete raw swap history')
    bundle = update_state(state, bootstrap, account.name, minute, context, raw, expected, check)
    strategy._robust40_result = bundle['result']
    strategy._robust40_index = pd.DatetimeIndex(me_conf.equity_dfs[0].candle_begin_time)


def parent_ratios(equities, strategy):
    if not hasattr(strategy, '_robust40_result'):
        raise ValueError('MODEL_ROBUST40 requires prepare_live before calc_ratio')
    original = pd.DatetimeIndex(equities[0].candle_begin_time)
    values = strategy._robust40_result['parent'].reindex(utc_index(original))
    if values.isna().any().any():raise ValueError('Missing ROBUST40 parent weights')
    values.index = original
    return values.copy()


def transform_targets(spot, swap, strategy):
    from .execution import apply_s5_delta
    result = strategy._robust40_result
    # Prove that core aggregation applied the child timing exactly once.
    for kind, frame in [('spot', spot), ('swap', swap)]:
        normalized_frame = _frame(frame)
        reference = result['targets'][kind].reindex(index=normalized_frame.index, columns=normalized_frame.columns)
        if kind == 'swap':
            delta = result['delta'].reindex(index=normalized_frame.index, columns=normalized_frame.columns, fill_value=0)
            normalized_frame = apply_s5_delta(normalized_frame, delta)
        if reference.isna().any().any() or not np.allclose(normalized_frame, reference, rtol=1e-10, atol=1e-12):
            raise ValueError('Framework target differs from shared ROBUST40 calculation')
        if kind == 'swap':
            swap = normalized_frame.set_axis(frame.index).set_axis(frame.columns, axis=1)
    return spot, swap
