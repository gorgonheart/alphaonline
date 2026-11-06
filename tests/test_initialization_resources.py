"""Resource scheduling only: no market calculations, services, or orders."""
import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def resource_module():
    from tools.robust40 import resources
    return resources


def ready_files(market, stamp):
    from datetime import datetime, timezone
    folder = market / f'{datetime.fromtimestamp(stamp, timezone.utc).minute}m'
    folder.mkdir(parents=True)
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_'):
        (folder / f'{prefix}{stamp}.ready').touch()


def test_window_requires_ready_no_live_worker_and_guard(tmp_path):
    r = resource_module()
    stamp = 1793577600  # Exact five-minute boundary.
    assert stamp % 300 == 0
    assert r.window_budget(stamp+80, tmp_path, False)[0] == 'busy'
    ready_files(tmp_path, stamp)
    assert r.window_budget(stamp+20, tmp_path, False) == ('busy', 2*1024**3)
    assert r.window_budget(stamp+80, tmp_path, False) == ('exclusive', int(4.5*1024**3))
    assert r.window_budget(stamp+80, tmp_path, True)[0] == 'busy'
    assert r.window_budget(stamp+270, tmp_path, False)[0] == 'busy'
    assert r.window_budget(stamp+301, tmp_path, False)[0] == 'busy'


def test_foreground_detection_covers_both_installations_not_initializers():
    r = resource_module()
    assert r.is_foreground(['python', '/srv/a/core/account_exec.py', '123'])
    assert r.is_foreground(['python', '/srv/b/tools/robust40/cli/server_robust40.py', 'trade'])
    assert not r.is_foreground(['python', '/srv/a/tools/robust40/cli/server_robust40.py', 'compute'])
    assert not r.is_foreground(['python', '/srv/a/startup.py'])


def test_linux_wrapper_groups_entire_initializer_without_shell(monkeypatch):
    r = resource_module()
    monkeypatch.setattr(r.sys, 'platform', 'linux')
    monkeypatch.setattr(r.os, 'getuid', lambda:1000, raising=False)
    monkeypatch.setattr(r.os, 'getgid', lambda:1000, raising=False)
    command, env, unit = r.initialization_command(['python', 'worker', 'prepare'], 123)
    assert command[:3] == ['sudo', '-n', 'systemd-run']
    assert '--uid=1000' in command and '--slice=user-1000.slice' in command
    assert '--property=MemoryHigh=4764729344' in command
    assert '--property=MemorySwapMax=12884901888' in command
    assert '--property=MemoryMax=5301600256' in command
    assert command[-3:] == ['python', 'worker', 'prepare']
    assert env['ROBUST40_RESOURCE_UNIT'] == unit
    assert unit.endswith('.service')


def test_gate_manual_path_unchanged_and_pressure_drives_admission(monkeypatch, tmp_path):
    r = resource_module()
    monkeypatch.setenv('ROBUST40_RESOURCE_UNIT', 'owned.service')
    monkeypatch.setattr(r.sys, 'platform', 'linux')
    group = tmp_path/'owned.scope'; group.mkdir()
    (group/'memory.high').write_text(str(2*1024**3))
    monkeypatch.setattr(r, 'current_scope', lambda: group)
    sleeps=[]
    reading=dict(system_available=100*1024**2,system_swap_free=12*1024**3,memory_full=0.,io_full=0.)
    monkeypatch.setattr(r,'pressure_snapshot',lambda:reading)
    def resume(seconds):
        sleeps.append(seconds)
        reading['system_available']=3*1024**3
    monkeypatch.setattr(r.time, 'sleep', resume)
    r.wait_for_window()
    assert len(sleeps) == 1
    monkeypatch.delenv('ROBUST40_RESOURCE_UNIT')
    (group/'memory.high').unlink()
    r.wait_for_window()  # Traditional/manual offline paths have no resource gate.


def test_bounded_iterator_runs_gate_before_submitting():
    from concurrent.futures import Future
    from tools.bounded_pool import iter_results
    events=[]
    class Pool:
        def submit(self, fn, value):
            events.append('submit'); f=Future(); f.set_result(fn(value)); return f
    result=list(iter_results(Pool(), lambda x:x, [(1,), (2,)], 1,
                             before_submit=lambda:events.append('gate')))
    assert result == [1,2]
    assert events[:4] == ['gate','submit','gate','submit']


