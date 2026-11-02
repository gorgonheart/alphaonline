import ast
import shutil
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np

import pandas as pd
import pytest
from scipy import sparse

from tools.robust40.warmup import export_package, opted_in, prepare_startup, restore_startup, validate_snapshot
from tools.robust40.market_inputs import latest_ready, freeze_inputs


def make_pair(folder, minute=20, hours=4):
    folder.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range(f'2026-01-01 00:{minute:02}', periods=hours, freq='h')
    pool = [{'name': 'one'}, {'name': 'two', 're_timing': {'name': 'signal'}}]
    for i, spec in enumerate(pool, 1):
        child = folder/f'S{i}-{spec["name"]}'
        child.mkdir(exist_ok=True)
        pd.DataFrame({'candle_begin_time': idx}).to_pickle(child/'选币结果.pkl')
        pd.DataFrame(index=idx).to_pickle(child/'ratio_spot.pkl')
        pd.DataFrame({'BTCUSDT': .1*i}, index=idx).to_pickle(child/'ratio_swap.pkl')
        for state in ('state0', 'state1') if spec.get('re_timing') else ('state0',):
            pd.to_pickle({'simulate_to':idx[-1],
                         'spot':dict(columns=[],sim=SimpleNamespace(lot_sizes=np.array([]))),
                         'swap':dict(columns=['BTCUSDT'],sim=SimpleNamespace(lot_sizes=np.array([.001])))}, child/f'lots_{state}.pkl')
            pd.DataFrame({'candle_begin_time':idx}).to_pickle(child/f'account_{state}.pkl')
    (folder/'snapshot_time.txt').write_text(str(idx[-1]))
    return dict(minute=minute, account_identity='demo', strategy_pool=pool,
                context=dict(index=idx, columns=['swap:BTCUSDT'],
                             simulation=dict(spot_lot_sizes=np.array([]),swap_lot_sizes=np.array([.001])),
                             matrices=[sparse.csr_matrix([[.1*i]]*hours) for i in (1,2)]),
                result=dict(parent=pd.DataFrame({0:1.}, index=idx)))


def test_package_rejects_different_child_and_model_units(tmp_path):
    bundle=make_pair(tmp_path)
    bundle['context']['simulation']['swap_lot_sizes'][0]=.01
    with pytest.raises(ValueError,match='Child and MODEL instrument units differ'):
        validate_snapshot(tmp_path,bundle)


def package(tmp_path, minute=20):
    account = tmp_path/'demo.py'
    account.write_text(f"account_config={{'hour_offset':'{minute}m'}}\nstrategy_name='model'\nstrategy_config={{'name':'ModelRobust40Strategy',"
                       f"'params':{{'runtime':{{'state_path':'data/model/demo.pkl',"
                       f"'warmup_path':'package'}}}}}}\nblack_list=[]\n", encoding='utf8')
    output = tmp_path/'package'
    output.mkdir()
    bundle = make_pair(tmp_path/'source', minute)
    pd.to_pickle(bundle, output/'runtime_bootstrap.pkl')
    export_package(account, tmp_path/'source', output)
    return account, bundle


@pytest.mark.parametrize('minute', [20,40,55])
def test_first_start_restores_matching_pair(tmp_path, minute):
    account, bundle = package(tmp_path, minute)
    plans = prepare_startup([account], tmp_path)
    restore_startup(plans)
    snapshot = tmp_path/'data/snapshot/demo_model'
    saved = pd.read_pickle(tmp_path/'data/model/demo.pkl')
    assert validate_snapshot(snapshot, saved) == bundle['context']['index'][-1]
    assert not plans[0][0].exists()
    # A clean restart preserves the existing complete pair.
    plans = prepare_startup([account], tmp_path)
    shutil.rmtree(snapshot)
    restore_startup(plans)
    assert validate_snapshot(snapshot, saved) == bundle['context']['index'][-1]


def test_restart_never_rewinds_advanced_state(tmp_path):
    account, _ = package(tmp_path)
    restore_startup(prepare_startup([account], tmp_path))
    snapshot = tmp_path/'data/snapshot/demo_model'
    advanced = make_pair(snapshot, hours=6)
    state = tmp_path/'data/model/demo.pkl'
    pd.to_pickle(advanced, state)
    plans = prepare_startup([account], tmp_path)
    shutil.rmtree(snapshot)
    restore_startup(plans)
    assert validate_snapshot(snapshot, pd.read_pickle(state)) == advanced['context']['index'][-1]


def test_mismatch_is_rejected_before_cleanup(tmp_path):
    account, _ = package(tmp_path)
    restore_startup(prepare_startup([account], tmp_path))
    snapshot = tmp_path/'data/snapshot/demo_model'
    (snapshot/'snapshot_time.txt').write_text('2026-01-01 04:20')
    with pytest.raises(ValueError, match='cutoff differ'):prepare_startup([account], tmp_path)
    assert (snapshot/'S1-one/lots_state0.pkl').exists()


def test_changed_blacklist_rejected_before_cleanup(tmp_path):
    account, _ = package(tmp_path)
    with account.open('a') as f:f.write("black_list=['BTCUSDT']\n")
    with pytest.raises(ValueError, match='configuration changed'):prepare_startup([account], tmp_path)


