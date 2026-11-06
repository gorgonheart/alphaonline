import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def helper():
    path=ROOT/'tools/run_deployment_tests.py'
    spec=importlib.util.spec_from_file_location('disposable',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_committed_copy_has_no_machine_credentials_and_is_deletable(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    (repo/'runtime.py').write_text('value=42')
    (repo/'tests').mkdir();(repo/'tests/test_empty.py').write_text('def test_ok(): assert True')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
    (repo/'config.json').write_text('{"secret":"must-not-copy"}')
    (repo/'accounts.local.json').write_text('{"private":"must-not-copy"}')
    run=helper().prepare_run(repo)
    assert run.parent==repo/'test'
    values=json.loads((run/'source/config.json').read_text())
    assert values['is_simulate']=='dry_run' and 'secret' not in values
    assert not (run/'source/accounts.local.json').exists()
    shutil.rmtree(repo/'test')
    assert (repo/'runtime.py').read_text()=='value=42'
    assert (repo/'config.json').exists()
    assert helper().prepare_run(repo).exists()


def test_test_directory_symlink_rejected(tmp_path):
    import pytest
    outside=tmp_path/'outside';outside.mkdir()
    root=tmp_path/'root';root.mkdir()
    try:(root/'test').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlink permissions')
    with pytest.raises(ValueError):helper().prepare_run(root)


def test_finished_runs_release_payload_and_keep_bounded_logs(tmp_path):
    mod=helper()
    finish=getattr(mod,'finish_run',None)
    assert callable(finish), 'Completed verification still retains full payload'
    root=tmp_path/'test';root.mkdir()
    active=root/'verify-active';active.mkdir();(active/'keep').write_text('running')
    for i in range(27):
        run=root/f'verify-{i:03}';run.mkdir()
        (run/'source').mkdir();(run/'source/large').write_bytes(b'x'*8192)
        (run/'verify.log').write_text(f'finished {i}')
        finish(run)
        assert not run.exists()
    assert (active/'keep').read_text()=='running'
    assert len(list((root/'logs').glob('verify-*.log')))==24
    assert (root/'logs/verify-026.log').read_text()=='finished 26'


def test_finish_refuses_unowned_directory(tmp_path):
    mod=helper();finish=getattr(mod,'finish_run',None)
    assert callable(finish)
    other=tmp_path/'data';other.mkdir();(other/'verify.log').write_text('keep')
    finish(other)
    assert (other/'verify.log').read_text()=='keep'
