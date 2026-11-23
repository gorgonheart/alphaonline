# Phase 5 (Sub-project 2A) Low-Concurrency & Hot-Path Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the 4 approved Phase 5 (2A) trading-side optimizations (`P5-D1` `agg_pos_ratio`, `P5-B2` parallel `restore_child`, `P5-A1` 3-way parallel `read_inputs` + multi-unit zero-copy `read_arrow_tail`, `P5-C1` 3-thread `signal_block` sections + live `_polars_threads=budget`) in `D:/alphaonline` with strict TDD, independent Python oracles, and atomic commits.

**Architecture:** Keep `D:/alpha` and all 7 cross-project data contracts untouched. Replace pandas-fragmented `agg_pos_ratio` with column-contiguous NumPy accumulation, parallelize isolated read/restore work in `handoff.py` with main-thread telemetry aggregation and file-level Arrow `TimestampType.unit` bounds scaling, and split `section_state.py` into pure read-only block computation + deterministic main-thread `store.add_section` / atomic S3 checkpoint publish while relaxing `_polars_threads` only when `compute_gate is None`.

**Tech Stack:** Python `3.11.11` (`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`), `pandas 2.2.3`, `numpy 2.0.2`, `polars 1.36.1`, `pyarrow 17.0.0`, `concurrent.futures.ThreadPoolExecutor`, `pytest`.

**Spec:** [2026-11-23-phase5-2a-low-concurrency-design.md](../specs/2026-11-23-phase5-2a-low-concurrency-design.md)

## Global Constraints

