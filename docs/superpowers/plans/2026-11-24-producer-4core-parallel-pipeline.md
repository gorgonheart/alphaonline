# 生产端全链路 4 核并行流水线与尾部增量加速 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 破除过度保守的 `< 2.8 GiB` 单核串行限制，在完全遵守 7 条跨项目数据契约且保持产出逐位精确一致（`check_exact=True`）的前提下，打通 `D:/alpha` 采集落盘、重采样、批次富集、C 因子计算、Arrow 序列化与年度 Pivot 更新全链路的 4 核多进程与流水线并行，将上游 `.ready` 发布延迟从 `+137~146 s` 压缩至 `~+25~35 s` 量级并彻底消除假缺口 REST 抢跑。

**Architecture:** 在 `D:/alpha`（分支 `feat/v202-migration-2b`）分四层协同推进：(1) 采集落盘层将默认 `num_writers` 提升至 `4`、`ws_wait_sec` 显式配置化并调至 `14.0 s`，`WsRunner → Writer` 改传轻量 `kline` 字典并在 `flat_pkl` 落盘与 `.gap` 更新后立即写 `.ws_ready` 放行屏障，屏障全齐后通过 `cancel` 事件即时唤醒退出 REST 兜底线程（真缺口按是否有 `.gap` 区分 `limit=99` 与 `limit=5`）；(2) 重采样层反转读取优先级（优先读 240 行轻量 `flat_pkl`，不足时回退 `TSManager` 月分区），接入 `RuntimeSupervisor` 托管的常驻 4 进程池消除每轮 `spawn` 冷启动与 GIL 争用，并可选内存回传尾部 `12` 根 1h K 线；(3) 预处理核心层为 `gen_batch_data_incremental` 增加前缀精确校验的尾部增量富集快路径（省去每轮对 1.5 万行全表重复 join/reindex/sort），并在常驻 4 进程池上并发混跑 `spot + swap` 全部 Batch（Worker 内完成全历史 C 因子计算后当场切 `.tail(publish_kline_num)` 序列化为 Arrow `RecordBatch` 字节流并释放 1.5 万行含因子大表，`spot` 与 `swap` 各自收齐即刻并行发布 `.arrow`、Pivot 与 `.ready`）；(4) Pivot 层将 `update_pivot_rows` 的 `3,068` 次 pandas `.loc` 循环重构为单次时间索引定位 + NumPy 二维矩阵批量赋值。

**Tech Stack:** Python 3.11 (`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`), `pandas 2.2.3`, `numpy 2.0.2`, `pyarrow 17.0.0`, `cffi` (4 C extensions), `multiprocessing` (`spawn`), `pytest`.

**Spec:** 对话内已确认的全链路 4 核并行架构方案（2026-11-24）。

## Global Constraints

- 唯一本地 Python 解释器：`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`；Windows pytest 必须传 `-p no:cacheprovider --basetemp=D:/alpha/.tmp-pytest`。
- 严禁引入新第三方依赖；严禁修改任何因子数学实现（`动量V2` / `ILLQ` / `CciV3`）或 `D:/alphaonline` 策略/账户代码。
- 严格遵守 7 条跨项目契约：`batch PKL` 永不落预计算因子且保留完整历史；C 因子必须在完整历史上计算后再裁切 `.tail(publish_kline_num)` 写 `.arrow`；原子发布顺序（`.arrow` + `market_pivot_*` 落盘后最后写 `{trade_type}_dict.ready`）；保留 `candle_origins` 与 `FundingPublication` 24h 回填语义。
- 保持单元测试猴补丁兼容：当调用方未传多进程 `batch_executor` 或因子模块为内存 mock/闭包不可跨进程导入时，自动平滑走进程内 `ThreadPoolExecutor` 路径，确保既有 397 项单测与跨仓契约测试零破坏（仅同步更新显式断言旧 `num_writers=2` 与旧 `spot→swap` 强制串行的专项用例）。
- 默认离线开发与验证，未经明确授权不重启测试机或正式机常驻进程；保留 `D:/alphaonline` 已有未提交文档改动；严禁泄露任何密钥或 Webhook。

## Review Focus

