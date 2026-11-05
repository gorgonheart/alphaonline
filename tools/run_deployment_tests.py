"""Run offline deployment checks in a disposable root/test directory.

Only the verification command uses this tool. Runtime imports and configuration
never refer to test/. Source comes from the current committed tree, including
tests omitted by a production sparse checkout. No machine secrets are copied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def prepare_run(root: Path) -> Path:
    root = root.resolve()
    directory = root / 'test'
    if directory.is_symlink():
        raise ValueError('test must be a real directory inside this installation')
    directory.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='verify-', dir=directory))
    source = run / 'source'
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', str(root), str(source)],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--quiet', '--detach', revision],
                   check=True, capture_output=True)
    # All market/data paths are private scratch; no production JSON is copied.
    market=run/'data'
    (market/'preprocess_1h_resample').mkdir(parents=True)
    (market/'exginfo').mkdir()
    settings=dict(realtime_data_path=str(market),proxy={},is_simulate='dry_run',
                  error_webhook_url='',shared_factor_pipeline=False)
    config = source / 'config.json'
    config.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    config.chmod(0o600)
    return run


def run_tests(root: Path, tests: list[str]) -> int:
    run = prepare_run(root)
    source = run / 'source'
    scratch = run / 'tmp'
    scratch.mkdir()
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPYCACHEPREFIX=str(run / 'cache/pycache'), NUMBA_CACHE_DIR=str(run / 'cache/numba'),
               TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
    for name in ('X3S_TRADING_ACCOUNT','ROBUST40_SERVER_WORKER','ROBUST40_WARMUP_WORKER'):
        env.pop(name,None)
    env.update(X3S_USE_FILE_LOGGING='0',MPLBACKEND='Agg')
    log = run / 'verify.log'
    command = [sys.executable, '-X', 'utf8', '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp=' + str(run / 'pytest'), *tests]
    print(f'Offline verification directory: {run}', flush=True)
    with log.open('wb') as output:
        result = subprocess.run(command, cwd=source, env=env, stdout=output, stderr=subprocess.STDOUT)
    print(log.read_text(encoding='utf-8', errors='replace'), end='')
    print(f'Log retained for manual cleanup: {log}', flush=True)
    return result.returncode


if __name__ == '__main__':
    if not sys.argv[1:]:
        raise SystemExit('Pass offline pytest file paths, or use tools/deploy_alphatrade.sh verify')
    raise SystemExit(run_tests(Path(__file__).resolve().parents[1], sys.argv[1:]))