- Interpreter: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`; pytest flags on Windows: `-q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check`.
- Offline only: no network access, no starting live trading/producers, no placing orders, no restarting remote services, no deleting production data, no overwriting pre-existing uncommitted files in `D:/alphaonline`.
- Preserve all existing public signatures (`agg_pos_ratio`, `restore_snapshot`, `read_inputs`, `read_arrow_tail`, `run_section_blocks`, `run_section_blocks_group`, `_calc_section_blocks`).
- Do NOT touch `positions/ModelRobust40Strategy.py` (avoids ~29 s production Numba recompilation), do NOT touch `apply_position_limits` / `redistribute_excess_weight` (`D1-A`), do NOT implement hardlink CoW (`P5-B1` deferred), and do NOT touch `D:/alpha`.
- Preserve CRLF/LF line endings and Chinese comment/doc conventions in modified files.

## Review Focus

- `agg_pos_ratio` (`P5-D1`): non-aligned/subset/unsorted `pos_ratio.index`, missing index raising `KeyError`, and `NaN` cells (in `df` or `pos_ratio`) matching `DataFrame.add(..., fill_value=0)` semantics when a column is missing in some strategies and finite in later strategies — pinned in Task 1.
- `restore_snapshot` (`P5-B2`): multi-child snapshot with `re_timing` (`lots_state0/1` + `account_state0/1` + `选币结果.pkl`) where one child fails mid-restore must drain all worker threads before propagating the error — pinned in Task 2.
- `read_arrow_tail` & `read_inputs` (`P5-A1`): non-UTC tz (`Asia/Hong_Kong`) and sub-unit nanosecond `start`/`end` bounds under `s`/`ms`/`us` units, null timestamps falling back to `utc_times`, and empty `spot`/`swap` Arrow raising `ValueError` without falsely tagging `failed_phase='revision_arrow_*_s'` — pinned in Task 3.
- `_calc_section_blocks` (`P5-C1`): mixed `signal_block` + legacy families preserving exact `store.section_columns` order, and sibling `signal_block` failure aborting before ANY `store.add_section` or S3 `.pkl`/`.tmp` write — pinned in Task 4.
- Background initialization throttle (`P5-C1`): non-None `config.compute_gate` keeping `_polars_threads=min(2, budget)` in `prepare_factor_selection` AND keeping `_calc_section_blocks` serial — pinned in Task 4.

---

### File Map

- Create: `tests/test_phase5_optimizations.py` — independent Python oracle and concurrency/failure tests for `P5-D1`, `P5-B2`, `P5-A1`, and `P5-C1`.
- Modify: `core/model/backtest_config.py:993-1006` — add `_agg_side_ratios` helper and rewrite `agg_pos_ratio`.
- Modify: `tools/robust40/handoff.py:209-282, 348-350` — parallelize `restore_child` calls (`P5-B2`), parallelize `read_inputs` 3-way reads with main-thread telemetry (`P5-A1`), and add file-level `TimestampType.unit` zero-copy binary search in `read_arrow_tail` (`P5-A1`).
- Modify: `core/utils/section_state.py:43-133` — split `compute_section_blocks` / `compute_section_blocks_group` + `publish_section_outputs`; keep `run_section_blocks*` delegating to them (`P5-C1`).
- Modify: `core/select_coin.py:652-698` — dispatch `signal_block` families on `ThreadPoolExecutor(max_workers=min(3, n))` when `gate is None`, commit in declaration order on main thread (`P5-C1`).
- Modify: `core/backtest.py:392-396` — pass `_polars_threads = budget if gate is None else min(2, budget)` to `calc_cross_sections` (`P5-C1`).
- Modify: `tests/test_shared_compute_handoff.py:8-45` — update live `compute_gate=None` budget assertion and add `compute_gate!=None` assertion (`P5-C1`).
- Modify: `docs/DEVELOPMENT.md`, `docs/kb/roadmap-and-backlog.md`, `docs/2026-11-23-phase5-and-v202-migration-plan.md` — record implementation, decisions (`C1-A`, `D1-A`, `P5-B1` deferred), and test results.

---

### Task 1: `P5-D1` — Column-Contiguous NumPy `agg_pos_ratio`

**Files:**
- Create: `tests/test_phase5_optimizations.py`
- Modify: `core/model/backtest_config.py:993-1006`

**Interfaces:**
- Consumes: `MultiEquityConfig.ratio_dfs: list[tuple[pd.DataFrame, pd.DataFrame]]`, `pos_ratio: pd.DataFrame`
- Produces: `_agg_side_ratios(side_frames: list[pd.DataFrame], pos_ratio: pd.DataFrame, target_index: pd.Index) -> pd.DataFrame` (module-level or static helper in `core/model/backtest_config.py`), updated `MultiEquityConfig.agg_pos_ratio(self, pos_ratio) -> Tuple[pd.DataFrame, pd.DataFrame]`

- [ ] **Step 1: Write failing tests for `P5-D1` in `tests/test_phase5_optimizations.py`**

Include an independent Python oracle `_oracle_agg_pos_ratio(ratio_dfs, pos_ratio)` duplicating the exact legacy `DataFrame.add(..., fill_value=0)` loop, plus tests:
1. `test_p5_d1_agg_pos_ratio_matches_oracle_across_shapes_and_edges`:
   - 14 strategies with overlapping/sparse columns, subset & reordered `pos_ratio.index`, single strategy preserving unsorted column order, empty `ratio_dfs`, pure-swap (0-column spot DataFrames).
   - Assert `pd.testing.assert_frame_equal(actual_spot, exp_spot, check_exact=True)` (and swap; allow `rtol=0, atol=1e-15` if needed only on random float sums, though same summation order should be exact).
2. `test_p5_d1_agg_pos_ratio_nan_and_missing_index_semantics`:
   - Strategy 0 has `NaN` in col `A`, strategy 1 lacks col `A`, strategy 2 has `0.5` in col `A` at row 0 and `NaN` at row 1; plus `pos_ratio` containing `NaN` weight at one row. Assert exact match against `_oracle_agg_pos_ratio` (row 0 finite, row 1 `NaN`).
   - Missing timestamp in `pos_ratio.index` raises `KeyError`.
3. `test_p5_d1_agg_pos_ratio_avoids_dataframe_add`:
   - Monkeypatch `pd.DataFrame.add` to raise `AssertionError('agg_pos_ratio must use preallocated NumPy accumulation')` and assert `agg_pos_ratio` succeeds and matches oracle (this fails RED against current code!).

- [ ] **Step 2: Run the P5-D1 tests to verify RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py -k p5_d1 -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: `test_p5_d1_agg_pos_ratio_avoids_dataframe_add` FAILS with `AssertionError: agg_pos_ratio must use preallocated NumPy accumulation`.

- [ ] **Step 3: Implement `_agg_side_ratios` and update `agg_pos_ratio` in `core/model/backtest_config.py:993-1006`**

Implement `_agg_side_ratios(side_frames, pos_ratio, target_index)`:
- Iteratively compute `all_cols = pd.Index([]); for df in side_frames: all_cols = all_cols.union(df.columns)` to match pandas `add` column ordering exactly.
- If `len(all_cols) == 0`, return `pd.DataFrame(index=target_index, columns=all_cols)`.
- Allocate `acc = np.zeros((len(all_cols), len(target_index)), dtype=np.float64)` (column-contiguous) and lazy `seen_finite = seen_nan = None`.
- For each `(idx, df)`:
  - Extract row-aligned 2D array without unnecessary `.loc` copy when `df.index.equals(target_index)`; else use `get_indexer` when `df.index.is_unique` and all `>= 0`; else fall back to `df.loc[target_index]` (preserving `KeyError` and duplicate-index semantics).
  - Multiply by `w = pos_ratio[idx].to_numpy(dtype=np.float64, copy=False)`.
  - Fast path (no NaN in `arr` and no NaN in `w`): for `j, c in enumerate(col_pos): acc[c] += arr[:, j] * w` (and if `seen_finite is not None: seen_finite[c] = True`).
  - NaN path: lazily allocate `seen_finite` / `seen_nan` (marking previously accumulated finite entries); for each `(j, c)` compute `prod = arr[:, j] * w`, `is_nan = np.isnan(prod)`, add `np.where(is_nan, 0.0, prod)` into `acc[c]`, set `seen_finite[c] |= ~is_nan`, `seen_nan[c] |= is_nan`.
- After the loop, if `seen_nan is not None`, set `acc[seen_nan & ~seen_finite] = np.nan`.
- Return `pd.DataFrame(acc.T, index=target_index, columns=all_cols)`.
- In `agg_pos_ratio(self, pos_ratio)`: if `not self.ratio_dfs: return pd.DataFrame(), pd.DataFrame()`; call `_agg_side_ratios` for spot (`[p[0] for p in self.ratio_dfs]`) and swap (`[p[1] for p in self.ratio_dfs]`).

- [ ] **Step 4: Run P5-D1 tests and related existing tests to verify GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py tests/test_model_config_semantics.py tests/test_upgrade_231.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: All PASS.

- [ ] **Step 5: Commit `P5-D1`**

```powershell
git add core/model/backtest_config.py tests/test_phase5_optimizations.py
git commit -m "perf(phase5): vectorize agg_pos_ratio with column-contiguous NumPy accumulation (P5-D1)"
```

---

### Task 2: `P5-B2` — Parallel `restore_child` in `restore_snapshot`

**Files:**
- Modify: `tools/robust40/handoff.py:1-15, 345-355`
- Modify: `tests/test_phase5_optimizations.py`

**Interfaces:**
- Consumes: `restore_child(child, time)` in `tools/robust40/handoff.py:309-325`
- Produces: unchanged signature `restore_snapshot(snapshot, revised_from, values)` executing `restore_child` across up to `min(4, len(children))` threads in deterministic sorted child order.

- [ ] **Step 1: Write failing tests for `P5-B2` in `tests/test_phase5_optimizations.py`**

Add tests:
1. `test_p5_b2_restore_snapshot_runs_children_concurrently_and_matches_serial`:
   - Build a realistic temporary snapshot with 4 child folders (`S1-a`..`S4-d`, including one `re_timing` child with `lots_state0.pkl`, `lots_state1.pkl`, `account_state0.pkl`, `account_state1.pkl`, `选币结果.pkl`, `ratio_spot.pkl`, `ratio_swap.pkl`), `sections/s3.pkl`, `model/state.pkl`, `warmup.pkl`, `snapshot_time.txt`.
   - Clone into two identical copies (`serial_snap`, `parallel_snap`). Run serial reference (`restore_child` sequentially + same post-steps) on `serial_snap`, and run `handoff.restore_snapshot` on `parallel_snap` while spying on `restore_child` using a `threading.Barrier(2, timeout=2.0)` to prove at least 2 child restores overlap concurrently (fails RED on current serial loop with `BrokenBarrierError`!).
   - Assert every restored `.pkl` and `snapshot_time.txt` in `parallel_snap` matches `serial_snap` identically.
2. `test_p5_b2_restore_snapshot_propagates_child_error_after_draining_workers`:
   - Inject an exception in one child while a sibling child sleeps briefly (`0.05 s`) and sets an `exited` flag in `finally`; assert `restore_snapshot` raises the injected exception AND `exited.is_set()` is already `True` when caught (zero dangling threads).

- [ ] **Step 2: Run P5-B2 tests to verify RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py -k p5_b2 -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: `test_p5_b2_restore_snapshot_runs_children_concurrently_and_matches_serial` FAILS (`BrokenBarrierError` because `restore_child` is currently serial).

- [ ] **Step 3: Implement parallel `restore_child` dispatch in `tools/robust40/handoff.py:348-350`**

- Import `ThreadPoolExecutor` from `concurrent.futures`.
- Replace lines 348–349:
  ```python
  children = sorted({p.parent for p in lots})
  if len(children) <= 1:
      for child in children:
          restore_child(child, time)
  else:
      with ThreadPoolExecutor(max_workers=min(4, len(children))) as pool:
          futures = [pool.submit(restore_child, child, time) for child in children]
          for fut in futures:
              fut.result()
  ```
  *(Exiting the `with` block joins all remaining worker threads automatically even if an earlier `fut.result()` raises.)*

- [ ] **Step 4: Run P5-B2 tests and snapshot/checkpoint regression suite to verify GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py tests/test_checkpoint_live.py tests/test_robust40_checkpoint_trial.py tests/test_server_snapshot_lifecycle.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: All PASS.

- [ ] **Step 5: Commit `P5-B2`**

```powershell
git add tools/robust40/handoff.py tests/test_phase5_optimizations.py
git commit -m "perf(phase5): parallelize restore_child across 4 threads in restore_snapshot (P5-B2)"
```

---

### Task 3: `P5-A1` — 3-Way Parallel `read_inputs` & Multi-Unit Zero-Copy `read_arrow_tail`

**Files:**
- Modify: `tools/robust40/handoff.py:209-282`
- Modify: `tests/test_phase5_optimizations.py`

**Interfaces:**
- Consumes: `telemetry.active()`, `telemetry.add(...)`, `telemetry._current` (or helper in `telemetry.py` if cleaner), `read_pivot_inputs`, `input_frames`, `utc_times`, `cutoff`.
- Produces: unchanged public signatures `read_inputs(...)` and `read_arrow_tail(...)`, plus internal unit-scaling helper `_arrow_ts_bounds(ts_type, start, end)`.

- [ ] **Step 1: Write failing tests for `P5-A1` in `tests/test_phase5_optimizations.py`**

Add tests:
1. `test_p5_a1_read_arrow_tail_multi_unit_and_tz_matches_reference`:
   - Parametrized over `unit, tz` in `[('ns', 'UTC'), ('ms', 'UTC'), ('us', None), ('s', 'Asia/Hong_Kong')]`, plus sub-unit nanosecond `start`/`end` offsets (`start + 500ns`, `end + 500ns`) and both `indexed=True` and `indexed=False, tail=10`.
   - Write an Arrow IPC file with `candle_begin_time` of `pa.timestamp(unit, tz=tz)`, `close`, `first_candle_time`, `symbol_swap` (categorical).
   - Compare `read_arrow_tail` output against independent oracle using `utc_times` (and `input_frames` for `indexed=True`), asserting `pd.testing.assert_frame_equal(..., check_exact=True)` (native `datetime64[{unit}]` preserved, exact boundary inclusion/exclusion).
   - Also monkeypatch `handoff.utc_times` to assert it is NOT called on `candle_begin_time` (only on `first_candle_time` if present; test a schema without `first_candle_time` where `utc_times` is called 0 times — fails RED against current code!).
2. `test_p5_a1_read_arrow_tail_null_timestamp_falls_back_safely`:
   - Arrow file containing a null in `candle_begin_time` or non-timestamp string column falls back to `utc_times` without crashing.
3. `test_p5_a1_read_inputs_parallel_three_way_preserves_telemetry_and_errors`:
   - Inside `telemetry.round_log('acct', 1700000000)` with a spy `_write` capturing JSON events:
     - Use a `threading.Barrier(3, timeout=2.0)` across monkeypatched `read_arrow_tail` (`spot`, `swap`) and `read_pivot_inputs` to prove all 3 branches run concurrently (fails RED against current serial loop!).
     - Assert emitted telemetry contains positive float keys `revision_arrow_spot_s`, `revision_arrow_swap_s`, `revision_pivot_s`.
   - Subcase (error in `read_pivot_inputs`): assert original exception propagates AND emitted `end` event has `failed_phase == 'revision_pivot_s'`.
   - Subcase (empty `spot` dict returned): assert `ValueError('Missing same-round spot Arrow for revision checks')` propagates AND `failed_phase` is NOT set to `'revision_arrow_spot_s'`.

- [ ] **Step 2: Run P5-A1 tests to verify RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py -k p5_a1 -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: `test_p5_a1_read_arrow_tail_multi_unit_and_tz_matches_reference` (utc_times spy) and `test_p5_a1_read_inputs_parallel_three_way_preserves_telemetry_and_errors` (Barrier) FAIL.

- [ ] **Step 3: Implement `P5-A1` in `tools/robust40/handoff.py:209-282`**

1. In `read_arrow_tail`:
   - Define `_TS_UNIT_TO_NS = {'s': 10**9, 'ms': 10**6, 'us': 10**3, 'ns': 1}`.
   - Once per file after `reader = pa.ipc.open_file(source)`:
     - Inspect `ts_type = reader.schema.field('candle_begin_time').type`.
     - If `pa.types.is_timestamp(ts_type)` and `ts_type.unit in _TS_UNIT_TO_NS`:
       - `ts_unit = ts_type.unit`; `scale = _TS_UNIT_TO_NS[ts_unit]`
       - `end_key = end.value // scale`
       - `start_key = (start.value + scale - 1) // scale`
     - Else `ts_unit = None`.
   - Per batch:
     - If `ts_unit is not None` and `ts_col.null_count == 0`:
       - `raw = ts_col.cast(pa.int64()).to_numpy(zero_copy_only=True)`
       - `right = int(np.searchsorted(raw, end_key, side='right'))`
       - `left = max(0, right - tail) if tail is not None else int(np.searchsorted(raw, start_key, side='left'))`
       - If `right <= left: continue`
       - Slice and convert `frame = batch.select(data_columns).slice(left, right - left).to_pandas(use_threads=False)`.
       - If `indexed`: `frame.index = pd.DatetimeIndex(raw[left:right].copy(), dtype=f'datetime64[{ts_unit}]', name='candle_begin_time')`.
     - Else: existing `utc_times(ts_col.to_numpy())` fallback path.
   - Keep `missing_revision_fee`, `is_unique`, `first_candle_time`, and `is_monotonic_increasing` logic unchanged.
