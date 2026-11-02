"""Compare real-data full replay with a stopped/restarted MODEL_ROBUST40 run."""
import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.robust40.offline import assignment, hybrid_function, canonical
from positions.ModelRobust40Strategy import calculate, advance, slice_context


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True);p.add_argument('--reference', required=True)
    p.add_argument('--parent-config', required=True);p.add_argument('--cut-hours', type=int, default=240)
    p.add_argument('--verify-only', action='store_true', help='Check existing artifacts without writing a bootstrap')
    args = p.parse_args();project = Path(args.project);root = Path(args.reference)
    c = pd.read_pickle(root/'context.pkl');minute = c['minute']
    config = project/f'config_MODEL_ROBUST40_{minute}m.py'
    raw = {canonical(k): v for k, v in pd.read_pickle(assignment(config, 'raw_data_path')/'swap_dict.pkl').items()}
    params = assignment(args.parent_config, 'strategy_config')['params']
    hybrid = hybrid_function(project/'positions/HybridRotationDriftStrategy.py')(c['equities'], SimpleNamespace(params=params));hybrid.attrs = {}
    started = time.perf_counter();progress = lambda s: print(f'{time.perf_counter()-started:.2f}s {s}', flush=True)
    full = calculate(c, hybrid, raw, progress)
    for field in ['parent', 'delta', 'account']:
        old = pd.read_pickle(root/f'{field}.pkl')
        np.testing.assert_allclose(full[field].to_numpy(), old.to_numpy(), rtol=1e-12, atol=1e-9)
    prefix = slice_context(c, c['index'][:-args.cut_hours])
    prior = calculate(prefix, hybrid.loc[hybrid.index <= prefix['index'][-1]], raw, progress)
    progress('resume saved virtual-account states')
    resumed = advance(c, hybrid, raw, prior, progress)
    for field in ['parent', 'delta', 'account']:
        np.testing.assert_allclose(resumed[field].to_numpy(), full[field].to_numpy(), rtol=1e-12, atol=1e-9)
        progress(f'{field}: resume/full max error {np.max(abs(resumed[field].to_numpy()-full[field].to_numpy())):.3g}')
    bundle = dict(version=1, identity=f'MODEL_ROBUST40-v1-{minute}m', minute=minute,
        params=params, strategy_pool=assignment(config, 'strategy_pool'),
        simulator_config=assignment(config, 'simulator_config'), context=c, result=full)
    if args.verify_only:
        progress('verification complete; existing bootstrap preserved')
        return
    destination = root/'runtime_bootstrap.pkl'
    if destination.exists():raise FileExistsError(destination)
    pd.to_pickle(bundle, destination)
    progress('verified bootstrap saved')


if __name__ == '__main__':main()
