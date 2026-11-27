# 回馈 `position-mgmt-trading_v2.4.1` 的通用性能优化 PR 索引

> **适用范围**：将 `alphaonline` 相对 `position-mgmt-trading_v2.4.1` 已落地并经离线 Oracle / 线上验收的**完全通用性能优化**，逐项拆分为可独立评审、按依赖顺序合入的 PR 草案。
> **基准说明**：本仓库现行参数面向 **4c8g（4 vCPU / 7.5–8 GiB RAM）Linux** 调优；每个 PR 均单独标注在 **2c8g** 与 **2c4g** 主机上的风险等级与参数回退建议，并显式标明是否依赖上游 `alpha` 数据中心输出 Arrow 快照。

---

## 一、全部通用 PR 总览矩阵（按推荐合入顺序）

| PR 编号 | 主题 | 优先级 | 前置依赖 | 上游 `alpha` Arrow 依赖 | 2c8g 风险 | 2c4g 风险 | 文档链接 |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| **PR-01** | `agg_pos_ratio` 列连续 NumPy 向量化 | P0 | 无 | **无** | 🟢 无（降峰值内存） | 🟢 无（降峰值内存） | [PR-01](PR-01-agg-pos-ratio-numpy.md) |
| **PR-02** | `data_bridge` 外部数据缓存与向量化 | P0 | 无 | **无** | 🟢 低 | 🟡 中（需收窄缓存上限） | [PR-02](PR-02-data-bridge-cache-and-vectorize.md) |
| **PR-03** | 时序因子跳 `shift(0)` 与直建 Polars | P0 | 无 | **无** | 🟢 无（降峰值内存） | 🟢 无（降峰值内存） | [PR-03](PR-03-factor-polars-direct-and-skip-shift0.md) |
| **PR-04** | 映射对齐向量化、删空扫与 `check_flags` 加速 | P0 | 无 | **无**（去硬睡依赖上游原子写 `.ready`） | 🟢 无 | 🟢 无 | [PR-04](PR-04-datatools-fast-align-and-ready-poll.md) |
| **PR-05** | Step 4/5/6 策略池多进程并行与 LPT 调度 | P0 | 无 | **无** | 🟡 中（建议 `backtest_job_num=2`） | 🔴 **高（4G 易 OOM，须设为 `1`）** | [PR-05](PR-05-backtest-multiprocess-lpt.md) |
| **PR-06** | Step 6 跨策略/再择时只读 Pivot 共享 + `equity` 计数向量化 | P0 | 跨策略共享依赖 **PR-05**；再择时与计数独立 | **无** | 🟡 中（Windows `spawn` 需控并发） | 🔴 **高（Windows `spawn` 严禁多 worker 缓存）** | [PR-06](PR-06-step6-market-pivot-sharing-and-equity-counts.md) |
| **PR-07** | 后半程读写并发：`FactorHub` 线程安全 + 并行 `process_equities` + `AsyncDiskWriter` + 单户内存直传 | P1 | 内部成组（`FactorHub` 为前置） | **无** | 🟢 低（建议读线程上限 `2`） | 🟡 中（需限读线程 ≤2 且谨慎开异步写） | [PR-07](PR-07-post-calc-parallel-io-and-async-disk.md) |
| **PR-08** | Binance `bStocks`/杠杆档位跨进程磁盘缓存与惰性杠杆精调 | P1 | 无 | **无** | 🟢 无 | 🟢 无 | [PR-08](PR-08-binance-disk-cache-and-lazy-leverage.md) |
| **PR-09** | `startup.py` 有界防重入异步统计池（`StatisticsTasks`） | P1 | 无 | **无** | 🟢 无（显著降风险） | 🟢 **强烈推荐（4G 必上，设 `max_workers=1`）** | [PR-09](PR-09-bounded-statistics-executor.md) |
| **PR-10** | 通用 Numba/Cython 滚动统计原语库（`fast_rolling.py`） | P1 | 无（复用既有 `numba`） | **无** | 🟡 中（首次冷编译毛刺） | 🟡 中（冷编译瞬时吃 CPU/内存，需离线预热） | [PR-10](PR-10-fast-rolling-numba-primitives.md) |
| **PR-11** | 行情读取 Arrow IPC `mmap` 零拷贝快路径（自动回退 `pkl`） | P2（双端协同） | 无（推荐配合 PR-04） | **🔴 强依赖上游发布 `.arrow`** | 🟢 低（Page Cache 充足） | 🟡 中（Page Cache 薄，需控 `max_rows`） | [PR-11](PR-11-arrow-mmap-fast-path.md) |
| **PR-12** | 共享 IPC 分块计算管线（`FactorBlocks` + `temporary_gc` + 可选开关） | P2（架构级） | 推荐配合 PR-03 / PR-11 | **🟡 可选协同**（`arrow_strict` 强依赖上游 `.arrow`；`auto` 兼容 `pkl`） | 🟡 中（需下调 `job_num=2` 与截面并发） | 🔴 **高（4c8g 默认参数会 OOM，必须按分档表降配 + 注意 2–3 GB 临时磁盘）** | [PR-12](PR-12-shared-factor-blocks-pipeline.md) |

