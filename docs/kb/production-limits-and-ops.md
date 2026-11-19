# 正式环境存储、内存、资源限额与运维规范

> 所属体系：[统一开发入口](../README.md) · 综合 [2026-11-05-production-disk-policy.md](../2026-11-05-production-disk-policy.md) 与 `43.167.235.164` 2026-11-19 实测资源状态。

---

## 1. 正式服务器硬件与当前水位（`43.167.235.164`，2026-11-19 实测）

| 资源 | 总量 | 已用 | 可用 | 使用率 / 备注 |
| --- | --- | --- | --- | --- |
| 根分区 `/`（`/dev/vda2`） | `178 GiB` | `146 GiB` | **`25 GiB`** | **86%**（需密切警惕，历史曾因磁盘满触发 PM2 丢清单与 ENOSPC） |
| 物理内存 RAM | `7.5 GiB` | `1.5 GiB` | `6.0 GiB`（含 buff/cache `1.9 GiB`） | 典型 4c8g 规格 |
| Swap | `17 GiB` | `6.8 GiB` | `11 GiB` | 支撑大历史初始化时的换出峰值 |

> 磁盘空间紧张背景：单台正式机同时承载 `alpha2` + `alphatrade`（6 个 MODEL 账户）以及旧实例 `alpha` + `alphatradetest`（5 个传统账户）和历史备份归档；每个 offset 全量历史约 `7.3 GiB`，单户 MODEL 有效快照约 `0.7–1.4 GiB`。

---

## 2. MODEL 运行资源与 cgroup 限额（正式机 `tools/robust40/initialization_config.py`）

正式机已显式开启以下配置（保留在本地工作区差异中，部署不可覆盖）：

| 参数 | 正式机当前值 | 含义与边界 |
| --- | --- | --- |
| `INITIALIZATION['data_pipeline']` | `True` | 启用共享时序→IPC 编排（仅初始化/大计算使用） |
| `INITIALIZATION['factor_symbol_batch_size']` | `32` | 32 币分片写块 |
| `INITIALIZATION['selection_block_hours']` | `2016` | 选币时间块大小（非内存上限） |
| `INITIALIZATION['minimum_history_bars']` | `14000` | 全量初始化最低历史门槛 |
| `MODEL_JOB_NUM` | `4` | MODEL 唯一总并发上限（时序最多 4 进程，数值库单线程） |
| `LIVE_BARS` | `4050` | 普通续算交易最终保留根数 |
| `ARROW_DEMAND_ENABLED` | `True` | 联动上游按需发布（正常轮 ≥5000 根，初始化/重建请求全量 `rows=0`） |
| `CHECKPOINT_POINTS` | `26` | 恢复点深度（覆盖 `cutoff-24h` 及其严格更早恢复点） |
| `REVISION_HISTORY_HOURS` | `168` | 修订比对回看窗口（7 天） |
| `DIAGNOSTIC_LOG_COUNT` | `24` | 失败诊断日志保留上限 |

### Linux 自动初始化 cgroup 策略（`RESOURCE_POLICY`）

- **全局单槽位**：同时只允许 **1 个**账户执行后台全量初始化（每户实测 13–15 分钟，6 户串行约 84 分钟——这也是 2026-11-19 修复新币误判重建如此关键的原因）。
- **内存与 Swap 上限**：`MemoryHigh = 4.5 GiB`（`exclusive_memory_bytes`），`MemoryMax = 5.0 GiB`（`max_memory_bytes`），`SwapMax = 12 GiB`，预留 `64 MiB` 监督进程余量。
- **CPU 与优先级**：初始化 `nice=10`、`cpu_weight=10`、`backtest_workers=1`；正常交易 `nice=3`、`backtest_workers=3`。
- **压力准入与保护**：可用内存 `< 256 MiB` 不准入；`< 128 MiB` 持续 `30s` 触发紧急保护；`memory_full_limit=25%`、`io_full_limit=50%`。