1. **`Writer` 提前写 `.ws_ready` 的崩溃与并发读取一致性**：必须在 `flat_pkl` 完成 `.tmp + os.replace` 原子替换且 `_update_gap_marker` 更新完毕**之后**才写 `.ready` / `.ws_ready`，确保屏障放行时 `flat_pkl` 与 `.gap` 100% 可读且一致；`_update_tsmanager` 异常不得回滚已落盘的 `flat_pkl` 与 `.ws_ready`（Task 1 测试锁定）。
2. **`flat_pkl` 优先重采样在新上币/跨月补齐边界的等价性**：当 `flat_pkl` 闭合行数 `< once_update_candles`（如刚做完深历史补齐或旧 `flat_pkl` 缺失而 `TSManager` 拥有更多闭合行）时，必须对比回退 `_read_base_tail`，取闭合覆盖更完整的一方，确保与纯月分区读取 100% 逐位一致（Task 2 测试锁定）。
3. **尾部增量富集快路径（Tail Fast-Path）的防伪与回退完备性**：当存在历史价格/成交量修订、`history_trim_ts` 截断头行、代币拆分跳空重写，或 `spot_swap_matches` 对手方/交集区间变化影响到历史前缀时，必须精准识别并自动回退全表富集路径，保证与独立全表 Oracle `check_exact=True` 逐位零差异（Task 3 测试锁定）。
4. **多进程 Batch Worker 异常隔离与禁止半成品 `.ready`**：任一 Batch Worker 抛出因子或 I/O 异常时，对应市场严禁写出 `.arrow` 或 `.ready` 门控文件，且常驻进程池在单轮异常后下一轮仍可继续正常服务（Task 3 测试锁定）。
5. **NumPy 矩阵化 `update_pivot_rows` 的非连续时间戳、新币扩列、下架保列与 `NaN` 语义**：当部分币种存在停牌非连续时间戳、新增交易对触发 `CategoricalIndex` 扩列、已下架币种在新行保持 `NaN`（`funding_rate` 填 `0.0`），或传入空值/全 `NaN` 列时，输出与旧逐列 `.loc` Oracle 必须 `check_exact=True` 完全一致（Task 4 测试锁定）。

---

### Task 1: 采集落盘层 — 4-Writer、轻量 WS 载荷、优先 `.ws_ready` 与可取消 `14s` 分级兜底

**Files:**
- Modify: `D:/alpha/config.py`, `D:/alpha/core/handle.py`, `D:/alpha/core/ws/ws_runner.py`, `D:/alpha/core/storage/writer.py`, `D:/alpha/core/pipeline/runtime_supervisor.py`, `D:/alpha/core/pipeline/update_cycle.py`, `D:/alpha/core/pipeline/data_update.py`
- Modify (existing tests updating intentional defaults): `D:/alpha/tests/test_2b_config_and_handle.py`
- Test: `D:/alpha/tests/test_parallel_pipeline_phase1.py`

**Interfaces:**
- `config.py` / `BmacHandle`: `num_writers: int = 4`, `ws_wait_sec: float = 14.0`（支持 `config.json` 覆盖与非负校验）
- `WsRunner._kline_to_writer_item`: 产出包含 `"kline"` 原生字典的轻量载荷；`Writer.handle_item` 兼容 `"kline"` 与 `"df"` 两种输入，并在 `flat_pkl` + `_update_gap_marker` 完成后立即写 `.ready`/`.ws_ready`，再执行 `_update_tsmanager`
- `update_cycle._start_rest_fallback`: 状态字典含 `"cancel": threading.Event()`；屏障双市场 0 `skipped` 且无活跃 `.gap` 时立即 `cancel.set()` 零等待收尾；触发兜底时区分 `.gap` symbol（`limit=once_update_candles`）与单根漏推 symbol（`limit=5`）

- [x] **Step 1: 编写 Task 1 失败测试（轻量载荷兼容、`.ws_ready` 先于慢 `TSManager` 落盘、屏障 100% 即时取消 REST 兜底、分级 `limit=5 vs 99`、`num_writers=4`/`ws_wait_sec=14.0` 默认值）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 1 最小完备代码**
- [x] **Step 4: 运行专项与既有 Writer/WS/Supervisor 测试确认 GREEN 并提交（`3e8972b`）**

---

### Task 2: 重采样层 — `flat_pkl` 优先快速路径、常驻计算进程池与内存尾部直传

**Files:**
- Modify: `D:/alpha/core/pipeline/runtime_supervisor.py`, `D:/alpha/core/pipeline/resample.py`, `D:/alpha/core/pipeline/update_cycle.py`
- Test: `D:/alpha/tests/test_parallel_pipeline_phase1.py`

