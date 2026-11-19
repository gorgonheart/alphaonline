# CLAUDE.md

Current cross-project unified entry point: [统一开发入口与全局摘要](docs/README.md)（专题知识库见 `docs/kb/`，逐条流水日志见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)）. Read it first; it reflects the current 2.3.3 / 1.1.12 baseline and supersedes historical architecture descriptions below.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

仓位管理实盘框架 (position-management live-trading framework) by 邢不行. A multi-strategy crypto quant system that runs a backtest every 5 minutes against fresh market data, aggregates sub-strategy equity curves into target position ratios, then places spot/swap orders on Binance. Backtest and live trading share the same code path — live trading is "backtest up to now, then trade the latest period."

## Running

Framework version: **2.3.1 (20260911)** with local Arrow/precompute/parallel/dry_run extensions. See `docs/superpowers/specs/2026-10-14-upgrade-core-2.3.1.md` for merge decisions and cross-project regression results. `requirements.txt` pins the validated `xbx-py11==0.1.23` dependency bundle.

The Python environment is the `alpha311` conda env (the `alpha` env fails on `numpy._core` for newly-pickled files). There is no build step or lint config — run scripts directly with the interpreter. A pytest suite lives in `tests/` (see "Tests" below).

```powershell
# Live/main loop (runs forever, every 5 min). Mode is controlled by config, not CLI.
python startup.py

# Single account run for one timestamp (what startup.py spawns per account via subprocess)
$env:X3S_TRADING_ACCOUNT = "_55mBTC样例.py"; python core/account_exec.py <unix_timestamp>
```

Run mode is set by `is_simulate` in [config.py](config.py) (NOT a CLI flag):
- `debug` → `is_debug=True`, prints orders and exits, no real trading
- `simulate` → debug + runs the simulator to produce an equity curve
- `dry_run` → full pipeline but skips cancel/place orders (logs intended orders only)
- `none` → real trading

`clean_start=True` wipes `data/snapshot` on boot. Setting it `False` reuses incremental snapshots and is dangerous (warns and sleeps 7s).

## Configuration layering

Config resolves in this order (later overrides earlier), all funneled through [config.py](config.py):
1. Defaults in `config.py`
2. The account file named by `X3S_TRADING_ACCOUNT` env var, loaded from `accounts/*.py`. Only an allowlist of attrs is imported (`account_config`, `strategy_pool`, `strategy_config`, `leverage`, `black_list`, etc.) — see `allowed_attrs` in config.py.
3. `config.json` at repo root, if present (allowlist: `realtime_data_path`, `is_simulate`, `factor_col_limit`, `lookback_days`, `incremental_lookback_hours`).

Account files starting with `_` (e.g. `_模板.py`, `_55mBTC样例.py`) are templates/samples and are skipped by `load_multi_accounts()`. `startup.py` only runs an account when `run_time.minute == hour_offset` parsed from that account file.

`config.py` calls `exit()` at import time if the data center paths don't exist — the data center must be running first.

## The data center boundary (the slow part)

Market data is produced by a SEPARATE project at `realtime_data_path` (default `d:/alpha/data`, the BMAC data center) and consumed here read-only. This is the cross-process read/write that currently costs ~1 minute and is the target of the shared-memory optimization work (see `docs/superpowers/specs/`).

Key paths derived in config.py:
- `bmac_data_path = <realtime_data_path>/preprocess_1h_resample` — OHLCV + precomputed factor data, organized by minute offset subdir (`{minute}m/`)
- `bmac_exginfo_path = <realtime_data_path>/exginfo` — exchange info (weights)

Synchronization is **file-flag based**, in [core/utils/datatools.py](core/utils/datatools.py):
- Data center writes `spot_dict_{ts}.ready` / `swap_dict_{ts}.ready` flag files when a batch is done.
- `check_bmac_update_flag(run_time)` polls for those flags (gives up 4 min past run_time).
- `snapshot_bmac_exginfo()` copies exginfo pkls to local `data/cache/` at the start of each run to dodge a read/write race (`EOFError: Ran out of input`) while the data center rewrites them.
- `load_bmac_data()` merges per-symbol candle frames — **Arrow fast path first, pkl fallback**.

### Data route: Arrow-first, pkl-fallback

