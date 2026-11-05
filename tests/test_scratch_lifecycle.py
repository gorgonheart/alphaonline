import os
from pathlib import Path
import pytest
from tools.robust40 import lifecycle


def test_discard_unlinks_frozen_inode_but_preserves_public(tmp_path):
    base=tmp_path/'scratch';w=base/'123';w.mkdir(parents=True)
    public=tmp_path/'public';public.write_bytes(b'price')
    os.link(public,w/'frozen')
    assert lifecycle.discard(w,base)
    assert public.read_bytes()==b'price' and not w.exists()


def test_discard_rejects_boundary_and_symlink(tmp_path):
    base=tmp_path/'scratch';base.mkdir();outside=tmp_path/'outside';outside.mkdir()
    assert not lifecycle.discard(base,base)
    try:(base/'link').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlinks unavailable')
    assert not lifecycle.discard(base/'link',base) and outside.exists()


def test_account_sweep_preserves_current_and_other_account(tmp_path,monkeypatch):
    base=tmp_path/'data/server_initialization/a';base.mkdir(parents=True)
    for x in ['1','2','3']:(base/x).mkdir()
    other=base.parent/'b/1';other.mkdir(parents=True)
    monkeypatch.setattr(lifecycle,'active_roots',lambda:set())
    lifecycle.sweep_initialization(tmp_path,'a.py',3)
    assert not (base/'1').exists() and not (base/'2').exists()
    assert (base/'3').exists() and other.exists()


def test_active_worker_blocks_sweep(tmp_path,monkeypatch):
    base=tmp_path/'data/server_initialization/a/1';base.mkdir(parents=True)
    monkeypatch.setattr(lifecycle,'active_roots',lambda:{base})
    lifecycle.sweep_initialization(tmp_path,'a.py',2)
    assert base.exists()


def test_delete_failure_does_not_mask_original(tmp_path,monkeypatch):
    base=tmp_path/'scratch';w=base/'1';w.mkdir(parents=True)
    monkeypatch.setattr(lifecycle.shutil,'rmtree',lambda p:(_ for _ in ()).throw(PermissionError('mapped')))
    assert not lifecycle.discard(w,base)
    assert w.exists()


def test_diagnostics_bounded_without_walk(tmp_path):
    for i in range(30):(tmp_path/f'{i}.log').write_text('small')
    lifecycle.prune_logs(tmp_path,24)
    assert len(list(tmp_path.glob('*.log')))==24


def test_unreadable_recovery_keeps_only_small_diagnostic(tmp_path):
    import pandas as pd
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'snapshot/a';live.mkdir(parents=True)
    pd.to_pickle({'complete':False},live/'warmup.pkl')
    backup=live.with_name('a.replay-previous');backup.mkdir()
    (backup/'warmup.pkl').write_bytes(b'broken')
    (backup/'large-payload').write_bytes(b'x'*10000)
    recover_interrupted(live)
    assert not list((tmp_path/'model_replay').rglob('large-payload'))
    assert any(p.read_bytes()==b'broken' for p in (tmp_path/'model_replay').rglob('warmup.pkl'))


def test_normal_success_has_no_process_scan(tmp_path,monkeypatch):
    monkeypatch.setattr(lifecycle,'active_roots',lambda:pytest.fail('unnecessary scan'))
    lifecycle.finish_initialization(tmp_path/'data/server_initialization/a/1',tmp_path,'a.py',1)


@pytest.mark.parametrize('fails',[False,True])
def test_initialization_exit_reclaims_only_owned_workspace(tmp_path,monkeypatch,fails):
    from tools.robust40.cli import server_robust40 as worker
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(lifecycle,'active_roots',lambda:set())
    work=tmp_path/'data/server_initialization/a/1'
    def compute(*args):
        work.mkdir(parents=True);(work/'large').write_bytes(b'input')
        if fails:raise ValueError('compute failed')
    monkeypatch.setattr(worker,'_initialize',compute)
    if fails:
        with pytest.raises(ValueError,match='compute failed'):worker.initialize(Path('a.py'),work,1,tmp_path/'data/snapshot/a')
    else:worker.initialize(Path('a.py'),work,1,tmp_path/'data/snapshot/a')
    assert not work.exists()


def test_orphan_replay_reclaimed_without_new_revision(tmp_path):
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'snapshot/a';live.mkdir(parents=True)
    orphan=tmp_path/'model_replay/a/work-abcdefgh/snapshot';orphan.mkdir(parents=True)
    (orphan/'large').write_text('old')
    other=tmp_path/'model_replay/a-b/work-abcdefgh';other.mkdir(parents=True)
    from unittest.mock import patch
    with patch.object(lifecycle,'active_roots',return_value=set()):recover_interrupted(live)
    assert not orphan.parent.exists() and other.exists() and live.exists()


def test_warning_as_error_cannot_mask_cleanup_failure(tmp_path,monkeypatch):
    import warnings
    base=tmp_path/'scratch';w=base/'1';w.mkdir(parents=True)
    monkeypatch.setattr(lifecycle.shutil,'rmtree',lambda p:(_ for _ in ()).throw(PermissionError('mapped')))
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        assert not lifecycle.discard(w,base)


def test_replay_orphan_worker_blocks_cleanup(tmp_path,monkeypatch):
    live=tmp_path/'data/snapshot/a';live.mkdir(parents=True)
    work=tmp_path/'data/model_replay/a/work-abcdefgh';work.mkdir(parents=True)
    monkeypatch.setattr(lifecycle,'active_roots',lambda:{tmp_path.resolve()})
    lifecycle.sweep_replay(live)
    assert work.exists()


def test_foreign_python_is_not_inspected(monkeypatch):
    import psutil
    if not hasattr(os,'getuid'):pytest.skip('POSIX uid ownership')
    from types import SimpleNamespace
    foreign=SimpleNamespace(pid=-10,info={'name':'python'},uids=lambda:SimpleNamespace(real=os.getuid()+1),environ=lambda:pytest.fail('foreign environment'))
    monkeypatch.setattr(psutil,'process_iter',lambda fields:[foreign])
    assert lifecycle.active_roots()==set()


def test_recovery_reuses_explicit_caller_lock(tmp_path):
    from tools.robust40.server import account_lock
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'data/snapshot/a';live.mkdir(parents=True)
    (tmp_path/'data/model_replay/a').mkdir(parents=True)
    with account_lock(live):recover_interrupted(live,True)


def test_interrupted_transaction_waits_for_orphan_before_switch(tmp_path,monkeypatch):
    import pandas as pd
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'data/snapshot/a';live.mkdir(parents=True)
    pd.to_pickle({'complete':False},live/'warmup.pkl')
    backup=live.with_name('a.replay-previous');backup.mkdir()
    pd.to_pickle({'complete':True},backup/'warmup.pkl')
    monkeypatch.setattr(lifecycle,'active_roots',lambda:{tmp_path.resolve()})
    with pytest.raises(OSError):recover_interrupted(live)
    assert backup.exists() and not pd.read_pickle(live/'warmup.pkl')['complete']


def test_success_cleanup_error_is_not_calculation_failure(tmp_path,monkeypatch):
    from tools.robust40 import server
    work=tmp_path/'data/server_initialization/a/1';work.mkdir(parents=True)
    monkeypatch.setattr(lifecycle.shutil,'rmtree',lambda p:(_ for _ in ()).throw(PermissionError('mapped')))
    server.cleanup_initialization(work,tmp_path,'a.py',1)
    assert work.exists()
