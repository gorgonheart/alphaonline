# 框架 `v2.4.1` 增量升级与消费端第二批（环节 5+6+7）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 `D:/alphaonline` 既有本地定制、7 条跨项目数据契约、崩溃恢复原子性与数值逐位一致性（`check_exact=True`）的前提下，按「方案 A」在同一隔离特性分支上先完成 `position-mgmt-trading_v2.4.1` 增量窄合入（Phase A），再在其两阶段架构上落地消费端第二批环节 5+6+7 协同加速与 Numba 离线预热（Phase B），预计消费端每轮再省 `~9–12 s`。

**Architecture:** 分两阶段 6 个独立可测、原子提交的 Task 推进：(1) **Phase A（Tasks 1–3）** 基于 `D:/alphaonline/position-mgmt-trading_v2.4.1` 增量合入 `exchange_data_paths`、选币去除冗余 `是否交易==1` 过滤、`align_spot_swap_mapping*` 分组修正、BN/OKX 客户端与划转流水修复、`group_statistics.py` 6 子图组汇总，以及 `calc_by_account` / `trade_by_account` 两阶段拆分与 `account_group.py`（补齐组内 `MODEL` 成员走 `server_robust40.py trade --stage calc/trade` 与全链路 `dry_run` 防写保护，强制默认 `lookback_save_interval_hours=0` 关闭归档）；(2) **Phase B（Tasks 4–6）** 落地环节 5（`:X4:50` 无锁预热导入 + 释放锁等 `.ready` + `spot` 先到流式先读 + 独立 `wait_ready_s` 遥测）、环节 6（Step 6 `ProcessPoolExecutor` `fork` 后主进程后台预读 `449 MiB` `model/state.pkl` + `equity.py` 保存去重 + `process_equities` 4 线程并行读 + `AsyncDiskWriter` 后台非阻塞落盘 + 单户 `ratio` 内存直传）与环节 7（`DeferredAtomicSave` 后台写 `state.pkl.<tmp>` + `fsync` 与算仓并发重叠、`finish_live` 写 `complete=True` 前 `commit` 原子替换 + `server_robust40.py warm-numba` 预编译）。

**Tech Stack:** Python `3.11.11` (`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`), `pandas 2.2.3`, `numpy 2.0.2`, `polars 1.36.1`, `pyarrow 17.0.0`, `numba 0.60.0`, `matplotlib`, `pytest`.

**Spec:** [2026-11-25-v241-and-consumer-batch2-design.md](../specs/2026-11-25-v241-and-consumer-batch2-design.md)

## Global Constraints

