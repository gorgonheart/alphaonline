"""Persistent per-account history; strict overlap validation before any new target.

Only trusted local pickle files are supported. This module never contacts an exchange.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from scipy import sparse

from .features import decision_days
from .engine import advance
from .offline import hybrid_function
from tools.local_risk_model import lock, atomic_save


def utc_index(index):
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_convert('UTC').tz_localize(None)
    if not index.is_unique or not index.is_monotonic_increasing or index.hasnans:
        raise ValueError('Invalid or duplicate time axis')
    return index


def expected_bar(run_time, minute, utc_offset=0):
    now = pd.Timestamp(run_time)
    now = now.tz_convert('UTC').tz_localize(None) if now.tz is not None else now-pd.Timedelta(hours=utc_offset)
    decision_days(pd.DatetimeIndex([now]), minute)
    return now-pd.Timedelta(hours=1)


def validate_context(c):
    index = utc_index(c['index'])
    decision_days(index, c['minute'])
    if len(index) == 0 or not index.equals(pd.date_range(index[0], index[-1], freq='h')):
        raise ValueError('Hourly history has a gap')
    if len(c['matrices']) != 14 or len(c['equities']) != 14:
        raise ValueError('MODEL_ROBUST40 requires 14 children')
    if len(set(c['columns'])) != len(c['columns']):
        raise ValueError('Duplicate instruments')
    if not c['leverage'].index.equals(index) or not np.isfinite(c['leverage']).all().all():
        raise ValueError('Missing retiming history')
    for m in c['matrices']:
        if m.shape != (len(index), len(c['columns'])) or not np.isfinite(m.data).all():
            raise ValueError('Invalid child position history')


def _expand_matrix(matrix, old, columns):
    coo = matrix.tocoo()
    dest = pd.Index(columns).get_indexer(old)
    return sparse.csr_matrix((coo.data, (coo.row, dest[coo.col])), shape=(matrix.shape[0], len(columns)))


def _same(a, b, label, atol=1e-10, rtol=1e-8):
    if not np.allclose(a, b, atol=atol, rtol=rtol, equal_nan=True):
        raise ValueError(f'Historical revision in {label}; rebuild this offset baseline explicitly')


def merge_context(old, new, minimum_overlap=168, warmup=1000):
    """Append closed bars, preserve prior observations and frozen virtual lot sizes.

    Moving-window child equity is accepted only if it differs by one constant
    scale, after its initialization region. No silent overwrite of old history.
    """
    validate_context(old);validate_context(new)
    if old['minute'] != new['minute']:
        raise ValueError('Offset differs from saved state')
    last = old['index'][-1]
    if new['index'][-1] < last:
        raise ValueError('Incoming history is stale')
    overlap = old['index'].intersection(new['index'][warmup:])[-minimum_overlap:]
    if len(overlap) < minimum_overlap:
        raise ValueError('Insufficient initialized overlap to continue')
    addition = new['index'][new['index'] > last]
    if len(addition) and addition[0] != last+pd.Timedelta(hours=1):
        raise ValueError('Gap before incoming bars')
    columns = sorted(set(old['columns']) | set(new['columns']))
    oi = old['index'].get_indexer(overlap);ni = new['index'].get_indexer(overlap)
    ai = new['index'].get_indexer(addition)
    matrices = []
    for i, (a, b) in enumerate(zip(old['matrices'], new['matrices'])):
        a = _expand_matrix(a, old['columns'], columns);b = _expand_matrix(b, new['columns'], columns)
        _same(a[oi].toarray(), b[ni].toarray(), f'S{i+1} positions')
        matrices.append(sparse.vstack([a, b[ai]], format='csr'))
    _same(old['leverage'].loc[overlap], new['leverage'].loc[overlap], 'retiming')
    equities = []
    for i, (a, b) in enumerate(zip(old['equities'], new['equities'])):
        a = a.set_index('candle_begin_time');b = b.set_index('candle_begin_time').copy()
        if not overlap.isin(a.index).all() or not overlap.isin(b.index).all():
            raise ValueError('Child equity overlap missing')
        scale = a.loc[overlap[-1], 'close']/b.loc[overlap[-1], 'close']
        if not np.isfinite(scale) or scale <= 0:raise ValueError('Invalid child equity scale')
        b.loc[:, ['open', 'high', 'low', 'close']] *= scale
        if set(a.columns) != set(b.columns):raise ValueError('Child factor columns changed')
        _same(a.loc[overlap], b.loc[overlap, a.columns], f'S{i+1} equity/factors', atol=1e-8, rtol=1e-6)
        equities.append(pd.concat([a, b.loc[addition, a.columns]]).reset_index())
    simulation = dict(old['simulation'])
    row_fields = {'leverages', 'require_rebalance', 'capital_flow_arr'}
    for key in row_fields:
        simulation[key] = np.concatenate([simulation[key], new['simulation'][key][ai]])
    for kind in ('spot', 'swap'):
        cols = [c for c in columns if c.startswith(kind+':')]
        oc = [c for c in old['columns'] if c.startswith(kind+':')]
        nc = [c for c in new['columns'] if c.startswith(kind+':')]
        old_sizes = pd.Series(old['simulation'][kind+'_lot_sizes'], index=oc)
        sizes = pd.Series(new['simulation'][kind+'_lot_sizes'], index=nc)
        simulation[kind+'_lot_sizes'] = old_sizes.reindex(cols).fillna(sizes).to_numpy()
        if not np.isfinite(simulation[kind+'_lot_sizes']).all():raise ValueError('Missing lot sizes')
        keys = [kind+'_'+k+'_p' for k in ('open', 'close', 'vwap1m')]
        if kind == 'swap':keys.append('funding_rates')
        for key in keys:
            a = pd.DataFrame(old['simulation'][key], index=old['index'], columns=oc).reindex(columns=cols)
            b = pd.DataFrame(new['simulation'][key], index=new['index'], columns=nc).reindex(columns=cols)
            common = sorted(set(oc) & set(nc))
            _same(a.loc[overlap, common], b.loc[overlap, common], key)
            if key == 'funding_rates':a = a.fillna(0);b = b.fillna(0)
            simulation[key] = np.ascontiguousarray(pd.concat([a, b.loc[addition]]).to_numpy())
    for key in simulation.keys()-row_fields:
        if not isinstance(simulation[key], np.ndarray) and simulation[key] != new['simulation'][key]:
            raise ValueError(f'Simulator parameter changed: {key}')
    # Newly listed instruments may bring older prices, but never revise old symbols.
    close = old['close'].reindex(columns=columns).combine_first(new['close'].reindex(columns=columns))
    close = close.loc[:new['index'][-1]]
    result = dict(old, index=old['index'].append(addition), columns=columns, matrices=matrices,
        leverage=pd.concat([old['leverage'], new['leverage'].loc[addition]]),
        equities=equities, simulation=simulation, close=close)
    validate_context(result)
    return result


def expand_result(result, old_columns, columns):
    """Map saved lots by instrument, retaining delisted positions and pending orders."""
    out = dict(result)
    def account(frame):
        states = []
        for kind, state in zip(('spot', 'swap'), frame.attrs['simulation_state']):
            old = [c for c in old_columns if c.startswith(kind+':')]
            new = [c for c in columns if c.startswith(kind+':')]
            equity, lots, targets, prices, initialized = state
            align = lambda values: pd.Series(values, index=old).reindex(new, fill_value=0).to_numpy()
            states.append((equity, align(lots), align(targets), align(prices), initialized))
        frame = frame.copy();frame.attrs['simulation_state'] = tuple(states)
        return frame
    out['virtual_accounts'] = [account(a) for a in result['virtual_accounts']]
    out['account'] = account(result['account'])
    symbols = lambda kind: [c.split(':', 1)[1] for c in columns if c.startswith(kind+':')]
    out['targets'] = {k: v.reindex(columns=symbols(k), fill_value=0) for k, v in result['targets'].items()}
    out['delta'] = result['delta'].reindex(columns=symbols('swap'), fill_value=0)
    return out


def update_state(state_path, bootstrap_path, identity, minute, incoming, raw_swap, expected, validate=None):
    """A failed update leaves the previous state unchanged; duplicate runs verify input."""
    state_path = Path(state_path);bootstrap_path = Path(bootstrap_path)
    if state_path.resolve() == bootstrap_path.resolve():raise ValueError('State must not overwrite bootstrap')
    with lock(state_path):
        bundle = pd.read_pickle(state_path if state_path.exists() else bootstrap_path)
        if bundle['version'] != 1 or bundle['minute'] != minute:
            raise ValueError('Wrong bootstrap version or minute')
        if bundle.get('account_identity', identity) != identity:
            raise ValueError('State belongs to another account')
        if incoming['index'][-1] != expected:
            raise ValueError('Incoming data does not end at the expected closed bar')
        if validate is not None:validate(bundle)
        if not bundle['result']['parent'].index.equals(bundle['context']['index']):
            raise ValueError('Saved calculation and history time axes differ')
        context = merge_context(bundle['context'], incoming)
        previous = expand_result(bundle['result'], bundle['context']['columns'], context['columns'])
        calc = hybrid_function(Path(__file__).resolve().parents[2]/'positions/HybridRotationDriftStrategy.py')
        hybrid = calc(context['equities'], SimpleNamespace(params=bundle['params']));hybrid.attrs = {}
        result = advance(context, hybrid, raw_swap, previous)
        updated = dict(bundle, context=context, result=result, account_identity=identity)
        atomic_save(state_path, updated)
        return updated
