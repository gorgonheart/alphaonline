# PR-12：共享 IPC 分块计算管线（`FactorBlocks` + `temporary_gc` + 可选 `shared_factor_pipeline`）

- **拟提交标题**：`perf(pipeline): add opt-in shared IPC FactorBlocks pipeline, bounded backpressure, and scoped gen-0 GC`
- **优先级**：P2（架构级低内存/高吞吐引擎；建议作为可选开关 `shared_factor_pipeline = False` 引入）
- **上游 `alpha` Arrow 依赖**：**🟡 可选协同**（支持 `auto` 模式兼容原 `pkl`；但配合 **PR-11** 的上游 `.arrow` 快照走 `arrow_strict` 描述符直读才能省去父进程整库载入并发挥最大收益）
- **其他 PR 依赖**：推荐先合入 **PR-03**（直建 Polars）、**PR-04**（映射向量化）与 **PR-11**（Arrow mmap）

---

## 1. 场景

当账户策略池庞大（例如 10~14 个组合、80+ 个时序因子、多个截面因子、4050~14000+ 根 K 线）时，`v2.4.1` 的传统管线会经历多次全市场宽表读写与整表内存驻留：
`load_realtime_data` 写 `all_candle_df_list.pkl` → `calc_factors` 读入并写百余个因子 parquet → `calc_cross_sections` 读回全量宽表 → `select_coins` 每个策略组合各自 `preload_all_factor_data` 拼宽表。

## 2. 目标

