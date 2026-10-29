"""Explicit offline replay of one account's completed local child outputs.

Writes a NEW research baseline only; does not run children or contact an exchange.
"""
import argparse
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.robust40.offline import assignment, canonical, hybrid_function
from tools.robust40.live import build_context
from tools.robust40.runtime import utc_index, validate_context, merge_context
from tools.robust40.engine import calculate, advance, slice_context


def read_children(root, snapshots, pool, minute, filters):
    equities, ratios = [], []
    for i, spec in enumerate(pool):
        name = f'S{i+1}-{spec["name"]}'
        saved = pd.read_pickle(root/name/'config.pkl')
        for key, expected in filters.items():
            actual = getattr(saved, key)
            normalize = lambda values: sorted({canonical(s) for s in values})
            if normalize(actual) != normalize(expected):
                raise ValueError(f'{name}: saved {key} differs from current account; rerun children first')
        frame = pd.read_pickle(root/name/'equity_df.pkl')
        frame['candle_begin_time'] = utc_index(frame.candle_begin_time)
        if not frame.candle_begin_time.dt.minute.eq(minute).all():
            raise ValueError(f'{name}: wrong minute')
        equities.append(frame)
        ratios.append(tuple(pd.read_pickle(snapshots/name/f'ratio_{k}.pkl') for k in ('spot','swap')))
    # process_equities uses the latest child start. Require matching latest bars:
    # accepting the minimum end would conceal a partially completed account run.
    ends = {d.candle_begin_time.iloc[-1] for d in equities}
    if len(ends) != 1:
        raise ValueError('Child outputs do not end at the same closed bar')
    start = max(d.candle_begin_time.iloc[0] for d in equities)
    equities = [d.loc[d.candle_begin_time >= start].copy() for d in equities]
    return equities, ratios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='Existing local accounts file')
    parser.add_argument('--output', required=True, help='New directory, never overwritten')
    parser.add_argument('--cut-hours', type=int, default=24)
    args = parser.parse_args()
    if args.cut_hours < 1:parser.error('--cut-hours must be positive')
    out = Path(args.output)
    if out.exists():raise FileExistsError(out)
    source = Path(args.account)
    strategy = assignment(source, 'strategy_config')
    pool = assignment(source, 'strategy_pool')
    simulator = assignment(source, 'simulator_config')
    options = strategy['params']['runtime'];minute = options['minute']
    name = source.stem+'_'+assignment(source, 'strategy_name')
    started = time.perf_counter()
    def progress(message):print(f'{time.perf_counter()-started:.1f}s {message}', flush=True)
    # Loading config does not initialize an exchange. Bind only the selected account.
    import os
    os.environ['X3S_TRADING_ACCOUNT'] = str(source.resolve())
    from config import bmac_data_path, black_list, white_list
    filters = dict(black_list=black_list, white_list=white_list)
    eq, ratios = read_children(Path('data/子策略回测结果')/name, Path('data/snapshot')/name, pool, minute, filters)
    sizes, pivots = {}, {}
    old = pd.read_pickle(options['bootstrap_path'])['context']
    for kind in ('spot','swap'):
        parts = {}
        for path in sorted((Path(bmac_data_path)/f'{minute}m').glob(f'market_pivot_{kind}_*.pkl')):
            for key, frame in pd.read_pickle(path).items():parts.setdefault(key, []).append(frame)
        pivots[kind] = {k: pd.concat(v).sort_index() for k,v in parts.items()}
        info = pd.read_pickle(Path('data/cache')/f'exginfo_{kind}.pkl')
        current = pd.Series(pd.to_numeric(info.lot_size).to_numpy(), index=info.symbol.map(canonical))
        columns = [c.split(':',1)[1] for c in old['columns'] if c.startswith(kind+':')]
        sizes[kind] = pd.Series(old['simulation'][kind+'_lot_sizes'], index=columns).combine_first(current)
    del old
    index = utc_index(eq[0].candle_begin_time)
    leverage = pd.DataFrame(1., index=index, columns=range(14))
    for i,spec in enumerate(pool):
        if spec.get('re_timing'):
            p = Path('data/子策略回测结果')/name/f'S{i+1}-{spec["name"]}'/'再择时动态杠杆.csv'
            value = pd.read_csv(p, index_col='candle_begin_time', parse_dates=True)['动态杠杆']
            value.index = utc_index(value.index);leverage[i] = value.reindex(index)
    context = build_context(eq, ratios, leverage, pivots, sizes, minute, simulator)
    validate_context(context)
    del eq,ratios,pivots
    merge_context(context, context)  # Includes initialized overlap and market checks.
    progress(f'validated local inputs: {len(index)} bars, {index[0]} to {index[-1]}')
    raw = {}
    for path in sorted((Path(bmac_data_path)/f'{minute}m').glob('swap_dict_batch*.pkl')):
        for symbol,frame in pd.read_pickle(path).items():
            symbol = canonical(symbol)
            if symbol in raw:raise ValueError('Duplicate raw symbol')
            frame['candle_begin_time'] = utc_index(frame.candle_begin_time)
            frame['first_candle_time'] = pd.to_datetime(frame.first_candle_time, utc=True).dt.tz_localize(None)
            raw[symbol] = frame.loc[frame.candle_begin_time <= index[-1]]
    if not raw:raise ValueError('Raw swap history missing')
    params = strategy['params']['parent']
    hybrid = hybrid_function(Path('positions/HybridRotationDriftStrategy.py'))(context['equities'], SimpleNamespace(params=params))
    hybrid.attrs = {}
    full = calculate(context, hybrid, raw, progress)
    prefix = slice_context(context, index[:-args.cut_hours])
    previous = calculate(prefix, hybrid.loc[prefix['index']], raw, progress)
    resumed = advance(context, hybrid, raw, previous, progress)
    for key in ('parent','delta','account'):
        np.testing.assert_allclose(full[key].to_numpy(), resumed[key].to_numpy(), rtol=1e-12, atol=1e-9)
    progress('full/resumed replay matched')
    bundle = dict(version=1, identity=f'LIVE-MODEL_ROBUST40-{source.stem}', minute=minute,
                  account_identity=source.stem, params=params, strategy_pool=pool,
                  simulator_config=simulator, context=context, result=full, source_filters=filters)
    out.mkdir(parents=True, exist_ok=False)
    pd.to_pickle(bundle, out/'runtime_bootstrap.pkl')
    full['account'].to_csv(out/'account.csv')
    progress(f'saved new baseline {out}; account configuration unchanged')


if __name__ == '__main__':main()
