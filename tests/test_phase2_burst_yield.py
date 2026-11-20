"""Phase 2 TDD tests for BURST <-> YIELD dynamic initialization resource governance."""
from types import SimpleNamespace
import pytest

from tools.robust40 import resources as r

G = 1024 ** 3


def _sample(**kw):
    base = dict(system_available=3 * G, system_swap_free=12 * G, memory_full=0.0, io_full=0.0)
    base.update(kw)
    return base


def _policy(**kw):
    p = dict(r.POLICY)
    p.update(
        dynamic_burst_enabled=True,
        period_seconds=300,
        yield_preempt_seconds=25,
        burst_resume_hold_seconds=6,
        burst_min_available_bytes=int(1.5 * G),
        yield_gate_available_bytes=int(1.2 * G),
        yield_gate_memory_full=10.0,
        yield_gate_io_full=15.0,
        burst_cpu_weight=100,
        burst_io_weight=100,
        burst_cpu_quota='',
        yield_cpu_weight=5,
        yield_io_weight=10,
        yield_cpu_quota='20%',
    )
    p.update(kw)
    return p


def test_select_mode_starts_in_burst_when_idle_and_preempts_before_trading_window():
    pol = _policy()
    stamp = 1793577600  # Exact 5m boundary (stamp % 300 == 0)

    # Idle window (t + 60s): starts directly in BURST
    mode, reason, idle_since = r.select_resource_mode(stamp + 60, _sample(), False, None, None, pol)
    assert mode == 'burst'
    assert reason == 'idle_exclusive'

    # Foreground trading starts at t + 180s -> immediate YIELD (0s latency)
    mode, reason, idle_since = r.select_resource_mode(stamp + 180, _sample(), True, 'burst', None, pol)
    assert mode == 'yield'
    assert reason == 'foreground_active'
    assert idle_since is None

    # Preempt window (t + 280s, 20s before next 5m boundary) -> immediate YIELD even if foreground=False
    mode, reason, idle_since = r.select_resource_mode(stamp + 280, _sample(), False, 'burst', None, pol)
    assert mode == 'yield'
    assert reason == 'preempt_window'


def test_select_mode_yield_to_burst_requires_hysteresis_hold_and_ram_headroom():
    pol = _policy()
    stamp = 1793577600

    # Foreground just stopped at t + 100s -> stay in YIELD, record idle_since
    mode, reason, idle_since = r.select_resource_mode(stamp + 100, _sample(), False, 'yield', None, pol)
    assert mode == 'yield'
    assert reason == 'resume_hold'
    assert idle_since == stamp + 100

    # 4 seconds later (< 6s hold) -> still YIELD
    mode, reason, idle_since = r.select_resource_mode(stamp + 104, _sample(), False, 'yield', idle_since, pol)
    assert mode == 'yield'
    assert idle_since == stamp + 100

    # 6 seconds later (>= 6s hold) -> promote back to BURST
    mode, reason, idle_since = r.select_resource_mode(stamp + 106, _sample(), False, 'yield', idle_since, pol)
    assert mode == 'burst'
    assert reason == 'idle_exclusive'

    # Under low RAM (< 1.0 GiB) -> immediate YIELD even when foreground=False
    mode, reason, _ = r.select_resource_mode(stamp + 120, _sample(system_available=int(0.8 * G)), False, 'burst', None, pol)
    assert mode == 'yield'
    assert reason == 'low_available_ram'

    # Regression (2026-11-20 live init finding): when foreground=False and RAM is plentiful (5 GiB),
    # initializer's own IPC block IO (io_full=35%) must NOT self-throttle to YIELD!
    mode, reason, _ = r.select_resource_mode(stamp + 120, _sample(system_available=5 * G, io_full=35.0, memory_full=5.0), False, 'burst', None, pol)
    assert mode == 'burst'
    assert reason == 'idle_exclusive'

    # When RAM is tight (< 1.2 GiB) AND pressure is high, yield even without foreground
    mode, reason, _ = r.select_resource_mode(stamp + 120, _sample(system_available=int(1.1 * G), io_full=20.0), False, 'burst', None, pol)
    assert mode == 'yield'
    assert reason == 'io_pressure'


def test_apply_mode_properties_never_touches_memory_high():
    pol = _policy()
    calls = []

    def fake_ctl(*args):
        calls.append(args)
        return SimpleNamespace(stdout='')

    r.apply_mode_properties('owned.service', 'burst', pol, ctl=fake_ctl)
    r.apply_mode_properties('owned.service', 'yield', pol, ctl=fake_ctl)

    assert len(calls) == 2
    assert calls[0] == ('set-property', '--runtime', 'owned.service', 'CPUWeight=100', 'IOWeight=100', 'CPUQuota=')
    assert calls[1] == ('set-property', '--runtime', 'owned.service', 'CPUWeight=5', 'IOWeight=10', 'CPUQuota=20%')
    for call in calls:
        assert not any('Memory' in arg for arg in call)


