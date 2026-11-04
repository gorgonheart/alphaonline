"""Dry-run must not change exchange settings or buy/transfer account assets."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def function(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('mode', ['dry_run', 'none'])
def test_startup_account_settings_are_live_only(mode):
    account = Mock()
    namespace = dict(is_simulate=mode, refresh_diff_time=Mock(),
                     load_multi_accounts=lambda: [Path('test.py')],
                     init_system=Mock(return_value=(account, None)), logger=Mock(),
                     time=SimpleNamespace(sleep=lambda _: None))
    function('startup.py', 'check_accounts', namespace)()
    for method in ['reset_max_leverage', 'set_single_side_position', 'set_multi_assets_margin']:
        assert getattr(account.bn, method).call_count == (mode == 'none')


@pytest.mark.parametrize('mode', ['dry_run', 'none'])
def test_account_worker_refresh_is_read_only_in_dry_run(mode):
    account = Mock()
    account.update_account_info.return_value = True
    run = Mock()
    namespace = dict(is_simulate=mode, is_debug=False, datetime=datetime,
                     sys=SimpleNamespace(argv=['account_exec.py', '1793721900']),
                     init_system=lambda: (account, object()), logger=Mock(), divider=Mock(),
                     run_by_account=run)
    assert function('core/account_exec.py', 'main', namespace)() is not None
    assert account.bn.reset_max_leverage.call_count == (mode == 'none')
    account.update_account_info.assert_called_once_with(is_operate=(mode == 'none'))
    run.assert_called_once()


@pytest.mark.parametrize('operate', [False, True])
def test_read_only_account_refresh_does_not_convert_dust(operate):
    from core.model.account_config import AccountConfig
    from core.model.account_type import AccountType
    client = Mock()
    client.get_account_overview.return_value = dict(
        swap_assets=dict(equity=100., swap_position_df=pd.DataFrame()),
        spot_assets=dict(equity=100., usdt=0., dust_spot_df=pd.DataFrame(), spot_position_df=pd.DataFrame()))
    account = SimpleNamespace(bn=client, use_spot=True, if_transfer_bnb=True,
                              account_type=AccountType.PORTFOLIO_MARGIN, is_pure_long=False,
                              buy_bnb_value=11, if_use_bnb_burn=False)
    assert AccountConfig.update_account_info(account, is_operate=operate)
    assert client.transfer_bnb_for_dust_spot.call_count == int(operate)
    assert client.collect_asset.call_count == int(operate)
    client.replenish_bnb.assert_not_called()