The data center publishes each candle batch in **two formats side by side**: `{spot,swap}_dict_{ts}.arrow` (single IPC FILE, one record batch per symbol, `schema.metadata[b'symbols']` = JSON manifest) **and** the legacy `{spot,swap}_dict_batch*.pkl`. This project prefers Arrow everywhere and falls back to pkl only when Arrow is missing/unreadable:
- Candles: `load_bmac_data()` → `arrow_reader.read_arrow_market_dict_for_run_time()` (mmap zero-copy), falling back to the `_dict_batch*.pkl` glob. Arrow snapshots are a rolling ~5000-row-per-symbol window; pkl is full history.
- Market pivot uses the producer's year-partitioned `market_pivot_{spot,swap}_*.pkl` through the two-argument `read_bmac_market_pivot(hour_offset, symbol_type)` contract. Consumer-side Arrow pivot rebuilding was removed; do not restore it.
- `arrow_reader.py` is the sole reader; its layout is locked to the producer `d:/alpha/core/arrow_publish.py`. A single-file Arrow schema is shared across all batches, so per-symbol dtype cannot be reproduced (a harmless int→float artifact on formerly-gapped symbols — see the parity tests).

When changing data loading, preserve consistency between what the data center writes and what this project reads — the two must agree at every step, and Arrow and pkl routes must produce equivalent results (enforced by `tests/test_arrow_*`).

## Execution flow

```
startup.py (run_loop, every 5 min)
  └─ check_bmac_update_flag           # wait for data center ready flags
  └─ for each account file whose hour_offset matches run_time.minute:
       subprocess → core/account_exec.py <ts>
         └─ init_system()              # build AccountConfig + MultiEquityBacktestConfig
         └─ core/real_trading.run_by_account()   # !!! core logic !!!
              1. snapshot exginfo, fetch market info, cancel open orders
              2. me_conf.backtest_strategies()   → core/backtest.run_backtest_multi
                   read data → time-series factors → cross-section factors
                   → select coins (ProcessPoolExecutor, 4 workers)
                   → aggregate ratios → simulate equity curves
              3. process_equities()     # resample sub-strategy equity to hold_period, calc pos-mgmt factors
              4. calc_ratios()          # position-management strategy → per-strategy capital ratio
              5. agg_pos_ratio() + apply_position_limits()   # → df_spot_ratio / df_swap_ratio
              6. calc_target_position() # → target holdings
              7. calc_order_amount()    # → orders
              8. place spot/swap orders (unless debug/dry_run)
              9. collect assets, save results
       └─ run_statistics_async()        # spawned in a thread pool, runs statistics.py after order offset
```

## Core module map

- [startup.py](startup.py) — scheduler / infinite loop, spawns one subprocess per account per cycle.
- [core/account_exec.py](core/account_exec.py) — single-account entry point (the subprocess target).
- [core/real_trading.py](core/real_trading.py) — `run_by_account`, the end-to-end live pipeline.
- [core/backtest.py](core/backtest.py) — `run_backtest_multi`, the 6-step parallel backtest (data→factors→select→aggregate→simulate). Uses `ProcessPoolExecutor` with LPT (longest-task-first) scheduling.
- [core/model/backtest_config.py](core/model/backtest_config.py) — `BacktestConfig` (one strategy config), `BacktestConfigFactory` (generates parameter-sweep combinations), `MultiEquityBacktestConfig` (the position-management layer that turns multiple sub-strategy equity curves into one ratio). Central data structure — read this first.
- [core/select_coin.py](core/select_coin.py) — factor calc (`calc_factors`, `calc_cross_sections` — has both Polars and pandas paths) and coin selection (`select_coins`).
- [core/factor.py](core/factor.py) — `calc_factor_vals`, dispatches to a factor's `signal_multi_params` (new fast API) or `signal` (legacy). Pulls extra data via `extra_data_dict` → `data_bridge`.
- [core/data_bridge.py](core/data_bridge.py) — loaders for external data sources (coin-cap, coin-btc) registered in `config.data_source_dict`.
- [core/utils/datatools.py](core/utils/datatools.py) — all data-center I/O and flag synchronization (Arrow-first candle + pivot loading, pkl fallback).
- [core/utils/arrow_reader.py](core/utils/arrow_reader.py) — consumer-side Arrow IPC mmap reader; layout locked to producer `d:/alpha/core/arrow_publish.py`.
- [core/model/strategy_config.py](core/model/strategy_config.py) — `StrategyConfig` plus the `select_mode='entry_exit'` engine (see below).
- [core/equity.py](core/equity.py) — simulator/equity-curve builder; consumes the market pivot.
- [core/utils/path_kit.py](core/utils/path_kit.py) — `get_folder_path` / `get_file_path`, everything resolved relative to `PROJECT_ROOT`. Use these instead of hardcoding paths.

