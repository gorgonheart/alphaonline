"""Recovery control-flow tests: no services, exchange calls or production state."""
from pathlib import Path
from types import SimpleNamespace
import sys
import json
import pandas as pd
import pytest
from tools.robust40 import server, replay, handoff


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('Network forbidden'))


def setup_trade(tmp_path,monkeypatch,error):
    from tools.robust40.cli import server_robust40 as worker
    server.save_meta(tmp_path,dict(complete=True))
    monkeypatch.setattr(server,'settings',lambda *a:({'strategy_pool':[]},{}))
    monkeypatch.setattr(server,'location',lambda *a:tmp_path)
    monkeypatch.setattr(server,'inspect',lambda *a:('ready',''))
    def fail(*a):raise error
    monkeypatch.setattr(handoff,'prepare',fail)
    calls=[]
    monkeypatch.setitem(sys.modules,'core.account_exec',SimpleNamespace(main=lambda:calls.append('account')))
    return worker,calls


def test_out_of_range_checkpoint_has_typed_recovery_result():
    times=pd.date_range('2026-11-03 20:15',periods=25,freq='h')
    with pytest.raises(replay.RebuildRequired):
        replay.common_before([times,times],pd.Timestamp('2026-10-28 21:15'))
    assert replay.common_before([times,times],times[-1])==times[-2]


def test_trade_requests_rebuild_before_account_calls(tmp_path,monkeypatch):
    from tools.robust40 import telemetry
    logs=[];monkeypatch.setattr(telemetry,'_write',logs.append)
    worker,calls=setup_trade(tmp_path,monkeypatch,replay.RebuildRequired('no common checkpoint'))
    assert worker.trade(Path('fake.py'),1793436000)==replay.REBUILD_REQUIRED_EXIT
    assert not calls and pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is False
    end=json.loads(logs[-1].split(' ',1)[1])
    assert end['status']=='rebuild_required' and end['committed'] is False
    assert 'error_type' not in end


@pytest.mark.parametrize('error',[ValueError('bad calculation'),OSError(28,'disk full'),PermissionError('denied')])
def test_unclassified_preflight_error_is_not_automatic_rebuild(tmp_path,monkeypatch,error):
    worker,calls=setup_trade(tmp_path,monkeypatch,error)
    with pytest.raises(type(error)):
        worker.trade(Path('fake.py'),1793436000)
    assert not calls and pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is True


STAMP=int(pd.Timestamp('2026-11-01 12:35',tz='UTC').timestamp())


def publication(market,stamp=STAMP,minute=35,full=True):
    import pyarrow as pa
    folder=market/f'{minute}m';folder.mkdir(parents=True,exist_ok=True)
    for kind in ('spot','swap'):
        table=pa.table({'close':[1.]}).replace_schema_metadata({b'full_history':b'1' if full else b'0'})
        with pa.OSFile(str(folder/f'{kind}_dict_{stamp}.arrow'),'wb') as sink:
            with pa.ipc.new_file(sink,table.schema) as writer:writer.write_table(table)
    for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
        (folder/f'{prefix}{stamp}.ready').touch()


def setup_coordinator(tmp_path,monkeypatch):
    import subprocess
    from tools.robust40 import initialization_config,resources
    monkeypatch.setattr(initialization_config,'ARROW_DEMAND_ENABLED',True)
    monkeypatch.setattr(resources,'supervisor_command',lambda command,*a:command)
    monkeypatch.setattr(server.Coordinator,'_monitor',lambda *a:None)
    monkeypatch.setattr(server.Coordinator,'_ensure_pending_thread',lambda *a:None)
    account=tmp_path/'account.py';account.write_text('')
    values=dict(strategy_name='s',account={'hour_offset':'35m'},strategy_pool=[],precompute_factor_configs={})
    monkeypatch.setattr(server,'enabled',lambda *a:True)
    monkeypatch.setattr(server,'settings',lambda *a:(values,{'minute':35}))
    calls=[];process=SimpleNamespace(poll=lambda:None)
    def spawn(command,**kw):calls.append(command);return process
    monkeypatch.setattr(subprocess,'Popen',spawn)
    return server.Coordinator(tmp_path),account,tmp_path/'market',calls,process,values