2. In `read_inputs` (normal Arrow fast branch L214–227):
   - Helper `_timed(fn, *args, **kwargs)` running in worker thread returning `(fn(*args, **kwargs), time.perf_counter() - t0)`.
   - Submit 3 futures (`'spot'`, `'swap'`, `'pivot'`) in `with ThreadPoolExecutor(max_workers=3) as pool:`.
   - Inside the `with` block, collect results in deterministic order `('spot', 'swap', 'pivot')`:
     - On exception from `fut.result()`: if `telemetry.active()`, record `failed_phase` on the main thread's telemetry dict (`setdefault('failed_phase', phase_name)`) and re-raise (exiting `with` waits for remaining threads).
     - On success: if `telemetry.active()`, call `telemetry.add(**{phase_name: round(elapsed_s, 6)})`.
   - Validate non-empty `spot`/`swap` outside phase-failure tagging (preserving exact `ValueError` and `failed_phase` semantics), then merge `spot` → `swap` → `pivot` into `out`.

- [ ] **Step 4: Run P5-A1 tests and revision/replay regression suite to verify GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py tests/test_revision_input_reads.py tests/test_replay_logging.py tests/test_removed_series_governance.py tests/test_universe_expansion_revision.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: All PASS.

- [ ] **Step 5: Commit `P5-A1`**

