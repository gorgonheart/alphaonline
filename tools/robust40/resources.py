"""MODEL task resources, independent of strategy mathematics and account parameters.

Linux initializers run as the original user in a dedicated systemd service under
that user's slice, a sibling of the existing PM2 session. memory.high is a reclaim
target (fixed 4.5GiB), NOT an instantaneous RSS guarantee. memory.max
is the 5GiB last resort; an OOM fails initialization rather than installing it.
Windows/offline calls retain normal process execution; they do not claim Linux
memory enforcement. No trading process or upstream producer is moved or stopped.
"""
import os
import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import threading
import time

from .initialization_config import RESOURCE_POLICY as POLICY

UNIT_ENV = 'ROBUST40_RESOURCE_UNIT'
CGROUP_ROOT = Path('/sys/fs/cgroup')
PREFIXES = ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_')


def configure_process(role):
    """Called before config/native libraries, so spawned threads/children inherit."""
    if role not in ('prepare', 'initialize', 'rebuild', 'compute', 'trade'):
        return
    if sys.platform == 'linux':
        priority = POLICY['trading_nice' if role == 'trade' else 'initialization_nice']
        # Do not attempt a privileged priority increase if an operator already
        # launched this worker with an even lower scheduling priority.
        os.setpriority(os.PRIO_PROCESS, 0, max(priority, os.getpriority(os.PRIO_PROCESS, 0)))
    for name in ('POLARS_MAX_THREADS', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = os.environ.get('PMT_STAGE_POLARS_THREADS', '1') if name == 'POLARS_MAX_THREADS' else '1'


def is_foreground(arguments):
    """Recognize actual calculation/trading workers across both installations."""
    for i, arg in enumerate(arguments):
        name = str(arg).replace('\\', '/').rsplit('/', 1)[-1]
        if name == 'account_exec.py':
            return True
        if name == 'server_robust40.py' and i+1 < len(arguments) and arguments[i+1] == 'trade':
            return True
    return False


def foreground_running(now=None, max_age_seconds=None):
    import psutil
    current = time.time() if now is None else float(now)
    max_age = POLICY.get('foreground_max_age_seconds', 3600) if max_age_seconds is None else max_age_seconds
    for process in psutil.process_iter(['cmdline', 'create_time']):
        try:
            if not is_foreground(process.info['cmdline'] or []):
                continue
            created = process.info.get('create_time')
            if max_age and created is not None and (current - float(created)) > max_age:
                continue
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def window_budget(now, market, foreground):
    """Legacy window diagnostic; no longer controls admission or memory.high."""
    period = POLICY['period_seconds']
    stamp = int(now // period)*period
    elapsed = now-stamp
    busy = foreground or elapsed < POLICY['busy_head_seconds'] or elapsed >= period-POLICY['busy_tail_seconds']
    if not busy:
        minute = (stamp//60) % 60
        folder = Path(market)/f'{minute}m'
        busy = not all((folder/f'{prefix}{stamp}.ready').is_file() for prefix in PREFIXES)
    return ('busy', POLICY['busy_memory_bytes']) if busy else ('exclusive', POLICY['exclusive_memory_bytes'])


def initialization_command(command, stamp, root=None):
    """Create a per-job group; sudo is noninteractive and Python stays unprivileged."""
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if sys.platform != 'linux':
        return command, env, None
    uid, gid = os.getuid(), os.getgid()
    unit = f'robust40-init-{os.getpid()}-{int(stamp)}.service'
    env[UNIT_ENV] = unit
    wrapper = ['sudo', '-n', 'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
               '--service-type=exec', f'--unit={unit}', f'--uid={uid}', f'--gid={gid}',
               f'--slice=user-{uid}.slice', f'--working-directory={Path(root or Path(__file__).resolve().parents[2])}',
               f'--property=CPUWeight={POLICY["initialization_cpu_weight"]}',
               f'--property=MemoryHigh={POLICY["exclusive_memory_bytes"]-POLICY["supervisor_reserve_bytes"]}',
               f'--property=MemoryMax={POLICY["max_memory_bytes"]-POLICY["supervisor_reserve_bytes"]}', '--property=OOMPolicy=stop',
               f'--property=MemorySwapMax={POLICY["swap_max_bytes"]}',
               f'--setenv={UNIT_ENV}={unit}', '--setenv=PYTHONIOENCODING=utf-8']
    # Only functional environment, never credentials or the parent's account profile.
    for key in ('PYTHONPATH', 'NUMBA_CACHE_DIR'):
        if key in env:
            wrapper.append(f'--setenv={key}={env[key]}')
    return wrapper + command, env, unit


def _systemctl(*arguments):
    return subprocess.run(['sudo', '-n', 'systemctl', *arguments], check=True,
                          capture_output=True, text=True, timeout=10)


def current_scope():
    unit = os.environ[UNIT_ENV]
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            relative = Path(line[3:].lstrip('/'))
            if relative.name == unit and '..' not in relative.parts:
                path = CGROUP_ROOT/relative
                if (path/'memory.high').is_file():
                    return path
    raise RuntimeError('Initializer is not inside its expected memory-controlled systemd unit')


def _pressure(path):
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return 0.
    line = next((v for v in lines if v.startswith('full ')), '')
    return float(dict(item.split('=') for item in line.split()[1:]).get('avg10', 0.))


def pressure_snapshot():
    import psutil
    return dict(system_available=psutil.virtual_memory().available,
                system_swap_free=psutil.swap_memory().free,
                memory_full=_pressure('/proc/pressure/memory'),
                io_full=_pressure('/proc/pressure/io'))


def admission_decision(sample, *, foreground=False, dynamic_yield=False):
    """Only defer NEW bounded work under actual pressure, not worker existence."""
    available = sample['system_available']
    if available < POLICY['admission_min_available_bytes']:
        return False, 'ram_reserve'
    if available < POLICY['pressure_available_bytes']:
        if sample['system_swap_free'] < POLICY['swap_reserve_bytes']:
            return False, 'swap_reserve'
        if sample['memory_full'] >= POLICY['memory_full_limit']:
            return False, 'memory_pressure'
        if sample['io_full'] >= POLICY['io_full_limit']:
            return False, 'io_pressure'
    if dynamic_yield and foreground:
        if available < POLICY.get('yield_gate_available_bytes', int(1.2 * 1024**3)):
            return False, 'foreground_yield_ram'
        if sample['memory_full'] >= POLICY.get('yield_gate_memory_full', 10.):
            return False, 'foreground_yield_memory'
        if sample['io_full'] >= POLICY.get('yield_gate_io_full', 15.):
            return False, 'foreground_yield_io'
    return True, 'available'


def select_resource_mode(now, system, foreground, prev_mode, idle_since, policy=None):
    """Pure state machine for BURST <-> YIELD CPU/IO governance (never shrinks MemoryHigh).

    Returns ``(mode, reason, next_idle_since)`` where ``mode`` is ``'burst'`` or ``'yield'``.
    """
    pol = POLICY if policy is None else policy
    period = pol.get('period_seconds', 300)
    preempt = pol.get('yield_preempt_seconds', 25)
    elapsed = now % period
    in_preempt = preempt > 0 and elapsed >= (period - preempt)

    yield_reason = None
    avail = system['system_available']
    tight_ram = avail < pol.get('yield_gate_available_bytes', int(1.2 * 1024**3))
    if foreground:
        yield_reason = 'foreground_active'
    elif in_preempt:
        yield_reason = 'preempt_window'
    elif avail < pol.get('pressure_available_bytes', 1024**3):
        # Genuine system-wide RAM shortage (< 1.0 GiB) yields even when idle.
        yield_reason = 'low_available_ram'
    elif tight_ram and system['memory_full'] >= pol.get('yield_gate_memory_full', 10.):
        yield_reason = 'memory_pressure'
    elif tight_ram and system['io_full'] >= pol.get('yield_gate_io_full', 15.):
        # Only yield on IO pressure when RAM is also tight; otherwise idle initializer's
        # own IPC block reads/writes would self-throttle to 20% CPU quota.
        yield_reason = 'io_pressure'

    if yield_reason is not None:
        return 'yield', yield_reason, None

    # No immediate yield trigger; check burst headroom and hysteresis hold.
    if system['system_available'] < pol.get('burst_min_available_bytes', int(1.5 * 1024**3)):
        return ('yield' if prev_mode != 'burst' else 'burst'), 'headroom_hysteresis', None

    if prev_mode == 'yield':
        hold = pol.get('burst_resume_hold_seconds', 6)
        started_idle = now if idle_since is None else idle_since
        if (now - started_idle) < hold:
            return 'yield', 'resume_hold', started_idle

    return 'burst', 'idle_exclusive', None


def apply_mode_properties(unit, mode, policy=None, ctl=None):
    """Apply runtime cgroup v2 CPU/IO properties for `burst` or `yield`. Never touches Memory*."""
    pol = POLICY if policy is None else policy
    run_ctl = _systemctl if ctl is None else ctl
    if mode == 'burst':
        cpu_w = int(pol.get('burst_cpu_weight', 100))
        io_w = int(pol.get('burst_io_weight', 100))
        quota = str(pol.get('burst_cpu_quota', ''))
    elif mode == 'yield':
        cpu_w = int(pol.get('yield_cpu_weight', 5))
        io_w = int(pol.get('yield_io_weight', 10))
        quota = str(pol.get('yield_cpu_quota', '20%'))
    else:
        raise ValueError(f'Unknown resource mode: {mode}')
    return run_ctl(
        'set-property', '--runtime', unit,
        f'CPUWeight={cpu_w}',
        f'IOWeight={io_w}',
        f'CPUQuota={quota}',
    )


def group_snapshot(group):
    def value(name):
        try:return int((group/name).read_text())
        except FileNotFoundError:return 0
    def fields(name):
        try:return {k:int(v) for k,v in (line.split() for line in (group/name).read_text().splitlines())}
        except FileNotFoundError:return {}
    mem = fields('memory.stat')
    return dict(memory=value('memory.current'), peak=value('memory.peak'),
                swap=value('memory.swap.current'), anon=mem.get('anon',0),
                file=mem.get('file',0), kernel=mem.get('kernel',0),
                events=fields('memory.events'),cpu_usage_usec=fields('cpu.stat').get('usage_usec',0),
                memory_full=_pressure(group/'memory.pressure'),io_full=_pressure(group/'io.pressure'))


@contextmanager
def phase(label):
    """Name the allocation site; shared sampler measures peaks between markers."""
    active = sys.platform == 'linux' and UNIT_ENV in os.environ
    started = time.perf_counter()
    def mark(event):
        # Wall-clock boundaries are useful offline/on Windows too. Only Linux
        # resource groups supply kernel memory/pressure counters.
        metrics=group_snapshot(current_scope()) if active else {}
        print('INIT_PHASE '+json.dumps(dict(event=event,phase=label,pid=os.getpid(),
            time=time.time(),elapsed_s=time.perf_counter()-started,**metrics)),flush=True)
    mark('start')
    try:yield
    finally:mark('end')


def wait_for_window():
    """Historical hook name; admits based on pressure and optional dynamic foreground yield."""
    if sys.platform != 'linux' or UNIT_ENV not in os.environ:
        return
    started = None
    reason = None
    dynamic = bool(POLICY.get('dynamic_burst_enabled', False))
    while True:
        fg = foreground_running() if dynamic else False
        allowed, why = admission_decision(pressure_snapshot(), foreground=fg, dynamic_yield=dynamic)
        if allowed:
            if started is not None:
                print(f'INIT_ADMISSION event=resume pid={os.getpid()} wait_s={time.monotonic()-started:.3f}',flush=True)
            return
        if started is None:started = time.monotonic()
        if why != reason:
            print(f'INIT_ADMISSION event=wait pid={os.getpid()} reason={why}',flush=True)
            reason = why
        time.sleep(POLICY['poll_seconds'])


def watch_resources(process, unit, market, emit):
    """Observe pressure outside the group; never shrink its limit on a clock edge.

    ``market`` remains in the controller CLI contract, not in resource admission.
    Existing ready validation still runs before freezing the initialization input.
    """
    if unit is None:
        return None
    def watch():
        group = None
        try:
            # systemd-run may still be creating the unit; no property is raised yet.
            deadline = time.monotonic()+30
            while process.poll() is None and group is None:
                try:
                    value = _systemctl('show', unit, '--property=ControlGroup', '--value').stdout.strip()
                    candidate = CGROUP_ROOT/value.lstrip('/')
                    if value and candidate.name == unit and (candidate/'memory.current').is_file():
                        group = candidate
                except subprocess.CalledProcessError:
                    pass
                if group is None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Initialization resource unit did not become available')
                    time.sleep(.5)
            peak = 0
            last_emit = 0.
            critical_since = None
            current_mode = None
            idle_since = None
            dynamic = bool(POLICY.get('dynamic_burst_enabled', False))
            while process.poll() is None and group is not None:
                task = group_snapshot(group)
                system = pressure_snapshot()
                peak = max(peak,task['memory'],task['peak'])
                now = time.monotonic()
                if dynamic:
                    fg = foreground_running()
                    wall_now = time.time()
                    next_mode, mode_reason, idle_since = select_resource_mode(
                        wall_now, system, fg, current_mode, idle_since, POLICY
                    )
                    if next_mode != current_mode:
                        apply_mode_properties(unit, next_mode, POLICY)
                        emit('INIT_RESOURCE_MODE ' + json.dumps(dict(
                            unit=unit, time=wall_now, prev=current_mode, mode=next_mode,
                            reason=mode_reason, foreground=fg,
                            available_mib=system['system_available'] // 2**20,
                        )))
                        current_mode = next_mode
                if now-last_emit >= POLICY['telemetry_seconds']:
                    emit('INIT_RESOURCE '+json.dumps(dict(unit=unit,time=time.time(),task=task,system=system,
                         mode=current_mode,admission=admission_decision(system))))
                    last_emit = now
                critical = (system['system_available'] < POLICY['critical_available_bytes']
                            and system['system_swap_free'] < POLICY['swap_reserve_bytes']
                            and system['memory_full'] >= POLICY['memory_full_limit'])
                critical_since = (critical_since or now) if critical else None
                if critical_since and now-critical_since >= POLICY['critical_seconds']:
                    emit(f'INIT_RESOURCE unit={unit} exhausted_ram_and_swap=true; stopping only this initializer')
                    _systemctl('stop',unit)
                    break
                time.sleep(POLICY['poll_seconds'])
            emit(f'INIT_RESOURCE unit={unit} event=end peak_mib={peak//2**20}')
        except Exception as exc:
            # No silent unbounded fallback if resource management loses control.
            if process.poll() is None:
                emit(f'INIT_RESOURCE unit={unit} control_failed={exc}; stopping only this initializer')
                try:
                    _systemctl('stop', unit)
                except Exception as stop_error:
                    emit(f'INIT_RESOURCE unit={unit} stop_failed={stop_error}')
    thread = threading.Thread(target=watch, daemon=True, name='initialization-resources')
    thread.start()
    return thread


def supervisor_command(command, stamp, root, market):
    """The small controller survives startup exits; Windows retains offline flow."""
    if sys.platform != 'linux':
        return command
    return [sys.executable, '-m', 'tools.robust40.resources', '--stamp', str(stamp),
            '--root', str(root), '--market', str(market), '--', *command]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stamp', type=int, required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--market', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:parser.error('Missing initialization command')
    configure_process('initialize')
    # The two installations share physical RAM. One full initializer globally;
    # queued supervisors are small and hold no market/state arrays.
    with queued_initialization_account(command), global_initialization_slot():
        command, env, unit = initialization_command(command,args.stamp,args.root)
        process = subprocess.Popen(command,env=env,cwd=args.root)
        controller = watch_resources(process,unit,args.market,lambda message:print(message,flush=True))
        code = process.wait()
        if controller:controller.join(timeout=3)
        return code


@contextmanager
def queued_initialization_account(command):
    """Same lock path as Coordinator; keep ownership while waiting for global RAM.

    Kept stdlib-only: a queued supervisor must not import pandas/MODEL.
    The compute worker still owns the distinct .initialization/snapshot locks.
    """
    if 'rebuild' not in command:
        yield;return
    destination=Path(command[command.index('--destination')+1]).resolve()
    folder=destination.parent.parent/'server_account_locks'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/(destination.name+'.dispatch.lock')).open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


@contextmanager
def global_initialization_slot():
    if sys.platform != 'linux':
        yield
        return
    import fcntl
    runtime = Path('/run/user')/str(os.getuid())
    # The directory is owned by the user manager; do not invent a public /tmp lock.
    with (runtime/'alphatrade-initialization.lock').open('a+b') as handle:
        print('INIT_RESOURCE waiting_for_global_initialization_slot', flush=True)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    sys.exit(main())
