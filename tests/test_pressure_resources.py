"""Admission uses actual pressure; swap is permitted without changing strategy math."""
from types import SimpleNamespace
from tools.robust40 import resources as r

G=1024**3


def sample(**kw):
    return dict(system_available=2*G,system_swap_free=12*G,
                memory_full=0.,io_full=0.,**kw)


def test_background_can_run_with_swap_even_below_old_512m_cutoff():
    s=sample();s['system_available']=400*1024**2
    assert r.admission_decision(s)==(True,'available')
    s.update(memory_full=35.)
    assert r.admission_decision(s)[0] is False


def test_swap_exhaustion_and_low_ram_blocks_new_work():
    s=sample();s.update(system_available=600*1024**2,system_swap_free=200*1024**2)
    assert r.admission_decision(s)==(False,'swap_reserve')
    s['system_available']=3*G
    assert r.admission_decision(s)[0]


def test_gate_is_not_driven_by_memory_high_or_offset(monkeypatch):
    monkeypatch.setattr(r.sys,'platform','linux')
    monkeypatch.setenv(r.UNIT_ENV,'test.service')
    readings=[dict(sample(),system_available=100*1024**2),sample()]
    monkeypatch.setattr(r,'pressure_snapshot',lambda:readings.pop(0),raising=False)
    sleeps=[];monkeypatch.setattr(r.time,'sleep',sleeps.append)
    r.wait_for_window()
    assert len(sleeps)==1


def test_wrapper_has_stable_high_and_12g_swap(monkeypatch):
    monkeypatch.setattr(r.sys,'platform','linux')
    monkeypatch.setattr(r.os,'getuid',lambda:1000,raising=False)
    monkeypatch.setattr(r.os,'getgid',lambda:1000,raising=False)
    args,_,_=r.initialization_command(['python','compute'],123)
    assert '--property=MemoryHigh=4764729344' in args
    assert '--property=MemorySwapMax=12884901888' in args
    assert '--property=CPUWeight=10' in args


def test_group_sample_reports_anon_cache_and_pressure(tmp_path,monkeypatch):
    (tmp_path/'memory.current').write_text('1234')
    (tmp_path/'memory.peak').write_text('2000')
    (tmp_path/'memory.stat').write_text('anon 700\nfile 400\nkernel 134\n')
    (tmp_path/'memory.events').write_text('high 3\noom 0\n')
    (tmp_path/'cpu.stat').write_text('usage_usec 1200000\n')
    (tmp_path/'memory.pressure').write_text('some avg10=3.0 avg60=0 total=55\nfull avg10=2.0 avg60=0 total=25\n')
    d=r.group_snapshot(tmp_path)
    assert d['anon']==700 and d['file']==400 and d['memory']==1234
    assert d['memory_full']==2. and d['cpu_usage_usec']==1200000
