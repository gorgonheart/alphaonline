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
