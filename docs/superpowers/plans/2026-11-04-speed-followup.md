# 共享计算提速与传统45m验证 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; native execution in the current directory is already authorized. TDD before production changes.

**Goal:** Reduce full initialization and continuation latency without changing factors, selection, simulation, MODEL/S3 or target results.

**Architecture:** Keep the current shared columnar pipeline and existing snapshot protocol. Optimize bounded object lifetimes and projected reads first; retain concurrency/aggregation candidates only after full-cost comparison. No increase to default memory limits.

**Tech Stack:** alpha311, pandas, Polars, PyArrow17, NumPy/Numba, Ubuntu systemd dryrun.

**Spec:** User-approved priorities in the preceding audit and docs/2026-11-04-shared-io-optimization-review.md. Traditional validation uses accounts backup/45m_gonheart1020.py.

## Global constraints
- Preserve current dirty tree; no unrelated edits, commits, alpha changes or actual orders.
- All new mathematical/behavior changes first need failing independent tests. Original implementation remains the oracle; no regenerated golden files.
- Measure read/convert/spawn/compute/write/cleanup together, distinguish cold/warm and upstream overlap.
- Before replacing the test server's 45m MODEL account, wait for idle, stop test scheduling, rename existing account-owned data/state to a dated backup. Keep other accounts intact.
- Use only existing protocol checks. No extra hashes/manifests or production cleanup.

## Review focus
- Windows mmap ownership and deletion while pandas mapping remains alive.
- Caller GC state, exception paths, young/old cyclic object lifetimes.
- Empty/sparse Arrow histories, optional fields, dictionary/timestamp dtypes and index ownership.
- Traditional account's non-MODEL initialization/retiming and dryrun guards.
- Cross-day, gap/replay and mixed-market sorting semantics; unsupported strategies retain fallback.

### Task 1: Bounded GC lifecycle
Files: core/utils/factor_data.py, core/select_coin.py, core/backtest.py, tools/initialization_pipeline.py; tests/test_compute_gc.py.
- [x] RED tests: temporary cycles reclaimed without collecting unrelated old cycles; original enabled/disabled state restored on success/error; mapped output usable after store deletion; selection remains exact without per-group full GC.
- [x] Implement minimal local lifecycle control, preserving explicit final-owner cleanup; no global disabling/freezing.
- [x] GREEN existing ownership/selection/IO tests plus full-layout mapping probe; record full cost and GC generation/timing.

### Task 2: Arrow projection and factor-stage hotspots
Files: tools/robust40/handoff.py and research/speed_followup.py; tests/test_shared_io_optimization.py.
- [x] RED test that unused Arrow fields are excluded at IPC decode, with exact old-reader output/index ownership.
- [x] Measure projected IPC decode versus current full decode ABBA on frozen full publication; retain only if beneficial.
- [x] Profile current factor path including conversion and transport; document dominant remaining operations, do not rewrite formulas without independent oracle.

### Task 3: Concurrency/aggregation candidate trials
Files: research speed harness; core changes only for a proven candidate.
- [x] Compare new block-section 2/4 native threads, exact section/S3 output and whole-stage costs.
- [x] Compare original aggregation with existing v2 using actual selections and independent special-case tests; reject on unexplained mismatch.
- [x] Evaluate initialization 2-combination concurrency and MODEL preparation/simulation costs before any default change; keep serial if full-cost gain is unproved.
- [x] Profile-driven addition: typed dates in MODEL `coin_features` bypass repeated pandas parsing, string inputs retain original parser. RED typed-parser guard, exact all-symbol feature oracle, no mathematical change; verify in replay/continuation before adoption.


Candidate rulings: IPC projection and selection-GC removal were withdrawn; default section threads remain2. Aggregation-only concurrency showed potential but complete simulation concurrency is unproved, so no default change. MODEL feature oracle and integration are tracked under Task4 before deployment.

### Task 4: Complete integration and traditional dryrun
- [x] Ubuntu old/new full initialization and continuation comparisons, current relevant Windows/Linux tests; preserve immutable baseline.
- [x] Stage credential-safe traditional45m account from user-specified backup, retaining dryrun and proxy7897; rename prior account state and verify no MODEL state is consumed.
- [x] Run traditional45m dryrun plus MODEL continuation, collect real phase logs and equivalence evidence. No actual trades or manual notifications.
- [x] Fresh review, fix important findings RED/GREEN, update DEVELOPMENT and report adopted/rejected candidates with limitations.