---

## 3. 磁盘生命周期与目录所有权红线

### 3.1 四类目录严格区分

| 目录类别 | 典型路径 | 清理规则与归属 |
| --- | --- | --- |
| **1. 业务持久状态（禁删）** | `data/snapshot/<account>_*`<br>`data/子策略回测结果/`<br>`alpha2/data/preprocess_1h_resample/`<br>`preprocess_1h_resample_origins/`<br>`.arrow-demand/*.json` | 有效快照、S3/MODEL 恢复点、账户交易/统计记录、策略起点、Arrow 需求控制文件；严禁当作临时文件删除。 |
| **2. 业务临时工作区（受控自动回收）** | `data/server_initialization/`<br>`data/model_replay/` | 属于业务工作区，**不属于**可随意删除的 `test/`。由 `tools/robust40/lifecycle.py` 持账户锁、排除活跃进程后自动回收失败现场，仅保留最多 64 KiB 损坏诊断与 24 份日志。 |
| **3. 可删除测试目录（纯隔离）** | `/home/ubuntu/alphatrade/test/`<br>`/home/ubuntu/alpha2/test/` | 所有测试、对拍、微基准、验证缓存、回退备份均须放此目录内；**整个 `test/` 可随时人工删除，不影响任何业务导入与运行**。`tools/run_deployment_tests.py` 退出后自动删除自身 `verify-*`，保留 `test/logs/` 最近 24 份。 |
| **4. 生产者单轮 Arrow 快照** | `alpha2/data/preprocess_1h_resample/<offset>/*.arrow` | `arrow_snapshot_keep=1`：新快照原子发布成功后再清旧轮，失败回收 `.tmp`。 |

### 3.2 硬链接与峰值空间陷阱

- 初始化冻结行情优先使用**硬链接**。硬链接不额外占逻辑块，但**只要工作区未释放，上游 `unlink` 旧 Arrow 后物理磁盘块仍不会回收**（2026-11-05 曾因 7 次失败初始化残留硬链接白占 26 GiB）。
- `arrow_snapshot_keep=1` 不等于发布峰值只占 1 份空间：原子替换期间新 `.tmp` 与旧快照并存，需预留至少 1 份额外写入空间。
- **严禁**在正式机默认开启每小时全量行情/快照采集；任何新增写盘点必须在设计中明确生产者、消费者、释放时机与异常回收策略。

---

## 4. 生产发布与回退操作清单

1. **发布窗口**：避开整点/各 offset 密集发布期（优先在 `:19–:24` 等无活跃 `server_robust40` worker 的空闲间隙更新）。
2. **先隔离验证，再更新工作区**：推送至 `/home/ubuntu/repos/*.git` 裸仓 → 在 `test/verify-*` 克隆该提交并用目标 `alpha311` 离线跑完相关 pytest → 备份待变更旧文件到 `test/<release>/runtime-before/` → `git pull --ff-only origin main` → 禁网冒烟校验 → 核对 `startup.py` PID 与所有快照 `complete=True`。
3. **无重启生效边界**：只修改交易子进程加载模块（如 `handoff.py`、`telemetry.py`、`ModelRobust40Strategy.py`、`statis_func.py`、`statistics.py`）时，无需重启 `startup.py`，下一自然轮子进程自动编译并加载新代码（注意：修改含 Numba `@jit`/`@jitclass` 的模块时，首轮会触发约 25–30s 的一次性 Numba 磁盘缓存重编译，次轮恢复常态）。
4. **必须重启边界**：修改 `startup.py`、`Coordinator` 或更换 Python 环境时才需重启 PM2，且严禁在 PM2 `interpreter` 参数中传入含 `/home/ubuntu` 的绝对路径。
5. **当前线上备份点**：`2026-11-19` 发布 `edbbba3` 前的 4 个原文件备份在 `/home/ubuntu/alphatrade/test/universe-expansion-fix-20261119/runtime-before/`。