提供一套默认关闭、通过 `config.shared_factor_pipeline = True` 按需开启的共享分块计算管线：
1. **统一物理布局 `FactorBlocks`**：以「时间块（如 2016h）× 币种分片（如 32 币）」的未压缩 Arrow IPC 文件落盘，保持原始行序 `_row` 不变，消除 `all_candle_df_list.pkl` 中转与按因子拆几百个小 parquet 的 I/O 放大；
2. **有界背压 `ordered_results`**：限制 `max_pending = 2 * job_num`，严格保序同时避免慢币拖垮内存；
3. **阶段进程隔离 `_run_block_stage`**：在「因子 → 截面 → 选币」阶段边界以独立 `spawn` 子进程运行并精准控制 `POLARS_MAX_THREADS`/`OMP_NUM_THREADS`/`NUMBA_NUM_THREADS`，阶段结束即 100% 归还原生堆内存（彻底杜绝 Polars + `fork` 死锁）；
4. **局部第 0 代 GC（`temporary_gc`）**：在短周期大表转换窗口暂停自动全代 GC、退出时仅做 `gc.collect(0)`，消除几十秒全代扫描卡顿；
5. **截面块合读与跨族并发（`P5-C1`）**：支持实现 `signal_block` / `get_state_input_columns` 协议的截面因子同族多参单次读块、实盘多线程跨族并发并在主线程按声明顺序提交。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/backtest.py` 与 `core/select_coin.py`：
1. `all_candle_df_list.pkl` 的全市场 pickle 写出与重读本身就是巨额纯冗余 I/O 与内存峰值。
2. `calc_factors` 按列拆写出数十~上百个 `factor_*.parquet` 与 `factor_full_*.parquet`，后续每个策略组合选币时又反复水平拼接全市场宽表。
3. Polars 原生内存池（jemalloc/mimalloc）在同进程跨阶段运行时不易及时归还 OS，且一旦主进程初始化了 Polars 线程池，后续 Linux `fork` 会触发死锁风险。

## 4. 项目文档中的相关分析与设计

- 共享因子/截面/选币分块设计：[2026-11-03 共享分块设计](../superpowers/specs/2026-11-03-shared-factor-selection-design.md)、[2026-11-03 共享计算审查](../2026-11-03-shared-compute-review.md)
- 局部 gen-0 GC 与共享 I/O 实测：[2026-11-04 提速跟进审查](../2026-11-04-speed-followup-review.md)
- Phase 4 同族截面合读 & Phase 5 截面跨族并发：[2026-11-23 Phase 5 (2A) 设计](../superpowers/specs/2026-11-23-phase5-2a-low-concurrency-design.md)（§5.2 `P5-C1`）
- 参考提交：`9451bdd`（`FactorBlocks` + `ordered_results` + `temporary_gc` + `prepare_factor_selection`）、`1dae6a2`（`ordered_results` 及时释放 `result, future`）、`06c8b15`（`run_section_blocks_group`）、`70b6124`（`P5-C1` 3 线程跨族截面）

## 5. 依赖

- **内部代码依赖**：
  - 强烈推荐先合入 **PR-03**（否则分块 worker 内仍存在 pandas→Polars 往返）与 **PR-04**；
  - 兼容守卫：`supports_factor_blocks(factory)` 自动检测 `use_custom_func`、`select_mode == 'entry_exit'` 或未标记 `shared_input_safe` 的自定义截面因子，不符合条件时自动回退 `v2.4.1` 原生路径，零破坏。
- **🟡 上游 `alpha` Arrow 协同依赖**：
  - `input_mode = 'auto'`（默认）：无需上游 `.arrow`，仍可通过 `iter_preprocessed_candles` 流式读取 `pkl`，但父进程仍需逐市场加载 `pkl`；
  - `input_mode = 'arrow_strict'`：**强依赖上游 `alpha` 发布单文件 `{spot,swap}_dict_{ts}.arrow`（及 **PR-11**）**——此时主进程仅生成轻量 `(arrow_path, batch_idx, symbol_type)` 描述符，由各子进程按需零拷贝读取单币 batch，才能将父进程内存压到最低并彻底免除 `all_candle_df_list.pkl`。

## 6. 收益与证据

| 优化子项 | 实测环境与口径 | 收益 |
| :--- | :--- | :--- |
| `temporary_gc`（局部 gen-0 回收） | Ubuntu 4c8g 全历史初始化映射转换 | 全代 GC 耗时 **`13.127 s → 0.0023 s`**；整段映射读转释放 **`20.662 s → 8.110 s`** |
| `FactorBlocks` + `ordered_results` 背压 | Ubuntu 4c8g 14060 根全量初始化（924 任务 / 368 万~461 万行） | 在 4.5 GiB cgroup 内存上限内 **0 OOM** 跑通全市场初始化；32 币分片写块 87 份 IPC 与传统路径 **逐字节一致** |
| `run_section_blocks_group` + `P5-C1` 3 线程跨族并发 | Ubuntu 4c8g 测试机自然轮（`2026-11-23`） | `sections` 阶段 **`14.51 s → 9.95 s`（`-31.4%`）**，`swap_out_pages = 0` |
| `selection` 流式分块选币 | Ubuntu 4c8g 14 策略组合 | 选币阶段稳定 **`2.8–3.5 s`** |

## 7. 代码改动

### 目标文件
- `core/utils/factor_data.py`（新增：`FactorBlocks`、`ordered_results`、`temporary_gc`、`read_market`）
- `core/utils/section_state.py`（新增/扩展：`compute_section_blocks`、`compute_section_blocks_group`、`publish_section_outputs`、`run_section_blocks_group`）
- `core/backtest.py`（新增：`supports_factor_blocks`、`_run_block_stage`、`prepare_factor_selection`、`persist_block_selections`、`_prepare_shared_reports`）
- `core/select_coin.py`（新增：`_calc_factor_blocks`、`_calc_section_blocks`、`select_factor_blocks`、`iter_candle_descriptors`）
- `core/utils/functions.py`（新增：`iter_preprocessed_candles`）
- `config.py`（新增：`shared_factor_pipeline = False`、`factor_symbol_batch_size`、`selection_block_hours`）

### 可选独立拆出项
若 `v2.4.1` 维护者认为整套 `FactorBlocks` 侵入面积较大，可先单独拆出 `temporary_gc`（仅 25 行）用于大表转换场景，其余部分置于 `shared_factor_pipeline = False` 特性开关后渐进开放。

## 8. 2c4g / 2c8g 主机风险与适配建议

> ⚠️ **极重要提示**：本管线的现行参数（`job_num=4`、`batch_size=32`、3 线程截面、`_polars_threads=4`）针对 **4c8g** 调优。在 4c8g 实测中，`FACTOR_BLOCKS` 阶段 4 个 worker 进程树 RSS 峰值 **`2.00–2.95 GiB`**，且临时未压缩 IPC 块占用约 **`2.5–3.1 GiB` 磁盘空间**（阶段结束后释放）。**直接将 4c8g 默认参数搬到 2c4g 会触发严重 Swap 或 OOM！**

| 主机规格 | 风险等级 | 风险点与强制参数适配表 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 安全 | `job_num = 4`、`factor_symbol_batch_size = 32`、`selection_block_hours = 2016`、实盘截面 `3 线程 + _polars_threads = 4`。需确保磁盘可用空间 `>= 6 GiB`。 |
| **2c8g** | 🟡 **中风险** | 8G 内存充裕，但仅 2 核：开 4 进程 + 3 线程截面会严重争核。<br>**推荐配置**：`job_num = 2`、`factor_symbol_batch_size = 24`、截面并发上限 `min(2, len(block_entries))`、`_polars_threads = 2`。 |
| **2c4g** | 🔴 **高风险（必须降配）** | **三大风险**：① 4 worker RSS 峰值 `2.95 GiB` + OS + 父进程会直接突破 4G 物理内存；② 实盘 3 线程并行读多块 IPC 会放大截面段瞬时内存；③ 临时 IPC 块需 `~2.5–3.1 GiB` 磁盘与页缓存空间。<br>**2c4g 强制适配建议**：<br>1. `job_num = 1`（或最多 `2` 且配足 `>= 8 GiB` Swap）；<br>2. `factor_symbol_batch_size = 16`（减半父进程单批 concat 峰值）；<br>3. 截面阶段**强制串行**（禁用 `P5-C1` 跨族多线程）且 `_polars_threads = 1`（至多 `2`）；<br>4. 若磁盘空间紧张，可将 `FactorBlocks(..., backend='lz4')` 启用 LZ4 压缩，以少量 CPU 换取临时磁盘与页缓存减半；<br>5. 策略池简单（1–3 个策略）的 2c4g 主机建议保持 `shared_factor_pipeline = False`。 |