- **唯一 Python 解释器**：`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（严禁使用 `conda run` 或其他环境；`requirements.txt` 0 变更，禁止 `pip install`）。
- **Windows pytest 标准命令**：每次测试必须传 `-q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check`。
- **分支与脏工作区保护（极其重要）**：
  - 当前仓库位于 `feat/phase5-2b-consumer-hardening`（`6cd263b`），工作区存在未提交的 `docs/*` 修改及未跟踪对照目录 `2.3.3core/`、`position-mgmt-trading_v2.4.1/`。
  - 在 Task 1 开始前执行 `git checkout -b feat/v241-and-consumer-batch2`（切勿使用 `git reset`、`git checkout --` 或 `git clean`）；每个 Task 提交时**只能 `git add` 该 Task 明确列出的文件路径**，严禁运行 `git add .` 或 `git add -A`，严禁把 `2.3.3core/` 或 `position-mgmt-trading_v2.4.1/` 加入版本控制。
- **严禁整文件覆盖 `core/`**：参考源码位于 `D:/alphaonline/position-mgmt-trading_v2.4.1/`，仅可按本计划指定点位做窄合并，必须完整保留 Arrow mmap、Polars 截面、`entry_exit` 续算、`MODEL` 26 点恢复、`dry_run` 防写、`StatisticsTasks` 有界统计池与 `portfolio_margin_client.py` 本地定制。
- **生产红线与离线铁律**：默认 `lookback_save_interval_hours = 0`（禁止默认归档）；测试全程离线（禁止真实外网请求、禁止启动 `startup.py` 实盘循环、禁止 SSH 操作远端服务器）；保持被编辑文件原有 CRLF/LF 行尾与顶部版权注释。

## Review Focus

1. **账户组内 `MODEL` 成员处于 `dry_run` 或触发退出码 `75` 重建时的行为** — `MODEL` 成员在 `--stage calc` 返回退出码 `75` 时不得抛未捕获 `CalledProcessError` 炸毁主循环，且因未产出当轮 `handoff.json` 必须触发 `run_group` 整组熔断跳过下单；`dry_run` 下 `--stage calc` 与 `--stage trade` 均不得调用 `reset_max_leverage`、撤单、下单或归集（Task 3 测试锁定）。
2. **`--wait-ready` 预热子进程在等待 `.ready` 期间与后台初始化 `install()` 的锁冲突** — 子进程在 `:X4:50 → swap.ready` 轮询期间严禁持有 `server.account_lock(destination)`；若等待期间 `warmup.pkl` 的 `(st_mtime_ns, st_size)` 发生变化（后台初始化刚完成安装），须在重新持锁后丢弃旧 `old_inputs` 与早期 `spot` 缓存并重走最新快照检查（Task 4 测试锁定）。
3. **`spot` `.ready` 已到但 `swap` `.ready` 超时或 `history_trim_date` 失配** — `swap` 超时须回收后台 `spot` 读线程、返回退出码 `76`（`Coordinator.handle_result` 视为跳过本轮返回 `False` 而不报错），且全程不修改 `warmup.pkl`（保持 `complete=True`）；`spot`/`swap` Arrow `history_trim_date` 不一致须抛 `ValueError` 中止（Task 4 测试锁定）。
4. **Step 6 预读 `model/state.pkl` 的 `fork` 时序与外部改写失效** — `start_model_state_preload` 必须在 Step 6 `ProcessPoolExecutor` 提交任务**之后**才启动线程；若 `state.pkl` 在预读后、消费前被改写（`mtime_ns` 或 `size` 改变），`consume_preloaded_model_state` 必须返回 `None` 回退同步重读；若 Step 6 抛错未进入 `MODEL`，`step6_market_handoff` 退出时必须清空缓存引用防止内存泄漏（Task 5 测试锁定）。
5. **环节 7 `DeferredAtomicSave` 在 `validate_targets` 失败或写盘失败时的崩溃恢复原子性** — `finish_live` 调用前磁盘上的 `model/state.pkl` 必须保持旧内容不变；若 `calc_ratios` / `agg_pos_ratio` / `validate_targets` 抛异常，`calc_by_account` 的 `finally` 必须调用 `abort()` 删除 `.tmp` 并释放 `.lock`；若后台写盘/`fsync` 抛 `OSError`，`finish_live` 中的 `commit()` 必须在修改 `revision_inputs.pkl` 和 `warmup.pkl` **之前**抛出异常并保留旧 `state.pkl`（Task 6 测试锁定）。

---

## File Map

- **Phase A（`v2.4.1` 增量窄合入）**：
  - Modify: `.gitignore` — 忽略 `2.3.3core/` 与 `position-mgmt-trading_v*/`
  - Modify: `core/version.py` — 更新版本号至 `2.4.1` (`v2.4.1.20261120`)
  - Modify: `config.py` — 新增 `exchange_data_paths = {}`、`lookback_save_interval_hours = 0`、账户组目录成员查找、账户级交易所路径解析
  - Modify: `core/utils/datatools.py` — 新增 `resolve_bmac_data_path`、扩展 `check_bmac_update_flag(run_time, exchanges=None)`、去除 `check_flags` 冗余 `sleep(1)` 并设 `poll_interval=0.1`、同步修复 `align_spot_swap_mapping` 与 `align_spot_swap_mapping_fast`
  - Modify: `core/select_coin.py` — `_selection_input_filters` 移除 `pl.col('是否交易') == 1`（保留 `KLINE_COLS`）
  - Modify: `core/binance/standard_client.py`, `core/binance/portfolio_margin_client.py` — 修复子账户转出按 `fromAccountType` 过滤（保留 `portfolio_margin_client` 本地定制）
  - Modify: `core/okx/base_client.py`, `core/okx/portfolio_margin_client.py` — 合入 `v2.4.1` OKX TradFi/限价 `51138`/`0.97` 最小额/`54000`/10 天划转流水修复
  - Modify: `core/utils/statis_func.py`, `core/utils/statistics.py`, `core/utils/statistics_debug.py`, `delist.py`, `monitor.py` — 新增 `statis_func.net_fund`、路径式 `init_system(str(profile))`
  - Create: `core/utils/group_statistics.py` — 6 子图账户组统计汇总（源自 `v2.4.1`）
  - Modify: `core/account_manager.py`, `core/equity.py`, `core/position.py`, `core/real_trading.py`, `core/account_exec.py`, `startup.py` — 账户单元加载、`calc_by_account`/`trade_by_account` 两阶段拆分、`scale` 动态杠杆、`dry_run` 与禁归档守卫
  - Create: `core/account_group.py`, `accounts/_demo_group/_manage.py`, `accounts/_demo_group/demo_a.py`, `accounts/_demo_group/demo_b.py` — 账户组核心编排与示例配置（扩展 `dispatcher` 兼容 `MODEL` 成员）
  - Modify: `tools/robust40/server.py`, `tools/robust40/cli/server_robust40.py` — 支持 `--stage {all,calc,trade}` 与 `--scale` 透传
  - Modify / Create Tests: `tests/test_align_fast_equivalence.py`, `tests/test_upgrade_241.py`
- **Phase B（消费端第二批环节 5 + 6 + 7）**：
  - Modify: `tools/robust40/initialization_config.py`, `tools/robust40/telemetry.py`, `tools/robust40/handoff.py`, `tools/robust40/server.py`, `tools/robust40/cli/server_robust40.py`, `startup.py` — 环节 5（`PREWARM_LEAD_SECONDS=10`、`--wait-ready`、`wait_and_read_inputs_streaming`、`reset_timer`）+ `warm-numba` 子命令
  - Create: `core/utils/async_disk.py` — 单线程作用域后台写盘器 `AsyncDiskWriter`
  - Modify: `tools/local_risk_model.py`, `core/equity.py`, `core/backtest.py`, `core/model/backtest_config.py`, `core/real_trading.py`, `positions/ModelRobust40Strategy.py` — 环节 6（`state.pkl` 后台预读、`equity.py` 保存去重、`process_equities` 并行读、`AsyncDiskWriter` 接入、单户 `ratio` 内存直传）+ 环节 7（`DeferredAtomicSave` 并发写盘与 `finish_live` 原子提交）
  - Create Test: `tests/test_consumer_batch2_pipeline.py`
  - Modify Docs (Task 6 收尾): `docs/DEVELOPMENT.md`, `docs/README.md`, `docs/kb/upstream-core-relations.md`, `docs/kb/performance-analysis.md`, `docs/kb/branches-and-sync.md`

---

### Task 1: Phase A-1 — 配置、多交易所路径、选币过滤与映射对齐修复

**Files:**
- Modify: `.gitignore`
- Modify: `core/version.py`
- Modify: `config.py`
- Modify: `core/utils/datatools.py`
- Modify: `core/select_coin.py`
- Modify: `tests/test_align_fast_equivalence.py`
- Create: `tests/test_upgrade_241.py`

**Interfaces:**
- Produces in `config.py`: `exchange_data_paths: dict = {}`, `lookback_save_interval_hours: int = 0`
- Produces in `core/utils/datatools.py`:
  - `resolve_bmac_data_path(exchange: str | None = None) -> Path`
  - `check_flags(run_time, flag_paths: List[Path], poll_interval: float = 0.1) -> bool`（移除 `all(...)` 命中后的 `time.sleep(1)`）
  - `check_bmac_update_flag(run_time, exchanges=None) -> bool`
  - `align_spot_swap_mapping(df, column_name, n)` 与 `align_spot_swap_mapping_fast(df, column_name, n)`（空值掩码统一基于 `column_name`）

- [ ] **Step 0: 切出隔离特性分支（保留未提交 `docs/*`）**

Run:
```powershell
git -C D:/alphaonline checkout -b feat/v241-and-consumer-batch2
```
Expected: `Switched to a new branch 'feat/v241-and-consumer-batch2'` 且 `git status -sb` 中原有 `M docs/...` 保持原样。

- [ ] **Step 1: 编写 Task 1 失败测试（`tests/test_upgrade_241.py` 与 `tests/test_align_fast_equivalence.py`）**

在 `tests/test_align_fast_equivalence.py` 中更新 `test_swap_path_mismatch_column` 以锁死 `v2.4.1` 新语义（当 `column_name='symbol_spot'` 且 `spot_col=['A','A','A','A','A']`、`swap_col=['','A','A','','A']`、`n=2` 时，仅前 2 行被置空 `['','','A','A','A']`，不再受 `symbol_swap==''` 干扰，且 `fast` 与原版相等），并新增模拟真实单币 `swap` 表（`symbol_swap` 全 `'BTCUSDT'`、`symbol_spot` 含多段空/非空重上市）的回归断言。

新建 `tests/test_upgrade_241.py`，写入以下测试：
1. `test_version_is_241`: 断言 `core.version.sys_version == '2.4.1'` 且 `build_version == 'v2.4.1.20261120'`。
2. `test_datatools_resolve_exchange_paths_and_fast_check_flags(tmp_path, monkeypatch)`:
   - 构造 `bn_root/preprocess_1h_resample/25m` 与 `okx_root/preprocess_1h_resample/25m` 及其 `spot_dict_{ts}.ready` / `swap_dict_{ts}.ready`；
   - monkeypatch `datatools.bmac_data_path = bn_root / 'preprocess_1h_resample'` 与 `datatools.exchange_data_paths = {'okx': str(okx_root)}`；
   - 断言 `check_bmac_update_flag(run_time, ['binance', 'okx'])` 在 `okx` 缺 `.ready` 时超时/轮询等待、补齐后 **`< 0.3 s` 即返回 `True`**（证明 `time.sleep(1)` 已被移除）。
3. `test_select_coin_no_longer_filters_is_trading_zero(tmp_path)`:
   - 构造含 `是否交易=0` 与 `是否交易=1` 行的最小 Polars DataFrame，调用 `core.select_coin._selection_input_filters(stg)` 过滤，断言 `是否交易=0` 但因子非空的行被保留，同时 `'是否交易' in core.select_coin.KLINE_COLS` 仍成立。
4. `test_config_group_account_lookup_and_exchange_override(tmp_path)`:
   - 在子进程（隔离 `PROJECT_ROOT` 或临时 `accounts/grp_alpha/member1.py` + `_manage.py` + `config.json` 含 `exchange_data_paths={'okx': ...}`）中设 `X3S_TRADING_ACCOUNT=member1` 导入 `config`，断言成功定位到 `accounts/grp_alpha/member1.py`、`config.exchange_name == 'okx'`、`config.bmac_data_path` 指向 `okx` 路径，且默认 `config.lookback_save_interval_hours == 0`。

- [ ] **Step 2: 运行测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py tests/test_align_fast_equivalence.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL（版本号仍为 `2.3.3`、`resolve_bmac_data_path` 未定义、`是否交易=0` 被过滤、`align_spot_swap_mapping` 仍按 `symbol_swap` 置 `NaN`）。

- [ ] **Step 3: 实现 Task 1 最小完备改动**

1. `.gitignore`：在 `2.3.1core/` 下追加 `2.3.3core/` 与 `position-mgmt-trading_v*/`。
2. `core/version.py`：更新 `sys_version = '2.4.1'`、`build_version = f'v{sys_version}.20261120'`。
3. `config.py`：
   - 在 `realtime_data_path` 后定义 `exchange_data_paths = {}`，在 `lookback_days = 0` 附近定义 `lookback_save_interval_hours = 0`；
   - 在 `if not account_config_path.exists(): account_config_path = get_file_path(...)` 后加入非 `_` 开头组子目录唯一成员匹配逻辑（对照 `position-mgmt-trading_v2.4.1/config.py:95-101`）；
   - `config.json` `allowed_attrs` 设为 `('realtime_data_path', 'exchange_data_paths', 'error_webhook_url', 'is_simulate', 'factor_col_limit', 'lookback_days', 'lookback_save_interval_hours', 'incremental_lookback_hours', 'proxy', 'shared_factor_pipeline')`；
   - 在 `config.json` 读取块后添加：
     ```python
     exchange_name = str(account_config.get('exchange_name', exchange_name)).lower()
     realtime_data_path = exchange_data_paths.get(exchange_name, realtime_data_path)
     lookback_save_interval_hours = lookback_save_interval_hours or 0
     ```
4. `core/utils/datatools.py`：
   - 从 `config` 导入 `exchange_data_paths`；
   - 实现 `resolve_bmac_data_path(exchange: str | None = None) -> Path`（使用模块当前 `exchange_data_paths` 与 `bmac_data_path`）；
   - 修改 `check_flags(run_time, flag_paths: List[Path], poll_interval: float = 0.1)`：全部存在时立即 `return True`（删掉 `time.sleep(1)`），未齐时 `time.sleep(poll_interval)`；
   - 修改 `check_bmac_update_flag(run_time, exchanges=None)` 支持多交易所路径集合；
   - `align_spot_swap_mapping` L155 改为 `df.loc[df[column_name].eq(''), 'group'] = np.nan`；
   - `align_spot_swap_mapping_fast` 去掉 `swap = df['symbol_swap'].to_numpy()`，将 `valid = swap != ''` 改为 `valid = col_ne`。
5. `core/select_coin.py`：`_selection_input_filters` 中 `filters = [pl.col('是否交易') == 1]` 改为 `filters = []`。

- [ ] **Step 4: 运行专项与关联回归确认 GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py tests/test_align_fast_equivalence.py tests/test_arrow_pkl_parity.py tests/test_shared_selection_stage.py tests/test_streaming_selection.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 1**

```powershell
git -C D:/alphaonline add .gitignore core/version.py config.py core/utils/datatools.py core/select_coin.py tests/test_align_fast_equivalence.py tests/test_upgrade_241.py
git -C D:/alphaonline commit -m "feat(v241): integrate exchange_data_paths, fast ready check, align fix and selection filter"
```

---

### Task 2: Phase A-2 — BN/OKX 客户端修复与统计扩展（`net_fund` + `group_statistics.py`）

**Files:**
- Modify: `core/binance/standard_client.py`
- Modify: `core/binance/portfolio_margin_client.py`
- Modify: `core/okx/base_client.py`
- Modify: `core/okx/portfolio_margin_client.py`
- Modify: `core/utils/statis_func.py`
- Create: `core/utils/group_statistics.py`
- Modify: `core/utils/statistics.py`
- Modify: `core/utils/statistics_debug.py`
- Modify: `delist.py`
- Modify: `monitor.py`
- Modify: `tests/test_upgrade_241.py`

**Interfaces:**
- Produces in `core/utils/statis_func.py`: `net_fund(df: pd.DataFrame) -> pd.DataFrame`
- Produces in `core/utils/group_statistics.py`: `load_member_equity`, `load_member_transfer`, `calc_net`, `build_group_equity`, `build_summary_msg`, `draw_group_equity`
- Produces in `core/okx/base_client.py`: `_is_blocked_tradfi`, `set_fee_burn`, `fetch_transfer_history`（10 天 `bills` + `bills_archive` 去重），`spot_inst_type = 'SPOT'`（子类 `OkxPortfolioMarginClient.spot_inst_type = 'MARGIN'`）

- [ ] **Step 1: 在 `tests/test_upgrade_241.py` 中补充客户端与组统计失败测试**

新增测试：
1. `test_bn_subaccount_transfer_direction_uses_sub_side`:
   - 构造 fake `BinanceStandardClient` 与 `BinancePortfolioMarginClient` 实例（mock `exchange.sapi_get_sub_account_transfer_subuserhistory` 返回 `transfer_type=2` 转出记录：一条 `fromAccountType='MARGIN', toAccountType='SPOT'`，一条 `fromAccountType='SPOT', toAccountType='SPOT'`），断言普通子账户仅保留 `fromAccountType in ('SPOT', 'USDT_FUTURE')`，统一子账户仅保留 `fromAccountType == 'MARGIN'`（修复前普通账户会误把子账户杠杆转母账户现货记为转出，统一账户会漏记子账户 MARGIN 转母账户 SPOT）；同时断言 `hasattr(BinancePortfolioMarginClient, 'get_statistics_overview')` 仍存在（锁死本地定制未丢失）。
2. `test_okx_tradfi_limit_51138_and_transfer_history`:
   - 验证 `OkxPortfolioMarginClient.spot_inst_type == 'MARGIN'`；
   - 验证 `_is_blocked_tradfi({'instCategory': '3'}, 'spot') is True`（当 `filter_bstocks=True`）且 `'swap'` 对 `instCategory='4'` 在不含 `'TRADIFI_PERPETUAL'` 时返回 `True`；
   - 验证 `_parse_okx_price_limit` 兼容 `51138` 卖单最低卖价且重试价格按 `price_limit * 1.001`（买单 `0.999`）向内收 `0.1%`；
   - 验证 `fetch_transfer_history` 合并 `bills` 与 `bills_archive` 按 `billId` 去重并汇总为 `['time', '账户总净值', 'type']`。
3. `test_group_statistics_builds_6_subplots_with_missing_member_transfer(tmp_path, monkeypatch)`:
   - 在 `tmp_path` 构造 2 个成员的 `账户信息/equity.csv`（含本地扩展列 `observed_at`、`equity_source` 等，仅成员 A 有 `transfer.csv`、成员 B 无 `transfer.csv`），调用 `build_group_equity` 与 `draw_group_equity(..., pic_path)`，断言生成的 PNG 文件非空且 `calc_net` 正确按份额法抵消/计入出入金。

- [ ] **Step 2: 运行新增测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py -k "bn_subaccount or okx_tradfi or group_statistics" -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL。

- [ ] **Step 3: 合入客户端与统计模块改动**

1. `core/binance/standard_client.py`（约 L468-476）与 `core/binance/portfolio_margin_client.py`（约 L827-835）：精确替换 `transfer_type` 子账户过滤四行为 `sub_side = res['toAccountType'] if transfer_type == 1 else res['fromAccountType']`（参照 `position-mgmt-trading_v2.4.1` 对应段落，**严禁改动 `portfolio_margin_client.py` 其余方法**）。
2. `core/okx/base_client.py` 与 `core/okx/portfolio_margin_client.py`：与 `2.3.3core` 本地原本 0 功能差异，完整合入 `position-mgmt-trading_v2.4.1/core/okx/{base_client.py, portfolio_margin_client.py}` 的全部新增与修复。
3. `core/utils/statis_func.py`：新增 `net_fund(df)`（保留 `.copy()` 防 `SettingWithCopyWarning`）；`core/utils/statistics.py` 与 `core/utils/statistics_debug.py` 改为 `from core.utils.statis_func import net_fund`（移除各自重复定义），并将 `init_system(profile.stem)` / `init_system(account_profile_file.stem)` 改为 `init_system(str(profile))`（**保留** `statistics.py` 全部 `statistics_snapshot` 单源净值与底图双列 `fig.legend` 定制！）。
4. `delist.py` 与 `monitor.py`：同步 `init_system(str(account_profile_file))`。
5. 新建 `core/utils/group_statistics.py`（源自 `position-mgmt-trading_v2.4.1/core/utils/group_statistics.py`；注意该模块在被导入时引用 `from core.account_group import load_group`——可放在函数内延迟导入或在 Task 2 先建最小 `core/account_group.py` / 在 Task 3 补齐，建议在 `group_statistics.py` 顶层或 `main()` 内正常引用，若 Task 2 先不创建 `account_group.py`，可在 `group_statistics.py` 的 `main()` 内 `from core.account_group import load_group` 使模块单独导入测试零依赖！）。

- [ ] **Step 4: 运行客户端与统计回归确认 GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py tests/test_upgrade_231.py tests/test_upgrade_233.py tests/test_statistics_snapshot.py tests/test_spot_cost_estimate.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 2**

```powershell
git -C D:/alphaonline add core/binance/standard_client.py core/binance/portfolio_margin_client.py core/okx/base_client.py core/okx/portfolio_margin_client.py core/utils/statis_func.py core/utils/group_statistics.py core/utils/statistics.py core/utils/statistics_debug.py delist.py monitor.py tests/test_upgrade_241.py
git -C D:/alphaonline commit -m "feat(v241): merge BN/OKX client fixes, net_fund helper and group_statistics"
```

---

### Task 3: Phase A-3 — 账户单元、两阶段 `calc`/`trade`、账户组与 `MODEL` + `dry_run` 融合

**Files:**
- Modify: `core/account_manager.py`
- Modify: `core/equity.py`
- Modify: `core/position.py`
- Modify: `core/real_trading.py`
- Modify: `core/account_exec.py`
- Create: `core/account_group.py`
- Create: `accounts/_demo_group/_manage.py`, `accounts/_demo_group/demo_a.py`, `accounts/_demo_group/demo_b.py`
- Modify: `tools/robust40/server.py`
- Modify: `tools/robust40/cli/server_robust40.py`
- Modify: `startup.py`
- Modify: `tests/test_upgrade_241.py`

**Interfaces:**
- `core/account_manager.py`:
  - `load_multi_accounts() -> list[Path]`（含组内成员，忽略 `_` 开头文件与 `_` 开头组目录，重名抛 `ValueError`）
  - `load_account_units() -> list[Path]`
  - `read_config_value(config_file_path: Path, key: str, default: str) -> str`
  - `read_hour_offset_from_config(config_file_path: Path) -> str`
  - `read_exchange_from_config(config_file_path: Path) -> str`
  - `run_account_exec(account_path: Path, run_time, *args)`
- `core/position.py`:
  - `calc_target_position(account_config: AccountConfig, df_spot_ratio: pd.DataFrame | None = None, df_swap_ratio: pd.DataFrame | None = None, is_debug=False, scale: float = 1.0) -> pd.DataFrame`
- `core/real_trading.py`:
  - `is_archive_round(run_time) -> bool`
  - `calc_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time) -> bool`
  - `trade_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time, ratio_folder: Path = runtime_folder, scale: float = 1.0, *, preloaded_ratios: tuple[pd.DataFrame, pd.DataFrame] | None = None)`
  - `run_by_account(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time)`
- `core/account_group.py`:
  - `is_account_group(path: Path) -> bool`, `load_group(group_path: Path) -> dict`, `calc_scales(equities, weights, max_scale) -> dict`, `build_transfer_msg(group, equities, scales) -> str | None`
  - `run_group(group: dict, run_time, *, dispatcher=None)`（`dispatcher(account_path, run_time, *stage_args)` 默认为 `run_account_exec`）
- `tools/robust40/server.py` & `tools/robust40/cli/server_robust40.py`:
  - `Coordinator.command(self, account, run_time, market, *extra_args)`：透传 `*extra_args`（如 `'--stage', 'calc'` 或 `'--stage', 'trade', '--scale', '1.25'`）；当 `extra_args` 含 `'--stage', 'trade'` 时对已就绪 `MODEL` 账户免重复 `.ready`/Arrow 校验直接返回 `server_robust40.py trade` 命令。
  - `server_robust40.py trade` 支持可选 `--stage {all,calc,trade}`（默认 `'all'`）与 `--scale <float>`（默认 `1.0`）：`stage == 'trade'` 时跳过 `handoff.prepare`/`begin`/`revision_inputs.pending.pkl`（因同轮 `calc` 已提交快照），仅在 `account_lock` 下转发给 `core.account_exec.main()` 执行 `run_trade_stage`。

- [ ] **Step 1: 在 `tests/test_upgrade_241.py` 中编写账户组、两阶段 `dry_run` 与 `MODEL` 成员联动失败测试**

新增测试：
1. `test_account_group_scales_and_transfer_thresholds`:
   - 测试 `calc_scales({'a': 40.0, 'b': 960.0}, {'a': 0.05, 'b': 0.95}, max_scale=2.0)` -> `a=1.25, b=0.989583...`；极端偏离触发 `max_scale=2.0` 截断；
   - 测试 `build_transfer_msg` 在 `absolute`（阈值 `0.05`，`4% vs 5%` 不提醒）与 `relative`（阈值 `0.15`，`|0.04-0.05|/0.05 = 0.20 > 0.15` 提醒，且发往 `transfer_webhook`）下的精确分界。
2. `test_calc_target_position_scale_caps_spot_and_keeps_backward_compat(tmp_path, monkeypatch)`:
   - 验证不传 `df_spot_ratio`/`df_swap_ratio` 时自动回退读 `runtime_folder`；
   - 验证传入内存 DataFrame 且 `scale=1.5 > 1` 时：现货目标权重被除以 `1.5`（名义金额不超过自身 `1x` 权益），而合约目标手数按 `1.5x` 放大；同时验证 `LiveRebRobust40` 执行器兼容。
3. `test_run_group_with_model_member_exit75_skips_whole_group_and_dry_run_guards(tmp_path, monkeypatch)`:
   - 模拟组内两个成员（含 1 个 `MODEL` 成员），当 `dispatcher` 在 `--stage calc` 对 `MODEL` 成员触发退出码 `75`（经 `Coordinator.handle_result` 转后台重建返回 `False`，不写当轮 `handoff.json`）时，断言 `run_group` 不抛未捕获异常、不进入 `--stage trade`、并向 `transfer_webhook` 发送 `【账户组跳过】` 通知；
   - 验证 `dry_run` 模式下 `prepare_account` 与 `trade_by_account` 零调用 `reset_max_leverage`、`cancel_all_*_orders`、`proceed_*_order`、`collect_asset`、`adjust_base_margin`，且 `lookback_save_interval_hours == 0` 时 `calc_by_account` 绝不将 `conf.lookback_days = 7` 改写为 `0`。

- [ ] **Step 2: 运行新增测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py -k "account_group or calc_target_position_scale or run_group_with_model" -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL。

- [ ] **Step 3: 实现 Task 3 代码**

1. 复制并适配 `accounts/_demo_group/{_manage.py, demo_a.py, demo_b.py}`（源自 `position-mgmt-trading_v2.4.1/accounts/_demo_group/`）与 `core/account_group.py`（为 `run_group` 增加关键字参数 `*, dispatcher=None`，默认 `dispatch = dispatcher or run_account_exec`）。
2. 更新 `core/account_manager.py`、`core/equity.py`、`core/position.py`（保留单参回退读 `runtime_folder`）、`core/real_trading.py`（保留 `step6_market_handoff`、`dry_run` 全拦截、`lookback_save_interval_hours > 0` 才干预 `lookback_days`，以及单账户 `run_by_account` 将内存 `(df_spot_ratio, df_swap_ratio)` 传给 `trade_by_account`）和 `core/account_exec.py`（`prepare_account` 保留 `dry_run` 保护；`run_trade_stage` 在 `rebalance_mode.mode == 'RebRobust40'` 时调用 `register_execution()`）。
3. 更新 `tools/robust40/server.py::Coordinator.command` 与 `tools/robust40/cli/server_robust40.py`：支持 `--stage` 与 `--scale` 参数透传（`stage == 'trade'` 时在 `account_lock` 下直接调用 `account_exec.main()` 执行下单轮）。
4. 更新 `startup.py`：
   - 使用 `load_account_units()`、`is_account_group`、`load_group`、`read_hour_offset_from_config`、`read_exchange_from_config`、`resolve_bmac_data_path`；
   - 构造组调度回调 `_dispatch_unit_member(path, run_time, *extra_args)`：通过 `server_initializer.command(path, run_time, resolve_bmac_data_path(read_exchange_from_config(path)), *extra_args)` + `subprocess.run(..., check=False)` + `server_initializer.handle_result(...)` 统一承载单账户与组内成员（传统或 `MODEL`）；
   - 保留 `StatisticsTasks(statistics_config['max_workers'], logger.warning)`、`warmup_plans`、`clean_startup` 与 `check_accounts()` 的 `is_simulate == 'dry_run'` 早退。

- [ ] **Step 4: 运行 Phase A 全套回归确认 GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py tests/test_2b_rolling_s3_and_trim.py tests/test_removed_series_governance.py tests/test_rebuild_handoff.py tests/test_server_initialization.py tests/test_server_snapshot_lifecycle.py tests/test_deployment.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 3（Phase A `v2.4.1` 底座收口）**

```powershell
git -C D:/alphaonline add core/account_manager.py core/equity.py core/position.py core/real_trading.py core/account_exec.py core/account_group.py accounts/_demo_group/_manage.py accounts/_demo_group/demo_a.py accounts/_demo_group/demo_b.py tools/robust40/server.py tools/robust40/cli/server_robust40.py startup.py tests/test_upgrade_241.py
git -C D:/alphaonline commit -m "feat(v241): add account groups, calc/trade stage split and MODEL group compatibility"
```

---

### Task 4: Phase B-1（环节 5）— Worker `:X4:50` 预热、无锁等待、`spot` 先到流式先读与独立遥测

**Files:**
- Modify: `tools/robust40/initialization_config.py`
- Modify: `tools/robust40/telemetry.py`
- Modify: `tools/robust40/handoff.py`
- Modify: `tools/robust40/server.py`
- Modify: `tools/robust40/cli/server_robust40.py`
- Modify: `startup.py`
- Create: `tests/test_consumer_batch2_pipeline.py`

**Interfaces:**
- `tools/robust40/initialization_config.py`: `PREWARM_LEAD_SECONDS = 10`
- `tools/robust40/telemetry.py`: `reset_timer(**values) -> None`（重置当前轮 `_start = time.perf_counter()` 并写入 `values` 如 `prewarm_s`, `wait_ready_s`, `spot_early_lead_s`）
- `tools/robust40/handoff.py`:
  - `read_pivot_inputs(root, minute, end, start=None, *, kinds=('spot', 'swap')) -> dict`
  - `wait_and_read_inputs_streaming(bmac_data_path, run_time, minute, stamp, end, start, required_columns, *, deadline, poll_interval=0.05) -> tuple[str, dict | None, tuple[str | None, str | None], dict]`
    - 返回 `(status, inputs_dict, (spot_trim, swap_trim), stats_dict)`，其中 `status` 为 `'ok'`、`'timeout'`（任意 `.ready` 超时未到）或 `'missing_arrow'`（Arrow 缺失/损坏/缺少 `required_columns`）。
    - 当 `spot_dict_{stamp}.ready` + `market_pivot_spot_{stamp}.ready` 就绪时，立即在 `ThreadPoolExecutor(max_workers=2)` 中异步启动 `spot` Arrow `required_columns` 校验 + `read_arrow_tail(..., 'spot', ..., indexed=True)` 与 `read_pivot_inputs(..., kinds=('spot',))`；
    - 当 `swap_dict_{stamp}.ready` + `market_pivot_swap_{stamp}.ready` 就绪时，记录 `ready_perf = time.perf_counter()`，校验 `swap` Arrow `required_columns`，并发读取 `swap` Arrow 与 `swap` Pivot，汇合 `spot` 结果并返回与 `read_inputs(...)` 100% 等价的 `out` 字典及耗时明细（`revision_arrow_spot_s`, `revision_arrow_swap_s`, `revision_pivot_s`, `wait_ready_s`, `spot_early_lead_s`）。
- `tools/robust40/server.py`:
  - `Coordinator.launch_prewarmed_trade(self, account, run_time, market) -> tuple[subprocess.Popen, list[str], dict] | None`：对单户就绪 `MODEL` 账户在 `:X4:50` 校验旧快照 `status == 'ready'` 后立即 `subprocess.Popen` 启动 `[sys.executable, server_robust40.py, 'trade', '--account', str(account), '--stamp', str(stamp), '--wait-ready']`。
  - `Coordinator.handle_result(...)`：新增识别 `returncode == WAIT_FOR_DATA_EXIT`（`76`）且为 `trade` 模式时，记录 `logger.warning(f'{Path(account).stem}: 数据中心数据未更新，跳过当前，{run_time}')` 并返回 `False`（不抛 `CalledProcessError`、不触发统计）。

- [ ] **Step 1: 在 `tests/test_consumer_batch2_pipeline.py` 中编写环节 5 失败测试**

写入以下测试：
1. `test_stage5_streaming_spot_first_finishes_before_swap_ready_and_matches_oracle(tmp_path, monkeypatch)`:
   - 构造完整合成 `25m` `spot_dict_{stamp}.arrow`、`swap_dict_{stamp}.arrow`、`market_pivot_spot_2026.pkl`、`market_pivot_swap_2026.pkl`；
   - 先只写 `spot_dict_{stamp}.ready` 与 `market_pivot_spot_{stamp}.ready`，拦截 `read_arrow_tail` 在 `kind == 'spot'` 完成时置位 `spot_done = threading.Event()`；后台线程等待 `spot_done.wait(timeout=2.0)` **确认 `spot` 已在 `swap.ready` 出现前读完**后再写出 `swap_dict_{stamp}.ready` 与 `market_pivot_swap_{stamp}.ready`（若未实现流式先读，`spot_done.wait` 超时直接证伪！）；
   - 断言最终返回的 `inputs` 与同步 `handoff.read_inputs(...)` 逐键 `pd.testing.assert_frame_equal(..., check_exact=True)` 完全一致，且 `spot_early_lead_s > 0`。
2. `test_stage5_wait_ready_releases_account_lock_and_excludes_wait_from_total_s(tmp_path)`:
   - 在 `wait_and_read_inputs_streaming` 等待 `swap.ready` 期间，断言外部另一线程/进程尝试获取 `server.account_lock(destination)` **立即成功（不抛 `BlockingIOError`）**；
   - 验证 `telemetry.reset_timer(wait_ready_s=0.35)` 后，`total_s` 不包含前置 `0.35 s` 等待，而日志 payload 中保留 `wait_ready_s == 0.35`。
3. `test_stage5_timeout_returns_76_and_missing_columns_returns_75(tmp_path)`:
   - `swap.ready` 超时（`deadline = time.time() + 0.15`）返回 `status == 'timeout'`（`server_robust40` 退出码 `76`、`Coordinator.handle_result` 返回 `False` 不抛错、原快照 `warmup.pkl` 的 `complete` 仍为 `True`）；
   - Arrow 缺少 `required_columns` 返回 `status == 'missing_arrow'`（退出码 `75` 交接重建）。

- [ ] **Step 2: 运行环节 5 测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_consumer_batch2_pipeline.py -k stage5 -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL（`wait_and_read_inputs_streaming` / `reset_timer` / `launch_prewarmed_trade` 未定义）。

- [ ] **Step 3: 实现环节 5 代码**

1. `tools/robust40/initialization_config.py`：新增 `PREWARM_LEAD_SECONDS = 10`。
2. `tools/robust40/telemetry.py`：新增 `reset_timer(**values)`（当 `state := _current.get()` 非空时，置 `state['_start'] = time.perf_counter()` 并 `state.update(values)`）。
3. `tools/robust40/handoff.py`：
   - 为 `read_pivot_inputs` 增加关键字参数 `*, kinds=('spot', 'swap')`；
   - 实现 `wait_and_read_inputs_streaming(...)`；
   - 为 `prepare` / `_prepare` 增加可选参数 `*, preloaded_old=None, preloaded_current=None`：当调用方已通过流式读取拿到 `old` 与 `current` 时直接进入 `compare_s`（`first_revision`），免除重复读盘！
4. `tools/robust40/cli/server_robust40.py`：
   - 为 `trade` 模式增加 `--wait-ready` 参数；
   - 当 `a.mode == 'trade' and a.wait_ready` 时：**在进入 `server.account_lock` 之前**先完成 `from core.account_exec import main` 预导入，短暂持 `account_lock` 读取 `(warmup_sig, original, old_inputs)` 后释放锁，调用 `handoff.wait_and_read_inputs_streaming(...)`，就绪后调用 `telemetry.reset_timer(...)` 并重新进入 `with server.account_lock(destination):`（若 `warmup.pkl` 的 `(st_mtime_ns, st_size)` 在等待期间变了，自动回退常规同步检查与 `handoff.prepare`）。
5. `tools/robust40/server.py` 与 `startup.py`：
   - 实现 `Coordinator.launch_prewarmed_trade` 与 `handle_result` 对 `WAIT_FOR_DATA_EXIT`（`76`）的静默跳过处理；
   - 在 `startup.py::run_loop()` 中，当 `not is_debug` 且 `len(due_units) == 1` 且该单元为 `enabled(unit)` 单账户 `MODEL` 时，睡眠至 `run_time.timestamp() - PREWARM_LEAD_SECONDS` 后优先调用 `launch_prewarmed_trade`（返回 `None` 时平滑回退原有 `check_bmac_update_flag` + `command` 流程）。

- [ ] **Step 4: 运行环节 5 与恢复/交接回归套件确认 GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_consumer_batch2_pipeline.py tests/test_phase5_optimizations.py tests/test_rebuild_handoff.py tests/test_universe_expansion_revision.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 4**

```powershell
git -C D:/alphaonline add tools/robust40/initialization_config.py tools/robust40/telemetry.py tools/robust40/handoff.py tools/robust40/server.py tools/robust40/cli/server_robust40.py startup.py tests/test_consumer_batch2_pipeline.py
git -C D:/alphaonline commit -m "perf(batch2-stage5): prewarm MODEL trade worker and stream spot inputs before swap ready"
```

---

### Task 5: Phase B-2（环节 6）— `model/state.pkl` 隐藏预读、`equity.py` 保存去重、`process_equities` 并行读与 `AsyncDiskWriter`

**Files:**
- Create: `core/utils/async_disk.py`
- Modify: `tools/local_risk_model.py`
- Modify: `core/equity.py`
- Modify: `core/backtest.py`
- Modify: `core/model/backtest_config.py`
- Modify: `core/real_trading.py`
- Modify: `positions/ModelRobust40Strategy.py`
- Modify: `tests/test_consumer_batch2_pipeline.py`

**Interfaces:**
- `core/utils/async_disk.py`:
  - `class AsyncDiskWriter`: 单后台工作线程队列写盘器，提供 `submit(fn, *args, **kwargs) -> Future`、`flush() -> None`（阻塞等待已提交任务全部完成并重抛首个异常）、`close() -> None` 及 `__enter__` / `__exit__`。
- `tools/local_risk_model.py`:
  - `start_model_state_preload(path: Path) -> None`
  - `consume_preloaded_model_state(path: Path) -> dict | None`
  - `clear_preloaded_model_state() -> None`
- `core/backtest.py`:
  - `step6_market_handoff(*, model_state_preload_path: Path | None = None)`：在 Step 6 `ProcessPoolExecutor` 提交全部 `_step6_worker` **之后**调用 `start_model_state_preload`，并在 `finally:` 调用 `clear_preloaded_model_state()`。
- `core/model/backtest_config.py`:
  - `process_equities(self, run_time: datetime, hour_offset: str = '0m', *, inputs=None, async_writer: AsyncDiskWriter | None = None)`
  - `calc_ratios(self, *, timing_inputs=None, async_writer: AsyncDiskWriter | None = None)`

- [ ] **Step 1: 在 `tests/test_consumer_batch2_pipeline.py` 中编写环节 6 失败测试**

新增测试：
1. `test_stage6_model_state_preload_hits_cache_and_invalidates_on_mtime_change(tmp_path, monkeypatch)`:
   - 写出测试 `state.pkl`；调用 `start_model_state_preload(state_path)`，随后把 `pd.read_pickle` monkeypatch 为抛 `AssertionError('should hit preloaded state')`，断言 `consume_preloaded_model_state(state_path)` 准确返回预读字典，且第二次调用返回 `None`（单次消费即释放）；
   - 再次启动预读后修改文件内容使 `size`/`mtime_ns` 改变，断言 `consume_preloaded_model_state` 返回 `None`（拒绝陈旧缓存）；
   - 断言 `step6_market_handoff(model_state_preload_path=state_path)` 在内部抛异常退出后缓存槽为空（无泄漏）。
2. `test_stage6_equity_fast_counts_and_ratio_snapshot_match_oracle_exact(tmp_path)`:
   - 构造含正数、负数、`0.0`、`NaN` 及 0 列侧的 `df_spot_ratio` / `df_swap_ratio` 与历史快照，对比新 `calc_equity` / `create_ratio_snapshot` 与独立旧版 `.count(axis=1)` + 三次 `reindex` Oracle，断言 `account_df`、`ratio_spot.pkl`、`ratio_swap.pkl` `check_exact=True` 100% 一致。
3. `test_stage6_parallel_process_equities_and_async_disk_writer_parity_and_error_propagation(tmp_path)`:
   - 构造 6 个子策略目录（含 `资金曲线.csv` / `资金曲线_再择时.csv` 与 `ratio_{spot,swap}.pkl`），分别运行串行同步基线与 `AsyncDiskWriter` + 并行 `process_equities`，`writer.flush()` 后断言 `equity_dfs`、`ratio_dfs` 及磁盘上 6 个 `equity_df.pkl` 逐位完全一致；
   - 向 `AsyncDiskWriter` 提交一个抛 `OSError('disk full')` 的任务，断言 `writer.flush()` 准确重抛 `OSError('disk full')`。

- [ ] **Step 2: 运行环节 6 测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_consumer_batch2_pipeline.py -k stage6 -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL（`core.utils.async_disk` 与 `start_model_state_preload` 未定义）。

- [ ] **Step 3: 实现环节 6 代码**

1. 新建 `core/utils/async_disk.py` 实现 `AsyncDiskWriter`（内部封装 `ThreadPoolExecutor(max_workers=1, thread_name_prefix='async_disk')` + 已提交 `Future` 列表，`flush()` 按序 `.result()` 并清空列表，`close()` 调用 `shutdown(wait=True)`）。
2. 在 `tools/local_risk_model.py` 实现线程安全的 `start_model_state_preload`、`consume_preloaded_model_state`、`clear_preloaded_model_state`。
3. 在 `core/backtest.py`：
   - 新增模块级 `_ROUND_MODEL_STATE_PRELOAD_PATH = None`，扩展 `step6_market_handoff(*, model_state_preload_path=None)` 保存并在 `finally:` 调用 `clear_preloaded_model_state()`；
   - 在 `run_backtest_multi` 的 `with ProcessPoolExecutor(max_workers=pool_workers) as executor:` 提交完 `futures = {...}` **紧接下一行**、`for f in as_completed(futures):` **之前**，若 `_ROUND_MODEL_STATE_PRELOAD_PATH is not None`，调用 `start_model_state_preload(_ROUND_MODEL_STATE_PRELOAD_PATH)`。
4. 在 `positions/ModelRobust40Strategy.py::prepare()`（约 L1466）：在 `with lock(state):` 内优先调用 `consume_preloaded_model_state(target)`，命中打印 `logger.info('MODEL仓管：命中Step 6后台预加载状态')`，未命中回退 `pd.read_pickle(target)`。
5. 在 `core/equity.py`：删除 `create_ratio_snapshot` 第 3 次冗余 `reindex`；将 `calc_equity` 中 `symbol_long_num` / `symbol_short_num` 替换为 `np.errstate(invalid='ignore')` 下的二维 NumPy `(arr > 0).sum(axis=1, dtype=np.int64)` 与 `(arr < 0).sum(axis=1, dtype=np.int64)`。
6. 在 `core/model/backtest_config.py` 与 `core/real_trading.py`：
   - `process_equities` 在 `inputs is None and len(configs) > 1` 时用 `ThreadPoolExecutor(max_workers=min(4, len(configs)))` 并行读取处理各子策略，主线程按 `configs` 原顺序归集，`equity_df.pkl` 支持交由 `async_writer` 写盘（传 `equity_df.copy(deep=False)`）；
   - `calc_ratios` 支持将 `仓位比例-原始.csv`（传 `df_ratio.copy(deep=False)`）与 `仓位比例.csv`（传 `df_ratio.copy(deep=False)`）交由 `async_writer` 写盘；
   - `core/real_trading.py`：为 `calc_by_account` 增加可选 `*, async_writer: AsyncDiskWriter | None = None`——单账户 `run_by_account` 创建 `with AsyncDiskWriter() as writer:` 贯穿 `calc_by_account(..., async_writer=writer)`（把 `runtime_folder/df_{spot,swap}_ratio.pkl` 及 `spot/swap_ratio_*.csv` 也投递进 `writer`，并通过 `me_conf._live_target_ratios` 把内存 DataFrame 直接传给 `trade_by_account(..., preloaded_ratios=...)`）与 `trade_by_account`，在 `run_by_account` 返回前由 `with` 块自动 `writer.flush()`；而账户组 `run_calc_stage` 未传外部 `writer` 时，`calc_by_account` 内部自建 `with AsyncDiskWriter() as writer:` 并在返回 `True` 前 `flush()`，保证 `save_handoff` 读盘 100% 安全！

- [ ] **Step 4: 运行环节 6 与 Step 6 / P3 / P4 / P5 回归套件确认 GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_consumer_batch2_pipeline.py tests/test_p4_optimizations.py tests/test_phase5_optimizations.py tests/test_checkpoint_live.py tests/test_main_chain_parity.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 5**

```powershell
git -C D:/alphaonline add core/utils/async_disk.py tools/local_risk_model.py core/equity.py core/backtest.py core/model/backtest_config.py core/real_trading.py positions/ModelRobust40Strategy.py tests/test_consumer_batch2_pipeline.py
git -C D:/alphaonline commit -m "perf(batch2-stage6): preload MODEL state post-fork, deduplicate equity save and overlap non-blocking writes"
```

---

### Task 6: Phase B-3（环节 7）— `DeferredAtomicSave` 状态写盘重叠、`finish_live` 事务闭环、`warm-numba` 与全仓回归文档收尾

**Files:**
- Modify: `tools/local_risk_model.py`
- Modify: `positions/ModelRobust40Strategy.py`
- Modify: `tools/robust40/server.py`
- Modify: `core/real_trading.py`
- Modify: `tools/robust40/cli/server_robust40.py`
- Modify: `tests/test_consumer_batch2_pipeline.py`
- Modify: `docs/DEVELOPMENT.md`, `docs/README.md`, `docs/kb/upstream-core-relations.md`, `docs/kb/performance-analysis.md`, `docs/kb/branches-and-sync.md`

**Interfaces:**
- `tools/local_risk_model.py`:
  - `class DeferredAtomicSave(path: Path, bundle, lock_cm=None)`:
    - 构造即启动单后台线程创建同目录临时文件 `.<rand>.tmp` 并执行 `pd.to_pickle(bundle, tmp)` + `os.fsync(fd)`；
    - `commit() -> None`（幂等）：`join` 线程；若线程有异常则清理 `tmp`、退出 `lock_cm` 并重抛；否则在当前线程 `os.replace(tmp, path)` 后退出 `lock_cm`；
    - `abort() -> None`（幂等，吞掉写盘异常仅做清理）：`join` 线程、删除 `tmp`、退出 `lock_cm`。
- `positions/ModelRobust40Strategy.py`:
  - `_update_state(..., defer_save: bool = False)`：当 `defer_save=False`（默认）时保持同步 `atomic_save(state_path, updated)`；当 `defer_save=True` 时返回 `(updated, DeferredAtomicSave(state_path, updated, lock_cm))`；
  - `prepare(...)`：当 `options.get('server_init') is True and not initializing` 时传 `defer_save=True`，将句柄存入 `strategy._robust40_pending_save`（若创建句柄前发生异常则立即释放 `lock_cm`）。
- `tools/robust40/server.py::finish_live(strategy)`:
  - 在替换 `revision_inputs.pending.pkl`、`validate_snapshot` 与 `complete(..., complete=True)` **最前面**取出 `strategy._robust40_pending_save` 并调用 `.commit()`。
- `tools/robust40/cli/server_robust40.py`:
  - 新增 `warm_numba_kernels() -> None` 与 CLI 模式 `warm-numba`（无需 `--account` / `--stamp`）。

- [ ] **Step 1: 在 `tests/test_consumer_batch2_pipeline.py` 中编写环节 7 与 `warm-numba` 失败测试**

新增测试：
1. `test_stage7_deferred_atomic_save_overlaps_compute_and_commits_before_warmup_complete(tmp_path, monkeypatch)`:
   - 构造已存在的旧 `state.pkl = {'v': 'old'}`；用 `threading.Event` 让后台 `pd.to_pickle` 与主线程模拟计算握手证明真并发重叠；
   - 断言在调用 `pending.commit()` **之前**：`pd.read_pickle(state_path) == {'v': 'old'}` 且 `state.pkl.lock` 仍处于锁定状态；
   - 调用 `finish_live(dummy_strategy)` 后：断言 `pd.read_pickle(state_path)` 已变为新 bundle、`state.pkl.lock` 已释放、`warmup.pkl['complete'] is True` 且目录内零 `.tmp` 残留。
2. `test_stage7_deferred_save_abort_and_write_error_preserve_old_state_and_release_lock(tmp_path, monkeypatch)`:
   - 场景 A（下游 `validate_targets` 抛异常触发 `abort()`）：断言旧 `state.pkl` 未变、`.tmp` 已删、`state.pkl.lock` 已释放（可再次 `lock(state_path)`）；
   - 场景 B（后台 `pd.to_pickle` 抛 `OSError('io error')`）：断言 `finish_live` 在首步 `commit()` 即抛出 `OSError('io error')`，且 `revision_inputs.pending.pkl` 未被替换为正式文件、`warmup.pkl['complete']` 仍为 `False`、旧 `state.pkl` 完好、`state.pkl.lock` 已释放。
3. `test_warm_numba_cli_exits_zero`:
   - 运行子进程 `[sys.executable, 'tools/robust40/cli/server_robust40.py', 'warm-numba']`，断言 `returncode == 0` 且输出包含预热完成提示。

- [ ] **Step 2: 运行环节 7 测试确认 RED**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_consumer_batch2_pipeline.py -k "stage7 or warm_numba" -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: FAIL（`DeferredAtomicSave` 未定义、`warm-numba` 未加入 `choices`）。

- [ ] **Step 3: 实现环节 7 与 `warm-numba` 代码**

1. `tools/local_risk_model.py`：实现 `DeferredAtomicSave`。
2. `positions/ModelRobust40Strategy.py`：在 `prepare()` / `_prepare()` / `_update_state()` 接入实盘续算 `defer_save=True` 路径，异常路径严格保证 `lock_cm.__exit__` 不漏锁。
3. `tools/robust40/server.py::finish_live`：首行执行 `pending_save.commit()`（先落稳 `state.pkl`，再替换 `revision_inputs.pkl`、校验 `validate_snapshot`、最后写 `warmup.pkl(complete=True)`）。
4. `core/real_trading.py::calc_by_account`：外层 `try ... finally:` 检查若 `getattr(me_conf.strategy, '_robust40_pending_save', None)` 仍未消费（说明未能成功走到 `finish_live`），立即 `delattr` 并调用 `.abort()`。
5. `tools/robust40/cli/server_robust40.py`：新增 `warm_numba_kernels()`（用 4 行 × 2 币微型合成数组调用 `LiveRebRobust40([0.01, 0.01], [0.01, 0.01]).calc_lots(...)` 与 `ModelRobust40Strategy.start_simulation(...)` 含 `resume_state` 分支），并在 `argparse` 中支持 `mode == 'warm-numba'`（此时不要求 `--account` 与 `--stamp`）。

- [ ] **Step 4: 运行全套关联回归测试（Phase A + Phase B + 跨仓契约）确认 100% GREEN**

Run:
```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_upgrade_241.py tests/test_consumer_batch2_pipeline.py tests/test_align_fast_equivalence.py tests/test_arrow_pkl_parity.py tests/test_arrow_demand.py tests/test_full_arrow_initialization.py tests/test_precompute_contract.py tests/test_main_chain_parity.py tests/test_p4_optimizations.py tests/test_phase5_optimizations.py tests/test_2b_rolling_s3_and_trim.py tests/test_removed_series_governance.py tests/test_rebuild_handoff.py tests/test_universe_expansion_revision.py tests/test_checkpoint_live.py tests/test_server_initialization.py tests/test_server_snapshot_lifecycle.py tests/test_statistics_snapshot.py tests/test_spot_cost_estimate.py tests/test_upgrade_231.py tests/test_upgrade_233.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
```
Expected: 全部 PASS，0 failed。

- [ ] **Step 5: 提交业务代码并增量更新知识库文档**

1. 先独立提交 Task 6 业务与测试代码（不混入此前工作区已有的 `docs/` 修改）：
```powershell
git -C D:/alphaonline add tools/local_risk_model.py positions/ModelRobust40Strategy.py tools/robust40/server.py core/real_trading.py tools/robust40/cli/server_robust40.py tests/test_consumer_batch2_pipeline.py
git -C D:/alphaonline commit -m "perf(batch2-stage7): overlap MODEL state.pkl save with target aggregation and add warm-numba CLI"
```
2. 在**完整保留工作区原有未提交文档内容**的基础上，增量更新 [docs/DEVELOPMENT.md](../../DEVELOPMENT.md)（顶部新增本次 `v2.4.1` 增量升级 + 消费端第二批环节 5+6+7 离线落地与测试记录）、[docs/README.md](../../README.md)、[docs/kb/upstream-core-relations.md](../../kb/upstream-core-relations.md)、[docs/kb/performance-analysis.md](../../kb/performance-analysis.md)、[docs/kb/branches-and-sync.md](../../kb/branches-and-sync.md)（标注离线落地于 `feat/v241-and-consumer-batch2`，待 Carbon 终审与授权后部署测试机验收；文档改动可按用户习惯保留在工作区或连同 `docs/superpowers/` 规范与计划待 Carbon 验收后统一归档提交）。