**Interfaces:**
- `RuntimeSupervisor.get_compute_pool() -> ProcessPoolExecutor`（按 `compute_workers` 惰性初始化常驻 `spawn` 池，`shutdown()` 干净回收）
- `update_resample_from_base(handle, trade_type, run_time, *, executor=None, return_tail_bars: int = 0)`：`executor` 非空时向共享进程池提交分片并发执行（`executor=None` 保持进程内线程池兼容）；`return_tail_bars > 0` 时返回 `(symbol_begin_time, symbol_tail_dfs)`

- [x] **Step 1: 编写 Task 2 失败测试（`flat_pkl >= once_update_candles` 零读 `TSManager`、`< once_update_candles` 自动回退更长 `TSManager` 分区保持逐位一致、共享 `executor` 并行与 `return_tail_bars` 返回精确尾部）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 2 代码**
- [x] **Step 4: 运行专项与既有 Resample 测试确认 GREEN 并提交（`33d0854`）**

---

### Task 3: Pivot 层 — NumPy 二维矩阵化 `update_pivot_rows` 替代逐币 `.loc`

**Files:**
- Modify: `D:/alpha/core/strategy/preprocess.py`
- Test: `D:/alpha/tests/test_parallel_pipeline_phase1.py`

**Interfaces:**
- `update_pivot_rows(pivot, market_dict, trade_type) -> bool`：签名完全不变，与独立参考旧版逐列 `.loc` Oracle 在连续/非连续时间轴、新上币扩列、下架币保列、`NaN` 比较、`funding_rate` 补 0 场景下 `check_exact=True` 完全一致，且 500 币 × 5000 行更新耗时显著快于旧实现。

- [x] **Step 1: 编写 Task 3 独立旧版 Oracle 对拍与性能回归失败测试**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现单次索引映射 + NumPy 二维矩阵批量更新**
- [x] **Step 4: 运行专项与 `test_module4_gen_pivot_incremental.py` 等全部 Pivot 测试确认 GREEN 并提交（`1ea9b30`）**

---

### Task 4: 预处理核心层 — 前缀校验尾部增量富集、`spot || swap` 跨 4 进程 Batch 并行与 Worker 内直转 Arrow `RecordBatch`

**Files:**
- Modify: `D:/alpha/core/strategy/arrow_publish.py`, `D:/alpha/core/strategy/preprocess.py`, `D:/alpha/core/pipeline/update_cycle.py`
- Modify (existing tests updating intentional sequential behavior): `D:/alpha/tests/test_2b_preprocess_peak_and_trim.py`, `D:/alpha/tests/test_shared_factor_pool.py`
- Test: `D:/alpha/tests/test_parallel_pipeline_phase1.py`

**Interfaces:**
- `gen_batch_data_incremental(..., resample_tail_hints=None)`：支持前缀精确校验的尾部增量富集快路径及 `resample_tail_hints` 免读盘直拼，异常/修订/配对变更/截断自动回退全表富集，与全表 Oracle `check_exact=True` 一致。
- `publish_arrow_ipc_batches(ipc_batches, schema_bytes, symbols, out_path, publish_kline_num, ...)`：将 Worker 进程序列化好的 `RecordBatch` IPC 缓冲零损组装写出原子 `.arrow` 快照。
- `preprocess_offset_data(handle, run_time, symbol_begin_time_spot=None, symbol_begin_time_swap=None, *, batch_executor=None, resample_tails=None)`：双市场 `spot || swap` 并行推进（`spot` 先完先发 `.ready`）；传入 `batch_executor`（常驻进程池）且因子模块可跨进程导入时，8 个 Batch 在进程池内完成“尾部增量富集 + Raw Batch 落盘 + 真多核 C 因子 + `.tail(publish_kline_num)` `RecordBatch` 序列化 + 立即释放大表”；未传 `batch_executor` 或含测试闭包因子时平滑走进程内共享 `ThreadPoolExecutor` 路径。

- [x] **Step 1: 编写 Task 4 失败测试（尾部快路径 vs 全表 Oracle 逐位精确一致、历史修订/配对变更/截断自动回退、4 进程 `batch_executor` 产出 `.arrow`/Batch PKL/Pivot/Origins/FundingPublication 与单核逐位完全一致、单 Batch 报错禁止发布 `.ready` 且池可复用）**
- [x] **Step 2: 运行测试确认 RED**
- [x] **Step 3: 实现 Task 4 代码**
- [x] **Step 4: 运行 `D:/alpha` 全量测试套件（`410 passed`）、`D:/alphaonline` 跨仓契约回归测试（`84 passed, 4 skipped`）及合成生产规模测速基准（`3.24x` 墙钟提速，`check_exact=True`），更新知识库文档并提交（`f06b8f1`）**
