# 未完成与未尝试的任务方向（优先级清单）

> 所属体系：[统一开发入口](../README.md) · 严格区分“已设计未实施”“已本地提交未发布”“需先观测证据再定”，不把待办当已完成。

---

## P0：近期明确待落地 / 待验收项

| 编号 | 任务方向 | 现状与依据 | 推进前置条件 / 备注 |
| --- | --- | --- | --- |
| **Q1** | **问题 B：24h 盈亏报告 `[未匹配]` 归属回填** | [plan.md](../../plan.md) §2 已完成设计（B1：`build_recent_strategy_backfill_map` 增加 `now` 基准、新增 `build_strategy_maps` 按 `{**backfill, **current}` 合并、`statistics.py` 与 `statis_func.py` 接入；实测 25 文件读取仅 0.106s） | 触及 `core/utils/statis_func.py` 与 `core/utils/statistics.py`，需先决定是否与未发布的本地提交 `6c02e58` 合并发布，或基于 `edbbba3` 单独发。 |
| **Q2** | **决定本地未发布提交 `6c02e58` 的去留** | `2026-11-07` 统计展示精简与多空分侧前 5 仅在本地 `main`，未发正式/测试 | 需用户确认是否与 Q1 同批发布到生产与测试。 |
| **Q3** | **测试机同步 `edbbba3` + 正式机自然新币上市验收** | 正式机已部署 `edbbba3` 并在 `25m` 正常跑完首轮；测试机 `43.173.122.28` 尚未同步 | 观测下一次上游新增交易对时 `logs/server_robust40.log` 中 `mode=incremental`、`added_columns`/`added_column_names` 及后续 T+1 轮零 `rebuild_required`、零 `ValueError`。 |
| **Q4** | **正式机磁盘空间治理（当前可用仅 25 GiB / 86%）** | 根分区 `178G` 已用 `146G`，目录中仍存多份历史发布归档包（`*.bundle`、`*.tar.gz`、`alphatrade1` 等） | 须按 [production-limits-and-ops.md](production-limits-and-ops.md) 逐项盘点归属、经用户明确授权后再清理，严禁盲删业务状态。 |

---

## P1：中短期高确定性技术改进

| 编号 | 任务方向 | 现状与依据 | 边界与要求 |
| --- | --- | --- | --- |
| **F1** | **问题 A Phase 2：零持仓退市/移除序列放宽** | 当前 `removed_series` / `removed_columns` 保持保守（仍触发全量重建），已埋 `removed_series_keys` / `removed_column_names` 遥测 | 连续观察两周若确认存在“仅因无持仓币移除触发重建”，再做 `state.pkl` 持仓检查（确认所有虚拟账户 `lots == 0` 才放宽）。 |
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