def test_rebuild_waits_without_worker_then_starts_once_on_full_data(tmp_path,monkeypatch):
    c,account,market,calls,process,_=setup_coordinator(tmp_path,monkeypatch)
    run=pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime()
    publication(market,full=False)
    c.request_rebuild(account,run,market,'checkpoint gap')
    c.drain_pending()
    assert not calls and not list((tmp_path/'data/server_initialization').glob('*/*'))
    request=next((market/'35m/.arrow-demand').glob('*/*.json'))
    assert json.loads(request.read_text())['rows']==0
    publication(market,full=True)
    c.drain_pending();c.request_rebuild(account,run,market,'checkpoint gap');c.drain_pending()
    assert len(calls)==1 and 'rebuild' in calls[0]


def test_deterministic_failed_publication_is_not_restarted(tmp_path,monkeypatch):
    c,account,market,calls,process,_=setup_coordinator(tmp_path,monkeypatch)
    publication(market)
    run=pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime()
    c.request_rebuild(account,run,market,'gap')
    process.poll=lambda:1
    c.drain_pending();c.drain_pending()
    assert len(calls)==1
    publication(market,stamp=STAMP+3600)
    c.drain_pending()
    assert len(calls)==2 and str(STAMP+3600) in calls[-1]


def test_full_header_is_cached_until_file_version_changes(tmp_path,monkeypatch):
    import pyarrow as pa
    from tools.robust40.market_inputs import full_publication
    market=tmp_path/'market';publication(market,full=False)
    original=pa.ipc.open_file;reads=[]
    monkeypatch.setattr(pa.ipc,'open_file',lambda *a,**kw:reads.append(1) or original(*a,**kw))
    cache={}
    assert full_publication(market,35,{'close'},cache)[0] is None
    first=len(reads)
    assert full_publication(market,35,{'close'},cache)[0] is None
    assert len(reads)==first
    publication(market,full=True)
    assert full_publication(market,35,{'close'},cache)[0]==STAMP
    assert len(reads)>first


def test_rebuild_rechecks_publication_after_slot_and_uses_existing_initializer(tmp_path,monkeypatch):
    from tools.robust40.cli import server_robust40 as worker
    import config
    account=tmp_path/'a.py';account.write_text('')
    market=tmp_path/'market';publication(market,stamp=STAMP+3600)
    monkeypatch.setattr(config,'bmac_data_path',market)
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(server,'settings',lambda *a:({'strategy_name':'s','strategy_pool':[],'precompute_factor_configs':{}},{'minute':35}))
    monkeypatch.setattr(server,'inspect',lambda *a:('rebuild','incomplete'))
    calls=[];monkeypatch.setattr(worker,'initialize',lambda *a,**kw:calls.append(a))
    worker.rebuild(account,STAMP,tmp_path/'data/snapshot/a_s')
    assert len(calls)==1 and calls[0][2]==STAMP+3600
    assert calls[0][1]==tmp_path/'data/server_initialization/a'/str(STAMP+3600)
    monkeypatch.setattr(server,'inspect',lambda *a:('ready',''))
    worker.rebuild(account,STAMP,tmp_path/'data/snapshot/a_s')
    assert len(calls)==1


def test_queued_initializer_owns_dispatch_lock_before_global_slot(tmp_path):
    from tools.robust40 import resources
    destination=tmp_path/'data/snapshot/a_s'
    command=['python','server_robust40.py','rebuild','--destination',str(destination)]
    with resources.queued_initialization_account(command):
        with pytest.raises(OSError):
            with server.account_lock(destination.with_name(destination.name+'.dispatch')):
                pytest.fail('Another dispatcher acquired an active account queue')
    with server.account_lock(destination.with_name(destination.name+'.dispatch')):pass


def test_last_initialization_attempt_is_bounded_and_survives_parent_restart(tmp_path):
    values={'strategy_name':'s'}
    server.save_meta(tmp_path,dict(complete=False,origin='old',settings=values))
    server.record_initialization_attempt(tmp_path,values,STAMP+3600)
    assert server.initialization_attempted(tmp_path,values,STAMP+3600)
    assert not server.initialization_attempted(tmp_path,values,STAMP+7200)
    assert not server.initialization_attempted(tmp_path,{'strategy_name':'changed'},STAMP+3600)
    server.record_initialization_attempt(tmp_path,values,STAMP+7200)
    meta=pd.read_pickle(tmp_path/'warmup.pkl')
    assert meta['origin']=='old' and meta['initialization_attempt']['stamp']==STAMP+7200
    assert len(list(tmp_path.iterdir()))==1


