"""Exercise the deployed scheduling functions without loading accounts or exchanges."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest


def function(path, name, namespace):
    source = Path(path).read_text(encoding='utf8')
    node = next(f for f in ast.parse(source).body if isinstance(f, ast.FunctionDef) and f.name == name)
    exec('from __future__ import annotations\n' + ast.get_source_segment(source, node), namespace)
    return namespace[name]


@pytest.mark.parametrize('minute,ready,middle_offset,expected', [
    (25, True, '15m', ['a', 'c']), (25, False, '15m', []),
    (45, True, '15m', []), (25, True, '25min', ['a', 'c']),
])
def test_schedule_before_ready(minute, ready, middle_offset, expected, monkeypatch):
    accounts = [Path(s+'.py') for s in 'abc']
    offsets = dict(zip(accounts, ['25m', middle_offset, '25m']))
    run = datetime(2026, 11, 5, 12, minute)
    events = []
    def record(tag, value=None):
        events.append((tag, value))
    def gate(_):
        record('gate')
        return ready
    def offset(account):
        record('read', account)
        return offsets[account]
    class Coordinator:
        def prepare(self, all_accounts, *_):
            assert list(all_accounts) == [a for a in accounts if offsets[a] != '25min']
            record('prepare')
        def command(self, account, *_):
            record('command', account.stem)
            return [account.stem], {}
        def handle_result(self, *args):
            return True
    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace(bmac_data_path='market'))
    ns = dict(datetime=datetime, divider=Mock(), random=Mock(), logger=Mock(), load_multi_accounts=lambda:accounts,
              is_debug=False, is_simulate='dry_run', debug_flag='', read_hour_offset_from_config=offset,
              sleep_until_run_time=lambda *a, **kw:record('wake') or run, server_initializer=Coordinator(), check_bmac_update_flag=gate,
              remedy_until_run_time=lambda stamp:record('paced', stamp), time=Mock(), gc=Mock(), os=SimpleNamespace(environ={}),
              PROJECT_ROOT='root', subprocess=Mock(), stat_executor=Mock(), run_statistics_async=Mock())
    function('startup.py', 'run_loop', ns)()
    assert [v for k,v in events if k=='read'] == accounts
    assert events[0] == ('wake', None)
    assert ('prepare', None) in events
    if minute == 45:
        assert ('gate', None) not in events and ('paced', run) in events
    else:
        assert max(i for i,(k,_) in enumerate(events) if k in ('read','prepare')) < events.index(('gate', None))
    assert [v for k,v in events if k=='command'] == expected
    assert ns['gc'].collect.call_count == len(expected)
    assert ns['stat_executor'].submit.call_count == len(expected)


@pytest.mark.parametrize('gate,prefixes', [
    ('check_bmac_update_flag', ('spot_dict_', 'swap_dict_')),
    ('check_bmac_pivot_flag', ('market_pivot_spot_', 'market_pivot_swap_')),
])
def test_shared_ready_settles_one_second(tmp_path, gate, prefixes):
    run = datetime.now()
    folder = tmp_path / f'{run.minute}m'
    folder.mkdir()
    paths = [folder / f'{p}{int(run.timestamp())}.ready' for p in prefixes]
    paths[0].touch()
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        paths[1].touch()
    ns = dict(datetime=datetime, timedelta=timedelta, bmac_data_path=tmp_path,
              logger=Mock(), time=SimpleNamespace(sleep=sleep))
    path='core/utils/datatools.py'
    function(path, 'check_flags', ns)
    check = function(path, gate, ns)
    assert check(run) and sleeps == [0.1]  # Batch2 Stage5: poll_interval=0.1, no post-ready sleep(1)
    sleeps.clear()
    assert not check(run-timedelta(minutes=5)) and sleeps == []
