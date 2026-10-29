"""Read supplied backtest inputs without importing configs or starting a service."""
import ast
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse


def assignment(path, name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
    safe = dict(dict=dict, list=list, range=range, zip=zip, Path=Path, WindowsPath=Path)
    for n in ast.walk(node.value):
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in safe):
            raise ValueError(f'Unsupported config expression: {ast.unparse(n)}')
    return eval(compile(ast.Expression(node.value), str(path), 'eval'), {'__builtins__': {}, **safe})


def hybrid_function(path):
    """Load only the pure strategy, omitting its type-only core dependency."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == 'core.model.strategy_config')]
    # The sandbox/reference directories remain read-only, including numba caches.
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == 'cache':
            node.value = ast.Constant(False)
    module = types.ModuleType('_robust40_hybrid')
    module.__file__ = str(path);module.PosStrategyConfig = object
    sys.modules[module.__name__] = module
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), module.__dict__)
    return module.calc_ratio


def canonical(symbol):
    return str(symbol).replace('-', '')


def rename_columns(frame):
    out = frame.rename(columns=canonical)
    if not out.columns.is_unique:
        raise ValueError('Canonical instrument names collide')
    return out


def load_context(project, minute, parent_config):
    project = Path(project);offset = f'{minute}m'
    config = project/f'config_MODEL_ROBUST40_{offset}.py'
    pool = assignment(config, 'strategy_pool')
    root = project/'results/children'/f'OV1028B_{offset}_ORIGINAL'
    folders = [root/f'S{i+1}-{s["name"]}' for i, s in enumerate(pool)]
    equities = [pd.read_pickle(p/'equity_df.pkl') for p in folders]
    ratios = [];columns = set();index = None
    for folder in folders:
        pair = {}
        for kind in ['spot', 'swap']:
            d = rename_columns(pd.read_pickle(folder/f'df_{kind}_ratio.pkl')).fillna(0)
            if index is None:index = d.index
            if not d.index.equals(index):raise ValueError('Child ratio histories differ')
            pair[kind] = d
            columns.update(f'{kind}:{s}' for s in d.columns)
        ratios.append(pair)
    columns = sorted(columns);mapping = {c: i for i, c in enumerate(columns)}
    matrices = []
    for pair in ratios:
        parts = []
        for kind, d in pair.items():
            coo = sparse.coo_matrix(d.to_numpy())
            dest = np.array([mapping[f'{kind}:{s}'] for s in d.columns], dtype=int)
            parts.append(sparse.csr_matrix((coo.data, (coo.row, dest[coo.col])), shape=(len(index), len(columns))))
        matrices.append(parts[0]+parts[1])
    del ratios
    leverage = pd.DataFrame(1., index=index, columns=range(14))
    for i, folder in enumerate(folders):
        timing = folder/'再择时动态杠杆.csv'
        if timing.exists():
            leverage[i] = pd.read_csv(timing, index_col='candle_begin_time', parse_dates=True)['动态杠杆'].reindex(index)
    if leverage.isna().any().any():raise ValueError('Retiming history missing')
    sim = assignment(config, 'simulator_config');n = len(index)
    args = dict(init_capital=float(sim['initial_usdt']), leverages=np.ones(n),
                spot_c_rate=sim['spot_c_rate'], swap_c_rate=sim['swap_c_rate'],
                spot_min_order_limit=float(sim['spot_min_order_limit']), swap_min_order_limit=float(sim['swap_min_order_limit']),
                min_margin_rate=sim['margin_rate'], require_rebalance=np.ones(n, dtype=np.int8),
                capital_flow_arr=np.zeros(n), capital_flow_mode=-1)
    raw_root = assignment(config, 'raw_data_path')
    close = []
    for kind in ['spot', 'swap']:
        symbols = [c.split(':', 1)[1] for c in columns if c.startswith(kind+':')]
        pivots = {k: rename_columns(v) for k, v in pd.read_pickle(raw_root/f'market_pivot_{kind}.pkl').items()}
        prices = pivots['close'].reindex(columns=symbols)
        prices.columns = [f'{kind}:{s}' for s in symbols];close.append(prices)
        for key in ['open', 'close', 'vwap1m']:
            args[f'{kind}_{key}_p'] = np.ascontiguousarray(pivots[key].loc[index, symbols].to_numpy())
        if kind == 'swap':
            args['funding_rates'] = np.ascontiguousarray(pivots['funding_rate'].loc[index, symbols].to_numpy())
        qty = pd.read_csv(project/'data/min_qty'/f'最小下单量_{kind}.csv', encoding='utf-8-sig')
        power = -np.log10(qty['最小下单量']).round().astype(int)
        sizes = pd.Series(.1**power.to_numpy(), index=qty['币种'].map(canonical))
        args[f'{kind}_lot_sizes'] = sizes.reindex(symbols, fill_value=.1**power.max()).to_numpy()
    context = dict(index=index, minute=minute, columns=columns, leverage=leverage, matrices=matrices,
                   equities=equities, simulation=args, close=pd.concat(close, axis=1).reindex(columns=columns))
    params = assignment(parent_config, 'strategy_config')['params']
    hybrid = hybrid_function(project/'positions/HybridRotationDriftStrategy.py')(equities, types.SimpleNamespace(params=params))
    hybrid.attrs = {}
    raw = {canonical(k): v for k, v in pd.read_pickle(raw_root/'swap_dict.pkl').items()}
    return context, hybrid, raw
