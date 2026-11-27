# PR-05：Step 4/5/6 策略池多进程并行与 LPT（最长任务优先）调度

- **拟提交标题**：`perf(backtest): parallelize Step 4/5/6 multi-strategy pools with LPT scheduling and configurable worker limit`
- **优先级**：P0（多子策略账户核心提速项；**需配置主机分档并发上限**）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**（本 PR 是 **PR-06** 跨策略只读 Pivot 共享的前置依赖）

---

## 1. 场景

仓位管理账户的 `strategy_pool` 包含多个独立子策略组合（例如 6~14 个 `BacktestConfig`）。在 `run_backtest_multi` 中，Step 4（选币）、Step 5（选币结果聚合）与 Step 6（资金曲线模拟与再择时）按组合逐一执行，组合之间相互独立。

## 2. 目标

将 `run_backtest_multi` 的 Step 4、Step 5、Step 6 从单进程串行 `for conf in conf_list:` 升级为有界多进程池（`ProcessPoolExecutor`）并发执行，并采用 **LPT（Longest Processing Time first，按子策略数量降序）** 排序提交与 `as_completed` 实时收割，同时支持通过 `config.backtest_job_num` 统一控制并发度。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/backtest.py:212-246`：
1. Step 4（`select_coins`）、Step 5（`step5_aggregate_select_results`）、Step 6（`step6_simulate_performance`）全部在主进程中串行循环，即使主机有 4 核也只利用 1 核。
2. 当策略池中各组合包含的子策略数量不均衡时（如部分组合含 6~8 个子策略或再择时，部分仅含 1 个），若长任务排在队尾，并发尾延迟会显著拉长。
3. 原版 Step 6 未按原始顺序重排结果列表（并发完成后直接 append 会打乱 `equity_list` 与 `conf_list` 的一一对应关系）。

## 4. 项目文档中的相关分析与设计

- 架构与落地记录：[与外部版本 core 的关系](../kb/upstream-core-relations.md)（§2.3）、[重要性能分析](../kb/performance-analysis.md)（§1.2）
- 参考提交：`9451bdd`（Step 4/5/6 `ProcessPoolExecutor` + LPT + `backtest_job_num`）

## 5. 依赖

- **内部代码依赖**：无。但需确保 worker 函数（`_step4_worker`、`_step5_worker`、`_step6_worker`）为模块顶层函数，以兼容 Windows `spawn` 的 `pickle` 序列化。
- **上游 `alpha` Arrow 依赖**：**无**。
- **下游关系**：**PR-06**（Step 6 跨策略 Pivot 共享）直接建立在本 PR 的 Step 6 进程池架构之上。

## 6. 收益与证据

- **并行收益**：在 4c8g Ubuntu 上，14 个子策略组合的 Step 4/5/6 由单核串行升级为 4 核并发，配合 LPT 消除长尾后，Step 5 权重聚合可压至 `~1.9 s`，Step 6（叠加 PR-06 前约 `16.0 s`，叠加 PR-06 后 `5.38–10.4 s`）。
- **顺序等价**：Step 6 用 `equity_map[conf.name] = f.result()` 收割，最终 `[equity_map[conf.name] for conf in conf_list]` 严格恢复原始策略池顺序；`snapshot_time` 与磁盘产出完全等价。

## 7. 代码改动

### 目标文件
- `core/backtest.py`
- `config.py`（新增可选 `backtest_job_num` 配置项）

### 核心改动要点
1. 在 `core/backtest.py` 新增顶层可 pickle worker：`_step4_worker`、`_step5_worker`、`_step6_worker`。
2. 在 `run_backtest_multi` 中读取 `pool_workers = min(len(conf_list), getattr(config, 'backtest_job_num', ...))`：
   - **重要改进（面向通用 `v2.4.1` 用户）**：建议默认值按主机内存/核数自适应，例如 `min(max(os.cpu_count() // 1, 1), 4)` 且当可用/总物理内存 `< 6 GiB` 时自动收敛为 `1`（或暴露在 `config.py` 明确注释不同机型推荐值）。
   - 当 `pool_workers <= 1` 时，直接走原有的串行 `mem_save_exe` 路径，零进程池创建开销。
   - 当 `pool_workers > 1` 时，Step 4/5/6 均按 `sorted(conf_list, key=lambda c: len(c.strategy_list), reverse=True)` 提交 `ProcessPoolExecutor(max_workers=pool_workers)`，通过 `as_completed` 立即收割并释放已完成任务对象；Step 6 按 `conf_list` 恢复 `equity_list` 顺序。
3. Step 4 选币在多进程池内执行时，需同步注意子进程内部 `select_coin_with_conf` 的线程数，避免「4 进程 × 每进程多 Polars 线程」引发线程过载。

## 8. 2c4g / 2c8g 主机风险与适配建议

> ⚠️ **本 PR 是针对 4c8g 调优的典型高内存敏感项，不可在 2c4g 上盲目套用 `max_workers=4`！**

| 主机规格 | 风险等级 | 风险成因与强制适配建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 安全 | `backtest_job_num = 4`（默认上限），配合 PR-06 CoW 共享后 Step 6 物理内存净增约 `~2.05 GiB`，安全余量充足。 |
| **2c8g** | 🟡 **中风险** | 仅 2 核，开 4 进程只会增加上下文切换与内存副本，不会更快。**建议显式设 `backtest_job_num = 2`**；此时 8G 内存可轻松容纳 2 个并行 worker。 |
| **2c4g** | 🔴 **高风险（OOM / 严重 Swap 抖动）** | 若未合入 PR-06，每个 Step 6 worker 会各自读取一份全市场 Pivot（`~0.6–1.2 GiB`）；2~4 个 worker 并行极易直接突破 3.2 GiB 可用内存触发 OOM Killer 或数十秒 Swap 假死。**在 2c4g 上必须设 `backtest_job_num = 1`**（退回安全串行模式），或按总内存 `< 6 GiB` 自动降级为 `1`。 |