def test_lost_full_publication_returns_to_wait_without_initializing(tmp_path,monkeypatch):
    from tools.robust40.cli import server_robust40 as worker
    import config
    market=tmp_path/'market';publication(market,full=False)
    monkeypatch.setattr(config,'bmac_data_path',market)
    monkeypatch.setattr(server,'settings',lambda *a:({'precompute_factor_configs':{}},{'minute':35}))
    monkeypatch.setattr(worker,'initialize',lambda *a:pytest.fail('No full publication'))
    assert worker.rebuild(tmp_path/'a.py',STAMP,tmp_path/'snapshot/a')==replay.WAIT_FOR_DATA_EXIT


def test_only_model_trade_result_enters_rebuild(tmp_path,monkeypatch):
    import subprocess
    c=server.Coordinator(tmp_path);calls=[]
    monkeypatch.setattr(c,'request_rebuild',lambda *a:calls.append(a))
    account=tmp_path/'a.py';run=pd.Timestamp(STAMP,unit='s').to_pydatetime()
    model=['python',str(tmp_path/'tools/robust40/cli/server_robust40.py'),'trade']
    assert c.handle_result(account,run,tmp_path,model,{},0) is True
    assert c.handle_result(account,run,tmp_path,model,{},replay.REBUILD_REQUIRED_EXIT) is False
    assert len(calls)==1
    for command,code in ((['python',str(tmp_path/'core/account_exec.py')],75),(model,1)):
        with pytest.raises(subprocess.CalledProcessError):c.handle_result(account,run,tmp_path,command,{},code)
    assert len(calls)==1


def test_actual_startup_loop_continues_other_account_and_skips_failed_statistics(tmp_path):
    import ast,datetime
    source=Path('startup.py').read_text(encoding='utf8')
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='run_loop')
    events=[];accounts=[tmp_path/'a.py',tmp_path/'b.py'];run=datetime.datetime(2026,11,1,12,35)
    class Runner:
        def command(self,account,*a):return [account.stem],{}
        def handle_result(self,account,rt,market,command,env,code):
            events.append(('result',account.stem,code));return code==0
    def execute(command,**kwargs):
        assert kwargs.get('check') is False
        events.append(('run',command[0]));return SimpleNamespace(returncode=75 if command[0]=='a' else 0)
    ns=dict(datetime=datetime.datetime,random=SimpleNamespace(randint=lambda *a:1),divider=lambda *a,**k:None,
            load_multi_accounts=lambda:accounts,is_debug=True,is_simulate='debug',debug_flag='',
            read_hour_offset_from_config=lambda *a:'35m',get_debug_run_time=lambda *a:run,
            check_bmac_update_flag=lambda *a:True,server_initializer=Runner(),os=SimpleNamespace(environ={}),
            PROJECT_ROOT=str(tmp_path),subprocess=SimpleNamespace(run=execute),
            stat_executor=SimpleNamespace(submit=lambda account,*a:events.append(('statistics',account))),
            run_statistics_async=None,gc=SimpleNamespace(collect=lambda:None),time=SimpleNamespace(sleep=lambda *a:None),
            remedy_until_run_time=lambda *a:None,logger=SimpleNamespace(**{k:lambda *a:None for k in ('info','debug','warning','ok','critical')}))
    exec(compile(ast.Module(body=[function],type_ignores=[]),'startup.py','exec'),ns)
    ns['run_loop']()
    assert events==[('run','a'),('result','a',75),('run','b'),('result','b',0),('statistics','b')]


def test_new_partial_publication_does_not_launch_from_old_ready(tmp_path):
    from tools.robust40.market_inputs import full_publication
    market=tmp_path/'market';publication(market)
    (market/f'35m/spot_dict_{STAMP+3600}.ready').touch()
    assert full_publication(market,35)[0] is None


