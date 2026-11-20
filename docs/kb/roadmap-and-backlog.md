# 未完成与未尝试的任务方向（优先级清单）

> 所属体系：[统一开发入口](../README.md) · 严格区分“已设计未实施”“已本地提交未发布”“需先观测证据再定”，不把待办当已完成。
> 专项性能规划与跟进：[2026-11-19 Ubuntu 4c8g 性能深度优化规划与跟进](../2026-11-19-performance-optimization-plan.md)（含主交易分阶段优化、初始化 Burst↔Yield 动态调度与线上统计配置矩阵）。

---

## P0：近期明确待落地 / 待验收项

| 编号 | 任务方向 | 现状与依据 | 推进前置条件 / 备注 |
| --- | --- | --- | --- |
| **Q1** | **问题 B：24h 盈亏报告 `[未匹配]` 归属回填** | **已完成并上线测试与正式（`940c487`，2026-11-20）**：`build_recent_strategy_backfill_map(..., now=...)` + `build_strategy_maps` 合并（`{**backfill, **current}`）+ `run_time` 基准防 `55m` 跨整点漏档 + `None` 兜底；三端 127/127 全过，生产真实存档命中 `108/151`、`110/154` 币种 | 待下一自然整点实盘推送观测 24h `[未匹配]` 条数归零。 |
| **Q2** | **本地统计展示精简提交 `6c02e58` 合发** | **已随 `940c487` 上线测试与正式（2026-11-20）**，本地 `main` 与 `production/main` 0 分叉 | 已闭环。 |
| **Q3** | **正式机新上币扩张 + F1 移除治理自然实战验收** | `edbbba3` 已于 2026-11-20 凌晨 `ANTBUSDT` 新增实战通过（6 户零重建）；`8406c3f`（Phase 0–3 + F1）已于 2026-11-20 `14:15` 上线生产并在 `15m` 首轮验收性能 | 后续持续观测自然退市/美股休市（`removed_series` 分级放宽）与下一次自然初始化（Phase 2 `BURST ↔ YIELD`）线上遥测。 |
| **Q4** | **正式机磁盘空间治理（当前可用仅 25 GiB / 86%）** | 根分区 `178G` 已用 `146G`，目录中仍存多份历史发布归档包（`*.bundle`、`*.tar.gz`、`alphatrade1` 等） | 须按 [production-limits-and-ops.md](production-limits-and-ops.md) 逐项盘点归属、经用户明确授权后再清理，严禁盲删业务状态。 |

---

## P1：中短期高确定性技术改进

| 编号 | 任务方向 | 现状与依据 | 边界与要求 |
| --- | --- | --- | --- |
| **F1** | **问题 A Phase 2：`removed_series` 分级治理 + 单市场熔断** | **已上线生产（`8406c3f`）与测试机**：`tools/robust40/handoff.py` 增加 `max(30, 8%)` 单市场熔断（`UpstreamUniverseDropError`）+ 懒查 `state.pkl` 零持仓放宽 + 持仓币 pivot 核对与 `removed_held_symbols` 告警；上游 `alpha2` 保持 0 改动；双端 94/94 离线测试通过 | 待下一次自然退市/停盘轮在生产日志确认零误重建。 |
| **F1b** | **多级稀疏 Checkpoint（27–168h 每 24h 1 点）** | 当前 `CHECKPOINT_POINTS=26`（仅覆盖 26h），而 `REVISION_HISTORY_HOURS=168`（7 天）；27–168h 内若有真实历史值回补仍需全量重建 | 选做加固：在近 26h 逐小时 checkpoint 外，每 24h 额外保留 1 点（+6 点，单户 `< 2 MB`），需同步评估 `core/utils/checkpoints.py`、`sections/` 与 `ModelRobust40Strategy.py` 保留契约。 |
| **F2** | **跨平台 21 个现货准入差异闭环（Windows vs Linux）** | `2026-10-15` 15m 对账定位：Linux `fork` 继承统一账户杠杆现货规则，Windows `spawn` 的 dummy client 重取普通现货规则；隔离实验证实显式传递规则可消除差异，**业务修复尚未实施** | 见 [DEVELOPMENT.md 2026-10-15 诊断记录](../DEVELOPMENT.md#L865-L875)。 |
| **F3** | **时序因子前几大 CPU 热点单项优化** | `2026-11-06` 剖析：`ZfMeanQ`（29.2核秒）、`Cci异常隔离168_因果版`（23.5核秒）、`111_输入隔离`（17.0核秒）、`CorrBTC`（16.3核秒）、`BiasV1`（13.6核秒）、市值逐行处理（29.4核秒） | 必须单项独立 Python oracle + TDD + 低噪声同机全成本 ABBA 验收（严防像市值 log10 候选那样微基准快而整轮不快）。 |
| **F4** | **真实下单相邻 `ticker` 复用评估（原 P4 延续）** | `calc_target_position` 与 `calc_order_amount` 分别请求 spot/swap ticker；现 P1 已消除中间 13s 编译延迟，可结合线上执行分段日志评估是否复用同一快照 | 不改变 TWAP、资金等待、补 BNB 与成交/失败统计语义。 |

---

## P2：条件推进 / 长期观察项（需新证据触发）

| 编号 | 任务方向 | 说明与触发条件 |
| --- | --- | --- |
| **O1** | **更广范围子策略模拟行情复用（P2 扩展）** | 目前仅同再择时 `state0→state1` 复用；跨 14 个子策略共享 worker 只读行情需先拆 `read/align/kernel/save` 确认收益大于内存峰值代价。 |
| **O2** | **修订检查 Arrow 读取进一步窄化（P5）** | D1 切 5000 根后正式已从 `18.3s` 降至 `5–8s`；仅在仍有明显压力时再评估列投影/转换。 |
| **O3** | **聚合慢轮与传统 45m 聚合定位** | 传统 45m 聚合中位 `22.4s`、MODEL 偶发单轮 `26.4s`；先拆读盘/计算/换页，再决定是否核对并启用备用 v2 算法（见 [rejected-and-failed-attempts.md](rejected-and-failed-attempts.md)）。 |
| **O4** | **1.1.12 完整保留历史映射差异的离线账户验收** | `1000CATUSDT` / `CATUSDT` 历史映射修正已在代码侧就绪，若对历史长窗口跑真实账户选币/仓位影响分析，需单独离线对账。 |
| **O5** | **异步统计与下一 offset 资源争用 + 24h 归属旁路索引** | 仅当实际观测到统计与下一轮交易争抢 CPU/IO，或 24 个选币归档读取超过 `0.3s` 时再介入。 |
| **O6** | **初始化 MODEL / 跨日 / 季度重训专项剖析** | 下一次自然初始化或季度边界时拆分 `raw-read / features / experts / train / save`，不清有效 snapshot 造样本。 |
| **O7** | **清理历史遗留 Worktree / Stash** | `s3-hotfix` detached worktree、`research/coin-cap-proxy` worktree、`stash@{0}` 确认无追溯需求后可经授权清理。 |
