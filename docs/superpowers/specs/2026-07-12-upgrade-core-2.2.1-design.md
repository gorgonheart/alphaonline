# 设计文档：core 升级至 2.2.1（都江堰进出场引擎 + 修复），保留自研

日期：2026-07-12
分支：`upgrade/core-2.2.1`

## 目标

把 `core/`（当前基于 2.1.0 fork，version.py 显示 2.1.0）升级到 **完整 2.2.1 能力**：

1. 引入 2.2.1 全部能力——尤其是 `select_mode='entry_exit'` 都江堰进出场选币引擎（entry_list/exit_list、long/short 覆盖、exit_cooldown、fill_lag、max_hold_hours）。
2. 保留全部自研特性不受影响（Arrow reader、precompute_factor_configs、polars 向量化 calc_cross_sections、statistics 小改），且已有 `tests/` 全绿。
3. 新账户 `accounts/40m_dad0604_2.2.1.py`（回撤阈值空头，标准模式）能正确跑通。

## 方案

**Approach A：逐文件外科式合并**。以当前 `core/` 为基座，按 vendor 2.2.1 的改动分桶处理。参考树：`2.1.1core/`（fork 的 vendor 基线）、`2.2.1core/`（目标 vendor 版本），二者已加入 .gitignore，仅作对照，不纳入版本控制。

vendor 2.1.1→2.2.1 共改 12 个文件，分 4 桶：

### Bucket 1 — 整份 drop-in（当前 core 对这些文件零自研改动）
- `model/strategy_config.py` ← 2.2.1（+339 行，整个 entry_exit 引擎：`compute_entry_exit_numba`、`_run_entry_exit_one_side`、`_compute_holdings`、`run_entry_exit_selection_polars_lazy`、`calc_factor_expr_polars`、`filter_common_polars`、`_entry_exit_target_expr`，以及新配置字段 select_mode/fill_lag/exit_cooldown/max_hold_hours/entry_list/exit_list/long_·/short_·/ee_state_dir）
- `utils/ee_continuation.py` ← 新文件，直接拷入
- `utils/factor_hub.py` ← 2.2.1（locale 容错循环，顺带修掉当前 Windows 上硬编码 `en_US.UTF-8` 可能抛错的问题）

### Bucket 2 — vendor 仅装饰性改动 → 保留当前 core
- `simulator.py`、`binance/base_client.py`、`utils/log_kit.py`：vendor diff 只有水印 token（`1qt`→`5al5`）+ 一个尾逗号。不动，当前版本胜出。

### Bucket 3 — 真正需要手工合并（3 处）
- `model/backtest_config.py`：加入 vendor 的 `assign_ee_state_dir()` 方法 + 在 `load_strategy_config` 定稿后调用；**保留**自研 `precompute_factor_configs`（`__init__` 里的 attr + `create_config` 的传参）。两侧改动不重叠。
- `select_coin.py`：加入 vendor 的 entry_exit 分派（选币分支 `if stg_conf.select_mode=='entry_exit': run_entry_exit_selection_polars_lazy(...)`）+ 两处 incremental-lookback 的 `max(max_hold_period, incremental_lookback_hours)` fallback 修复；**保留**自研 `calc_factors` 里的 precompute 快路径与 `calc_cross_sections` 的 polars 向量化/并行写盘/Int8 cast。改动落在不同函数。
- **statistics 家族**（`statistics.py`、`statis_func.py`、`statistics_debug.py`）：以 vendor 2.2.1 为基座（它是超集：run_time 近 1h 成交额 + 选币归属 strategy 列 + 未匹配 fillna），再把当前 core 的少量小改叠加回去：
  - `label='Equity'`（去掉账户名前缀）
  - 读 csv 用 `encoding='gbk'`
  - `text_to_signal` 的 match-case 版本
  - 权益消息里 `strategy_pool[int(idx)]` 索引（当前 core 的写法，非 vendor 的 config_list[idx]）
  - sub_stg_file `if not ...exists(): continue` 守卫
  - pnl 报告调用当前 core 是注释掉的——**默认保持注释**（尊重自研选择），除非确认要开启

### Bucket 4 — 版本号
- `version.py` → 2.2.1

## 不改动的文件

vendor 从未改、当前 core 有自研的文件全部原样保留：`equity.py`、`position.py`、`data_bridge.py`、`real_trading.py`、`backtest.py`、`account_exec.py`、`model/account_config.py`、`utils/arrow_reader.py`、`utils/commons.py`、`utils/datatools.py`、`utils/functions.py`、`utils/runtime_data.py`。Arrow/perf 自研零回归风险。

## 账户依赖解析（requirement #2 无缺文件阻塞）

- `40m_dad0604_2.2.1.py` 用标准模式（filter_list），未用 entry_exit。
- filter 因子 `固定回撤阈值触发币数占比开仓门槛`、`动态回撤阈值` 经 FactorHub `factors.→sections.` fallback 命中 `sections/`（is_cross=True）。
- `Strategy_截面因子`/`Strategy_动态回撤阈值` 无 `strategy/` 包，按设计 fallback 到 `DummyStrategy`（inline-config + use_custom_func=False 的正常路径）。
- 账户用到自研 `precompute_factor_configs`，佐证不能整份替换 2.2.1、必须走合并。

## 验证

1. `tests/` 在 `alpha311` 下全绿（4 个 Arrow/align/parity/fallback 测试，skipif 依赖真实 30m 数据）——证明 requirement #1。
2. debug 模式跑目标账户：`$env:X3S_TRADING_ACCOUNT='40m_dad0604_2.2.1.py'; python core/account_exec.py <ts>` 端到端跑通、打印订单、无异常——证明 requirement #2。
3. 新增 1 个测试：合成小截面上断言 entry_exit 引擎产出合理 holdings（oracle-equivalence 风格）——证明 2.2.1 头号特性可用。
4. 更新 `CLAUDE.md`：版本号、tests/ 套件与约定、entry_exit / select_mode 特性说明。

## 交付方式

在分支 `upgrade/core-2.2.1` 实现，用户测试通过后自行 merge。
