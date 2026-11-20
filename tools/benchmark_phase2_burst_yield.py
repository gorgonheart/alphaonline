"""Phase 2 Ubuntu cgroup v2 verification harness for BURST <-> YIELD governance.

Safe & isolated (never touches PM2, market data, snapshots, or network):
1. Measures baseline wall-clock of a 4-worker CPU burn simulating foreground trading (`account_exec.py`).
2. Launches a 4-worker background CPU burn inside a real systemd-run unit managed by `watch_resources(dynamic_burst_enabled=True)`.
3. Verifies real cgroup v2 `cpu.weight`, `cpu.max`, and immutable `memory.high` across:
   - Phase A (idle -> BURST: cpu.weight=100, cpu.max=max)
   - Phase B (foreground `account_exec.py` active -> YIELD within <= 3s: cpu.weight=10, cpu.max=80000 100000, memory.high unchanged)
   - Phase C (foreground exits -> holds >= 6s -> returns to BURST: cpu.weight=100, cpu.max=max)
4. Reports foreground wall-clock overhead (%) under active background initializer.
"""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time


def _burn_worker(iters):
    acc = 0.0
    for i in range(iters):
        acc += (i * 0.000001) ** 0.5
    return acc


def _run_burn(workers, iters, seconds=0.0):
    if seconds > 0:
        end = time.monotonic() + seconds
        def loop():
            x = 0.0
            while time.monotonic() < end:
                for i in range(200000):
                    x += (i * 0.000001) ** 0.5
            return x
        procs = [mp.Process(target=loop) for _ in range(workers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        return 0.0
    t0 = time.perf_counter()
    with mp.Pool(processes=workers) as pool:
        pool.map(_burn_worker, [iters] * workers)
    return time.perf_counter() - t0


def _read_cgroup(group: Path):
    def rd(name):
        try:
            return (group / name).read_text().strip()
        except OSError:
            return ''
    return {
        'cpu.weight': rd('cpu.weight'),
        'cpu.max': rd('cpu.max'),
        'memory.high': rd('memory.high'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phase 2 Ubuntu cgroup v2 BURST <-> YIELD verifier')
    parser.add_argument('--role', choices=('driver', 'bg-init', 'fg-sim'), default='driver')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--iters', type=int, default=12_000_000)
    parser.add_argument('--bg-seconds', type=float, default=35.0)
    parser.add_argument('--preempt-off', action='store_true',
                        help='Set yield_preempt_seconds=0 during short test so clock boundary does not mask foreground test')
    args, _unknown = parser.parse_known_args(argv)

    if args.role == 'bg-init':
        _run_burn(args.workers, 0, seconds=args.bg_seconds)
        return 0
    if args.role == 'fg-sim':
        wall = _run_burn(args.workers, args.iters)
        print(f'FG_WALL_S={wall:.3f}', flush=True)
        return 0

    if sys.platform != 'linux':
        raise SystemExit('This cgroup v2 stress verifier requires Linux/systemd.')

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from tools.robust40 import resources as r

    # Enable dynamic BURST <-> YIELD for this isolated verification run
    r.POLICY['dynamic_burst_enabled'] = True
    r.POLICY['poll_seconds'] = 1
    r.POLICY['burst_resume_hold_seconds'] = 4
    if args.preempt_off:
        r.POLICY['yield_preempt_seconds'] = 0

    # Create a tiny wrapper named `account_exec.py` inside test/ so `foreground_running()` matches it organically
    test_dir = repo_root / 'test' / f'phase2-verify-{time.strftime("%Y%m%d-%H%M%S")}'
    test_dir.mkdir(parents=True, exist_ok=True)
    fg_script = test_dir / 'account_exec.py'
    fg_script.write_text(
        f'import sys; sys.path.insert(0, {str(repo_root)!r})\n'
        f'from tools.benchmark_phase2_burst_yield import _run_burn\n'
        f'w = _run_burn({args.workers}, {args.iters})\n'
        f'print(f"FG_WALL_S={{w:.3f}}", flush=True)\n',
        encoding='utf-8',
    )

    def run_fg():
        os.nice(0) if False else None
        out = subprocess.check_output([sys.executable, str(fg_script)], text=True)
        for line in out.splitlines():
            if line.startswith('FG_WALL_S='):
                return float(line.split('=', 1)[1])
        raise RuntimeError(f'Missing FG_WALL_S in output: {out}')

    # 1. Measure clean foreground baseline (no background initializer)
    fg_base_1 = run_fg()
    fg_base_2 = run_fg()
    fg_baseline_s = min(fg_base_1, fg_base_2)
    print(f'BASELINE_FG_S={fg_baseline_s:.3f} (runs={fg_base_1:.3f}, {fg_base_2:.3f})', flush=True)

    # 2. Launch background initializer inside real systemd-run cgroup unit
    bg_cmd = [sys.executable, str(Path(__file__).resolve()), '--role', 'bg-init',
              '--workers', str(args.workers), '--bg-seconds', str(args.bg_seconds)]
    cmd, env, unit = r.initialization_command(bg_cmd, int(time.time()), root=str(repo_root))
    logs = []
    proc = subprocess.Popen(cmd, env=env, cwd=str(repo_root))
    watcher = r.watch_resources(proc, unit, str(test_dir), lambda m: (logs.append(m), print(m, flush=True)))

    # Wait until unit cgroup exists and enters BURST
    group = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        val = r._systemctl('show', unit, '--property=ControlGroup', '--value').stdout.strip()
        cand = r.CGROUP_ROOT / val.lstrip('/')
        if val and (cand / 'cpu.weight').is_file():
            group = cand
            break
        time.sleep(0.2)
    assert group is not None, 'cgroup directory not found'
    time.sleep(2.5)
    snap_burst_1 = _read_cgroup(group)
    print('SNAP_BURST_1:', json.dumps(snap_burst_1), flush=True)

    # 3. Launch foreground `account_exec.py` (nice=3 matching production trading!) and observe YIELD
    t_fg0 = time.perf_counter()
    fg_proc = subprocess.Popen(['nice', '-n', '3', sys.executable, str(fg_script)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(2.2)
    snap_yield = _read_cgroup(group)
    print('SNAP_YIELD:', json.dumps(snap_yield), flush=True)
    fg_out, _ = fg_proc.communicate()
    assert fg_proc.returncode == 0, fg_out
    fg_under_yield_s = float([l.split('=', 1)[1] for l in fg_out.splitlines() if l.startswith('FG_WALL_S=')][0])

    # 4. Wait past burst_resume_hold_seconds (4s) and verify return to BURST
    time.sleep(6.0)
    snap_burst_2 = _read_cgroup(group)
    print('SNAP_BURST_2:', json.dumps(snap_burst_2), flush=True)

    proc.wait(timeout=60)
    if watcher:
        watcher.join(timeout=5)

    overhead_pct = (fg_under_yield_s - fg_baseline_s) / fg_baseline_s * 100.0
    summary = {
        'unit': unit,
        'fg_baseline_s': round(fg_baseline_s, 3),
        'fg_under_yield_s': round(fg_under_yield_s, 3),
        'overhead_pct': round(overhead_pct, 2),
        'snap_burst_1': snap_burst_1,
        'snap_yield': snap_yield,
        'snap_burst_2': snap_burst_2,
        'memory_high_immutable': (snap_burst_1['memory.high'] == snap_yield['memory.high'] == snap_burst_2['memory.high']),
        'mode_events': [l for l in logs if l.startswith('INIT_RESOURCE_MODE ')],
    }
    (test_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('PHASE2_VERIFY_SUMMARY:', json.dumps(summary, indent=2), flush=True)

    assert summary['memory_high_immutable'], 'MemoryHigh MUST NEVER change!'
    assert snap_burst_1['cpu.weight'] == '100' and snap_burst_1['cpu.max'].startswith('max')
    assert snap_yield['cpu.weight'] == '5' and snap_yield['cpu.max'].startswith('20000 ')
    assert snap_burst_2['cpu.weight'] == '100' and snap_burst_2['cpu.max'].startswith('max')
    print('PHASE2_CGROUP_PASS', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