```powershell
git add tools/robust40/handoff.py tests/test_phase5_optimizations.py
git commit -m "perf(phase5): parallelize preflight 3-way reads and use multi-unit zero-copy Arrow bounds (P5-A1)"
```

---

### Task 4: `P5-C1` — 3-Thread `signal_block` Sections & Live `_polars_threads=budget`

**Files:**
- Modify: `core/utils/section_state.py:43-133`
- Modify: `core/select_coin.py:652-698`
- Modify: `core/backtest.py:392-396`
- Modify: `tests/test_shared_compute_handoff.py:8-45`
- Modify: `tests/test_phase5_optimizations.py`

**Interfaces:**
- Consumes: `FactorBlocks.block_keys()`, `FactorBlocks.read_block(...)`, `FactorBlocks.add_section(column, parts)`
- Produces:
  - `compute_section_blocks(conf, factor, store, columns, params, column) -> tuple[str, dict, Path | None, Any]` in `core/utils/section_state.py`
  - `compute_section_blocks_group(conf, factor, store, param_specs) -> list[tuple[str, dict, Path | None, Any]]` in `core/utils/section_state.py`
  - `publish_section_outputs(store, outputs) -> None` in `core/utils/section_state.py`
  - Unchanged public `run_section_blocks(...)`, `run_section_blocks_group(...)`, `_calc_section_blocks(conf, store)`

