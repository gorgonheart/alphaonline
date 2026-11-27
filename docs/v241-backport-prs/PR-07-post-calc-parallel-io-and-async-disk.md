# PR-07：后半程读写并发化（`FactorHub` 线程安全 + 并行 `process_equities` + `AsyncDiskWriter` + 单户内存直传）

- **拟提交标题**：`perf(trading): parallelize process_equities reads, make FactorHub thread-safe, and overlap non-blocking disk writes`
- **优先级**：P1（中高确定性通用收益，需成组提交）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：本 PR 内部包含强耦合依赖链（`FactorHub` 线程安全锁 → `process_equities` 多线程；`preloaded_ratios` 内存直传 + 显式 `flush()` → `AsyncDiskWriter`），必须作为一个完整 PR 合入

---

## 1. 场景

Step 6 完成后，主进程进入仓管后半程：`process_equities` 读取所有子策略的 `资金曲线*.csv` 与 `ratio_{spot,swap}.pkl` 并写出 `equity_df.pkl`；`calc_ratios` 写出 `仓位比例-原始.csv` 与 `仓位比例.csv`；`calc_by_account` 写出 `runtime_folder/df_{spot,swap}_ratio.pkl`；紧接着单账户 `trade_by_account` 又从磁盘把刚写出的 `df_{spot,swap}_ratio.pkl` 读回内存。

## 2. 目标

