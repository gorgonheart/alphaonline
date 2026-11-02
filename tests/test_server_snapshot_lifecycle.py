import pandas as pd
import pytest
from tools.robust40 import server


def values():
    return dict(strategy_name='test',strategy_pool=[dict(name='child',strategy_list=[])],
                strategy_config=dict(name='ModelRobust40Strategy'),account=dict(hour_offset='20m'))


def prepared(tmp_path):
    v = values(); end = pd.Timestamp('2026-10-30 00:20')
    for name in server.required_files(v):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'x')
    (tmp_path/'snapshot_time.txt').write_text(str(end))
    p=tmp_path/'sections/s3.pkl';p.parent.mkdir(exist_ok=True);p.write_bytes(b'x')
    server.complete(tmp_path,'account',v,end,'2025-04-23 00:20')
    return v,end


def test_fast_check_reads_only_small_metadata(tmp_path,monkeypatch):
    v,end=prepared(tmp_path)
    read=pd.read_pickle
    def small_only(p):
        assert str(p).endswith('warmup.pkl')
        return read(p)
    monkeypatch.setattr(pd,'read_pickle',small_only)
    assert server.inspect(tmp_path,'account',v,end)[0]=='ready'
    assert server.inspect(tmp_path,'other',v,end)[0]=='rebuild'
    (tmp_path/'model/state.pkl').unlink()
    assert server.inspect(tmp_path,'account',v,end)[0]=='rebuild'


def test_completed_snapshot_does_not_require_unused_market_rule_copy(tmp_path):
    v,end=prepared(tmp_path)
    (tmp_path/'model/market_rules.pkl').unlink(missing_ok=True)
    assert server.inspect(tmp_path,'account',v,end)==('ready','')


def test_old_source_policy_rebuilds_without_loading_model(tmp_path,monkeypatch):
    from tools.robust40.warmup import settings
    from pathlib import Path
    current,_=settings(Path(__file__).resolve().parents[1]/'examples/model_robust40/MODEL_ROBUST40_V1_40m.py')
    assert current['simulation_data_policy']=='public_universe_exginfo_units_v1'
    old=dict(current);old.pop('simulation_data_policy')
    server.save_meta(tmp_path,dict(account='account',settings=old,complete=True))
    assert server.inspect(tmp_path,'account',current,'2026-10-30 00:40')==('rebuild','configuration changed')


@pytest.mark.parametrize('origin', [None, 'bad date', '2025-04-23 00:55', '2099-01-01 00:20'])
def test_bad_s3_origin_requests_rebuild_instead_of_repeated_worker_error(tmp_path, origin):
    v,end=prepared(tmp_path)
    meta=pd.read_pickle(tmp_path/'warmup.pkl')
    if origin is None:meta.pop('origin')
    else:meta['origin']=origin
    server.save_meta(tmp_path,meta)
    assert server.inspect(tmp_path,'account',v,end)[0]=='rebuild'


def test_wrong_actual_cutoff_minute_requests_rebuild(tmp_path):
    v,end=prepared(tmp_path)
    wrong=end-pd.Timedelta(hours=1)+pd.Timedelta(minutes=35)
    (tmp_path/'snapshot_time.txt').write_text(str(wrong))
    meta=pd.read_pickle(tmp_path/'warmup.pkl');meta['cutoff']=wrong
    server.save_meta(tmp_path,meta)
    assert server.inspect(tmp_path,'account',v,end)==('rebuild','cutoff offset mismatch')


def test_partial_round_and_clock_mismatch_not_ready(tmp_path):
    v,end=prepared(tmp_path)
    server.begin(tmp_path)
    assert server.inspect(tmp_path,'account',v,end)[0]=='rebuild'
    server.complete(tmp_path,'account',v,end,'2025-04-23 00:20')
    assert server.inspect(tmp_path,'account',v,end-pd.Timedelta(hours=1))[0]=='wait'
    assert server.inspect(tmp_path,'account',v,end+pd.Timedelta(hours=4000))[0]=='rebuild'


def test_dependency_guard_does_not_rewrite_parameters():
    v=values()
    params=('TopKTo2K',(168,'Cci过滤无现货删数据',2600),'val:==1')
    v['strategy_pool'][0]['strategy_list']=[dict(filter_list=[params])]
    with pytest.raises(ValueError,match='5366'):
        server.check_dependencies(v)
    assert v['strategy_pool'][0]['strategy_list'][0]['filter_list'][0]==params


def test_install_rollback_on_failed_directory_switch(tmp_path,monkeypatch):
    from pathlib import Path
    root=tmp_path/'snapshot';root.mkdir()
    old=root/'acct';old.mkdir();(old/'old').write_text('old')
    new=tmp_path/'stage';new.mkdir();(new/'new').write_text('new')
    replace=Path.replace
    def fail(self,target):
        if self==new:raise OSError('interrupted')
        return replace(self,target)
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError):server.install(new,old,root)
    assert (old/'old').read_text()=='old'
    assert (new/'new').exists()


def test_account_lock_releases_without_manual_stale_file_cleanup(tmp_path):
    dest=tmp_path/'snapshot/acct'
    with server.account_lock(dest):
        with pytest.raises(OSError):
            with server.account_lock(dest):pass
    with server.account_lock(dest):pass


def test_cleanup_only_successful_coordinator_scratch(tmp_path):
    work=tmp_path/'data/server_initialization/a/123';work.mkdir(parents=True)
    other=tmp_path/'research/a/123';other.mkdir(parents=True)
    server.cleanup_initialization(other,tmp_path,'a.py',123)
    assert other.exists()
    server.cleanup_initialization(work,tmp_path,'b.py',123)
    assert work.exists()
    server.cleanup_initialization(work,tmp_path,'a.py',123)
    assert not work.exists()
