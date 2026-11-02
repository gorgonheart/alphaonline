"""Build children, framework snapshots and MODEL state in a NEW isolated workspace.

Uses live data and read-only exchange market-rule queries. Never calls trading/startup.
Run from the project root with alpha311. Each offset needs its own run.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.robust40.market_inputs import (latest_ready, freeze_inputs, prepare_public_market)












def configure_worker():
    """Also executed by Windows spawn, before any framework module imports."""
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8')
    import core.utils.path_kit as paths
    workspace = Path(os.environ['ROBUST40_WARMUP_WORKSPACE']).resolve()
    paths.PROJECT_ROOT = str(workspace)
    import config
    config.bmac_data_path = workspace/'frozen_market'
    config.bmac_exginfo_path = config.bmac_data_path/'exginfo'
    config.is_simulate = 'debug'
    config.is_debug = True
    from tools.robust40.initialization_config import MODEL_JOB_NUM
    config.job_num = MODEL_JOB_NUM
    config.error_webhook_url = ''
    return config


# Multiprocessing imports this module as __mp_main__; children need the same paths.
if os.environ.get('ROBUST40_WARMUP_WORKER') == '1':
    configure_worker()






def run_worker(account, workspace, output, stamp):
    import pandas as pd
    import config
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.utils.datatools import snapshot_bmac_exginfo
    from tools.robust40.warmup import settings
    public_before, _ = settings(account)
    mef = MultiEquityBacktestConfig()
    for folder in [mef.factory.result_folder, config.snapshot_path, config.runtime_folder]:
        if not Path(folder).resolve().is_relative_to(workspace):
            raise ValueError('Warmup output escaped its isolated workspace')
    prepare_public_market()
    snapshot_bmac_exginfo()
    run_time = (pd.Timestamp(stamp, unit='s') + pd.Timedelta(hours=config.utc_offset)).to_pydatetime()
    mef.backtest_strategies(run_time)
    mef.process_equities(run_time, config.account_config['hour_offset'])
    if settings(account)[0] != public_before:
        raise ValueError('Account settings changed during warmup; rerun')
    from tools.robust40.cli.rebuild_robust40_live_baseline import main as rebuild
    sys.argv = [sys.argv[0], '--account', str(account), '--workspace', str(workspace),
                '--output', str(output), '--with-snapshot']
    rebuild()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--workspace', required=True, help='NEW isolated scratch directory')
    parser.add_argument('--output', required=True, help='NEW persistent package directory')
    parser.add_argument('--worker', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    account, workspace, output = (Path(p).resolve() for p in (args.account, args.workspace, args.output))
    if args.worker is not None:
        if os.environ.get('ROBUST40_WARMUP_WORKSPACE') != str(workspace):
            raise ValueError('Worker must be launched by the isolated warmup command')
        run_worker(account, workspace, output, args.worker)
        return
    if workspace.exists() or output.exists():raise FileExistsError('Workspace and output must both be new')
    from tools.robust40.warmup import settings
    values, options = settings(account)
    if values['strategy_config']['name'] != 'ModelRobust40Strategy':
        raise ValueError('Only MODEL_ROBUST40 accounts are supported')
    if values['account']['hour_offset'] != f'{options["minute"]}m':
        raise ValueError('Account and model minute differ')
    os.environ['X3S_TRADING_ACCOUNT'] = str(account)
    # Redirect config import side effects (mkdir/log paths) to scratch as well.
    import core.utils.path_kit as paths
    paths.PROJECT_ROOT = str(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    if (ROOT/'config.json').exists():
        # Preserve public framework overrides without copying notification URLs.
        import json
        original = json.loads((ROOT/'config.json').read_text(encoding='utf8'))
        keys = ('realtime_data_path', 'factor_col_limit', 'lookback_days', 'incremental_lookback_hours')
        (workspace/'config.json').write_text(json.dumps({k: original[k] for k in keys if k in original}))
    import config
    if config.exchange_name != 'binance':raise ValueError('Warmup currently supports Binance only')
    stamp, ready_time = latest_ready(config.bmac_data_path/f'{options["minute"]}m', options['minute'])
    freeze_inputs(config.bmac_data_path, config.bmac_exginfo_path, workspace/'frozen_market',
                  options['minute'], stamp, ready_time)
    env = dict(os.environ, ROBUST40_WARMUP_WORKER='1', ROBUST40_WARMUP_WORKSPACE=str(workspace))
    subprocess.run([sys.executable, str(Path(__file__).resolve()), '--account', str(account),
                    '--workspace', str(workspace), '--output', str(output), '--worker', str(stamp)],
                   cwd=ROOT, env=env, check=True)


if __name__ == '__main__':main()
