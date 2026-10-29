"""Offline regression using the five named accounts, without loading credentials."""
import ast
import importlib
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ['5m-onheart0607.py', '15m-nheart0701.py', '20m_heart0723.py',
            '25m-eart0607.py', '45m_gonheart0607.py']


def public_assignment(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
    # Only strategy definitions and explicitly allowed account fields are read.
    if name == 'account_config':
        allowed = {'hour_offset', 'buy_bnb_value', 'leverage', 'rebalance_mode',
                   'order_spot_money_limit', 'order_swap_money_limit'}
        return {k.value: ast.literal_eval(v) for k, v in zip(node.value.keys, node.value.values)
                if isinstance(k, ast.Constant) and k.value in allowed}
    assert name in {'strategy_config', 'strategy_pool'}
    allowed = {'dict': dict, 'range': range, 'list': list, 'zip': zip}
    for n in ast.walk(node.value):
        if isinstance(n, ast.Call):assert isinstance(n.func, ast.Name) and n.func.id in allowed
    return eval(compile(ast.Expression(node.value), str(path), 'eval'), {'__builtins__': {}, **allowed})


def baseline_source(name):
    return subprocess.run(['git', 'show', 'main:'+name], cwd=ROOT, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode('utf-8-sig')


def test_plain_framework_import_has_no_model_dependency():
    script = """
import sys
import core.position, core.real_trading, core.rebalance
assert not any(n.startswith('tools.robust40') for n in sys.modules)
assert not hasattr(core.rebalance, 'RebRobust40')
"""
    subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT, check=True, capture_output=True)


def test_reload_resets_opt_in_history_and_simulator_settings(tmp_path):
    opted = tmp_path/'opted.py'
    legacy = tmp_path/'legacy.py'
    opted.write_text("required_history_start = '2025-10-28 00:20'\n"
                     "simulator_config = dict(spot_min_order_limit=10, swap_min_order_limit=5)\n",
                     encoding='utf-8')
    legacy.write_text("strategy_name = 'legacy'\n", encoding='utf-8')
    script = """
import importlib, os, sys
os.environ['X3S_TRADING_ACCOUNT'] = sys.argv[1]
import config
assert config.required_history_start == '2025-10-28 00:20'
assert config.simulator_config['swap_min_order_limit'] == 5
os.environ['X3S_TRADING_ACCOUNT'] = sys.argv[2]
importlib.reload(config)
assert config.required_history_start is None
assert config.simulator_config['spot_min_order_limit'] == 20
assert config.simulator_config['swap_min_order_limit'] == 20
print('reload isolation passed')
"""
    result = subprocess.run([sys.executable, '-B', '-c', script, str(opted), str(legacy)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'reload isolation passed' in result.stdout


@pytest.mark.parametrize('minute', [20, 40, 55])
def test_model_examples_explicitly_select_private_extensions(minute):
    from core.model.strategy_config import PosStrategyConfig
    from core.model.rebalance_mode import RebalanceMode
    path = ROOT/'examples/model_robust40'/f'MODEL_ROBUST40_V1_{minute}m.py'
    strategy = public_assignment(path, 'strategy_config')
    parent = PosStrategyConfig(**strategy);parent.load()
    assert parent.params['enable_live_hooks'] is True
    account = public_assignment(path, 'account_config')
    execution = RebalanceMode.init(account['rebalance_mode']).create(np.ones(1),np.ones(1))
    assert execution.use_full_equity is True
    pool = public_assignment(path, 'strategy_pool')
    names = {f[0] for p in pool for c in p['strategy_list'] for k,v in c.items()
             if 'factor_list' in k or 'filter_list' in k for f in v}
    assert {'TopKTo2KRobust40','VolumeMaxBiasRobust40'} <= names
    assert not {'TopKTo2K','VolumeMaxBias_进出场阈值分离官方'} & names


@pytest.mark.parametrize('name', ACCOUNTS)
def test_real_account_strategy_loading_preserves_features(name):
    from core.model.strategy_config import PosStrategyConfig
    from core.model.backtest_config import BacktestConfig
    source = ROOT/'accounts'/name
    strategy = public_assignment(source, 'strategy_config')
    account = public_assignment(source, 'account_config')
    pool = public_assignment(source, 'strategy_pool')
    parent = PosStrategyConfig(**strategy);parent.load()
    assert parent.name == 'HybridRotationDriftStrategy'
    assert not parent.params.get('enable_live_hooks', False)
    assert 'prepare_live' not in parent.core_funcs and 'transform_targets' not in parent.core_funcs
    assert account['hour_offset'] == name.split('m')[0]+'m'
    assert len(pool) == 14
    for i, spec in enumerate(pool):
        conf = BacktestConfig(f'isolated-S{i+1}')
        conf.load_strategy_config(spec['strategy_list'], spec.get('re_timing'))
        assert conf.required_history_start is None
        assert conf.rebalance_mode.mode == 'RebAlways'
        assert conf.strategy_list
    assert account['order_spot_money_limit'] == 100
    assert account['order_swap_money_limit'] == (10 if name.startswith(('5m','45m')) else 50)


@pytest.mark.parametrize('name', ACCOUNTS)
@pytest.mark.parametrize('mode', ['RebAlways', 'RebByEquityRatio', 'RebByPositionRatio'])
@pytest.mark.parametrize('swap_net', [-.2, 0., .2])
def test_legacy_target_lots_match_main_even_after_model_registration(name, mode, swap_net, tmp_path, monkeypatch):
    import core.position as current
    from core.model.rebalance_mode import RebalanceMode
    from tools.robust40.live_execution import register_execution
    register_execution()
    old = types.ModuleType('baseline_position')
    exec(compile(baseline_source('core/position.py'), '<baseline position>', 'exec'), old.__dict__)
    settings = public_assignment(ROOT/'accounts'/name, 'account_config')
    idx = pd.date_range('2026-10-01 00:'+settings['hour_offset'][:-1].zfill(2), periods=1)
    # Include an inactive instrument and an existing position absent from targets.
    pd.DataFrame({'S': [.2]}, index=idx).to_pickle(tmp_path/'df_spot_ratio.pkl')
    pd.DataFrame({'U': [swap_net], 'UNTRADEABLE': [.4]}, index=idx).to_pickle(tmp_path/'df_swap_ratio.pkl')
    prices = pd.Series({'S': 101., 'U': 53., 'HELD': 7.})
    info = dict(symbol_list=['S','U','HELD'], min_qty={'S': 3, 'U': 2, 'HELD': 1})
    bn = SimpleNamespace(market_info={'spot': info, 'swap': info},
                         get_spot_ticker_price_series=lambda: prices,
                         get_swap_ticker_price_series=lambda: prices)
    params = {} if mode == 'RebAlways' else {'min_order_usdt_ratio': .2}
    acct = SimpleNamespace(bn=bn, spot_equity=200., swap_equity=800., leverage=1.,
        buy_bnb_value=settings.get('buy_bnb_value', 11),
        spot_position=pd.DataFrame({'当前持仓量': [1.]}, index=['S']),
        swap_position=pd.DataFrame({'当前持仓量': [-.2, 2.]}, index=['U','HELD']),
        rebalance_mode=RebalanceMode.init(dict(mode=mode,params=params)))
    monkeypatch.setattr(current, 'runtime_folder', tmp_path);old.runtime_folder = tmp_path
    pd.testing.assert_frame_equal(current.calc_target_position(acct, is_debug=True),
                                  old.calc_target_position(acct, is_debug=True), check_exact=True)


@pytest.mark.parametrize('path', ['core/model/account_config.py', 'core/model/strategy_config.py',
    'core/rebalance.py', 'sections/TopKTo2K.py', 'sections/VolumeMaxBias_进出场阈值分离官方.py',
    'positions/HybridRotationDriftStrategy.py'])
def test_shared_implementation_unchanged_from_main(path):
    assert ast.dump(ast.parse((ROOT/path).read_text(encoding='utf-8-sig'))) == ast.dump(ast.parse(baseline_source(path)))


def test_old_and_model_factor_boundaries_remain_independent():
    old = importlib.import_module('sections.TopKTo2K')
    new = importlib.import_module('sections.TopKTo2KRobust40')
    rows = []
    for t in range(2):
        for i in range(10):
            rank = i+1 if t == 0 else [2,1,3,4,5,6,7,8,9,10][i]
            rows.append(dict(candle_begin_time=pd.Timestamp('2026-01-01')+pd.Timedelta(hours=t),
                             symbol=str(i), is_spot=0, score_1=11-rank))
    df = pd.DataFrame(rows)
    assert old.signal(df.copy(), (2,'score',1), 'x').loc[10,'x'] == 1
    assert new.signal(df.copy(), (2,'score',1), 'x').loc[10,'x'] == 0


def test_existing_robust4h_draft_was_already_missing_its_strategy():
    draft = ROOT/'accounts/20m_robust_4h.py'
    if not draft.exists():pytest.skip('No local Robust4H draft')
    name = public_assignment(draft, 'strategy_config')['name']
    assert name == 'HybridRobust4HStrategy'
    assert not (ROOT/'positions'/(name+'.py')).exists()
    original = subprocess.run(['git','cat-file','-e','main:positions/'+name+'.py'],cwd=ROOT,capture_output=True)
    assert original.returncode != 0