def test_same_timestamp_different_position_rejected(tmp_path):
    account, _ = package(tmp_path)
    path = tmp_path/'package/snapshot/S1-one/ratio_swap.pkl'
    frame = pd.read_pickle(path);frame.iloc[-1,0] = .9;frame.to_pickle(path)
    with pytest.raises(ValueError, match='last positions differ'):prepare_startup([account], tmp_path)


def test_missing_retiming_state_rejected(tmp_path):
    account, _ = package(tmp_path)
    (tmp_path/'package/snapshot/S2-two/lots_state1.pkl').unlink()
    with pytest.raises(FileNotFoundError):prepare_startup([account], tmp_path)


def test_legacy_accounts_not_executed(tmp_path):
    account = tmp_path/'legacy.py'
    account.write_text("warmup_path='legacy_cache'\nraise AssertionError('Must not execute legacy config')")
    assert prepare_startup([account], tmp_path) == []


def test_keyword_form_opt_in(tmp_path):
    account = tmp_path/'model.py'
    account.write_text("strategy_config=dict(name='ModelRobust40Strategy', params=dict(runtime=dict(warmup_path='package')))")
    assert opted_in(account)


def test_clean_false_requires_existing_pair(tmp_path):
    account, _ = package(tmp_path)
    make_pair(tmp_path/'data/snapshot/demo_model')
    with pytest.raises(ValueError, match='no paired MODEL'):prepare_startup([account], tmp_path, False)


def test_failed_other_offset_does_not_touch_completed_package(tmp_path):
    first, second = tmp_path/'20m', tmp_path/'55m'
    first.mkdir();second.mkdir()
    account, bundle = package(first, 20)
    before = (first/'package/runtime_bootstrap.pkl').read_bytes()
    bad = make_pair(second/'source', 55)
    bad['minute'] = 40
    (second/'package').mkdir()
    pd.to_pickle(bad, second/'package/runtime_bootstrap.pkl')
    with pytest.raises(ValueError, match='offset or closed bar'):
        export_package(account, second/'source', second/'package')
    assert not (second/'package/warmup.pkl').exists()
    assert (first/'package/runtime_bootstrap.pkl').read_bytes() == before
    assert validate_snapshot(first/'package/snapshot', bundle).minute == 20


def test_latest_ready_requires_all_four_and_ignores_future(tmp_path):
    stamp = int(pd.Timestamp('2026-01-01 10:40', tz='UTC').timestamp())
    prefixes = ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_')
    for prefix in prefixes:
        for t in (stamp, stamp+3600):(tmp_path/f'{prefix}{t}.ready').touch()
    assert latest_ready(tmp_path, 40, now=stamp+60)[0] == stamp
    (tmp_path/f'market_pivot_swap_{stamp}.ready').unlink()
    with pytest.raises(ValueError, match='no complete'):latest_ready(tmp_path, 40, now=stamp+60)


def test_freeze_rejects_partial_new_publication(tmp_path):
    folder = tmp_path/'source/20m';folder.mkdir(parents=True)
    for kind in ('spot','swap'):
        (folder/f'{kind}_dict_batch0.pkl').touch()
        (folder/f'market_pivot_{kind}_2026.pkl').touch()
    with pytest.raises(ValueError, match='publishing newer'):
        freeze_inputs(tmp_path/'source', tmp_path/'exginfo', tmp_path/'frozen', 20, 0, 1)


def test_startup_handoff_surrounds_original_cleanup():
    tree = ast.parse(Path('startup.py').read_text(encoding='utf8'))
    calls = {n.func.id: n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id in ('prepare_startup','restore_startup','check_accounts')}
    cleanup = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == 'clean_startup']
    assert calls['prepare_startup'] < cleanup[0] < calls['restore_startup'] < calls['check_accounts']


def test_worker_spawn_keeps_framework_paths_isolated(tmp_path):
    root = Path.cwd()
    account = next((root/'examples/model_robust40').glob('*20m*.py'))
    script = tmp_path/'spawn_check.py'
    script.write_text('''import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import tools.robust40.cli.prewarm_robust40

def check():
    import config
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.utils import datatools
    from core.utils.path_kit import get_file_path
    workspace = Path(os.environ['ROBUST40_WARMUP_WORKSPACE'])
    mef = MultiEquityBacktestConfig()
    assert len(mef.factory.config_list) == 14
    for p in (config.snapshot_path, config.backtest_path, config.runtime_folder,
              mef.factory.result_folder, Path(get_file_path('logs', 'probe.log'))):
        assert Path(p).is_relative_to(workspace), p
    assert datatools.bmac_data_path == workspace/'frozen_market'
    assert datatools.bmac_exginfo_path == workspace/'frozen_market/exginfo'
    from tools.robust40.initialization_config import MODEL_JOB_NUM
    from core import select_coin
    assert config.job_num == select_coin.job_num == MODEL_JOB_NUM
    return 'isolated'

if __name__ == '__main__':
    assert check() == 'isolated'
    with ProcessPoolExecutor(max_workers=1) as pool:
        assert pool.submit(check).result() == 'isolated'
''', encoding='utf8')
    env = dict(os.environ, PYTHONPATH=str(root), X3S_TRADING_ACCOUNT=str(account),
               ROBUST40_WARMUP_WORKER='1', ROBUST40_WARMUP_WORKSPACE=str(tmp_path/'workspace'))
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True,
                            encoding='utf8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
