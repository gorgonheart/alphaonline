# 当前分支、未发布提交、Stash、Worktree 与多机同步状态

> 所属体系：[统一开发入口](../README.md) · 核对时间：`2026-11-27 09:31+08:00`

---

## 1. 未合并主干的分支检查结论

- **`D:/alphaonline`**：`git branch --no-merged main` 为**空**；本地 `main`、正式机 `production/main`（PID `374502`）与测试机 `alphatest`（PID `1884098`）已三端统一对齐 **`5cab8f8`**。
- **`D:/alpha`**：`git branch --no-merged main` 为**空**；本地 `main`、正式机 `production/main`（`alpha2` PID `374475`）与测试机 `alphatest`（`realtime_data` PID `2179086`）已三端统一对齐 **`5694033`**。

> **主干同步状态**：双仓本地 `main` 与正式机 `production/main` **0 分叉、0 未发布提交**，正式机与测试机代码完全一致（仅各机 `config.json` 与 `accounts/` 按环境独立维护；正式机并行旧实例 `alpha` / `alphatradetest` 未触碰）。

---

## 2. 本地 `main` 与生产/测试环境同步差异

| 仓库 | 本地 `main` | 正式服务器（`43.167.235.164`） | 测试服务器（`43.173.122.28`） | 关键差异说明 |
| --- | --- | --- | --- | --- |
| **`D:/alphaonline`** | `5cab8f8` | `5cab8f8`（`production/main`，`alphatrade` PID `374502`，主机开关已持久化至 `config.json`） | `5cab8f8`（`alphatrade-dryrun` PID `1884098`） | 三端代码 0 差异；正式机 6 户实盘 `none` 模式，测试机单户 `dry_run` 模式。 |
| **`D:/alpha`** | `5694033` | `5694033`（`production/main`，`alpha2` PID `374475`，`use_ws=True`） | `5694033`（`realtime_data` PID `2179086`） | 三端代码 0 差异；正式机 `kline_count_1h=15000` 直连，测试机走 Mihomo 代理。 |

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