- [ ] **Step 1: Write failing tests for `P5-C1` in `tests/test_phase5_optimizations.py` and update `tests/test_shared_compute_handoff.py`**

In `tests/test_phase5_optimizations.py`, add tests:
1. `test_p5_c1_parallel_block_sections_match_serial_and_preserve_order`:
   - Build multi-block `FactorBlocks` with all 4 production `signal_block` families (`VolumeMaxBias_进出场阈值分离官方` with `section_state=True, checkpoint_hours=10`, `TopKTo2K` 2 params, `固定回撤阈值触发币数占比开仓门槛` 1 param, `动态回撤阈值` 2 params) PLUS a 5th `legacy` (`signal_polars`-only) family interleaved in `section_params_dict`.
   - Run serial oracle (forcing `gate = lambda: None` or calling `run_section_blocks_group` serially) into `store_serial` / `snap_serial`, and parallel (`gate = None`) into `store_par` / `snap_par`.
   - Assert `store_par.section_columns == store_serial.section_columns`, every column matches with `check_exact=True` (`np.testing.assert_array_equal` / `allclose(atol=1e-15)`), and S3 `.pkl` checkpoint keys & output DataFrame match identically.
2. `test_p5_c1_block_families_execute_concurrently_when_gate_is_none_and_serially_when_gated`:
   - Two mock `signal_block` families synchronize on a `threading.Barrier(2, timeout=2.0)` when `config.compute_gate = None` (proving true concurrency — fails RED on current serial loop!).
   - With `config.compute_gate = lambda: None`, assert both families run on `threading.current_thread()` (main thread, serial) and never spawn worker threads.