def test_admission_decision_pauses_new_work_during_foreground_pressure_only():
    # Legacy 1-arg behavior unchanged
    assert r.admission_decision(_sample(system_available=int(0.8 * G), io_full=20.0)) == (True, 'available')

    # During dynamic_yield + foreground: tighter RAM/PSI gate pauses new batch submission
    assert r.admission_decision(
        _sample(system_available=int(1.0 * G)), foreground=True, dynamic_yield=True
    ) == (False, 'foreground_yield_ram')
    assert r.admission_decision(
        _sample(system_available=2 * G, io_full=20.0), foreground=True, dynamic_yield=True
    ) == (False, 'foreground_yield_io')
    assert r.admission_decision(
        _sample(system_available=2 * G, memory_full=15.0), foreground=True, dynamic_yield=True
    ) == (False, 'foreground_yield_memory')

    # Foreground active but plenty of RAM and low PSI -> allowed to progress quietly under cgroup Yield
    assert r.admission_decision(
        _sample(system_available=2 * G, memory_full=1.0, io_full=2.0), foreground=True, dynamic_yield=True
    ) == (True, 'available')


def test_watch_resources_dynamic_transitions_burst_to_yield_and_back(tmp_path, monkeypatch):
    group = tmp_path / 'owned.service'
    group.mkdir()
    (group / 'memory.current').write_text('100')
    monkeypatch.setattr(r, 'CGROUP_ROOT', tmp_path)
    monkeypatch.setitem(r.POLICY, 'dynamic_burst_enabled', True)
    monkeypatch.setitem(r.POLICY, 'burst_resume_hold_seconds', 6)
    monkeypatch.setitem(r.POLICY, 'yield_preempt_seconds', 25)

    stamp = 1793577600
    # Timeline: t+60 (idle->BURST), t+70 (fg->YIELD), t+80 (fg ends, hold), t+88 (hold elapsed->BURST)
    timeline = [
        (stamp + 60, False),
        (stamp + 70, True),
        (stamp + 80, False),
        (stamp + 88, False),
    ]
    idx = [0]
    calls = []
    logs = []
    process = SimpleNamespace(poll=lambda: None if idx[0] < len(timeline) else 0)

    monkeypatch.setattr(r, 'pressure_snapshot', lambda: _sample())
    monkeypatch.setattr(r, 'foreground_running', lambda: timeline[min(idx[0], len(timeline) - 1)][1])
    monkeypatch.setattr(r.time, 'time', lambda: timeline[min(idx[0], len(timeline) - 1)][0])
    monkeypatch.setattr(r.time, 'sleep', lambda _s: idx.__setitem__(0, idx[0] + 1))

    def fake_ctl(*args):
        calls.append(args)
        if args[0] == 'show':
            return SimpleNamespace(stdout='/owned.service')
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(r, '_systemctl', fake_ctl)
    t = r.watch_resources(process, 'owned.service', tmp_path, logs.append)
    t.join(2)
    assert not t.is_alive()

    set_calls = [c for c in calls if c[0] == 'set-property']
    assert len(set_calls) == 3  # BURST -> YIELD -> BURST
    assert 'CPUWeight=100' in set_calls[0]
    assert 'CPUWeight=5' in set_calls[1] and 'CPUQuota=20%' in set_calls[1]
    assert 'CPUWeight=100' in set_calls[2] and 'CPUQuota=' in set_calls[2]
    assert all(not any('Memory' in a for a in c) for c in set_calls)
    mode_logs = [line for line in logs if line.startswith('INIT_RESOURCE_MODE ')]
    assert len(mode_logs) == 3


def test_foreground_running_ignores_stale_orphan_workers(monkeypatch):
    import psutil

    now = 1795155000.0
    stale = SimpleNamespace(info={
        'cmdline': ['/usr/bin/python', '/home/ubuntu/alphatrade/core/account_exec.py', '1768578000'],
        'create_time': now - 26_576_845.0,  # ~307 days old stuck orphan
    })
    fresh = SimpleNamespace(info={
        'cmdline': ['/usr/bin/python', '/home/ubuntu/alphatrade/core/account_exec.py', '1795155000'],
        'create_time': now - 45.0,
    })

    monkeypatch.setattr(psutil, 'process_iter', lambda attrs=None: [stale])
    assert r.foreground_running(now=now, max_age_seconds=3600) is False

    monkeypatch.setattr(psutil, 'process_iter', lambda attrs=None: [stale, fresh])
    assert r.foreground_running(now=now, max_age_seconds=3600) is True

