# 当前分支、未发布提交、Stash、Worktree 与多机同步状态

> 所属体系：[统一开发入口](../README.md) · 核对时间：`2026-11-20 14:22+08:00`

---

## 1. 未合并主干的分支检查结论

- **`D:/alphaonline`**：`git branch --no-merged main` 为**空**（包含 `release/perf-phase0-3-and-removed-series` 在内的全部本地分支已合入 `main`）。
- **`D:/alpha`**：`git branch --no-merged main` 为**空**（当前本地 7 个分支已全部合入 `main`）。

> **重要区分**：“分支已合入本地 `main`” ≠ “已发布到正式/测试服务器”。当前存在 **1 个已合入本地 `main` 但未发布到生产**的提交，见下文 §2。

---

## 2. 本地 `main` 与生产/测试环境同步差异

| 仓库 | 本地 `main` | 正式服务器（`43.167.235.164`） | 测试服务器（`43.173.122.28`） | 关键差异说明 |
| --- | --- | --- | --- | --- |
| **`D:/alphaonline`** | `0b34ac6`（及文档提交） | `8406c3f`（`production/main`） | 增量同步至 `8406c3f`（含 Phase 0–3 + F1） | ① 本地 `main` 仍比生产多 1 个未发布提交 **`6c02e58`**（`2026-11-07` 统计展示精简与多空分侧前 5，涉及 `core/utils/statis_func.py`、`core/utils/statistics.py`、`tools/spot_cost_estimate.py`）；<br>② 正式机已于 `2026-11-20 14:15` fast-forward 升级至 `8406c3f`（Phase 0–3 性能优化 + F1 `removed_series` 分级治理与熔断，含 `foreground_max_age_seconds=3600` 僵尸过滤），已于 `0b34ac6` 合并回本地 `main`。 |
| **`D:/alpha`** | `41686c3` | `41686c3`（`production/main`） | `2026-11-06` 源码全文件对齐 `494d22a` | 生产与本地完全同步（`main...production/main` 0 差异）。 |

---

## 3. 历史已合入分支列表（可作为历史追溯参考）

### `D:/alphaonline`（均已合入 `main`）
- `codex/fix-hourly-statistics-pnl`（`c217ca1`）：1h 损益计划轮次匹配（已上线）
- `codex/upgrade-core-2.3.3`（`827128f`）：2.3.3 升级与现货成本（已上线）
- `codex/next-runtime-efficiency`（`2827d27`）：后半程 P1–P4、Arrow 联动、即时重建（已上线）
- `codex/model-history-config-cleanup`（`3d6f809`）：MODEL 历史配置清理（已上线）
- `codex/live-offset-parity`（`debac03`）：MODEL_ROBUST40 v1 独立偏移移植（已上线）
- `codex/robust4h-draft-20261029`（`5098bb7`）：早期 Robust4H 草稿存档（**不作为现行功能**）
- `upgrade/core-2.3.0`（`5098bb7`）、`upgrade/core-2.2.1`（`481cf95`）：历史升级分支

### `D:/alpha`（均已合入 `main`）
- `codex/coin-cap-proxy-20261105`（`7fd4356`）：市值代理开关（已合入 `494d22a`）
- `codex/arrow-demand-publication`（`b9cdd13`）：Arrow 需求联动 + 1.1.12（已上线）
- `codex/disposable-production-tests-20261105`（`ef4af11`）：可删除 `test/`（已上线）
- `codex/arrow-single-snapshot-20261105`（`9e5e15d`）：单轮 Arrow 保留（已上线）
- `codex/test-server-deployment-20261103`（`d9c8371`）：测试机部署与全量 Arrow（已上线）
- `codex/alpha2-deployment-20261102`（`8d9747b`）：`alpha2` Linux 部署（已上线）

---

## 4. 现存 Stash、额外 Worktree 与未跟踪文件

- **Stash**：
  - `D:/alphaonline` 存在 1 条历史保护 stash：`stash@{0}: On codex/upgrade-core-2.3.3: preserve user release notes during main integration`（内容已包含于主干，仅保留防丢，切勿盲目 `pop` 覆盖）。
  - `D:/alpha` 无 stash。
- **附加 Worktree**（勿误删其内容，如不再需要须经授权再用 `git worktree remove`）：
  - `D:/alphaonline`：`C:/Users/Administrator/.codex/visualizations/2026/10/30/01a18cde-ca8d-7b32-84c7-4644dfdedcaa/release-cleanup/s3-hotfix`（detached HEAD `f4361a6`）。
  - `D:/alpha`：`D:/alphaonline/research/coin-cap-proxy`（检出分支 `codex/coin-cap-proxy-20261105`，`7fd4356`）。
- **两仓未跟踪参考物（保留，不纳入提交）**：
  - `D:/alphaonline`：`2.3.3core/`（及忽略的 `2.1.1core`～`2.3.1core`）、`plan.md`、`.tmp_cmp.py`、若干 `docs/2026-11-05~06-*.{md,json,csv}` 现场对账报告、`research/`（被 `.gitignore` 忽略）。
  - `D:/alpha`：`coin-realtime-data_v1.1.12/`、`CLAUDE.md`、`factors_old/`、若干历史诊断脚本（`diag_*.py`、`tests/benchmark_momentum_v2.py`、`tests/diagnose_funding_http.py` 等）。