---

## 二、依赖拓扑与分批合入建议

```text
第一批（零依赖纯算子/低风险基础设施，2c4g/2c8g/4c8g 均直接受益）
├── PR-01 (agg_pos_ratio NumPy)
├── PR-02 (data_bridge 缓存与向量化)
├── PR-03 (factor 跳 shift(0) + 直建 Polars)
├── PR-04 (datatools align 向量化 + check_flags)
├── PR-08 (Binance 磁盘缓存 + 惰性杠杆精调)
├── PR-09 (StatisticsTasks 有界统计池)
└── PR-10 (fast_rolling 通用原语库)

第二批（多进程/多线程并发层，须带主机分档默认配置）
├── PR-05 (Step 4/5/6 多进程 + LPT) ──► PR-06 (Step 6 Pivot CoW/缓存共享 + equity 计数)
└── PR-07 (FactorHub 锁 ──► process_equities 并行读 + AsyncDiskWriter + 单户 ratio 内存直传)

第三批（跨项目契约 / 架构级管线，建议功能开关默认关闭）
├── PR-11 (Arrow IPC mmap 快路径，强依赖上游 alpha 同步支持 .arrow 发布)
└── PR-12 (FactorBlocks 共享分块管线 + temporary_gc，配合 PR-11 发挥最大收益)
```

---

## 三、不同规格主机推荐默认参数速查

| 配置项 | 涉及 PR | 4c8g（当前调优基线） | 2c8g 推荐值 | 2c4g 推荐值 |
| :--- | :--- | :---: | :---: | :---: |
| `job_num`（因子计算进程数） | PR-03 / PR-12 | `4` | `2` | `1`（或最多 `2` 且配足 Swap） |
| `backtest_job_num`（Step 4/5/6 进程池） | PR-05 / PR-06 | `4` | `2` | **`1`（禁用多进程防 OOM）** |
| `process_equities` 读盘线程数 | PR-07 | `min(4, N)` | `min(2, N)` | `min(2, N)` 或 `1` |
| `statistics_config['max_workers']` | PR-09 | `2` | `1` | **`1`（严禁 >1）** |
| `_CAP_CACHE_MAX` | PR-02 | `2048` | `1024` | `512` |
| `shared_factor_pipeline` | PR-12 | `True`（大策略池推荐） | `True`（`batch_size=16~24`） | 仅大内存策略谨慎开启，须设 `job_num=1`、`batch_size=16`、截面串行 |

---

## 四、明确不纳入通用回馈的项与已证伪避坑清单

### 不纳入通用回馈（绑定特定策略或运维形态）
* `MODEL_ROBUST40` 专属状态预读、延迟原子落盘（`DeferredAtomicSave`）、`merge_context` 快路径与 `warm-numba` CLI。
* 168h 修订检测与 26 点共同回放体系（`tools/robust40/handoff.py` / `replay.py`）。
* `VolumeMaxBias_进出场阈值分离官方` 专属 S3 Polars 续算。
* 绑定 Linux `systemd-run` 后台初始化的 `BURST ↔ YIELD` cgroup 动态治理（`tools/robust40/resources.py`）。

### 已实测证伪/撤回（严禁作为优化回馈）
详见 [避坑清单](../kb/rejected-and-failed-attempts.md)：
1. **资金曲线 CSV→内存旁路**：绕过 CSV 文本往返会导致 `4.44e-16` 浮点尾差，破坏严格逐位一致。
2. **删除选币循环中的 `gc.collect()`**：实测完整选币反慢（`112.8 s → 117.9 s`）。
3. **直接启用 `agg_multi_strategy_ratio_v2`**：曾出现索引名（`None` vs `candle_begin_time`）与边界差异，未经全量 Oracle 验收不可默认切换。
4. **上游直接裁成精确 `4050` 根 Arrow**：会破坏 `align_spot_swap_mapping` 的映射预热。
