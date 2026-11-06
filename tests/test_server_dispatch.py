from datetime import datetime
from pathlib import Path
import json
import pandas as pd
import pytest
from tools.robust40 import server


@pytest.fixture(autouse=True)
def manual_pending(monkeypatch):
    monkeypatch.setattr(server.Coordinator,'_ensure_pending_thread',lambda *a:None)


def full_headers(folder,stamp):
    import pyarrow as pa
    for kind in ('spot','swap'):
        table=pa.table({'close':[1.]}).replace_schema_metadata({b'full_history':b'1'})
        with pa.OSFile(str(folder/f'{kind}_dict_{stamp}.arrow'),'wb') as sink:
            with pa.ipc.new_file(sink,table.schema) as writer:writer.write_table(table)


@pytest.mark.parametrize('pivot_arrives', [True, False])
def test_dispatch_waits_for_same_round_pivot_within_execution_window(tmp_path, monkeypatch, pivot_arrives):
    import subprocess
    import time
    monkeypatch.setattr(server.Coordinator, '_monitor', lambda *a: None)
    account = tmp_path/'a.py'; account.write_text('')
    run = datetime(2026, 10, 30, 19, 55)
    stamp = int(run.timestamp())
    folder = tmp_path/'market/55m'; folder.mkdir(parents=True)
    full_headers(folder,stamp)
    missing = folder/f'market_pivot_swap_{stamp}.ready'
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_'):
        (folder/f'{prefix}{stamp}.ready').touch()
    # Older pivots must not satisfy this publication's readiness check.
    (folder/f'market_pivot_swap_{stamp-3600}.ready').touch()
    values = dict(strategy_name='s', account=dict(hour_offset='55m'), strategy_pool=[])
    monkeypatch.setattr(server, 'enabled', lambda a: True)
    monkeypatch.setattr(server, 'settings', lambda a: (values, dict(minute=55)))
    clock = [stamp+136.]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    def advance(seconds):
        clock[0] += seconds
        if pivot_arrives and clock[0] >= stamp+138:
            missing.touch()
    monkeypatch.setattr(time, 'sleep', advance)
    messages = []
    from core.utils.log_kit import logger
    monkeypatch.setattr(logger, 'info', messages.append)
    calls = []
    class Process:
        def poll(self): return None
    def launch(*args, **kwargs):
        assert missing.exists(), 'Initialization started before the matching pivot'
        calls.append(args)
        return Process()
    monkeypatch.setattr(subprocess, 'Popen', launch)
    c = server.Coordinator(tmp_path)
    try:
        assert c.command(account, run, tmp_path/'market') == (None, None)
        assert any(missing.name in m for m in messages)
        assert len(calls) == int(pivot_arrives)
        if pivot_arrives:
            assert stamp+138 <= clock[0] < stamp+240
        else:
            assert clock[0] == stamp+240
            assert any('超时' in m for m in messages)
    finally:
        for _, handle in c.jobs.values(): handle.close()


@pytest.mark.parametrize('minute', [20, 40, 55])
def test_active_accounts_route_to_server_initialization_without_old_package(minute, monkeypatch):
    from tools.robust40.warmup import prepare_startup
    root = Path(__file__).resolve().parents[1]
    account = root/'accounts'/f'{minute}m_robust_4h.py'
    if not account.exists():
        account = Path(__file__).resolve().parents[1]/'tests/fixtures/model_accounts'/account.name
    values, options = server.settings(account)
    assert server.enabled(account)
    assert options == dict(minute=minute, server_init=True)
    assert values['account']['hour_offset'] == f'{minute}m'
    assert server.location(root, account, values).name == account.stem+'_'+values['strategy_name']
    # Startup must not read the incompatible historical warmup package at all.
    monkeypatch.setattr(pd, 'read_pickle', lambda *a, **k: pytest.fail('Old warmup package accessed'))
    assert prepare_startup([account], root, clean_start=True) == []


