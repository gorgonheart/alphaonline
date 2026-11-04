"""Use the user-specified legacy account as a real framework compatibility case."""
from pathlib import Path
import runpy
from types import SimpleNamespace


def test_user_45m_pool_loads_without_model_hooks_or_model_state():
    from core.model.backtest_config import BacktestConfig
    from core.model.strategy_config import PosStrategyConfig
    from core.backtest import supports_factor_blocks
    from tools.robust40.server import enabled
    # Public fixture preserves the user-specified strategy; no private keys required.
    path=Path(__file__).resolve().parents[1]/'tests/fixtures/legacy_accounts/45m_gonheart1020.py'
    values=runpy.run_path(str(path))
    assert values['account_config']['hour_offset']=='45m'
    parent=PosStrategyConfig(**values['strategy_config']);parent.load()
    assert parent.name=='HybridRotationDriftStrategy'
    assert not enabled(path)
    assert not values['strategy_config']['params'].get('enable_live_hooks',False)
    children=[]
    for item in values['strategy_pool']:
        conf=BacktestConfig('traditional-speed-contract')
        conf.load_strategy_config(item['strategy_list'],item.get('re_timing'))
        children.append(conf)
    assert len(children)==14
    assert supports_factor_blocks(SimpleNamespace(config_list=children))
