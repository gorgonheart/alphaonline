"""Offline ABBA benchmark + exact parity check for Phase 3 (P3-1) Step 6 Pivot sharing.

Copies existing strategy ratio/snapshot files into a temporary directory under
`<repo>/test/p3-abba-<ts>/` (read-only on live `data/`), runs all strategies
through 4-worker ProcessPoolExecutor in ABBA order comparing:
  - A (baseline): uncached per-strategy pivot read (old step6_simulate_performance)
  - B (P3-1):     fork-preloaded shared read-only market_inputs (1 read total)
and verifies `check_exact=True` parity across all strategies before auto-cleaning.
"""
import gc
import multiprocessing
import os
import resource
import shutil
import socket
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _block_network():
    def _blocked(*args, **kwargs):
        raise RuntimeError("Offline benchmark forbids network I/O")

    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.socket.connect = _blocked  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked  # type: ignore[method-assign]


def _read_rss_mib() -> float:
    with open("/proc/self/status", encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) / 1024.0
    return 0.0


def _read_vm_hwm_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _child_maxrss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0


def _baseline_worker(conf, run_time):
    """Exact pre-P3-1 step6_simulate_performance behavior (no cross-strategy sharing)."""
    from core import backtest

    return backtest.step6_simulate_performance(conf, run_time, market_inputs=None)


def _p3_worker(conf, run_time):
    from core import backtest

    return backtest._step6_worker(conf, run_time)


class _ClonedConf:
    def __init__(self, conf, res_dst: Path, snap_dst: Path):
        self.name = conf.name
        self.timing = conf.timing
        self.strategy_list = conf.strategy_list
        self.incremental_lookback_hours = conf.incremental_lookback_hours
        self.checkpoint_hours = getattr(conf, "checkpoint_hours", 0)
        self.initial_usdt = conf.initial_usdt
        self.is_day_period = conf.is_day_period
        self.leverage = conf.leverage
        self.margin_rate = conf.margin_rate
        self.spot_c_rate = conf.spot_c_rate
        self.swap_c_rate = conf.swap_c_rate
        self.spot_min_order_limit = conf.spot_min_order_limit
        self.swap_min_order_limit = conf.swap_min_order_limit
        self.rebalance_mode = conf.rebalance_mode
        self._res_dst = res_dst
        self._snap_dst = snap_dst
        self._fullname = conf.get_fullname(as_folder_name=True)

    def get_result_folder(self):
        return self._res_dst

    def get_snapshot_folder(self):
        return self._snap_dst

    def get_fullname(self, as_folder_name=False):
        return self._fullname


def _clone_confs(orig_confs, dst_root: Path):
    """Clone BacktestConfig result & snapshot folders into dst_root so live data/ is untouched."""
    cloned = []
    for conf in orig_confs:
        res_src = conf.get_result_folder()
        snap_src = conf.get_snapshot_folder()
        res_dst = dst_root / "results" / conf.name
        snap_dst = dst_root / "snapshots" / conf.name
        res_dst.mkdir(parents=True, exist_ok=True)
        snap_dst.mkdir(parents=True, exist_ok=True)

        for fn in ("df_spot_ratio.pkl", "df_swap_ratio.pkl"):
            src = res_src / fn
            if src.exists():
                shutil.copy2(src, res_dst / fn)
        if snap_src.exists():
            for fp in snap_src.iterdir():
                if fp.is_file():
                    shutil.copy2(fp, snap_dst / fp.name)

        cloned.append(_ClonedConf(conf, res_dst, snap_dst))
    return cloned


