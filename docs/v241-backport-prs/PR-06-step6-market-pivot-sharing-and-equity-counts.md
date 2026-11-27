# PR-06：Step 6 跨策略/再择时只读 Pivot 共享与 `equity.py` 计数向量化

- **拟提交标题**：`perf(equity,backtest): share read-only market pivots across Step 6 workers/retiming and vectorize position counts`
- **优先级**：P0（多策略回测模拟阶段最大单一收益项）
- **上游 `alpha` Arrow 依赖**：**无**（直接复用现有年度 `market_pivot_*.pkl` 与 `exginfo_*.pkl`）
- **其他 PR 依赖**：跨子策略 `fork`/`spawn` 共享依赖 **PR-05**；再择时 `state0→state1` 局部复用与 `equity.py` 向量化可独立生效

---

## 1. 场景

在 Step 6 资金曲线模拟阶段，策略池中每个子策略组合都要读取全市场现货/合约年度 `market_pivot_*.pkl` 与 `exginfo_*.pkl`；带再择时（`TimingSignal`）的策略还要连续执行两次 `calc_equity`（`state0` 原始模拟 → `simu_timing` 算动态杠杆 → `state1` 再择时模拟）。

## 2. 目标

1. 在单轮 Step 6 内将全市场 `market_pivot` 与 `exginfo` 的磁盘读取从 **N 次（甚至含再择时 N+K 次）压至 1 次**，且严守只读不篡改、退出即释放内存；
2. 消除再择时对刚落盘 `account_state0.pkl` 的冗余重读；
3. 消除 `core/equity.py` 中 `create_ratio_snapshot` 的多余 `reindex` 与 `calc_equity` 中 4 次全表浮点掩码 `.count(axis=1)`。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/backtest.py` 与 `core/equity.py`：
1. 14 个子策略（含 2 个再择时）每轮重复反序列化全市场 `market_pivot_{spot,swap}_*.pkl` 高达 **16 次**，磁盘 I/O 与 unpickle 占用 Step 6 超过 65% 的耗时。
2. `simu_timing` 紧接 `calc_equity(state0)` 之后，却无条件从磁盘重读刚写出的 `account_state0.pkl`。
3. `core/equity.py:322-323` 使用 `df_spot_ratio[df_spot_ratio > 0].count(axis=1)` 统计多空持仓币数，每行 4 次构造全表 NaN 浮点掩码 DataFrame，极其低效。

## 4. 项目文档中的相关分析与设计

- 局部再择时复用实施与验证：[2026-11-05 后半程优化实施记录](../2026-11-05-runtime-efficiency-implementation.md)（P2/P3）
- 跨子策略 Pivot 共享设计与 ABBA 基准：[2026-11-19 性能优化规划](../2026-11-19-performance-optimization-plan.md)（Phase 3 `P3-1`）、[重要性能分析](../kb/performance-analysis.md)（§2 Phase 3）
- `equity.py` 计数与快照去重：[2026-11-25 消费端第二批设计](../superpowers/specs/2026-11-25-v241-and-consumer-batch2-design.md)（§5.2 B2.2）
- 参考提交：`1dae6a2`（再择时复用）、`8b4280c`（Phase 3 `fork` CoW / `spawn` 签名缓存 + `malloc_trim`）、`e27fbe1`（`equity.py` NumPy 计数与去冗余 reindex）

## 5. 依赖

- **内部代码依赖**：
  - 跨子策略共享依赖 **PR-05** 的 `ProcessPoolExecutor` 框架；
  - 若与 **PR-07**（后台线程）联用，**严禁在 Step 6 `ProcessPoolExecutor` 执行 `fork` 之前启动后台线程**（防止 Linux 多线程 `fork` 死锁）。
- **上游 `alpha` Arrow 依赖**：**无**（仍读原有 `market_pivot_*.pkl`）。

## 6. 收益与证据

| 测量环境与口径 | 优化前 | 优化后 | 收益 |
| :--- | ---: | ---: | :--- |
| Ubuntu 4c8g 测试机 14 子策略 ABBA（`tools/benchmark_phase3_step6.py`） | `16.00 s` | `5.38 s` | **`-66.4%`（14/14 `check_exact=True`）** |
| Ubuntu 4c8g 正式机实盘 14 子策略（含 2 再择时） | 16 次读盘 | `STEP6_MARKET_PRELOAD 4.08 s` + `16/16 reused_market=True`（单次 `0.001–0.006 s`） | **整轮净省 `~10–12 s`** |
| 本地合成再择时 `state0→state1` 局部复用（14050×256） | `0.460 s`（4 次读盘） | `0.334–0.355 s`（2 次读盘） | **读盘减半（RSS `+29 MiB`）** |

- **只读不变性证明**：`tests/test_p3_pivot_cache.py` 断言所有缓存的 Pivot / Exginfo DataFrame 在多策略、新上币、下架币及再择时运行后与原始快照 `check_exact=True` 零修改，且文件 `mtime/size` 变更立即失效重载。
- **计数等价证明**：`tests/test_consumer_batch2_pipeline.py::test_stage6_equity_fast_counts_and_ratio_snapshot_match_oracle_exact` 覆盖正负/0/`NaN`/空列，与旧 `.count(axis=1)` 逐位一致。

## 7. 代码改动

### 目标文件
- `core/backtest.py`
- `core/equity.py`

### 核心改动要点
1. **`core/backtest.py`**：
   - 新增 `_bmac_market_file_sig(hour_offset)`、`_load_step6_market_inputs(hour_offset)`、`_get_worker_step6_market_inputs(hour_offset)` 与 Linux `_trim_process_heap()`（`libc.so.6::malloc_trim(0)`）；
   - Linux `fork` 模式且 `len(conf_list) > 1` 时，在 `ProcessPoolExecutor` 创建前主进程加载 1 次 `_STEP6_FORK_MARKET`，所有 worker 经 OS CoW 只读共享物理页；`finally` 中立即置 `None` + `gc.collect()` + `_trim_process_heap()`；
   - Windows `spawn` 模式退化为每 worker 有界 1 条 `(hour_offset, file_sig)` 缓存；
   - `step6_simulate_performance` 与 `simu_timing` 支持可选关键字参数 `market_inputs=None`、`account_df=None`，向后兼容默认读盘。
2. **`core/equity.py`**：
   - `calc_equity` 增加可选关键字参数 `market_inputs=None`（使用 `dict(market_inputs['pivots'][...])` 浅壳包装，绝不原地修改底层 DataFrame）；
   - `create_ratio_snapshot` 在 `pd.concat` 前对两端各自 `reindex(columns=final_columns, fill_value=0)`，删除拼接后的第 3 次冗余 `reindex`；
   - `calc_equity` 将 4 次全表掩码 `.count(axis=1)` 替换为 `np.errstate(invalid='ignore')` 下的 `(spot_arr > 0).sum(axis=1, dtype=np.int64) + (swap_arr > 0).sum(axis=1, dtype=np.int64)`（含 `object` dtype 防御转换）。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 风险成因与适配建议 |
| :--- | :---: | :--- |
| **4c8g（Linux）** | 🟢 安全 | `fork` CoW 实测真实物理内存净增仅 `~2.05 GiB`（虽然 `ps` 累计 RSS 显示 `4.8 GiB`，系 CoW 页重复计费），退出即 `malloc_trim(0)` 归还 OS。 |
| **2c8g（Linux / Windows）** | 🟡 **中风险（Windows 需注意）** | Linux 2c8g 设 `backtest_job_num=2` 完全安全；**Windows `spawn` 模式不支持 CoW**，每个 worker 会独立持有一份完整 Pivot（`~0.6–1.0 GiB`/worker），2c8g Windows 建议 `backtest_job_num <= 2`。 |
| **2c4g（Linux / Windows）** | 🔴 **高风险（需分平台控制）** | 1. 仅用 **PR-06 中的再择时复用 + `equity.py` NumPy 计数**在 2c4g 上 **🟢 完全安全且强烈推荐**；<br>2. 跨策略缓存方面：2c4g 设 `backtest_job_num=1` 时主进程串行复用一份 `market_inputs` 仅占 `~0.6–1.0 GiB`（反而比反复读盘更稳）；**严禁在 2c4g + Windows `spawn` 下开多个带缓存的 Step 6 worker**，否则多份完整 Pivot 并存必爆 4G 内存。 |
