# Shared compute I/O optimization implementation plan

> **For agentic workers:** Use superpowers:executing-plans in the existing workspace. Steps below track evidence, not just code completion.

**Goal:** Optimize the three areas selected in the five-hour Ubuntu review and validate them on the authorized test server.

**Architecture:** Retain FactorBlocks and the original math/state interfaces. Add block adapters for the two time-local sections; filter projected price/mapping rows before full-history ordering; optimize revision reads only after measuring the Arrow/Pivot split. Emit actual stage boundaries in normal execution too.

**Tech Stack:** Existing pandas/Polars/PyArrow; Python alpha311. No dependency upgrades.

**Spec:** docs/2026-11-04-ubuntu-five-hour-review.md, optimization priorities 1–3, explicitly approved by the user's request to implement and test on server.

## Global constraints

- Current working directory, native execution; preserve prior uncommitted changes. No commit requested.
- Initialization, continuation, gap/replay and normal trade share the optimized entry.
- Keep formulas, null/dtype/row ordering, S3/MODEL checkpoint protocol and dry_run behavior unchanged.
- Only ubuntu@43.173.122.28:/home/ubuntu/alphatrade test deployment authorized; alpha and other hosts unchanged.
- Do not clear working snapshots; isolated tests use their own state/output. No live orders.
- Keep CPU/memory budgets; retain a candidate only with correctness and measured full-cost evidence.

## Review focus

- Cross sections require every symbol at a timestamp; no ranking independently by symbol shard.
- Sparse/duplicate rows, empty selections, NaN/null, odd offsets and shuffled physical order must retain semantics.
- Projection/filtering must retain dtype, source-row ordering and mmap ownership before cleanup.
- Revision reads must include old-year column unions, unsorted/duplicate time behavior and failure propagation.
- Normal logging must measure execution, not lazy plan construction; process stages must preserve thread budgets.

## Task 1: time-local sections

Files: sections/动态回撤阈值.py, sections/固定回撤阈值触发币数占比开仓门槛.py; tests/test_shared_io_optimization.py.

- [x] Add RED tests exercising real shared dispatcher with read_history forbidden; compare output to unchanged whole-history Polars and independent pandas formulas across blocks/shards/offsets/nulls.
- [x] Add minimal get_state_input_columns(params) and signal_block(frame, params, column, state=None) adapters to existing math, no persistent state.
- [x] Run targeted section and shared dispatch regressions.

## Task 2: projected mapping/report reads and real timings

Files: core/utils/factor_data.py, core/backtest.py, tools/initialization_pipeline.py; tests/test_shared_io_optimization.py.

- [x] RED tests for filtered history read preserving original row order, dtypes, empty schema, section columns and ownership; independently derive expected data by old read_history then filter.
- [x] Add optional predicate/start to history reading; skip old whole blocks for start and filter each projected block before concat/sort. Reuse for initialization swap mapping and normal recent price reports.
- [x] Add default lightweight common phase timing when config.compute_phase is absent, retaining initialization resource phase callback; split mapping, GC and scratch cleanup timing.
- [x] Run ownership/handoff/selection boundary regressions.

## Task 3: revision input reads

Files: tools/robust40/handoff.py; tests/test_shared_io_optimization.py; research-only server benchmark.

- [x] Measure existing Arrow spot/swap and Pivot reads separately on immutable test inputs, full process and allocation costs included.
- [x] RED test any selected optimization against original sequential reader, including year union and empty/duplicate/timezone cases.
- [x] Prefer bounded independent reads or eliminating duplicate conversion; do not introduce a second input source or skip annual files based solely on filename. Retain serial fallback if measured memory/time tradeoff is worse.
- [x] Verify exact revision inputs and earliest revision detection; retain diagnostic timings even if an optimization candidate is rejected.

## Task 4: Linux validation and deployment

- [x] Capture original source files for rollback and A/B before changing them.
- [x] Local relevant tests, fresh final reviewer, resolve substantive findings with RED/GREEN.
- [x] Ubuntu isolated same-input A/B: initialization sections/mapping, 4050 and revision read; include creation/read/compute/write/cleanup and cgroup CPU/anon/file/swap.
- [x] If evidence supports candidate, deploy only changed files at a safe idle boundary; preserve active state and dry_run; verify a real next scheduled cycle.
- [x] Update DEVELOPMENT and test report with gains, rejected candidates, limits and exact manual review scope.

User approved these three directions and server testing after the written audit. This plan makes that authorized work concrete; no additional strategy choice is required. Prior native/in-place preference persists.