def _run_once(mode: str, confs, run_time, pool_workers: int):
    from core import backtest
    from core.utils.factor_data import read_vmstat_swap

    sorted_confs = sorted(confs, key=lambda c: len(c.strategy_list), reverse=True)
    _, sw_start = read_vmstat_swap()
    t0 = time.perf_counter()
    preload_s = 0.0
    equity_map = {}

    if mode == "B_p3":
        offset = f"{run_time.minute}m"
        t_pre = time.perf_counter()
        backtest._STEP6_FORK_MARKET = (offset, backtest._load_step6_market_inputs(offset))
        preload_s = time.perf_counter() - t_pre
        worker_fn = _p3_worker
    else:
        backtest._STEP6_FORK_MARKET = None
        backtest._STEP6_WORKER_MARKET = None
        worker_fn = _baseline_worker

    try:
        with ProcessPoolExecutor(max_workers=pool_workers) as ex:
            futs = {ex.submit(worker_fn, c, run_time): c for c in sorted_confs}
            for f in as_completed(futs):
                c = futs[f]
                equity_map[c.name] = f.result()
    finally:
        backtest._STEP6_FORK_MARKET = None
        backtest._STEP6_WORKER_MARKET = None
        gc.collect()
        backtest._trim_process_heap()

    elapsed_s = time.perf_counter() - t0
    _, sw_end = read_vmstat_swap()
    return {
        "mode": mode,
        "elapsed_s": elapsed_s,
        "preload_s": preload_s,
        "swap_out_pages": max(0, sw_end - sw_start),
        "self_rss_mib": _read_rss_mib(),
        "child_hwm_mib": _child_maxrss_mib(),
        "equity_map": equity_map,
    }


def main():
    _block_network()
    account_name = sys.argv[1] if len(sys.argv) > 1 else "25m-eart1106"
    os.environ["X3S_TRADING_ACCOUNT"] = f"{account_name}.py"

    import config
    from core.account_manager import init_mef_system

    me_conf = init_mef_system(f"{account_name}.py")
    orig_confs = me_conf.factory.config_list
    pool_workers = min(len(orig_confs), getattr(config, "backtest_job_num", 4))

    # Infer run_time from latest bar in first strategy's df_swap_ratio.pkl
    sample_ratio = pd.read_pickle(orig_confs[0].get_result_folder() / "df_swap_ratio.pkl")
    last_bar_utc = pd.Timestamp(sample_ratio.index.max(), tz="UTC")
    run_time = (last_bar_utc + pd.Timedelta(hours=1) + pd.Timedelta(hours=config.utc_offset)).tz_localize(None)
    print(
        f"Benchmark P3-1 Step 6 | account={account_name} strategies={len(orig_confs)} "
        f"workers={pool_workers} start_method={multiprocessing.get_context().get_start_method()} "
        f"run_time={run_time}"
    )

    work_root = REPO_ROOT / "test" / f"p3-abba-{int(time.time())}"
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        seq = ["A_base", "B_p3", "B_p3", "A_base"]
        results = []
        ref_a = None
        ref_b = None
        for idx, mode in enumerate(seq):
            run_dir = work_root / f"run_{idx}_{mode}"
            confs = _clone_confs(orig_confs, run_dir)
            r = _run_once(mode, confs, run_time, pool_workers)
            print(
                f"  [{idx}] {mode:6s}: elapsed={r['elapsed_s']:.3f}s "
                f"(preload={r['preload_s']:.3f}s) swap_out={r['swap_out_pages']} "
                f"self_rss={r['self_rss_mib']:.0f}MiB child_hwm={r['child_hwm_mib']:.0f}MiB"
            )
            results.append(r)
            if mode == "A_base" and ref_a is None:
                ref_a = r["equity_map"]
            if mode == "B_p3" and ref_b is None:
                ref_b = r["equity_map"]

        # Exact parity check across ALL 14 strategies
        assert ref_a is not None and ref_b is not None
        assert set(ref_a.keys()) == set(ref_b.keys())
        for name in sorted(ref_a.keys()):
            pd.testing.assert_frame_equal(ref_b[name], ref_a[name], check_exact=True)
        print(f"PARITY: ALL {len(ref_a)}/{len(ref_a)} strategies check_exact=True PASSED!")

        a_times = [r["elapsed_s"] for r in results if r["mode"] == "A_base"]
        b_times = [r["elapsed_s"] for r in results if r["mode"] == "B_p3"]
        a_med = sum(a_times) / len(a_times)
        b_med = sum(b_times) / len(b_times)
        print(
            f"SUMMARY: A_base mean={a_med:.3f}s ({a_times}) | "
            f"B_p3 mean={b_med:.3f}s ({b_times}) | "
            f"delta={b_med - a_med:+.3f}s ({(b_med - a_med) / a_med * 100:+.1f}%)"
        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    main()
