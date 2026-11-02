"""Accounts own parameters; one positions file owns the MODEL strategy."""
import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('minute', [20, 40, 55])
def test_account_owns_full_strategy_parameters(minute):
    path = ROOT/'accounts'/f'{minute}m_robust_4h.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'strategy_config')
    value = ast.literal_eval(node.value)
    assert len(value['params']['parent']['cap_ratios']) == 14
    assert 'minute' not in value['params']['runtime']
    assert not any(isinstance(n, ast.ImportFrom) and n.module == 'tools.robust40.config' for n in tree.body)


def test_strategy_implementation_is_one_positions_file():
    from positions import ModelRobust40Strategy as strategy
    for name in ('coin_features', 'quarterly_predictions', 'constrained_parent',
                 'calculate', 'advance', 'update_state', 'restore_model', 'build_context'):
        assert getattr(strategy, name).__module__ == 'positions.ModelRobust40Strategy'
    assert not (ROOT/'tools/robust40/config.py').exists()


@pytest.mark.parametrize('minute', range(0, 60, 5))
def test_account_expression_is_offline_readable_and_explicitly_discoverable(tmp_path, minute):
    from tools.robust40.offline import assignment
    from tools.robust40.server import enabled
    account = tmp_path/'public.py'
    account.write_text(
        f"account_config = {{'hour_offset': '{minute}m'}}\n"
        "strategy_config = {'name': 'ModelRobust40Strategy', 'hold_period': '1H',\n"
        f" 'params': {{'parent': {{'cap_ratios': [{minute + 0.5}]}}, 'runtime': "
        "{'server_init': True}}}\n", encoding='utf-8')
    value = assignment(account, 'strategy_config')
    assert value['params']['runtime'] == dict(server_init=True)
    assert value['params']['parent']['cap_ratios'] == [minute + .5]
    assert enabled(account)
    # Offline extraction must not execute the rest of an account file.
    account.write_text(account.read_text(encoding='utf-8')+"raise RuntimeError('account executed')\n", encoding='utf-8')
    assert assignment(account, 'strategy_config') == value


def test_cli_worker_and_freeze_helpers_resolve_project_root():
    from tools.robust40.cli import server_robust40, prewarm_robust40
    assert server_robust40.ROOT == ROOT
    assert prewarm_robust40.ROOT == ROOT
    source = (ROOT/'tools/robust40/server.py').read_text(encoding='utf-8')
    assert source.count("'tools/robust40/cli/server_robust40.py'") == 2


@pytest.mark.parametrize('script', ['server_robust40', 'prewarm_robust40', 'check_server_factor_parity', 'summarize_replay_logs'])
def test_cli_help_works_from_another_directory_without_trading(tmp_path, script):
    path = ROOT/'tools/robust40/cli'/f'{script}.py'
    env = dict(os.environ, X3S_USE_FILE_LOGGING='0', PYTHONDONTWRITEBYTECODE='1')
    env.pop('ROBUST40_SERVER_WORKER', None)
    env.pop('X3S_TRADING_ACCOUNT', None)
    proc = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(path), '--help'],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert 'usage:' in proc.stdout


def test_no_strategy_math_left_in_tools():
    for stem in ('features', 'parent', 'model', 'execution', 'rebalance', 'simulation',
                 'engine', 'runtime', 'live', 'live_execution'):
        assert not (ROOT/'tools/robust40'/f'{stem}.py').exists(), stem


def test_production_market_helpers_do_not_import_research_command():
    from tools.robust40.market_inputs import freeze_inputs, prepare_public_market
    assert freeze_inputs.__module__ == 'tools.robust40.market_inputs'
    assert prepare_public_market.__module__ == 'tools.robust40.market_inputs'
    source = (ROOT/'tools/robust40/cli/server_robust40.py').read_text(encoding='utf-8')
    assert 'prewarm_robust40' not in source
