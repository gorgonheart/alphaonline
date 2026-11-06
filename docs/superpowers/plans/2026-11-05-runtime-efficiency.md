# Runtime efficiency implementation plan

> Execute inline using executing-plans and TDD; preserve existing work.

**Goal:** Implement the accepted next-version scope without changing strategy or execution mathematics.
**Spec:** docs/2026-11-05-next-version-scope.md
**Architecture:** Independent changes: stateless cached execution kernel, round-scoped simulation inputs, explicit in-memory equity handoff, bounded test lifecycle, producer/consumer Arrow demand. Existing snapshot and order contracts remain authoritative.
**Tech Stack:** Python alpha311, NumPy/Numba, pandas/Arrow, existing file-based coordination.

## Constraints and review focus

Work in current codex branch per user preference. No deployment, service restart, real orders or production cleanup. Server experiments only under test. Preserve caller arrays, non-default parameters, noncontiguous arrays, all offsets and legacy fallback. No whole-disk scans. Cold compilation and warm cross-process calls must be measured separately.

## Execution ledger / tasks

- [x] P1: tests/test_robust40_execution_cache.py — frozen old numeric oracle plus randomized/boundary lots, input immutability, new-process cache hits. Modify positions/ModelRobust40Strategy.py only: cached array/scalar function called by live adapter and existing jitclass; keep one formula. Run existing execution/model tests, benchmark fresh processes.
- [x] P2: tests for round-scoped worker market inputs; core/backtest.py reuses immutable same-round pivots via existing core/equity.py market_inputs. Measure read/align/kernel/save before deciding crop changes. Ensure worker inputs do not leak across offset/stamp and retiming retains both simulations.
- [x] P3: tests for equity/timing handoff and existing CSV semantics; reuse current inputs interface, preserving report output and exact index/float behavior. Do not bypass CSV parsing if that changes prior observable values without explicit evidence/review.
- [x] P4: existing-log stage timings for account reads, ticker, execution numeric call, final failure counts; no order flow changes or additional network calls. Validate dry_run and normal path with offline fakes.
- [x] D2: tools/run_deployment_tests.py bounded finished-run cleanup protecting active/unknown runs; tests under tests/test_deployment_tests*. Preserve logs while releasing large scratch, no trading-loop scanning.
- [x] D1: separate alpha producer branch and same-offset/consumer request protocol; request full before ready, release only after install, capture depth once per round for both markets. Independent tests for missing/late requests, concurrent consumers and short/full values. No upstream history truncation.
- [x] P5/P6/O1–O3: inspect remaining evidence after above changes; implement only evidence-supported narrow improvements, record deferrals rather than promise unmeasured savings.
- [x] Review: targeted then affected shared-flow regression; update DEVELOPMENT and implementation/performance report. Fresh whole-change reviewer after implementation. No implicit release/merge.

Each task: write failing test, run and record expected failure, implement, run relevant regressions, record timing including creation/read/write/release. Tests are not satisfied by replacing golden outputs.

## Decisions and results

2026-11-05: user authorized proceeding with complete scope. Begin P1, independent of producer protocol. Existing uncommitted documentation remains intact. Source commits/deployment are not needed to execute local tests; leave changes reviewable.

2026-11-05 实施收敛：P1/P4/D1/D2已实现并离线测试；P2只做同一再择时子策略复用，实测省读取但RSS+29MiB，不扩成全worker缓存；P3只绕过无损PKL重读，CSV解析绕过因尾差明确不启用。P5等D1真实上线后再测，P6/O1–O3保留观测，不作未经证实的并行/算法改写。以上勾选表示本轮逐项完成实施或有证据的取舍，不表示所有原设想均已实现。完整状态与测试/性能/限制见docs/2026-11-05-runtime-efficiency-implementation.md。未部署、未合并；旧用户文档保留。
