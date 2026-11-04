"""Configuration identity follows active inputs, not obsolete research metadata."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


def config():
    return dict(strategy_config=dict(name='ModelRobust40Strategy', params=dict(
        parent=dict(cap_ratios=[.2], rebalance_anchor_time='2021-01-01'),
        runtime=dict(server_init=True))), account=dict(hour_offset='40m'))


def test_unused_parent_fields_do_not_change_calculation_identity():
    from positions.ModelRobust40Strategy import effective_parent_params
    parent=config()['strategy_config']['params']['parent']
    old=dict(parent, method='native', gross_cap=2.5, risk_definitions=['down','up'],
        momentum_60=True, short_single_coin_max_ratio=.05,
        research_inputs='E:/absent', child_returns='E:/absent.pkl', conditional_risk_path='E:/absent2.pkl')
    assert effective_parent_params(old)==parent
    assert 'research_inputs' in old  # Input/state dictionaries must not be mutated.
    for key,value in [('cap_ratios',[.3]),('rebalance_anchor_time','2022-01-01'),('new_option',1)]:
        assert effective_parent_params(dict(old,**{key:value}))!=parent


def test_server_history_requirement_is_not_a_calculation_input():
    from tools.robust40.warmup import effective_settings
    values = config()
    expected = effective_settings(values)
    for date in (None, '2025-10-28 00:40:00', '2026-01-01'):
        supplied = dict(values, required_history_start=date)
        assert effective_settings(supplied) == expected
        assert supplied['required_history_start'] == date
    assert effective_settings(dict(values, get_kline_num=5000)) != expected


@pytest.mark.parametrize('mode', ['manual', 'unset', 'traditional'])
def test_other_paths_keep_history_requirement(mode):
    from tools.robust40.warmup import effective_settings
    values = config()
    runtime = values['strategy_config']['params']['runtime']
    if mode == 'manual':runtime['server_init'] = False
    elif mode == 'unset':runtime.clear()
    else:values['strategy_config']['name'] = 'HybridRotationDriftStrategy'
    dated = dict(values, required_history_start='2025-10-28 00:40:00')
    assert effective_settings(dated) != effective_settings(values)
    assert effective_settings(dated)['required_history_start'] == dated['required_history_start']


@pytest.mark.parametrize('minute', [20, 40, 55])
def test_server_accounts_do_not_maintain_unused_history_date(minute):
    import ast
    path = Path(__file__).resolve().parents[1] / 'accounts' / f'{minute}m_robust_4h.py'
    if not path.exists():
        path = Path(__file__).resolve().parents[1]/'tests/fixtures/model_accounts'/path.name
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    assert not any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'required_history_start' for t in n.targets
    ) for n in tree.body)


def test_old_metadata_compares_by_effective_settings_without_state_read(tmp_path,monkeypatch):
    from tools.robust40 import server
    now=config();old=copy.deepcopy(now)
    old['strategy_config']['params']['parent']['research_inputs']='E:/not-present'
    old['strategy_config']['params']['runtime']['minute']=40
    old['required_history_start']='2025-10-28 00:40:00'
    server.save_meta(tmp_path,dict(account='acct',settings=old,complete=False))
    read=pd.read_pickle;calls=[]
    def only_meta(path):
        assert Path(path).name=='warmup.pkl'
        calls.append(path);return read(path)
    monkeypatch.setattr(pd,'read_pickle',only_meta)
    assert server.inspect(tmp_path,'acct',now,'2026-10-31 00:40')==('rebuild','incomplete calculation')
    assert len(calls)==1
    now['strategy_config']['params']['parent']['cap_ratios']=[.3]
    assert server.inspect(tmp_path,'acct',now,'2026-10-31 00:40')==('rebuild','configuration changed')


@pytest.mark.parametrize('minute',range(0,60,5))
def test_minute_comes_only_from_account_hour_offset(tmp_path,minute):
    from tools.robust40.warmup import settings
    path=tmp_path/'account.py'
    values=config();values['account']['hour_offset']=f'{minute}m'
    path.write_text('account_config = '+repr(values['account'])+'\nstrategy_config = '+repr(values['strategy_config']),encoding='utf8')
    saved,options=settings(path)
    assert options['minute']==minute
    assert 'minute' not in saved['strategy_config']['params']['runtime']
    assert saved['account']['hour_offset']==f'{minute}m'


def test_false_retains_ordinary_dispatch_not_pause_or_force_retrain(tmp_path):
    from tools.robust40 import server
    values=config();values['strategy_config']['params']['runtime']['server_init']=False
    path=tmp_path/'account.py'
    path.write_text('account_config = '+repr(values['account'])+'\nstrategy_config = '+repr(values['strategy_config']),encoding='utf8')
    cmd,env=server.Coordinator(tmp_path).command(path,pd.Timestamp('2026-10-31 00:40'),tmp_path)
    assert Path(cmd[1]).name=='account_exec.py' and env is None


def test_worker_profile_uses_account_offset_without_runtime_minute(tmp_path):
    from tools.robust40.cli.server_robust40 import apply_profile
    c=SimpleNamespace(strategy_pool=[dict(filter_list=[
        ('VolumeMaxBias_进出场阈值分离官方',(720,.1,.9,'2026-08-01'),'val:==1')])],
        account_config={'hour_offset':'40m'},incremental_lookback_hours=0,data_config={},
        strategy_config={'params':{'runtime':{'server_init':True}}})
    apply_profile(c,'2026-08-01 00:40',tmp_path,True)
    assert c.strategy_pool[0]['filter_list'][0][1][3]=='20260801T004000'
    assert 'minute' not in c.strategy_config['params']['runtime']


@pytest.mark.parametrize('saved',[None, [], 'invalid', {'strategy_config':None}])
def test_malformed_saved_settings_request_rebuild(tmp_path,saved):
    from tools.robust40 import server
    server.save_meta(tmp_path,dict(account='acct',settings=saved,complete=False))
    assert server.inspect(tmp_path,'acct',config(),'2026-10-31 00:40')[0]=='rebuild'
