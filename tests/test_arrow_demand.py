import importlib.util
from pathlib import Path
from tools.robust40 import arrow_demand, initialization_config


def producer(producer_root):
    path=producer_root/'core/arrow_demand.py'
    assert path.exists(), 'Producer has no demand resolver'
    spec=importlib.util.spec_from_file_location('producer_arrow_demand',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_multiple_consumers_and_offsets(tmp_path, monkeypatch, producer_root):
    m=producer(producer_root)
    monkeypatch.setattr(initialization_config,'ARROW_DEMAND_ENABLED',True)
    folder=tmp_path/'market/25m'
    assert m.publication_rows(folder,0,enabled=True)==0
    def demand(owner, full, minute=25):
        arrow_demand.set_demand(tmp_path/'market',tmp_path/owner,Path('account.py'),minute,full)
    demand('trade1',True);demand('trade2',False)
    assert m.publication_rows(folder,4050,enabled=True)==0
    demand('trade1',False)
    assert m.publication_rows(folder,0,enabled=True,minimum=5000)==5000
    demand('trade1',True,55)
    assert m.publication_rows(folder,0,enabled=True,minimum=5000)==5000
    assert m.publication_rows(tmp_path/'market/55m',4050,enabled=True)==0
    # Disabled producer preserves its configured depth.
    assert m.publication_rows(folder,0,enabled=False)==0


def test_failed_or_partial_demand_is_conservative(tmp_path, producer_root):
    m=producer(producer_root)
    folder=tmp_path/'25m';requests=folder/'.arrow-demand/a';requests.mkdir(parents=True)
    for name, data in [('x.json','{'),('x.json','{"version":1,"rows":-1}'),('x.tmp','')]:
        for p in requests.iterdir():p.unlink()
        (requests/name).write_text(data)
        assert m.publication_rows(folder,4050,enabled=True)==0


def test_dispatch_requests_before_worker_start(tmp_path, monkeypatch):
    from tools.robust40 import server, resources
    import subprocess
    import pandas as pd
    monkeypatch.setattr(initialization_config,'ARROW_DEMAND_ENABLED',True)
    monkeypatch.setattr(resources,'supervisor_command',lambda cmd,*args:cmd)
    monkeypatch.setattr(server.Coordinator,'_monitor',lambda *a:None)
    account=tmp_path/'a.py';account.touch()
    market=tmp_path/'market'
    values={'strategy_name':'S'}
    class Process:
        def poll(self):return None
    def launch(*a,**kw):
        files=list((market/'25m/.arrow-demand').glob('*/*.json'))
        assert len(files)==1, 'Worker launched without registering full-history demand'
        import json
        assert json.loads(files[0].read_text())['rows']==0
        return Process()
    monkeypatch.setattr(subprocess,'Popen',launch)
    server.Coordinator(tmp_path)._launch(account,pd.Timestamp('2026-11-05 12:25'),values,'missing','prepare',market)


def test_configured_full_floor_protects_legacy_consumer(tmp_path, monkeypatch, producer_root):
    monkeypatch.setattr(initialization_config,'ARROW_DEMAND_ENABLED',True)
    arrow_demand.set_demand(tmp_path/'market',tmp_path/'trade','a.py',25,False)
    assert producer(producer_root).publication_rows(tmp_path/'market/25m',0,enabled=True)==0


def test_success_does_not_clear_newer_request_and_repairs_own_tmp(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(initialization_config,'ARROW_DEMAND_ENABLED',True)
    args=(tmp_path/'market',tmp_path/'trade','a.py',25)
    arrow_demand.set_demand(*args,True,stamp=200)
    path=next((tmp_path/'market/25m/.arrow-demand').glob('*/*.json'))
    arrow_demand.release_demand(*args,stamp=100)
    assert json.loads(path.read_text())['rows']==0
    path.with_suffix('.tmp').write_text('interrupted')
    arrow_demand.set_demand(*args,True,stamp=200)
    assert not path.with_suffix('.tmp').exists()
    arrow_demand.release_demand(*args,stamp=200)
    assert json.loads(path.read_text())['rows']==5000


def test_live_rows_preserves_mapping_prewarm_and_large_account_minimum():
    assert arrow_demand.live_rows({'min_kline_num':168}) == 5000
    assert arrow_demand.live_rows({'min_kline_num':1800}) == 5851


def test_request_lock_excludes_another_writer(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(initialization_config, 'ARROW_DEMAND_ENABLED', True)
    args=(tmp_path/'market',tmp_path/'trade','a.py',25)
    arrow_demand.set_demand(*args,True,stamp=200)
    path=next((tmp_path/'market/25m/.arrow-demand').glob('*/*.json'))
    with arrow_demand._request_lock(path):
        with pytest.raises(OSError):
            arrow_demand.set_demand(*args,False,stamp=200)


def test_published_required_depth_preserves_real_reader_mapping(tmp_path, producer_root):
    import pandas as pd
    from core.utils.arrow_reader import read_arrow_market_dict
    from core.utils.datatools import align_spot_swap_mapping_fast
    spec=importlib.util.spec_from_file_location('demand_publish',producer_root/'core/arrow_publish.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for minimum in (168,1800):
        rows=arrow_demand.live_rows({'min_kline_num':minimum})
        frame=pd.DataFrame(dict(candle_begin_time=pd.date_range('2025-01-01',periods=14060,freq='h'),
                                symbol=['AUSDT']*14060,symbol_spot=['AUSDT']*14060,symbol_swap=['AUSDT']*14060))
        outputs=[]
        for depth in (0,rows,4050):
            path=tmp_path/f'{minimum}-{depth}.arrow'
            module.publish_arrow_snapshot({'AUSDT':frame},path,depth,write_ready=False)
            loaded=read_arrow_market_dict(path,max_rows=rows)['AUSDT']
            outputs.append(align_spot_swap_mapping_fast(loaded,'symbol_spot',minimum).tail(4050).reset_index(drop=True))
        pd.testing.assert_frame_equal(outputs[0],outputs[1],check_exact=True)
        assert not outputs[0].equals(outputs[2]), 'Fixture must expose the unsafe 4050 crop'


def test_explicit_floor_and_larger_consumer_are_both_honored(tmp_path, monkeypatch, producer_root):
    monkeypatch.setattr(initialization_config, 'ARROW_DEMAND_ENABLED', True)
    arrow_demand.set_demand(tmp_path/'market',tmp_path/'trade','a.py',25,False,rows=5851)
    resolve=producer(producer_root).publication_rows
    assert resolve(tmp_path/'market/25m',0,enabled=True,minimum=5000)==5851
    assert resolve(tmp_path/'market/25m',0,enabled=True,minimum=8000)==8000


def test_full_request_lock_contention_defers_only_this_account(tmp_path, monkeypatch):
    from tools.robust40 import server
    import pandas as pd
    import subprocess
    def busy(*a,**kw):raise BlockingIOError('another writer')
    monkeypatch.setattr(arrow_demand, 'set_demand', busy)
    def forbidden(*a,**kw):raise AssertionError('Cannot initialize before full demand is registered')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    server.Coordinator(tmp_path)._launch(tmp_path/'a.py',pd.Timestamp('2026-11-05 12:25'),{},'missing','prepare',tmp_path/'market')


def test_notification_bad_depth_cannot_fail_installed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(initialization_config, 'ARROW_DEMAND_ENABLED', True)
    args=(tmp_path/'market',tmp_path/'trade','a.py',25)
    arrow_demand.set_demand(*args,True,stamp=200)
    arrow_demand.release_demand(*args,stamp=200,values={'min_kline_num':'invalid'})
    import json
    path=next((tmp_path/'market/25m/.arrow-demand').glob('*/*.json'))
    assert json.loads(path.read_text())['rows']==0
