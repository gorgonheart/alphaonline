"""Offline failures of the actual initializer/process lifecycle, without trading."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from tools.robust40 import server


def test_launch_failure_closes_log_and_allows_retry(tmp_path, monkeypatch):
    account = tmp_path/'a.py'; account.write_text('')
    run = datetime(2026, 10, 30, 20, 40)
    stamp = int(run.timestamp())
    folder = tmp_path/'market/40m'; folder.mkdir(parents=True)
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_'):
        (folder/f'{prefix}{stamp}.ready').touch()
    values = dict(strategy_name='s', account=dict(hour_offset='40m'), strategy_pool=[])
    monkeypatch.setattr(server, 'enabled', lambda a: True)
    monkeypatch.setattr(server, 'settings', lambda a: (values, dict(minute=40)))
    monkeypatch.setattr('time.time', lambda: stamp+1)
    monkeypatch.setattr(server.Coordinator, '_monitor', lambda *a: None, raising=False)
    handles = []
    def launch(*args, **kwargs):
        handles.append(kwargs['stdout'])
        if len(handles) == 1: raise OSError('process creation failed')
        assert kwargs['env']['PYTHONIOENCODING'] == 'utf-8'
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    c = server.Coordinator(tmp_path)
    with pytest.raises(OSError, match='process creation failed'):
        c.command(account, run, tmp_path/'market')
    assert handles[0].closed
    assert c.command(account, run, tmp_path/'market') == (None, None)
    assert len(handles) == 2
    assert handles[1].closed


@pytest.mark.parametrize('fails', [False, True])
def test_background_exit_is_reported_without_another_offset(tmp_path, monkeypatch, fails):
    from core.utils.log_kit import logger
    messages = []
    monkeypatch.setattr(logger, 'info', messages.append)
    monkeypatch.setattr(logger, 'error', messages.append)
    path = tmp_path/'initialize.log'
    script = "print('x'*20000, flush=True); print('初始化中文正常', flush=True)"
    if fails: script += "; raise RuntimeError('初始化失败原因')"
    with path.open('w', encoding='utf-8') as handle:
        process = subprocess.Popen([sys.executable, '-c', script], stdout=handle, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    c = server.Coordinator(tmp_path)
    try:
        thread = c._monitor(tmp_path/'a.py', process, path)
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert any(str(path) in m for m in messages)
        if fails: assert any('RuntimeError: 初始化失败原因' in m for m in messages)
        else: assert any('初始化完成' in m for m in messages)
        assert max(map(len, messages)) < 10000
    finally:
        process.wait(timeout=15)


@pytest.mark.parametrize('raises', [False, True])
def test_account_not_ready_preserves_snapshot_but_calculation_failure_does_not(tmp_path, monkeypatch, raises):
    from tools.robust40.cli import server_robust40 as worker
    import config
    from tools.robust40 import handoff
    monkeypatch.setattr(config,'strategy_config',{'params':{'runtime':{}}})
    (tmp_path/'model').mkdir()
    monkeypatch.setattr(handoff,'prepare',lambda *a:(None,{}))
    meta = dict(complete=True, cutoff='2026-10-30 10:40', origin='2025-04-25 00:40')
    pd.to_pickle(meta, tmp_path/'warmup.pkl')
    monkeypatch.setattr(server, 'settings', lambda a: ({}, {}))
    monkeypatch.setattr(server, 'location', lambda *a: tmp_path)
    monkeypatch.setattr(server, 'check_dependencies', lambda *a: None)
    monkeypatch.setattr(server, 'resume_capacity', lambda *a: 240)
    monkeypatch.setattr(server, 'inspect', lambda *a: ('ready', ''))
    stamp = int(pd.Timestamp('2026-10-30 11:40', tz='UTC').timestamp())
    monkeypatch.setattr('time.time', lambda: stamp+1)
    def main():
        if raises: raise ValueError('calculation interrupted')
        return None  # core.account_exec returns here when the account is not ready.
    monkeypatch.setitem(sys.modules, 'core.account_exec', SimpleNamespace(main=main))
    monkeypatch.setattr(sys, 'argv', ['worker'])
    if raises:
        with pytest.raises(ValueError, match='calculation interrupted'):
            worker.trade(Path('a.py'), stamp)
        assert pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is False
    else:
        worker.trade(Path('a.py'), stamp)
        assert pd.read_pickle(tmp_path/'warmup.pkl') == meta


@pytest.mark.parametrize('initializing',[True,False])
def test_spawn_keeps_profile_paths_and_unicode_output(tmp_path,initializing):
    root = Path(__file__).resolve().parents[1]
    account = root/'examples/model_robust40/MODEL_ROBUST40_V1_40m.py'
    rules = tmp_path/'frozen_market/exginfo/market_rules.pkl'
    rules.parent.mkdir(parents=True)
    pd.to_pickle(dict(market_info={'spot': {}, 'swap': {}}, bstocks=[]), rules)
    # Portable public machine settings: never depend on a Windows D: producer
    # or the developer's ignored config.json when workers change PROJECT_ROOT.
    producer=tmp_path/'producer'
    (producer/'preprocess_1h_resample').mkdir(parents=True)
    (producer/'exginfo').mkdir()
    (tmp_path/'config.json').write_text(json.dumps({'realtime_data_path':str(producer)}),encoding='utf8')
    probe = tmp_path/'spawn_probe.py'
    probe.write_text('''from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
import os
import sys
from tools.robust40.cli import server_robust40
def check():
    import config
    from core.utils.path_kit import get_file_path
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.utils.log_kit import logger
    workspace = Path(__file__).resolve().parent
    mef = MultiEquityBacktestConfig()
    assert len(mef.factory.config_list) == 14
    import json
    initializing=json.loads(os.environ['ROBUST40_SERVER_WORKER'])['initializing']
    assert config.input_mode == ('full_pkl' if initializing else 'arrow_strict') and config.section_state
    assert config.checkpoint_hours == 25 and config.incremental_lookback_hours == 0
    # Both profiles must reach factor imports in every Windows-spawned process.
    # A CPU-sized live pool previously spawned 20 workers and exhausted memory.
    from core import select_coin
    from tools.robust40.initialization_config import MODEL_JOB_NUM
    assert config.job_num == MODEL_JOB_NUM
    assert select_coin.job_num == config.job_num
    assert all(c.checkpoint_hours == 25 and c.incremental_lookback_hours == 0 for c in mef.factory.config_list)
    from core.utils import datatools,exchange_hub
    if initializing:
        assert datatools.get_dummy_client().get_market_info('spot') == {}
    else:
        from tools.robust40.market_inputs import public_market_client
        assert datatools.get_dummy_client is public_market_client
        cls=exchange_hub.get_base_client_class()
        cls.market_info={'spot':{'symbol_list':['ACCOUNT_ONLY']}}
        assert datatools.get_dummy_client().market_info == {}
        assert cls.market_info['spot']['symbol_list']==['ACCOUNT_ONLY']
    for path in (config.snapshot_path, config.runtime_folder, mef.factory.result_folder,
                 get_file_path('logs', 'probe.log')):
        assert Path(path).is_relative_to(workspace), path
    if initializing:assert config.bmac_data_path == workspace/'frozen_market'
    logger.debug('ℹ️ spawn初始化日志正常')
    print('子进程stderr正常', file=sys.stderr, flush=True)
    return 'SPAWN_OK'
if __name__ == '__main__':
    assert check() == 'SPAWN_OK'
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
        print(pool.submit(check).result(timeout=45), flush=True)
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING='gbk',
               ROBUST40_SERVER_WORKER=json.dumps(dict(account=str(account), root=str(tmp_path),
                    origin=str(server.configured_s3(server.settings(account)[0],40)[0]), initializing=initializing)))
    env.pop('ROBUST40_WARMUP_WORKER', None)
    result = subprocess.run([sys.executable, str(probe)], cwd=root, env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert 'SPAWN_OK' in result.stdout.decode('utf-8')
    assert '子进程stderr正常' in result.stderr.decode('utf-8')