### Coin selection modes (`select_mode`)

A `StrategyConfig` selects coins one of two ways, branched in `select_coin.py`:
- Default cross-section selection: `select_long_and_short_coin` (rank/pct/val filters → long/short picks).
- `select_mode='entry_exit'`: the 都江堰-style entry/exit engine in `strategy_config.py` (`run_entry_exit_selection_polars_lazy` → `_run_entry_exit_one_side` → the numba core `compute_entry_exit_numba`). Per cross-section it does exit → keep → refill-to-target-count using `entry_list`/`exit_list` gates, `fill_lag` (lagged refill rank), `exit_cooldown` (post-exit blacklist), and `max_hold_hours` (forced exit). Live/incremental runs continue from `ee_state_dir` via `core/utils/ee_continuation.py`; backtest runs compute from a flat start. The engine operates on Polars LazyFrames downstream of `load_bmac_data`, so it is **data-route-agnostic** (works identically on Arrow or pkl).

## Plugin-style registries ("hub" pattern)

Strategies, factors, and signals are loaded by name from directories, not imported directly:
- `factors/*.py` → `FactorHub.get_by_name(name)` — each factor module exposes `signal`/`signal_multi_params` and optional `extra_data_dict`.
- `strategy/` (sections) → `StrategyHub`
- `sections/*.py` — cross-section selection strategy implementations.
- `accounts/*.py` — per-account strategy_pool + position-management config.

A factor's `is_cross` property distinguishes cross-section factors from time-series factors; they run in separate phases.

## Conventions

- All code comments, logs, and docstrings are in Chinese. Match that style.
- The license header block (邢不行｜策略分享会 ...) appears at the top of every core file. Keep it on files that already have it.
- Time handling: data center timestamps are UTC; `utc_offset` (server tz vs UTC) is applied when filtering candles against `run_time`. Be careful with `tz_localize(None)` calls scattered through the merge logic.
- Results are written to `data/` subdirs (`仓位管理回测结果`, `子策略回测结果`, `cache`, `runtime`, `snapshot`) and pruned by `del_hist_files` with file-count limits.
- Memory is managed deliberately: `factor_col_limit` chunks factor columns, `mem_save_exe` and per-task `ProcessPoolExecutor` release memory between phases, `gc.collect()` after each account.

## Tests

Pytest suite in `tests/`, run under `alpha311`: `conda run -n alpha311 --no-capture-output python -m pytest tests/ -q`.
- Tests needing real data-center data are guarded with `pytest.mark.skipif` on data presence (e.g. `d:/alpha/data/preprocess_1h_resample/30m`) and **dynamically discover the latest snapshot ts** (snapshots rotate — never hardcode).
- Style is **oracle equivalence**: treat the legacy/producer function as the oracle and assert the new/fast path matches (`pd.testing.assert_frame_equal`, or byte/array equality). For features with no legacy oracle (entry_exit), assert against **hand-traced** expected output.
- No `conftest.py` / `pytest.ini` — tests self-insert the repo root on `sys.path`.
- Coverage: `test_arrow_pkl_parity.py` (candle merge Arrow vs pkl), `test_align_fast_equivalence.py`, `test_arrow_reader.py`, `test_load_bmac_data_fallback.py`, `test_arrow_pivot_parity.py` (pivot Arrow vs pkl), `test_entry_exit_engine.py` (numba engine semantics).
- Arrow/PKL parity compares all raw market columns and only permits additional factor columns explicitly declared in the producer configuration. Arrow intentionally carries C-precomputed factors while batch PKL stays raw. `test_precompute_contract.py` covers actual producer C calculation → Arrow publication → consumer reuse, plus local computation fallback. `test_upgrade_231.py` covers order routing, dry_run and continuation regressions. `test_main_chain_parity.py` compares pandas/Polars section outputs through selection, positions, orders and strategy attribution, and checks filtering and turnover statistics. Final 2.3.1 validation: 269 consumer tests and 24 relevant producer tests passed under the updated alpha311 environment.
