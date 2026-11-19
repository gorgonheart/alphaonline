# 历史 `docs/`、`research/` 与根目录 `.md` 归档索引

> 所属体系：[统一开发入口](../README.md) · 本索引梳理两仓现存 140+ 份历史文档与 `research/` 证据目录的分类定位，方便按需直达一手数据，无需全盘翻找。**历史方案/草稿均不等于现行状态，以 [README.md](../README.md) 与当前代码为准。**

---

## 1. 两仓根目录 `.md` 文件定位

| 文件 | 角色与当前状态 |
| --- | --- |
| `D:/alphaonline/AGENTS.md` | Agent 自动加载规则入口，指向 [docs/README.md](../README.md) 与 [docs/DEVELOPMENT.md](../DEVELOPMENT.md) |
| `D:/alphaonline/CLAUDE.md` | Claude Code 仓库概览（架构流程图、模块地图、命令速查），顶部已指向统一入口 |
| `D:/alphaonline/plan.md` | 2026-11-18 落地方案：问题 A（A1 + A′）**已于 2026-11-19 实施上线**；问题 B（24h `[未匹配]` 归属回填）**待实施** |
| `D:/alpha/AGENTS.md` | 生产者端 Agent 入口，指向两项目统一文档与生产者红线 |
| `D:/alpha/CLAUDE.md` | 生产者端 BMAC 架构速查（run loop、增量模块 1–5、C 扩展、Arrow 发布） |
| `D:/alpha/README.md` / `更新说明.md` / `修改内容.md` / `部署命令.md` | 生产者原始项目说明、增量模块 1–5 原始变更及历史手工部署命令（最新 Linux 部署以 `D:/alpha/docs/DEPLOYMENT.md` 为准） |
| `D:/alpha/jazzy-shimmying-valiant.md` | 历史阶段性草稿笔记（非现行规范） |

---

## 2. `D:/alphaonline/docs/` 专题文档分类导航（共 118+ 份）

### A. MODEL_ROBUST40 架构、初始化与恢复回放
- [MODEL_ROBUST40.md](../MODEL_ROBUST40.md)：v1 独立偏移接口、结果与上线条件
- [2026-10-29-strategy-isolation.md](../2026-10-29-strategy-isolation.md) / [2026-10-29-live-baseline-audit.md](../2026-10-29-live-baseline-audit.md) / [2026-10-29-live-backtest-differences.md](../2026-10-29-live-backtest-differences.md)
- [2026-10-30-server-initialization-review.md](../2026-10-30-server-initialization-review.md) / [2026-10-30-robust40-prewarm.md](../2026-10-30-robust40-prewarm.md) / [2026-10-30-dual-runtime-review.md](../2026-10-30-dual-runtime-review.md)
- [2026-10-31-checkpoint-live-review.md](../2026-10-31-checkpoint-live-review.md) / [2026-10-31-replay-observability.md](../2026-10-31-replay-observability.md) / [2026-10-31-model-price-revision.md](../2026-10-31-model-price-revision.md) / [2026-10-31-model-instrument-units-review.md](../2026-10-31-model-instrument-units-review.md)
- [2026-11-05-s3-listing-coverage-hotfix.md](../2026-11-05-s3-listing-coverage-hotfix.md) / [2026-11-05-rebuild-handoff-implementation.md](../2026-11-05-rebuild-handoff-implementation.md)

### B. 性能剖析、共享计算与资源预算
- [2026-11-01-hybrid-cache-ubuntu-review.md](../2026-11-01-hybrid-cache-ubuntu-review.md) / [2026-11-01-model-continuation-performance.md](../2026-11-01-model-continuation-performance.md) / [2026-11-01-timeseries-factor-audit.md](../2026-11-01-timeseries-factor-audit.md)
- [2026-11-02-fresh-initialization-performance.md](../2026-11-02-fresh-initialization-performance.md) / [2026-11-02-initialization-resource-review.md](../2026-11-02-initialization-resource-review.md) / [2026-11-02-low-memory-initialization-review.md](../2026-11-02-low-memory-initialization-review.md)
- [2026-11-03-shared-compute-review.md](../2026-11-03-shared-compute-review.md) / [2026-11-03-initialization-data-refactor-review.md](../2026-11-03-initialization-data-refactor-review.md)
- [2026-11-04-whole-flow-performance-review.md](../2026-11-04-whole-flow-performance-review.md) / [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) / [2026-11-04-shared-io-optimization-review.md](../2026-11-04-shared-io-optimization-review.md) / [2026-11-04-ubuntu-five-hour-review.md](../2026-11-04-ubuntu-five-hour-review.md)
- [2026-11-05-next-version-scope.md](../2026-11-05-next-version-scope.md) / [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md)
- [2026-11-06-alpha1112-factor-block-implementation.md](../2026-11-06-alpha1112-factor-block-implementation.md) / [2026-11-06-factor-block-causal-review.md](../2026-11-06-factor-block-causal-review.md) / [2026-11-06-factor-release-readiness.md](../2026-11-06-factor-release-readiness.md)

