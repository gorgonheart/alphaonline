"""Phase 0 offline frozen-input ABBA benchmark harness for FACTOR_BLOCKS.

Strictly offline and read-only against market/snapshot data:
- Blocks all outbound network sockets inside child runs.
- Writes scratch IPC blocks and summary reports ONLY under `<repo_root>/test/phase0-abba-<ts>/`.
- Cleans up temporary IPC blocks immediately after each run (and parity check).
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time


def build_abba_order(label_a, label_b, repeats=1):
    """Return balanced ABBA sequence repeated `repeats` times."""
    if repeats < 1:
        raise ValueError('repeats must be >= 1')
    return [label_a, label_b, label_b, label_a] * repeats


def parse_profile_line(line):
    """Parse `FACTOR_BLOCKS_PROFILE k=v ...` or `AGG_PROFILE k=v ...` into a dict."""
    parts = line.strip().split()
    if len(parts) < 2:
        return {}
    result = {'tag': parts[0]}
    for token in parts[1:]:
        if '=' not in token:
            continue
        key, raw = token.split('=', 1)
        if raw in ('True', 'False'):
            result[key] = raw == 'True'
            continue
        try:
            result[key] = int(raw) if raw.lstrip('-').isdigit() else float(raw)
        except ValueError:
            result[key] = raw
    return result


def summarize_runs(runs):
    """Aggregate per-label medians for wall_s, cpu_s, effective cores, RSS peak, and swap_out."""
    grouped = {}
    for item in runs:
        grouped.setdefault(item['label'], []).append(item)
    summary = {}
    for label, items in grouped.items():
        walls = [float(x['wall_s']) for x in items]
        cpus = [float(x['cpu_s']) for x in items]
        rss = [float(x['rss_peak_mib']) for x in items]
        swaps = [int(x.get('swap_out_pages', 0)) for x in items]
        eff_cores = [c / w if w > 0 else 0.0 for c, w in zip(cpus, walls)]
        summary[label] = {
            'count': len(items),
            'median_wall_s': statistics.median(walls),
            'min_wall_s': min(walls),
            'max_wall_s': max(walls),
            'median_cpu_s': statistics.median(cpus),
            'median_eff_cores': statistics.median(eff_cores),
            'median_rss_peak_mib': statistics.median(rss),
            'max_swap_out_pages': max(swaps),
        }
    return summary


def _read_vmstat_swap():
    from core.utils.factor_data import read_vmstat_swap
    return read_vmstat_swap()


def _read_psi(path):
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return 0.0
    line = next((v for v in lines if v.startswith('full ')), '')
    fields = dict(item.split('=') for item in line.split()[1:] if '=' in item)
    return float(fields.get('avg10', 0.0))


def _block_network():
    def _deny(*args, **kwargs):
        raise RuntimeError('Network access is strictly forbidden in offline Phase 0 benchmark')
    socket.socket.connect = _deny
    socket.create_connection = _deny


def _run_child_factor_blocks(args):
    """Child process entrypoint: run _calc_factor_blocks on frozen market input."""
    _block_network()
    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ['X3S_TRADING_ACCOUNT'] = str(Path(args.account).resolve())
    for env_key in ('POLARS_MAX_THREADS', 'PMT_STAGE_POLARS_THREADS', 'OMP_NUM_THREADS',
                    'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[env_key] = '1'

    import pandas as pd
    import core.utils.path_kit as path_kit
    path_kit.PROJECT_ROOT = str(repo_root)
    import config
    from core.backtest import apply_bstocks_black_list
    from core.select_coin import calc_factors
    from core.utils.factor_data import FactorBlocks
    from tools.robust40.market_inputs import public_market_client
    from core.utils import exchange_hub

    from types import SimpleNamespace
    if args.bmac_path:
        config.bmac_data_path = Path(args.bmac_path).resolve()
    from tools.robust40.initialization_config import LIVE_BARS
    config.get_kline_num = LIVE_BARS
    config.data_config['get_kline_num'] = LIVE_BARS
    config.job_num = int(args.job_num)
    config.factor_symbol_batch_size = int(args.batch_size)
    config.factor_async_write = bool(args.async_write)
    config.factor_reorder_buffer = bool(args.reorder_buffer)
    config.shared_factor_pipeline = True
    from core.utils import datatools as _datatools
    offline_dummy = SimpleNamespace(get_market_info=lambda kind: {})
    exchange_hub.get_dummy_client = lambda: offline_dummy
    _datatools.get_dummy_client = lambda: offline_dummy
    exchange_hub.get_base_client_class().bstocks_coins = set()

    from core.model.backtest_config import BacktestConfigFactory
    factory = BacktestConfigFactory.init()
    if getattr(config, 'strategy_pool', None):
        factory.generate_configs_by_strategies_with_timing(config.strategy_pool)
    else:
        factory.generate_configs()
    conf = factory.generate_all_factor_config()
    conf.input_mode = args.input_mode
    apply_bstocks_black_list(conf)

    from datetime import datetime
    run_time = datetime.fromisoformat(args.run_time)
    out_dir = Path(args.out_dir).resolve()
    store = FactorBlocks(out_dir / 'factors', hours=int(args.block_hours), minute=run_time.minute)
    try:
        calc_factors(conf, run_time=run_time, store=store)
        manifest = {
            'rows': store.rows,
            'files': sorted(str(p.relative_to(out_dir)) for p in (out_dir / 'factors').rglob('*.ipc')),
        }
        (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    finally:
        if not args.keep_blocks:
            store.close()


def _sample_process_tree(proc, stop_event, stats):
    import psutil
    peak_rss = 0
    seen_cpu = {}
    while not stop_event.is_set():
        try:
            root = psutil.Process(proc.pid)
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            break
        rss_sum = 0
        for p in procs:
            try:
                mem = p.memory_info()
                rss_sum += mem.rss
                ct = p.cpu_times()
                seen_cpu[p.pid] = ct.user + ct.system
            except psutil.Error:
                continue
        if rss_sum > peak_rss:
            peak_rss = rss_sum
        stop_event.wait(0.2)
    stats['rss_peak_mib'] = peak_rss / (1024 ** 2)
    stats['cpu_s'] = sum(seen_cpu.values())


def verify_block_parity(dir_a, dir_b, rtol=0.0, atol=0.0):
    """Verify exact filename, schema, row-order, and value parity of all IPC blocks."""
    import numpy as np
    import polars as pl
    path_a = Path(dir_a) / 'factors'
    path_b = Path(dir_b) / 'factors'
    files_a = sorted(p.relative_to(path_a) for p in path_a.rglob('*.ipc'))
    files_b = sorted(p.relative_to(path_b) for p in path_b.rglob('*.ipc'))
    if files_a != files_b:
        raise AssertionError(f'IPC file list mismatch: {len(files_a)} vs {len(files_b)}')
    total_rows = 0
    max_abs_diff = 0.0
    for rel in files_a:
        fa = pl.read_ipc(path_a / rel, memory_map=False)
        fb = pl.read_ipc(path_b / rel, memory_map=False)
        if set(fa.columns) != set(fb.columns) or fa.height != fb.height:
            raise AssertionError(f'IPC schema/shape mismatch in {rel}')
        fb = fb.select(fa.columns)
        if fa.dtypes != fb.dtypes:
            raise AssertionError(f'IPC dtype mismatch in {rel}')
        total_rows += fa.height
        if rtol == 0.0 and atol == 0.0:
            if not fa.equals(fb, null_equal=True):
                for col in fa.columns:
                    if not fa[col].equals(fb[col], null_equal=True):
                        raise AssertionError(f'IPC exact mismatch in {rel} col={col}')
        else:
            for col in fa.columns:
                if fa[col].equals(fb[col], null_equal=True):
                    continue
                if fa[col].dtype in (pl.Float32, pl.Float64):
                    va = fa[col].to_numpy()
                    vb = fb[col].to_numpy()
                    if not np.allclose(va, vb, rtol=rtol, atol=atol, equal_nan=True):
                        diff = np.nanmax(np.abs(va - vb))
                        raise AssertionError(f'IPC float mismatch in {rel} col={col} max_diff={diff}')
                    finite = np.isfinite(va) & np.isfinite(vb)
                    if finite.any():
                        max_abs_diff = max(max_abs_diff, float(np.max(np.abs(va[finite] - vb[finite]))))
                else:
                    raise AssertionError(f'IPC non-float mismatch in {rel} col={col}')
    return {'files': len(files_a), 'rows': total_rows, 'max_abs_diff': max_abs_diff}


def run_single_trial(repo_root, account, run_time, label, *, job_num, batch_size, block_hours,
                     input_mode, bmac_path, work_root, keep_blocks=False,
                     async_write=True, reorder_buffer=True):
    out_dir = work_root / f'run_{label}_{int(time.time() * 1000)}'
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(Path(__file__).resolve()), '--child',
        '--repo-root', str(repo_root),
        '--account', str(account),
        '--run-time', str(run_time),
        '--job-num', str(job_num),
        '--batch-size', str(batch_size),
        '--block-hours', str(block_hours),
        '--input-mode', str(input_mode),
        '--out-dir', str(out_dir),
    ]
    if bmac_path:
        cmd.extend(['--bmac-path', str(bmac_path)])
    if keep_blocks:
        cmd.append('--keep-blocks')
    if async_write:
        cmd.append('--async-write')
    if reorder_buffer:
        cmd.append('--reorder-buffer')

    swap_in_0, swap_out_0 = _read_vmstat_swap()
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    stop_event = threading.Event()
    tree_stats = {'rss_peak_mib': 0.0, 'cpu_s': 0.0}
    sampler = threading.Thread(target=_sample_process_tree, args=(proc, stop_event, tree_stats), daemon=True)
    sampler.start()
    stdout, _ = proc.communicate()
    stop_event.set()
    sampler.join(timeout=2.0)
    wall_s = time.perf_counter() - t0
    swap_in_1, swap_out_1 = _read_vmstat_swap()

    profile = {}
    for line in stdout.splitlines():
        if 'FACTOR_BLOCKS_PROFILE ' in line:
            idx = line.index('FACTOR_BLOCKS_PROFILE ')
            profile = parse_profile_line(line[idx:])

    (out_dir / 'stdout.log').write_text(stdout, encoding='utf-8')
    if proc.returncode != 0:
        tail = '\n'.join(stdout.splitlines()[-25:])
        raise RuntimeError(f'Trial {label} failed (rc={proc.returncode}); see {out_dir / "stdout.log"}\n{tail}')

    result = {
        'label': label,
        'job_num': job_num,
        'batch_size': batch_size,
        'block_hours': block_hours,
        'wall_s': round(wall_s, 3),
        'cpu_s': round(tree_stats['cpu_s'], 3),
        'rss_peak_mib': round(tree_stats['rss_peak_mib'], 1),
        'swap_in_pages': max(0, swap_in_1 - swap_in_0),
        'swap_out_pages': max(0, swap_out_1 - swap_out_0),
        'psi_mem_full': _read_psi('/proc/pressure/memory'),
        'psi_io_full': _read_psi('/proc/pressure/io'),
        'profile': profile,
        'out_dir': str(out_dir),
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phase 0 offline frozen-input ABBA benchmark for FACTOR_BLOCKS')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repo-root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--account', required=True, help='Path to account config .py file')
    parser.add_argument('--run-time', required=True, help='Frozen run_time timestamp, e.g. "2026-11-06 11:25:00"')
    parser.add_argument('--bmac-path', default='', help='Optional override path to frozen bmac data directory')
    parser.add_argument('--input-mode', default='arrow_strict', choices=('arrow_strict', 'arrow_full_strict'))
    parser.add_argument('--job-num', type=int, default=4)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--batch-size-b', type=int, default=0, help='Optional second batch size for ABBA sweep')
    parser.add_argument('--block-hours', type=int, default=2016)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--out-dir', default='')
    parser.add_argument('--keep-blocks', action='store_true')
    parser.add_argument('--async-write', action='store_true')
    parser.add_argument('--reorder-buffer', action='store_true')
    parser.add_argument('--compare-async', action='store_true',
                        help='Run ABBA comparing A=(sync, no reorder) vs B=(async writer + reorder) at --batch-size')
    parser.add_argument('--verify-parity', action='store_true',
                        help='Verify exact byte-for-byte IPC parity between first A and first B run, then clean blocks')
    args = parser.parse_args(argv)

    if args.child:
        _run_child_factor_blocks(args)
        return 0

    repo_root = Path(args.repo_root).resolve()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    work_root = repo_root / 'test' / f'phase0-abba-{stamp}'
    work_root.mkdir(parents=True, exist_ok=True)

    if args.compare_async:
        configs = {
            'A': {'job_num': args.job_num, 'batch_size': args.batch_size, 'async_write': False, 'reorder_buffer': False},
            'B': {'job_num': args.job_num, 'batch_size': args.batch_size, 'async_write': True, 'reorder_buffer': True},
        }
        order = build_abba_order('A', 'B', repeats=args.repeats)
    else:
        configs = {'A': {'job_num': args.job_num, 'batch_size': args.batch_size,
                         'async_write': args.async_write, 'reorder_buffer': args.reorder_buffer}}
        if args.batch_size_b > 0:
            configs['B'] = {'job_num': args.job_num, 'batch_size': args.batch_size_b,
                            'async_write': args.async_write, 'reorder_buffer': args.reorder_buffer}
            order = build_abba_order('A', 'B', repeats=args.repeats)
        else:
            order = ['A'] * max(1, args.repeats)

    runs = []
    parity_dirs = {}
    parity_result = None
    for label in order:
        cfg = configs[label]
        need_keep = args.keep_blocks or (args.verify_parity and label not in parity_dirs)
        res = run_single_trial(
            repo_root, args.account, args.run_time, label,
            job_num=cfg['job_num'], batch_size=cfg['batch_size'],
            block_hours=args.block_hours, input_mode=args.input_mode,
            bmac_path=args.bmac_path, work_root=work_root, keep_blocks=need_keep,
            async_write=cfg['async_write'], reorder_buffer=cfg['reorder_buffer'],
        )
        runs.append(res)
        print(json.dumps(res, ensure_ascii=False), flush=True)
        if args.verify_parity and label not in parity_dirs:
            parity_dirs[label] = Path(res['out_dir'])
            if 'A' in parity_dirs and 'B' in parity_dirs and parity_result is None:
                parity_result = verify_block_parity(parity_dirs['A'], parity_dirs['B'])
                print(f'PARITY_OK {json.dumps(parity_result)}', flush=True)
                if not args.keep_blocks:
                    for d in parity_dirs.values():
                        shutil.rmtree(d / 'factors', ignore_errors=True)
        elif not args.keep_blocks:
            shutil.rmtree(Path(res['out_dir']) / 'factors', ignore_errors=True)

    summary = summarize_runs(runs)
    report = {'configs': configs, 'order': order, 'runs': runs, 'summary': summary, 'parity': parity_result}
    report_path = work_root / 'summary.json'
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'PHASE0_ABBA_SUMMARY path={report_path}')
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
