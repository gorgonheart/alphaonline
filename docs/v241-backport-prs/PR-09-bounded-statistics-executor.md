# PR-09：`startup.py` 有界防重入异步统计池（`StatisticsTasks`）与单账户统计锁

- **拟提交标题**：`perf(startup): replace unbounded statistics ThreadPoolExecutor with bounded per-account deduplicating queue`
- **优先级**：P1（**2c4g / 2c8g 强烈建议必上，防止异步统计堆积导致 OOM**）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**

---

## 1. 场景

多账户或多账户组错峰实盘运行时，`startup.py` 在每轮交易完成后异步提交统计子进程（`statistics.py` / `group_statistics.py`），统计进程内部需加载 matplotlib 出图、查询持仓与历史文件，耗时较长且瞬时内存可达 `200–400 MiB/进程`。

## 2. 目标

将 `startup.py` 中无并发上限的 `ThreadPoolExecutor()` 替换为有界、按账户/组键防重入的异步执行器 `StatisticsTasks`，并通过跨进程文件锁 `account_lock` 防止同一账户的统计脚本并发写坏 `equity.csv` / `pos_*.pkl`。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/startup.py:67`：
```python
stat_executor = ThreadPoolExecutor()  # 统计脚本真的会跑很久很久
```
存在三个典型资源与稳定性隐患：
1. **无并发上限**：Python 默认 `ThreadPoolExecutor()` 线程数为 `min(32, cpu_count + 4)`，遇到网络抖动、企业微信超时或批量错峰账户集中结束时，会同时拉起多个 `statistics.py` 子进程，与下一轮交易计算争抢 CPU 和内存，极易在 4G/8G 主机上引发严重 Swap 或 OOM。
2. **无同账户防重入**：若上一轮某账户统计尚未结束，下一轮再次提交同一账户统计，两个子进程会并发读写同一 `equity.csv` 与 `*.pkl` 造成数据损坏。
3. **无异常观测**：原版 `stat_executor.submit(...)` 丢弃了 `Future` 对象，子进程异常静默吞掉。

## 4. 项目文档中的相关分析与设计

- 统计审计与架构设计：[2026-10-31 统计审计计划](../2026-10-31-statistics-audit-plan.md)、[2026-10-31 统计一期审查](../2026-10-31-statistics-phase1-review.md)
- 生产配置与隔离边界：[正式环境存储、内存与运维规范](../kb/production-limits-and-ops.md)
- 参考提交：`debac03`（新增 `tools/statistics_runtime.py` + `tests/test_statistics_phase1.py`）、`95dae79`（兼容 `v2.4.1` 账户组 `unit.name` / `unit.stem` 键）

## 5. 依赖

- **内部代码依赖**：无（新增轻量标准库工具 `core/utils/statistics_runtime.py` 或 `tools/statistics_runtime.py`，仅用 `threading`/`concurrent.futures`/`fcntl`/`msvcrt`）。
- **上游 `alpha` Arrow 依赖**：**无**。
- **跨平台兼容**：同时支持 Linux (`fcntl.flock`) 与 Windows (`msvcrt.locking`)。

## 6. 收益与证据

- **验证证据**：`tests/test_statistics_phase1.py`（`test_per_account_lock_and_scheduler_no_backlog`、`test_standalone_import_and_process_lock`、`test_atomic_failure_preserves_previous_file`）完整验证：
  - 同账户键重复提交直接跳过不堆积；
  - 达到 `max_workers` 上限时拒绝超额堆积并记录告警；
  - 后台统计失败时通过 `on_error`（`logger.warning`）显式暴露，不静默吞错；
  - 跨进程 `account_lock` 阻断同账户并发写文件。
- **资源收益**：在 4c8g 正式机 6 户错峰实盘稳定运行数周，彻底避免统计进程风暴与下一 offset 交易争用。

## 7. 代码改动

### 目标文件
- `core/utils/statistics_runtime.py`（新增，移植自 `alphaonline` 的 `tools/statistics_runtime.py`）
- `config.py`（新增 `statistics_config = dict(enabled=True, max_workers=2)`，支持 `config.json` 覆盖）
- `startup.py`（将 `ThreadPoolExecutor()` 替换为 `StatisticsTasks(statistics_config['max_workers'], logger.warning)`，提交时传入去重键 `unit.name` / `unit.stem`）
- `core/utils/statistics.py`（可选：在 `run()` 内对单账户目录加非阻塞 `account_lock`，锁被占用直接打印跳过）

### 核心改动要点
1. `StatisticsTasks(max_workers, on_error)`：维护 `self.active` 集合与容量上限；`submit(key, fn, *args)` 时若 `key in active` 或 `len(active) >= max_workers` 返回 `False` 并告警；任务完成回调在 `finally` 中释放 `key` 并上报未捕获异常。
2. `atomic_save(path, writer_fn)`：写临时文件再 `os.replace`，避免统计落盘中断损坏历史 `equity.csv` / `pos_*.pkl`。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 推荐 `statistics_config['max_workers'] = 2`。 |
| **2c8g** | 🟢 **反向降险** | 推荐 `max_workers = 1`（2 核机同时跑 2 个 matplotlib 统计进程会与下轮交易抢核）。 |
| **2c4g** | 🟢 **强烈推荐必上** | **必须设 `max_workers = 1`**。原版无界线程池在 2c4g 上一旦发生统计堆积极易 OOM；限制并发为 `1` 并防重入后，统计进程峰值内存被严格锁死在单份 `~200–350 MiB`。 |
