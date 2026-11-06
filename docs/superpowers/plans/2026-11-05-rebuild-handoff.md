# Rebuild handoff implementation plan

> Use superpowers:executing-plans inline, with test-driven-development for every behavioral change. User approved the design and TDD execution; retain the existing working directory and uncommitted changes.

**Goal:** Known unrecoverable revisions transition immediately into background initialization without restarting the scheduler or placing historical orders.
**Architecture:** A typed pre-trade exception becomes a MODEL-only exit result. Coordinator owns one lightweight pending-data thread, starts the existing resource-supervised initialization only when full data exists, and rechecks the publication after acquiring the compute slot. All calculations and atomic installation remain shared with explicit initialization.
**Tech Stack:** Python alpha311, existing subprocess/OS locks, Arrow headers, pandas metadata.
**Spec:** docs/superpowers/specs/2026-11-05-rebuild-handoff-design.md (approved 2026-11-05).

## Global constraints

No deployment/services/orders/production deletion. Keep old snapshot until validated installation; clean only owned scratch through existing lifecycle. Initialization completion does not submit orders. No new core/positions/sections/alpha changes in this task. Preserve previous task changes. Logs and local fixtures under test/.

## Review focus

- A normal nonzero exit or an order error must never become rebuild success.
- Waiting data cannot hold the global initializer slot or spawn a heavy process per account.
- Restart and cross-installation queueing must not duplicate an account initializer: dispatch lock -> global slot -> initialization lock -> snapshot lock.
- Publication can disappear/change while queueing; select and freeze one latest full same-offset round after the slot.
- Failed same-config/same-publication calculations do not loop; missing data is a wait state, not a failed calculation.

## T1: Typed result and classification

Files: replay.py, CLI server_robust40.py, telemetry.py, tests/test_checkpoint_live.py, new tests/test_rebuild_handoff.py.
Interfaces: RebuildRequired(ValueError), REBUILD_REQUIRED_EXIT; trade returns the dedicated code only for typed pre-trade rebuild conditions. main propagates this code after account lock/context cleanup.
- [x] RED: out-of-range common_before is typed; trade marks incomplete, calls no account flow, returns rebuild result; generic ValueError/OSError propagates without marking a previously valid snapshot incomplete.
- [x] GREEN: introduce typed error; catch only classified errors; preserve normal success/actual errors; log status=rebuild_required without overwriting it as ok.
- [x] Verify targeted checkpoint and telemetry tests. Use no production inputs or network.

## T2: Data readiness and supervised queue

Files: market_inputs.py, server.py, resources.py, CLI, tests/test_rebuild_handoff.py.
Interfaces: full_publication(source,minute,required,cache=None) -> (stamp|None, reason); Coordinator.request_rebuild(account,run_time,market,reason), drain_pending(); CLI rebuild(account,stamp,destination) rechecks latest full publication under existing init lock, then invokes initialize with actual stamp/workspace. WAIT_FOR_DATA_EXIT returns to pending without a failed calculation. No immediate trade.
- [x] RED: short->full changes launch once; waiting creates no workspace/process; account B proceeds while A waits; metadata headers cached until changed; after-slot publication changes choose latest; no full publication returns wait; already-valid state cancels duplicate; full initialization delegates unchanged and retains cleanup.
- [x] GREEN: one daemon thread per Coordinator only while pending; synchronize pending/jobs mutations without locking the live ready wait; use existing locks and resource supervisor. Keep account dispatch lock across global queue so parent restarts cannot pile up workers. Pending thread registers full request before launch and can resume invalid accounts outside their trading offset after startup.
- [x] Verify deterministic failure dedup, configuration/publication changes, lock ownership, cancellation, exceptions, and resource wrapper contracts.

## T3: Startup integration and final verification

Files: startup.py only subprocess result handling; server.py result-dispatch helper; existing/new offline tests and docs.
Interface: Coordinator.handle_result(account,run_time,market,command,env,returncode)->bool; True means ordinary success/statistics, False means MODEL rebuild handed off, otherwise CalledProcessError.
- [x] RED: MODEL rebuild skips statistics, immediately queues, continues next account; legacy same exit code remains error; successes and true failures retain behavior. AST-extract actual startup.run_loop with fakes to avoid services.
- [x] GREEN: replace check=True call by returncode dispatch; no execution ordering/mathematics changes.
- [x] Affected offline suite: checkpoint/replay lifecycle, server dispatch/reliability/completion, initialization worker, resources, upgrade231 routing and new tests. Count non-overlapping batch totals; fix only actual regressions. Common calculation equivalence uses existing explicit initialization and recovery tests, do not regenerate golden or pretend missing fixtures ran.
- [x] Fresh independent review per requesting-code-review; update DEVELOPMENT, approved spec status, final implementation/core-startup review and costs. Keep work uncommitted for human review; no merge/deployment.

## Rulings / outcomes

Ruling: use one Coordinator waiting thread rather than one Python waiter per account — avoids importing MODEL separately for 11 offsets — requires a small lock around jobs/pending state, not around live data waits.
Ruling: a separate bounded per-account dispatch OS lock covers the existing global resource queue — initializer snapshot locks are still owned by the worker — prevents duplicate queued processes after parent restart without adding a state registry.
Ruling: existing normal-round snapshot inspection stays; no per-hour checkpoint index or extra whole-model read is added solely to avoid the rare failed-copy branch.


2026-11-05 implementation rulings and verification:
- User approved implementation. Native TDD used; typed-result, queue/readiness, startup dispatch and review-found races were first observed failing, then fixed.
- One bounded `initialization_attempt` entry in existing warmup.pkl remembers actual publication/settings before compute, under snapshot lock. Success replaces metadata via the existing install protocol. No second registry or data copy.
- Queue log keeps its original requested stamp; `publication_stamp` and workspace use the latest admitted full publication. This deliberately avoids renaming an open log after queue admission.
- Review fixed differing resume-capacity checks and failure to cancel pending when an external initializer had already completed. An additional regression prevents re-requesting full Arrow after external completion.
- Final relevant batch: 146 passed, 19 skipped, 102.14s. 25 new recovery tests are included, not additional. Platform/saved-fixture skips remain limits; no full real 14050-bar rerun or Ubuntu service validation was performed.
- Windows real-account-settings/synthetic-state timing: pending-data poll median 34.75ms, P95 39.23ms; healthy preflight median 43.48ms; successful exit dispatch median 0.0087ms. These include small file/lock work but exclude cold imports and actual initialization. No zero-cost claim.
- Implementation and manual review: docs/2026-11-05-rebuild-handoff-implementation.md. No commit, merge or deployment.