def test_private_aggregation_worker_does_not_return_saved_matrices(monkeypatch):
    tree=ast.parse((ROOT/'core/backtest.py').read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_step5_worker')
    calls=[]
    ns=dict(step5_aggregate_select_results=lambda *a,**k:calls.append((a,k)) or ('huge','matrices'),
            _wait_for_compute_window=lambda:None)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'worker','exec'),ns)
    conf=SimpleNamespace(name='S1',strategy_list=[1])
    assert ns['_step5_worker'](conf, 'stamp', False) is None
    assert calls[0][1] == {'save_final_result':True}


def test_preflight_registers_one_lightweight_wait_before_market_ready(tmp_path, monkeypatch):
    from datetime import datetime
    from tools.robust40 import server
    a=tmp_path/'55.py'; a.write_text('')
    values={'strategy_name':'s','strategy_pool':[], 'account':{'hour_offset':'55m'}}
    monkeypatch.setattr(server,'enabled',lambda p:True)
    monkeypatch.setattr(server,'settings',lambda p:(values,{'minute':55}))
    c=server.Coordinator(tmp_path)
    launched=[]
    def launch(account, run, values, reason, mode, market):
        launched.append((account,mode)); c.jobs[account]=(SimpleNamespace(poll=lambda:None),SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(c,'_launch',launch)
    monkeypatch.setattr(c,'_ensure_pending_thread',lambda:None)
    c.prepare([a],datetime(2026,11,2,10,55),tmp_path/'absent_market')
    c.prepare([a],datetime(2026,11,2,10,55),tmp_path/'absent_market')
    assert launched==[] and list(c.pending)==[a.resolve()]
    assert not (tmp_path/'data/server_initialization').exists()


def test_preflight_ignores_legacy_and_recovers_other_offsets(tmp_path,monkeypatch):
    from datetime import datetime
    from tools.robust40 import server
    old=tmp_path/'old.py'; old.write_text('')
    other=tmp_path/'20.py'; other.write_text('')
    monkeypatch.setattr(server,'enabled',lambda p:p==other)
    monkeypatch.setattr(server,'settings',lambda p:({'account':{'hour_offset':'20m'},'strategy_name':'s'},{'minute':20}))
    c=server.Coordinator(tmp_path)
    monkeypatch.setattr(c,'_launch',lambda *a:pytest.fail('Unexpected initialization'))
    monkeypatch.setattr(c,'_ensure_pending_thread',lambda:None)
    c.prepare([old,other],datetime(2026,11,2,10,55),tmp_path/'market')
    assert old not in c.pending and other in c.pending


def test_priority_set_before_profile_import_and_not_for_test_import(monkeypatch):
    r=resource_module()
    monkeypatch.setattr(r.sys,'platform','linux')
    calls=[]
    monkeypatch.setattr(r.os,'setpriority',lambda *a:calls.append(a),raising=False)
    monkeypatch.setattr(r.os,'PRIO_PROCESS',0,raising=False)
    monkeypatch.setattr(r.os,'getpriority',lambda *a:0,raising=False)
    r.configure_process('prepare')
    assert calls[-1] == (0,0,10)
    r.configure_process('trade')
    assert calls[-1] == (0,0,3)
    r.configure_process('pytest')
    assert len(calls)==2


@pytest.mark.parametrize('outcome',['ready','timeout','changed','already_finished'])
def test_prepare_reuses_dependency_config_and_never_reads_before_ready(tmp_path,monkeypatch,outcome):
    from tools.robust40.cli import server_robust40 as worker
    from tools.robust40 import server, handoff
    import core.model.backtest_config as backtests
    import config
    stamp=1793577600+55*60
    account=tmp_path/'a.py';account.write_text('')
    market=tmp_path/'market';folder=market/'55m';folder.mkdir(parents=True)
    workspace=tmp_path/'workspace'
    destination=tmp_path/'snapshot/account'
    monkeypatch.setattr(config,'bmac_data_path',market)
    monkeypatch.setenv('X3S_TRADING_ACCOUNT',str(account))
    calls=[]
    values={'strategy_pool':[]}
    def settings(*args):
        calls.append('settings')
        return (dict(values,changed=True) if outcome=='changed' and calls.count('settings')>1 else values),{'minute':55}
    monkeypatch.setattr(server,'settings',settings)
    prepared=object()
    monkeypatch.setattr(backtests,'MultiEquityBacktestConfig',lambda:SimpleNamespace(
        factory=SimpleNamespace(generate_all_factor_config=lambda:prepared)))
    clock=[stamp+1]
    monkeypatch.setattr(time,'time',lambda:clock[0])
    def tick(seconds):
        clock[0]+=seconds
        assert not workspace.exists()
        if outcome!='timeout':
            for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
                (folder/f'{prefix}{stamp}.ready').touch()
    monkeypatch.setattr(time,'sleep',tick)
    monkeypatch.setattr(handoff,'recover_interrupted',lambda *a:None)
    monkeypatch.setattr(server,'inspect',lambda *a:('ready' if outcome=='already_finished' else 'rebuild','reason'))
    launched=[]
    def initialize(*args,**kwargs):
        assert all((folder/f'{p}{stamp}.ready').exists() for p in
                   ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'))
        assert kwargs['prepared_conf'] is prepared
        launched.append(args)
    monkeypatch.setattr(worker,'initialize',initialize)
    if outcome=='changed':
        with pytest.raises(ValueError,match='Configuration changed'):
            worker.prepare(account,workspace,stamp,destination)
    else:
        assert worker.prepare(account,workspace,stamp,destination)==(outcome=='ready')
    assert len(launched)==int(outcome=='ready')
    assert not workspace.exists()


def test_preflight_busy_snapshot_does_not_start_duplicate(tmp_path,monkeypatch):
    from datetime import datetime
    from tools.robust40 import server
    account=tmp_path/'a.py';account.write_text('')
    values={'strategy_name':'s','account':{'hour_offset':'55m'}}
    monkeypatch.setattr(server,'enabled',lambda a:True)
    monkeypatch.setattr(server,'settings',lambda a:(values,{'minute':55}))
    c=server.Coordinator(tmp_path)
    monkeypatch.setattr(c,'_launch',lambda *a:pytest.fail('Busy owner must not be initialized'))
    with server.account_lock(server.location(tmp_path,account,values)):
        c.prepare([account],datetime(2026,11,2,10,55),tmp_path/'market')


def test_startup_preflight_is_before_producer_wait():
    tree=ast.parse((ROOT/'startup.py').read_text(encoding='utf-8-sig'))
    run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_loop')
    calls=[n for n in ast.walk(run) if isinstance(n,ast.Call)]
    prepare=next(n for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr=='prepare')
    ready=next(n for n in calls if isinstance(n.func,ast.Name) and n.func.id=='check_bmac_update_flag')
    assert prepare.lineno < ready.lineno


def test_failed_aggregation_still_reaches_parent():
    tree=ast.parse((ROOT/'core/backtest.py').read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_step5_worker')
    def failed(*a,**k):raise OSError('disk full')
    ns=dict(step5_aggregate_select_results=failed,_wait_for_compute_window=lambda:None)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'worker','exec'),ns)
    with pytest.raises(OSError,match='disk full'):
        ns['_step5_worker'](SimpleNamespace(name='S1',strategy_list=[]),'stamp',False)


@pytest.mark.parametrize('fail',[False,True])
def test_controller_changes_only_owned_unit_and_stops_on_control_failure(tmp_path,monkeypatch,fail):
    r=resource_module()
    group=tmp_path/'owned.service';group.mkdir()
    (group/'memory.current').write_text('100')
    monkeypatch.setattr(r,'CGROUP_ROOT',tmp_path)
    turns=[0]; calls=[]; logs=[]
    process=SimpleNamespace(poll=lambda:None if turns[0]<3 else 0)
    def pressure():
        if fail:raise OSError('pressure observer unavailable')
        return dict(system_available=3*1024**3,system_swap_free=10*1024**3,memory_full=0.,io_full=0.)
    monkeypatch.setattr(r,'pressure_snapshot',pressure)
    monkeypatch.setattr(r.time,'sleep',lambda seconds:turns.__setitem__(0,turns[0]+1))
    def ctl(*args):
        calls.append(args)
        if args[0]=='show':return SimpleNamespace(stdout='/owned.service')
        if args[0]=='set-property' and fail:raise OSError('controller unavailable')
        if args[0]=='stop':turns[0]=3
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(r,'_systemctl',ctl)
    t=r.watch_resources(process,'owned.service',tmp_path,logs.append);t.join(2)
    assert not t.is_alive()
    if fail:
        assert calls[-1] == ('stop','owned.service')
        assert any('control_failed' in line for line in logs)
    else:
        properties=[c[-1] for c in calls if c[0]=='set-property']
        assert properties==[]  # No periodic shrink/reclaim of active tables.
        assert any('event=end' in line for line in logs)