def test_waiting_account_does_not_block_other_offset(tmp_path,monkeypatch):
    c,a,market,calls,process,values=setup_coordinator(tmp_path,monkeypatch)
    b=tmp_path/'b.py';b.write_text('')
    monkeypatch.setattr(server,'settings',lambda path:(dict(values,account={'hour_offset':'45m' if path==b else '35m'}),{'minute':45 if path==b else 35}))
    publication(market,full=False);publication(market,stamp=STAMP+600,minute=45)
    c.request_rebuild(a,pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime(),market)
    c.request_rebuild(b,pd.Timestamp(STAMP+600,unit='s',tz='UTC').to_pydatetime(),market)
    assert len(calls)==1 and str(b) in calls[0] and a in c.pending


def test_existing_queued_owner_blocks_duplicate_after_restart(tmp_path,monkeypatch):
    from tools.robust40 import resources
    c,a,market,calls,_,values=setup_coordinator(tmp_path,monkeypatch)
    publication(market)
    destination=server.location(tmp_path,a,values)
    command=['python','server_robust40.py','rebuild','--destination',str(destination)]
    with resources.queued_initialization_account(command):
        c.request_rebuild(a,pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime(),market)
        assert not calls
    c.drain_pending();assert len(calls)==1


def test_wait_exit_does_not_spin_workers_on_unchanged_publication(tmp_path,monkeypatch):
    c,a,market,calls,process,_=setup_coordinator(tmp_path,monkeypatch)
    publication(market)
    c.request_rebuild(a,pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime(),market)
    process.poll=lambda:replay.WAIT_FOR_DATA_EXIT
    c.drain_pending();c.drain_pending()
    assert len(calls)==1
    publication(market,stamp=STAMP+3600)
    c.drain_pending();assert len(calls)==2


@pytest.mark.parametrize('metadata',[None,[],42])
def test_invalid_metadata_does_not_prevent_recording_rebuild_attempt(tmp_path,metadata):
    pd.to_pickle(metadata,tmp_path/'warmup.pkl')
    server.record_initialization_attempt(tmp_path,{'strategy_name':'s'},STAMP)
    assert server.initialization_attempted(tmp_path,{'strategy_name':'s'},STAMP)


def test_pending_watcher_runs_without_waiting_for_next_offset(tmp_path,monkeypatch):
    from threading import Event
    c=server.Coordinator(tmp_path);observed=Event()
    c.pending[tmp_path/'a.py']={}
    def drain():
        with c._mutex:c.pending.clear()
        observed.set()
    monkeypatch.setattr(c,'drain_pending',drain)
    try:
        c._ensure_pending_thread()
        assert observed.wait(3), 'Background watcher did not start'
    finally:c.close()
    assert not c._pending_thread.is_alive()


@pytest.mark.parametrize('entry',['coordinator','worker'])
def test_recovery_recheck_uses_configured_resume_capacity(tmp_path,monkeypatch,entry):
    c,account,market,calls,_,values=setup_coordinator(tmp_path,monkeypatch)
    publication(market)
    monkeypatch.setattr(server,'resume_capacity',lambda values:24)
    monkeypatch.setattr(server,'inspect',lambda *a,**kw:('rebuild','gap exceeds incremental coverage') if (a[4] if len(a)>4 else kw.get('max_gap',240))==24 else ('ready',''))
    if entry=='coordinator':
        c.request_rebuild(account,pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime(),market)
        assert len(calls)==1
    else:
        import config
        from tools.robust40.cli import server_robust40 as worker
        monkeypatch.setattr(config,'bmac_data_path',market)
        monkeypatch.setattr(worker,'initialize',lambda *a:calls.append(a))
        worker.rebuild(account,STAMP,server.location(tmp_path,account,values))
        assert len(calls)==1



def test_external_completed_snapshot_cancels_wait_even_with_short_arrow(tmp_path,monkeypatch):
    c,account,market,calls,_,values=setup_coordinator(tmp_path,monkeypatch)
    publication(market,full=False)
    c.request_rebuild(account,pd.Timestamp(STAMP,unit='s',tz='UTC').to_pydatetime(),market)
    assert account in c.pending
    from tools.robust40.arrow_demand import set_demand
    set_demand(market,tmp_path,account,35,False,stamp=STAMP,rows=5000)
    monkeypatch.setattr(server,'inspect',lambda *a:('ready',''))
    c.drain_pending()
    assert account not in c.pending and not calls
    demand=next((market/'35m/.arrow-demand').glob('*/*.json'))
    assert json.loads(demand.read_text())['rows']==5000
