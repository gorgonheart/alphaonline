# 生产端计算-IO 重叠（收齐即算 + 计算期并行落盘）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不增加进程并发度、不给下游消费端运行期增加任何额外驻留内存或磁盘竞争的前提下，打通「WS 收齐即算 + 计算期并行落盘（跨阶段 `5m/1h` 并行落盘 + Batch 内异步刷 `batch.pkl`）」并修复重采样重叠窗口退回全量与老币尾部快路径漏命中问题，将 `25m` 轮生产端 `.ready` 就绪时间从 `+74.5 s` 进一步压缩至 `~+38~43 s` 且保持全产物 `check_exact=True` 逐位一致。

**Architecture:** 在 `D:/alpha`（分支 `feat/v202-migration-2b`）分三层推进：(1) **采集与屏障层**：基于已有的 `WsLocalCache`（SQLite WAL 并发只读零锁快照）提供窗口状态与 5m K 线批量直读，`wait_for_ready_barrier` 与 `_start_rest_fallback` 在 `ws_cache` 已收齐当轮闭合 K 线且无断档时免等 `Writer` 慢速磁盘队列即刻放行（`Writer` 在后台与下游计算并行完成 `5m flat_pkl + TSManager` 落盘），同时为 `CandleFileManager.save_ready/save_ws_ready` 引入 `O(1)` 路径缓存消除每轮数百万次目录 `glob` 扫描；(2) **重采样与跨阶段并行落盘层**：修复 `update_resample_candle_incremental` 在常规 7 根重叠窗口下误退回全表双读重算与返回 `tail_out=None` 的问题（改为前缀切片增量合并 + 100% 返回 12 根 1h 内存尾部），并在 `run_ws_cycle` 中支持「优先合并 `ws_cache` 最新 5m 闭合窗口极速产出内存 1h 尾部直供 `Preprocess` 开算 $\parallel$ 后台线程在 `Preprocess` 计算窗口内并行刷完 `1h_resample/*.pkl` 并在本轮收尾前汇合」；(3) **预处理核心层**：放宽 `gen_batch_data_incremental` 对 `df_old` 拥有更早历史头（如老币相差 11 小时）的前缀对齐校验使尾部富集快路径命中率达 `100%`，在 `_run_batch_worker_task` 内用单后台写盘线程异步执行 `batch.pkl` 序列化落盘以重叠主线程 `cffi` C 因子与 PyArrow 序列化（返回前 `join` 确保契约闭环），并在 `_release_memory()` 中增加 `pa.default_memory_pool().release_unused()` 回收闲置内存池。

**Tech Stack:** Python 3.11 (`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`), `sqlite3` (WAL read-only URI), `pandas 2.2.3`, `numpy 2.0.2`, `pyarrow 17.0.0`, `pytest`.

---

### Task 1: 采集与屏障层 — `WsLocalCache` WAL 直通屏障、`glob`-free `O(1)` `.ready` 更新与免误报兜底

**Files:**
- Modify: `D:/alpha/core/ws/ws_local_cache.py`, `D:/alpha/core/storage/candle_manager.py`, `D:/alpha/core/pipeline/barrier.py`, `D:/alpha/core/pipeline/update_cycle.py`
- Test: `D:/alpha/tests/test_compute_io_overlap_phase2.py`

- [x] **Step 1: 编写 Task 1 失败测试（`WsLocalCache` WAL 只读批量查询闭合状态与窗口、`Writer` 未落盘时屏障凭 `ws_cache` 即刻放行且 REST 兜底零误报、`CandleFileManager.save_ready/save_ws_ready` 消除重复 `glob` 且清理旧文件一致）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 1 代码**
- [x] **Step 4: 运行专项与既有 WS/Barrier/Storage 测试确认 GREEN 并提交**

---

### Task 2: 重采样层 — `ws_cache` 实时叠加、重叠窗口 `O(1)` 增量合并 + 100% 内存尾部输出与跨阶段并行落盘

**Files:**
- Modify: `D:/alpha/core/pipeline/resample.py`, `D:/alpha/core/pipeline/update_cycle.py`
- Test: `D:/alpha/tests/test_compute_io_overlap_phase2.py`

- [x] **Step 1: 编写 Task 2 失败测试（常规 7 根重叠窗口增量合并与全表 Oracle `check_exact=True` 且 100% 返回非空 `tail_df`、`Writer` 滞后时 `resample` 自动叠加 `ws_cache` 产出最新 1h K 线、`defer_disk_write=True` 秒返 `(begin_ts, tail_dfs, flush_fn)` 供下游先算并后台并行落盘一致）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 2 代码**
- [x] **Step 4: 运行专项与既有 Resample/WS Cycle 测试确认 GREEN 并提交**

---

### Task 3: 预处理核心层 — 老币历史头差 100% 命中尾部快路径、Batch 内异步并行刷 `batch.pkl` 与 PyArrow 内存池释放

**Files:**
- Modify: `D:/alpha/core/strategy/preprocess.py`
- Test: `D:/alpha/tests/test_compute_io_overlap_phase2.py`

- [x] **Step 1: 编写 Task 3 失败测试（`df_old` 起点早于 `1h_resample` 11 小时的老币 100% 命中 `Fast Path 1` 与 `Fast Path 2` 且与全表 Oracle `check_exact=True`、无 `FutureWarning`、`_run_batch_worker_task` 内 `set_candle` 与 C 因子真并发重叠且异常阻断发布、`_release_memory` 触发 `pa.default_memory_pool().release_unused()`）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 3 代码**
- [x] **Step 4: 运行 `D:/alpha` 全量套件、`D:/alphaonline` 跨仓契约套件与端到端重叠测速对拍，更新文档并提交**