def test_trade_cannot_bypass_isolated_profile(monkeypatch,tmp_path):
    from tools.robust40.cli import server_robust40 as worker
    monkeypatch.delenv('ROBUST40_SERVER_WORKER',raising=False)
    monkeypatch.setattr('sys.argv',['worker','trade','--account',str(tmp_path/'account.py'),'--stamp','1'])
    monkeypatch.setattr(worker,'trade',lambda *a:pytest.fail('Trading entry was reached'))
    with pytest.raises(ValueError,match='profile'):worker.main()


def test_ready_discovery_ignores_producer_batch_flags(tmp_path):
    from tools.robust40.market_inputs import latest_ready
    stamp=int(pd.Timestamp('2026-10-30 00:20',tz='UTC').timestamp())
    for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
        (tmp_path/f'{prefix}{stamp}.ready').touch()
        (tmp_path/f'{prefix}batch1_{stamp}.ready').touch()
    assert latest_ready(tmp_path,20,stamp+1)[0]==stamp


def test_legacy_dispatch_and_cleanup_preserve_only_opted_accounts(tmp_path,monkeypatch):
    old=tmp_path/'old.py';old.write_text('x=1')
    new=tmp_path/'new.py';new.write_text('x=1')
    values=dict(strategy_name='s')
    monkeypatch.setattr(server,'enabled',lambda a:Path(a)==new)
    monkeypatch.setattr(server,'settings',lambda a:(values,{}))
    snapshot=tmp_path/'data/snapshot'
    (snapshot/'new_s').mkdir(parents=True);(snapshot/'old_s').mkdir()
    (snapshot/'new_s/state').write_text('keep')
    server.clean_startup([old,new],tmp_path,snapshot)
    assert (snapshot/'new_s/state').read_text()=='keep'
    assert not (snapshot/'old_s').exists()
    command,env=server.Coordinator(tmp_path).command(old,datetime(2026,1,1),tmp_path)
    assert command[1].endswith('account_exec.py') and env is None


def test_one_background_initialization_and_deduplicate(tmp_path,monkeypatch):
    import subprocess,time
    monkeypatch.setattr(server.Coordinator, '_monitor', lambda *a: None)
    account=tmp_path/'a.py';account.write_text('')
    now=datetime.fromtimestamp(time.time()).replace(minute=20,second=0,microsecond=0)
    stamp=int(now.timestamp());folder=tmp_path/'market/20m';folder.mkdir(parents=True)
    full_headers(folder,stamp)
    for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
        (folder/f'{prefix}{stamp}.ready').touch()
    values=dict(strategy_name='s',account=dict(hour_offset='20m'),strategy_pool=[])
    monkeypatch.setattr(server,'enabled',lambda a:True)
    monkeypatch.setattr(server,'settings',lambda a:(values,dict(minute=20)))
    monkeypatch.setattr(time,'time',lambda:stamp+1)
    calls=[]
    class Process:
        def poll(self):return 1
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:calls.append(a) or Process())
    c=server.Coordinator(tmp_path)
    assert c.command(account,now,tmp_path/'market')==(None,None)
    assert c.command(account,now,tmp_path/'market')==(None,None)
    assert len(calls)==1


def test_profile_changes_only_execution_and_explicit_origin(tmp_path):
    from types import SimpleNamespace
    from tools.robust40.cli.server_robust40 import apply_profile
    config=SimpleNamespace(strategy_pool=[dict(filter_list=[('VolumeMaxBias_进出场阈值分离官方',(720,.1,.9,'2026-08-01'),'val:==1')])],
        incremental_lookback_hours=0,data_config={},strategy_config={'params':{'runtime':{}}},
        black_list=['X'],simulator_config={'fee':.001},account_config={'buy_bnb_value':11,'hour_offset':'20m'})
    apply_profile(config,'2026-08-01 00:20',tmp_path,True)
    assert config.input_mode=='arrow_full_strict' and config.section_state
    assert config.strategy_pool[0]['filter_list'][0][1]==(720,.1,.9,'20260801T002000')
    assert config.black_list==['X'] and config.simulator_config=={'fee':.001}
    assert config.account_config['buy_bnb_value']==11
