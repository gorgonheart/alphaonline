"""Build a fresh MODEL_ROBUST40 reference for one minute offset, entirely offline."""
import argparse
import sys
import time
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.robust40.offline import load_context
from positions.ModelRobust40Strategy import calculate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True)
    p.add_argument('--minute', required=True, type=int)
    p.add_argument('--parent-config', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    output = Path(args.output)
    if output.exists():raise FileExistsError(f'Refusing to overwrite reference output: {output}')
    output.mkdir(parents=True)
    started = time.perf_counter()
    progress = lambda s: print(f'{time.perf_counter()-started:.2f}s {s}', flush=True)
    progress('read child and market histories')
    context, hybrid, raw = load_context(args.project, args.minute, args.parent_config)
    progress(f'input ready: {len(context["index"])} hours, {len(context["columns"])} instruments')
    result = calculate(context, hybrid, raw, progress)
    for name in ['parent', 'base', 'weekly_mix', 'predictions', 'delta', 'account', 'train_windows']:
        result[name].to_pickle(output/f'{name}.pkl')
    for kind, target in result['targets'].items():target.to_pickle(output/f'target_{kind}.pkl')
    for i, a in enumerate(result['virtual_accounts']):a.to_pickle(output/f'virtual_{i}.pkl')
    pd.to_pickle(result['models'], output/'models.pkl')
    pd.to_pickle(context, output/'context.pkl')
    pd.to_pickle(result, output/'result.pkl')
    progress('reference generated; formal root backtest and live adapter verification still required')


if __name__ == '__main__':main()