1. 修复 `FactorHub.get_by_name` 每次调用重复 `locale.setlocale` 的线程安全与性能隐患；
2. 将 `process_equities` 对多子策略的 CSV/PKL 读取与周期转换改为有界线程池并行；
3. 引入轻量作用域后台写盘器 `AsyncDiskWriter`，将下游非阻塞产物（`equity_df.pkl`、`仓位比例*.csv`、单户 `df_*_ratio.pkl`）异步落盘，与后续计算及下单网络 I/O 重叠；
4. 单账户 `run_by_account` 将 `(df_spot_ratio, df_swap_ratio)` 通过内存直传给 `trade_by_account`，消除同进程先写后读。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/`：
1. `core/utils/factor_hub.py:56-65` 每次 `get_by_name()` 都执行 `locale.setlocale(locale.LC_ALL, ...)`（全局非线程安全，且重复触发 C 库 locale 切换）。
2. `core/model/backtest_config.py::process_equities` 串行循环读取 14 份 `资金曲线.csv` + 28 份 `ratio_*.pkl` 并同步写出 14 份 `equity_df.pkl`。
3. `core/real_trading.py` 在 `v2.4.1` 拆分为 `calc_by_account` 与 `trade_by_account` 后，单账户 `run_by_account` 仍通过磁盘 `runtime_folder/df_{spot,swap}_ratio.pkl` 交接，白白多出两次大宽表反序列化。

## 4. 项目文档中的相关分析与设计

- 详细设计：[2026-11-25 消费端第二批设计](../superpowers/specs/2026-11-25-v241-and-consumer-batch2-design.md)（§5.2 B2.3）
- 实施与审查修复：[2026-11-25 实施计划](../superpowers/plans/2026-11-25-v241-and-consumer-batch2.md)（Task 5）
- 参考提交：`e27fbe1`（主体实现）、`ee2ef23`（补齐 `save_final_select_results` 与 `simulate` 模式读盘前 `async_writer.flush()`）、`d21d5a1`（`FactorHub._ensure_locale` + `_lock` 线程安全修复）

## 5. 依赖

- **内部代码依赖（必须成组）**：
  1. `process_equities` 多线程 **强依赖** `FactorHub._ensure_locale()` + `threading.Lock()` 及并发前预热 `self.strategy.factor_list`（否则多线程调用 `setlocale` 与模块导入会竞态崩溃）。
  2. `AsyncDiskWriter` **强依赖** 4 个强制同步边界：
     - 账户组 `--stage calc` 模式：`calc_by_account` 返回前必须 `flush()`，确保 `save_handoff` 读到完整 `df_*_ratio.pkl`；
     - 单账户 `run_by_account` 必须通过 `preloaded_ratios` 内存直传给 `trade_by_account`，否则 `trade_by_account` 会与后台写盘竞争；
     - `trade_by_account` 在 `is_simulate == 'simulate'` 调用 `calc_equity` 前必须 `async_writer.flush()`；
     - `trade_by_account` 在 `save_final_select_results` 读取 `仓位比例.csv` 前必须 `async_writer.flush()`。
- **上游 `alpha` Arrow 依赖**：**无**。
- **反模式提醒**：本 PR **故意保留** `资金曲线.csv` 的磁盘读入，不绕过 CSV 文本往返，避免引入 `4.44e-16` 浮点尾差（见 [避坑清单](../kb/rejected-and-failed-attempts.md)）。

## 6. 收益与证据

- **功能与一致性证据**：
  - `tests/test_consumer_batch2_pipeline.py` 验证 6 子策略并行 `process_equities` + `AsyncDiskWriter` 产出的 `equity_dfs`、`ratio_dfs` 及磁盘 `equity_df.pkl` 与同步基线 `check_exact=True`；
  - 验证 `AsyncDiskWriter.flush()` 准确重抛后台 `OSError`，`dry_run` / `simulate` 均在读盘前完成 `flush()`；
  - 验证 `FactorHub` 在多线程 `process_equities` 下 0 次重复调用 `locale.setlocale`。
- **耗时收益**：叠加于消费端第二批实测中，消除单户 `df_*_ratio.pkl` 二次读盘及 14×`equity_df.pkl` + 4 份权重文件的同步写盘阻塞（单轮后半程综合缩减 `~2–4 s`，策略数越多收益越大）。

## 7. 代码改动

### 目标文件
- `core/utils/factor_hub.py`（修改）
- `core/utils/async_disk.py`（新增，约 55 行）
- `core/model/backtest_config.py`（修改 `process_equities`、`calc_ratios`）
- `core/real_trading.py`（修改 `run_by_account`、`calc_by_account`、`trade_by_account`）
- `core/position.py`（`calc_target_position` 支持直接传内存 `df_spot_ratio` / `df_swap_ratio`，`None` 时回退读盘）

### 核心改动要点
1. `core/utils/factor_hub.py`：引入类级 `_locale_initialized` 与 `_lock = threading.Lock()`，双检锁保证 `locale.setlocale` 仅执行一次且缓存写入线程安全。
2. `core/utils/async_disk.py`：实现 `AsyncDiskWriter`（单线程 `ThreadPoolExecutor(max_workers=1)`，`submit`/`flush`/`close` 及上下文管理器，异常退出时排空任务不覆盖主异常）。
3. `core/model/backtest_config.py`：
   - `process_equities(..., async_writer=None)`：预热 `FactorHub` 后用 `ThreadPoolExecutor(max_workers=min(4, len(configs)))` 并行处理各子策略，主线程按原始 `configs` 顺序归集，将 `equity_df.copy(deep=False).to_pickle` 投递给 `async_writer`；
   - `calc_ratios(..., async_writer=None)`：将 `仓位比例-原始.csv`（`.copy()`）与 `仓位比例.csv`（`.copy(deep=False)`）投递给 `async_writer`。
4. `core/real_trading.py`：`run_by_account` 用 `with AsyncDiskWriter() as writer:` 包裹 `calc_by_account` + `trade_by_account`，经 `me_conf._live_target_ratios` 直传内存比例，在所有下游读盘点前执行 `writer.flush()`。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 默认 `max_workers = min(4, len(configs))` + `AsyncDiskWriter` 开启。 |
| **2c8g** | 🟢 低风险 | 建议将 `process_equities` 线程池上限设为 `min(os.cpu_count() or 2, 4, len(configs))`（即 2 核机自动用 2 线程，避免 4 线程在 2 核上抢 GIL）。 |
| **2c4g** | 🟡 **中风险** | 1. 4 线程同时反序列化 4 组 `ratio_{spot,swap}.pkl` 会抬高瞬时内存，且 `AsyncDiskWriter` 队列在 `flush()` 前会短暂持有待落盘 DataFrame 引用（`~50–150 MiB`）；<br>2. 建议 2c4g 上：`process_equities` 线程数限制为 `1–2`；若内存吃紧可通过配置关闭 `async_writer`（此时 `preloaded_ratios` 单户内存直传与 `FactorHub` 锁依然 100% 生效且零额外内存占用）。 |