3. `test_p5_c1_parallel_section_failure_leaves_zero_partial_store_or_state`:
   - Two `signal_block` families: family 1 is persisted (`signal_with_state`), family 2 raises `RuntimeError('boom')`.
   - Assert `_calc_section_blocks` raises `RuntimeError('boom')`, `store.section_columns == []`, and `list(snapshot_folder.rglob('*')) == []` (zero partial `add_section` or S3 `.pkl`/`.tmp` writes).
4. `test_p5_c1_prepare_factor_selection_polars_threads_respects_compute_gate`:
   - For `budget = 4`: when `config.compute_gate = None`, `prepare_factor_selection` passes `_polars_threads == 4` to `calc_cross_sections` (fails RED against current `min(2, budget)`!); when `config.compute_gate = lambda: None`, it passes `_polars_threads == 2`.

In `tests/test_shared_compute_handoff.py:41`:
- Update `assert budgets == [1, min(2, budget), budget]` to `assert budgets == [1, budget, budget]` (since line 31 explicitly sets `config.compute_gate = None`).

- [ ] **Step 2: Run P5-C1 tests to verify RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py -k p5_c1 tests/test_shared_compute_handoff.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: Barrier concurrency test, `compute_gate=None` `_polars_threads=budget` test, and updated `test_shared_compute_handoff.py` FAIL.