### C. 统计、盈亏与成本估算
- [2026-10-31-statistics-phase1-review.md](../2026-10-31-statistics-phase1-review.md) / [2026-10-31-statistics-summary-review.md](../2026-10-31-statistics-summary-review.md)
- [2026-11-01-statistics-bn-snapshot-review.md](../2026-11-01-statistics-bn-snapshot-review.md) / [2026-11-01-statistics-display-and-unmatched.md](../2026-11-01-statistics-display-and-unmatched.md)
- [2026-11-03-alphatrade-55m-statistics-review.md](../2026-11-03-alphatrade-55m-statistics-review.md)
- [2026-11-06-spot-cost-estimate.md](../2026-11-06-spot-cost-estimate.md) / [2026-11-06-hourly-statistics-deployment.md](../2026-11-06-hourly-statistics-deployment.md)

### D. 第三方框架升级、磁盘治理与双线发布
- [superpowers/specs/2026-10-14-upgrade-core-2.3.1.md](../superpowers/specs/2026-10-14-upgrade-core-2.3.1.md) / [2026-11-06-upgrade-core-2.3.3-implementation.md](../2026-11-06-upgrade-core-2.3.3-implementation.md)
- [2026-11-05-production-disk-policy.md](../2026-11-05-production-disk-policy.md) / [2026-11-05-alphatrade-disk-cleanup.md](../2026-11-05-alphatrade-disk-cleanup.md) / [2026-11-05-lifecycle-production-deployment.md](../2026-11-05-lifecycle-production-deployment.md)
- [2026-11-03-test-server-proxy.md](../2026-11-03-test-server-proxy.md) / [2026-11-04-test-server-swap.md](../2026-11-04-test-server-swap.md) / [2026-11-04-ubuntu-four-offset-test.md](../2026-11-04-ubuntu-four-offset-test.md) / [2026-11-04-four-coins-root-causes.md](../2026-11-04-four-coins-root-causes.md)
- [2026-11-06-production-release.md](../2026-11-06-production-release.md) / [2026-11-06-main-consolidation.md](../2026-11-06-main-consolidation.md)

### E. `docs/superpowers/{specs,plans}/`
- 存放各阶段的正式设计规格（specs）与分步实施计划（plans），涵盖 Arrow mmap、Polars 截面、2.2.1/2.3.0/2.3.1/2.3.3 升级、1h 损益匹配、即时重建交接、1.1.12 与因子块、offset 预选等。

---

## 3. `D:/alpha/docs/` 生产者文档导航（共 22 份）

- **总入口与部署**：[DEVELOPMENT.md](../../../alpha/docs/DEVELOPMENT.md)、[DEPLOYMENT.md](../../../alpha/docs/DEPLOYMENT.md)
- **采集与调度**：[2026-10-29-funding-rate-api.md](../../../alpha/docs/2026-10-29-funding-rate-api.md)、[2026-10-30-backfill.md](../../../alpha/docs/2026-10-30-backfill.md)、[2026-10-31-dispatch-prewarm.md](../../../alpha/docs/2026-10-31-dispatch-prewarm.md)、[2026-10-31-shared-exchange-info.md](../../../alpha/docs/2026-10-31-shared-exchange-info.md)
- **发布、磁盘与代理**：[2026-11-03-test-server-deployment.md](../../../alpha/docs/2026-11-03-test-server-deployment.md)、[2026-11-05-arrow-single-snapshot.md](../../../alpha/docs/2026-11-05-arrow-single-snapshot.md)、[2026-11-05-coin-cap-proxy.md](../../../alpha/docs/2026-11-05-coin-cap-proxy.md)、[2026-11-05-disposable-test-directory.md](../../../alpha/docs/2026-11-05-disposable-test-directory.md)
- **底层设计与 C 扩展**：`D:/alpha/docs/superpowers/{specs,plans}/`（历史深度拆分、旧动量/ILLQ/CCI C 扩展、并行因子、Write-Once + Arrow 发布、重启策略起点保留）。

---

## 4. `D:/alphaonline/research/` 目录结构说明（`.gitignore` 忽略，本地证据库）

`research/` 为本地离线实验、日志、采样脚本与对账报告存储区，**不纳入 Git 版本控制，严禁业务源码引用**：

- **线上/本地对账脚本**：`research/live25_capture.py` / `live25_compare.py`（历史 `2026-10-15` 诊断结论已固化在 [DEVELOPMENT.md](../DEVELOPMENT.md#L865-L875)）
- **共享计算与性能基准**：`research/shared_compute_20261103/{measurements,paired-measurements}.md`、`research/speed_followup_20261104/`、`research/ubuntu_review_20261104_0606/`、`research/hybrid_cache_20261101/`、`research/model_continuation_20261101/`、`research/timeseries_audit_20261101/`
- **发布/隔离实验与脚本**：`research/upgrade233_deploy/`、`research/arrow_*`、`research/lifecycle-*`、`research/disposable_*`、`research/ubuntu_1755/`、`research/alpha_*`（代理/Mihomo/节点与发布辅助脚本）
- **历史附挂 worktree**：`research/coin-cap-proxy/`（对应 `D:/alpha` 分支 `codex/coin-cap-proxy-20261105`）
- **历史测试临时目录**：`research/pytest_*`（历史 pytest 临时运行残留，无业务依赖）