- [ ] **Step 3: Implement `P5-C1` across `section_state.py`, `select_coin.py`, and `backtest.py`**

1. **`core/utils/section_state.py:43-133`**:
   - Extract `compute_section_blocks(conf, factor, store, columns, params, column)` containing lines 45–83 of current `run_section_blocks` and returning `(column, parts, path, state)` (no `store.add_section`, no disk write).
   - Extract `compute_section_blocks_group(conf, factor, store, param_specs)`:
     - If `len(param_specs) <= 1 or persisted`: return `[compute_section_blocks(conf, factor, store, columns, params, column) for params, column, columns in param_specs]`.
     - Else: run existing single-block-read loop collecting `parts_by_col` and return `[(column, parts_by_col[column], None, None) for _, column, _ in param_specs]`.
   - Add `publish_section_outputs(store, outputs)`: for `column, parts, path, state in outputs`: `store.add_section(column, parts)`; if `path is not None`: atomic `.tmp` + `replace` write of `state`.
   - Update `run_section_blocks` to call `output = compute_section_blocks(...)`, `publish_section_outputs(store, [output])`, `return output[3]`.
   - Update `run_section_blocks_group` to call `publish_section_outputs(store, compute_section_blocks_group(...))`.
2. **`core/select_coin.py:652-698`**:
   - Pre-collect active family entries in `conf.section_params_dict` declaration order, each tagged `'block'` (with `param_specs`) or `'legacy'`.
   - If `len(block_entries) > 1 and gate is None`:
     - Run `compute_section_blocks_group(conf, e['factor'], store, e['param_specs'])` in `with ThreadPoolExecutor(max_workers=min(3, len(block_entries))) as pool:`.
     - Inside `with`, collect `block_results[e['name']] = fut.result()` in original order (all threads drained before any exception escapes; zero mutations before all families succeed).
     - After `with` succeeds, iterate `entries` in original declaration order: publish `'block'` entries via `publish_section_outputs(store, block_results.pop(e['name']))`, and execute `'legacy'` entries via the existing serial block (lines 676–696).
   - Else: iterate `entries` serially (existing behavior).
3. **`core/backtest.py:392-396`**:
   - Read `gate = getattr(config, 'compute_gate', None)` and pass `_polars_threads=budget if gate is None else min(2, budget)` to `_run_block_stage(calc_cross_sections, conf, store=store, ...)`.

- [ ] **Step 4: Run P5-C1 tests and all section/shared-compute regressions to verify GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py tests/test_shared_compute_handoff.py tests/test_shared_section_dispatch.py tests/test_section_block_states.py tests/test_section_polars_parity.py tests/test_section_resume_state.py tests/test_p4_optimizations.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: All PASS.

- [ ] **Step 5: Commit `P5-C1`**

```powershell
git add core/utils/section_state.py core/select_coin.py core/backtest.py tests/test_shared_compute_handoff.py tests/test_phase5_optimizations.py
git commit -m "perf(phase5): parallelize signal_block sections across 3 threads and relax live polars budget (P5-C1)"
```

---

### Task 5: End-to-End Offline Regression Verification & Documentation Sync

**Files:**
- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/kb/roadmap-and-backlog.md`
- Modify: `docs/2026-11-23-phase5-and-v202-migration-plan.md`

**Interfaces:**
- Consumes: completed commits from Tasks 1–4.
- Produces: verified full regression run + synchronized project knowledge base & changelog.

- [ ] **Step 1: Run comprehensive offline test suite across all touched subsystems**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_phase5_optimizations.py tests/test_p4_optimizations.py tests/test_revision_input_reads.py tests/test_replay_logging.py tests/test_removed_series_governance.py tests/test_universe_expansion_revision.py tests/test_checkpoint_live.py tests/test_robust40_checkpoint_trial.py tests/test_server_snapshot_lifecycle.py tests/test_shared_compute_handoff.py tests/test_shared_section_dispatch.py tests/test_section_block_states.py tests/test_section_polars_parity.py tests/test_section_resume_state.py tests/test_model_config_semantics.py tests/test_upgrade_231.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 100% PASS (zero failures, zero errors). Also run `git diff --check` on the 4 commits to ensure zero whitespace issues.

- [ ] **Step 2: Run quick local micro-benchmark verification for `P5-D1`, `P5-C1`, `P5-A1`, `P5-B2`**

Record exact before/after local timings (clearly labeled as Windows `alpha311` local benchmarks, NOT Ubuntu 4c8g live numbers) for documentation.

- [ ] **Step 3: Sync project documentation**

- Prepend a dated `2026-11-23` entry to `docs/DEVELOPMENT.md` summarizing Phase 5 (2A) commits (`P5-D1`, `P5-B2`, `P5-A1`, `P5-C1`), confirmed scope decisions (`C1-A`, `D1-A`, `P5-B1` deferred), 2B compatibility hardening (`ms`/`us`/`s` Arrow timestamps & main-thread telemetry), exact test counts, and pending remote test-server acceptance.
- Update `F5` in `docs/kb/roadmap-and-backlog.md` to reflect local implementation & offline TDD/Oracle verification completed (awaiting authorized test-server deployment & natural-round acceptance).
- Update `docs/2026-11-23-phase5-and-v202-migration-plan.md` §子项目 2A to note the finalized `C1-A + D1-A` adjustments and `P5-B1` deferral, plus commit hashes.

- [ ] **Step 4: Commit documentation sync (leaving all pre-existing user untracked/dirty files untouched)**

```powershell
git add docs/superpowers/specs/2026-11-23-phase5-2a-low-concurrency-design.md docs/superpowers/plans/2026-11-23-phase5-2a-low-concurrency.md docs/DEVELOPMENT.md docs/kb/roadmap-and-backlog.md docs/2026-11-23-phase5-and-v202-migration-plan.md
git commit -m "docs(phase5): record Phase 5 (2A) design, plan, and offline verification"
```
