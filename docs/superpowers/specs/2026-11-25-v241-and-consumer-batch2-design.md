# 框架 `v2.4.1` 增量升级与消费端第二批（环节 5+6+7）协同加速设计文档

> **所属体系**：[统一开发入口](../../README.md) · [开发规范与数据契约](../../kb/standards-and-contracts.md) · [与外部版本 `core` 的关系](../../kb/upstream-core-relations.md) · [重要性能分析](../../kb/performance-analysis.md)
> **日期**：`2026-11-25`
> **基准分支与提交**：`D:/alphaonline` `feat/phase5-2b-consumer-hardening`（HEAD `6cd263b`，含本地 `main=2001075` Phase 5 子项目 2A）
> **目标特性分支**：`feat/v241-and-consumer-batch2`（从 `6cd263b` 切出，保留工作区既有未提交 `docs/` 修改）
> **协作分工**：本设计经人工确认为**方案 A（同分支双阶段顺序落地）**；由 `antigravity cli`（`hydrogen` 模型）按配套实施计划执行编码与离线 TDD 测试，完成后交由 `Carbon` 模型执行代码终审、对拍验收与测试机部署。

---

## 1. 意图与背景（Why）

1. **上游第一批加速已就位，消费端交接与尾部 I/O 成为下一明确收益源**：
   生产端 `D:/alpha` 已完成全链路 4 核并行、WAL 直通屏障与计算-I/O 重叠改造并部署测试机 `alphatest`（`0d64e9d`，`20:25 CST` 主轮 `spot_dict.ready = +45.72 s`、`swap_dict.ready = +62.96 s`，`0` 修订直走 `incremental`）。在此基础上，消费端 `D:/alphaonline` 仍存在三段纯交接等待与单核同步落盘开销：
   - **环节 5（跨进程交接层）**：`check_flags` 硬编码额外 `sleep(1)`、所有 `.ready` 齐备后才拉起子进程承受 Python/Numba/Polars 冷导入（`~2 s`），且未利用 `spot` 比 `swap` 早就绪 `~15–17 s` 的窗口提前流式读取 `spot` 修订 Arrow 与 Pivot（预计可省 **`~3–5 s`**）。
   - **环节 6（Step 6 → `MODEL` 交接层）**：Step 6 子进程运行期间主进程空闲等待，随后 `MODEL` 第一步单核阻塞读取 `449 MiB` `model/state.pkl`（`2.81 s`）；同时 `core/equity.py` `save_s` 存在重复 `reindex` 与 4 次全表浮点掩码 `.count(axis=1)`，`process_equities` 串行读 14 份 CSV + 28 份 `ratio_*.pkl` 并同步写 14 份非下游阻塞 `equity_df.pkl`（预计合计可省 **`~5–6 s`**）。
   - **环节 7（`MODEL` 尾段层）**：`MODEL` 续算完成后同步写回 `449 MiB` `model/state.pkl` + `fsync`（`1.63 s`），完全可与后续互不依赖的 `calc_ratios` + `agg_pos_ratio` + `validate_targets`（合计 `~3.0–3.6 s`）并发重叠，并在 `finish_live` 写 `complete=True` 前汇合原子替换（预计可省 **`~1.6–2.0 s`**）。
2. **与官方实盘框架 `v2.4.0` → `v2.4.1` 增量升级文件高度重合，合一规划免除二次返工**：
   本地对照目录 `D:/alphaonline/position-mgmt-trading_v2.4.1`（相对 `2.3.3core`：`requirements.txt` **0 差异**，新增 5 文件、修改 21 文件）引入了多交易所独立数据路径（`exchange_data_paths`）、实盘账户组（`account_group.py` 两阶段 `--stage calc` / `--stage trade --scale k`、相对/绝对划转提醒阈值、独立 `transfer_webhook_url`、6 子图 `group_statistics.py`）、选币去除冗余 `是否交易 == 1` 过滤、现货/合约映射对齐分组修正，以及 BN 子账户划转方向与 OKX TradFi/限价 `51138`/出入金记录修复。其重构的 `startup.py`、`account_exec.py`、`real_trading.py`、`position.py`、`equity.py`、`datatools.py` 正好是环节 5/6/7 的目标文件。采用**同分支先合入 `v2.4.1` 底座（Phase A）、再在其新结构上落地环节 5+6+7（Phase B）**可使每个核心模块只改一次且保持提交边界清晰。

---

## 2. 目标、非目标与反目标

### 2.1 目标（Goals）
- **G1（Phase A · `v2.4.1` 无损窄合入）**：在不覆盖 `core/` 任何既有本地定制的前提下，完整合入 `v2.4.0` + `v2.4.1` 全部功能与问题修复，并为账户组补齐 `MODEL`（`server_init=True`）成员两阶段调度与 `dry_run` 全链路防写保护；保证现有单账户 `MODEL` 线上快照 **100% 兼容、零重建、产出逐位零差**。
- **G2（Phase B · 环节 5 跨进程预热与 `spot` 先到先读）**：在 `run_time - 10 s`（`:X4:50`，与 `initialization_config.py` 中 `RESOURCE_POLICY['yield_preempt_seconds'] = 10` 抢占让路窗精确对齐，且早于上游 `:X5:00` 4 核计算峰值）提前拉起 `MODEL` 交易子进程完成模块导入、快照检查与 `revision_inputs.pkl` 预读（等待 `.ready` 期间释放 `account_lock` 不阻塞后台初始化安装，`swap` 就绪重新持锁后复核 `warmup.pkl` 签名）；消除 `check_flags` 冗余 `sleep(1)`；当 `spot_dict` + `market_pivot_spot` 先 `.ready` 时立即流式读取 `spot` 侧 Arrow 与 Pivot，`swap` `.ready` 到达即刻收尾进入比对。
- **G3（Phase B · 环节 6 `state.pkl` 隐藏预读 + Step 6/`process_equities` 路径瘦身）**：在 Step 6 `ProcessPoolExecutor` 完成 `fork` 后由主进程单后台线程预读 `model/state.pkl`（带 `(mtime_ns, size)` 签名并在 `lock(state)` 下校验消费）；消除 `core/equity.py` 第 3 次冗余 `reindex` 与 4 次全表浮点掩码 `.count(axis=1)`；`process_equities` 多线程并行读取 14 份 CSV 与 28 份 `ratio_*.pkl`；单账户 `calc_by_account → trade_by_account` 内存直传 `(df_spot_ratio, df_swap_ratio)`，并将主进程后续交易不读的落盘（`equity_df.pkl`、`仓位比例*.csv`、单账户 `runtime_folder/df_*_ratio.pkl`）交由单后台写盘器异步重叠执行（账户返回前汇合）。
- **G4（Phase B · 环节 7 `state.pkl` 异步落盘重叠 + Numba 预热工具）**：`MODEL` 续算完立即在持锁后台线程写出 `state.pkl.<tmp>` + `fsync`，主线程并发执行 `calc_ratios` + `agg_pos_ratio` + `validate_targets`，在 `finish_live()` 写 `warmup.pkl(complete=True)` **之前** `join + os.replace`；同时在 `server_robust40.py` 提供离线 `warm-numba` 子命令消除修改 `ModelRobust40Strategy.py` 后的首轮 `+29 s` JIT 重编译。

### 2.2 非目标（Non-Goals）
- **不改动生产端 `D:/alpha`**，不改变 7 条跨项目数据契约。
- **不修改任何因子、截面、选币排序、子策略模拟器或 `MODEL` 仓管数学公式**；不改变截面因子（`VolumeMaxBias`、`固定回撤`）内部自身的 `是否交易 == 1` 逻辑（`v2.4.1` 仅移除 `core/select_coin.py` 选币入口的 `pl.col('是否交易') == 1`）。
- **不绕过子策略 `资金曲线*.csv` 文本往返**（保留 `check_exact=True` 严格逐位一致，避免 `4.44e-16` 浮点尾差）。
- **不改动本地单账户 `statistics.py` 的 `statistics_snapshot` 单源净值架构与底图双列图例方案**（本地 `statistics.py` 已通过底部独立双列 `fig.legend` 解决 14 子策略图例溢出，优于上游 `ax6` 内嵌图例；仅合入账户组路径 `init_system(str(profile))` 支持与新增 `group_statistics.py`、`statis_func.net_fund`）。
- **不默认开启 `runtime_data` 实盘归档**（`lookback_save_interval_hours` 默认强制为 `0`），不启用根目录独立常驻 `delist.py` / `monitor.py` 服务（仅同步其 `init_system(str(...))` 调用以保代码库一致）。

### 2.3 反目标（Anti-Goals）
- 严禁整目录复制 `position-mgmt-trading_v2.4.1/core/` 覆盖本地文件。
- 严禁在 Step 6 `ProcessPoolExecutor` 执行 `fork` **之前**启动后台线程或加载 `449 MiB` `model/state.pkl`（防止 Linux 多线程 `fork` 死锁及 4 个子进程 CoW 页膨胀）。
- 严禁让 Step 6 子进程延后退出跨入 `MODEL` 阶段（防止子进程残留内存挤占 4c8g 整机 `MODEL` 峰值 `2.4 GiB` 余量）。
- 严禁在 `state.pkl` 完成 `fsync + os.replace` **之前**将 `warmup.pkl` 标记为 `complete=True` 或发起任何交易所撤单/下单操作。
- 严禁让 `:X4:50` 预热等待时间计入 `MODEL_REPLAY` 的 `total_s`（等待耗时须单独记录 `wait_ready_s` / `prewarm_s` / `spot_early_lead_s`，保持 `total_s` 与历史口径严格可比）。

---

## 3. 备选方案与已选架构（Approaches）

在头脑风暴阶段对比了三种方案并由人工拍板选定 **方案 A**：

1. **方案 A（已选定）：同分支双阶段顺序落地 —— Phase A 窄合入 `v2.4.1` 底座 → Phase B 落地零差版环节 5+6+7**
   - **优势**：重叠文件（`startup.py`、`account_exec.py`、`real_trading.py`、`position.py`、`equity.py`、`datatools.py`、`server.py`）只重构一次；`calc_by_account` / `trade_by_account` 与 `calc_target_position` 的内存参数接口天然助力环节 6/7 免除冗余 `df_*_ratio.pkl` 重读；Phase A 与 Phase B 按原子 commit 分层，可独立验收与回退。
2. **方案 B（已弃用）：拆为两套独立分支（先在 `2.3.3` 做环节 5+6+7，日后再升 `v2.4.1`）**
   - **弃用原因**：5 个主干执行文件必然经历二次拆改与大面积合并冲突。
3. **方案 C（已弃用）：方案 A + 激进环节 6（Step 6 子进程内 `account_state*/lots_state*` 延后至 `MODEL` 期间刷盘）**
   - **弃用原因**：仅多省 `~0.6–1.0 s`，却会让子进程 CoW 内存与 `MODEL` 峰值叠加或增加跨进程 IPC 与父进程 GIL 争抢，风险收益比不佳。

---

## 4. 全局硬约束（Constraints）

1. **唯一 Python 与测试命令**：
   - 解释器：`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（严禁使用 `conda run` 或其他环境；`requirements.txt` 零变更，无需装包）。
   - Windows pytest 统一命令模板：
     ```powershell
     & 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest <test_paths> -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
     ```
2. **工作区与分支纪律**：
   - 当前 `D:/alphaonline` 在 `feat/phase5-2b-consumer-hardening`（`6cd263b`）上含有用户未提交的 `docs/*` 修改与未跟踪参考目录（`2.3.3core/`、`position-mgmt-trading_v2.4.1/` 等）。
   - 执行器创建分支时直接运行 `git checkout -b feat/v241-and-consumer-batch2`（保留未提交 `docs/*` 原样不动）；每个 Task 提交时**只允许 `git add <本Task明确的源码与测试文件>`**，严禁执行 `git add -A`、`git add .`、`git reset --hard` 或 `git checkout -- docs/`，也严禁把 `position-mgmt-trading_v2.4.1/`、`2.3.3core/` 提交入库（在 `.gitignore` 中补充忽略 `2.3.3core/` 与 `position-mgmt-trading_v*/`）。
3. **离线安全红线**：
   - 全程离线开发与测试（单测中禁止真实外网连接）；严禁启动 `startup.py` 实盘循环、严禁连接测试机/正式机重启服务或下单、严禁泄露任何账户密钥或 Webhook。
   - 保持被修改文件原有的行尾格式（CRLF/LF 不整文件翻转）与顶部版权注释头。

---

## 5. 详细架构设计

### 5.1 Phase A：`v2.4.1` 增量窄合入设计

#### A1. 配置、多交易所数据路径与数据/选币修正
1. **[.gitignore](../../.gitignore) & [core/version.py](../../core/version.py)**：
   - `.gitignore` 增加 `2.3.3core/` 与 `position-mgmt-trading_v*/` 防止对照包误入库。
   - `core/version.py` 更新 `sys_version = '2.4.1'`、`build_version = f'v{sys_version}.20261120'`。
2. **[config.py](../../config.py)**：
   - 顶部新增 `exchange_data_paths = {}` 与 `lookback_save_interval_hours = 0`（**强制默认 `0` 关闭归档**）。
   - `X3S_TRADING_ACCOUNT` 查找逻辑：当 `accounts/<trading_account>` 不存在且 `len(Path(trading_account).parts) == 1` 时，在 `accounts/` 下非 `_` 开头的账户组子目录中查找唯一同名文件（保留 `account_credentials` 本地凭据注入逻辑不变）。
   - `config.json` 的 `allowed_attrs` 扩充 `'exchange_data_paths'` 与 `'lookback_save_interval_hours'`，**完整保留**本地已有的 `'proxy'` 与 `'shared_factor_pipeline'`。
   - 在 `config.json` 覆盖之后解析账户级交易所与数据路径：
     ```python
     exchange_name = str(account_config.get('exchange_name', exchange_name)).lower()
     realtime_data_path = exchange_data_paths.get(exchange_name, realtime_data_path)
     lookback_save_interval_hours = lookback_save_interval_hours or 0
     ```
3. **[core/utils/datatools.py](../../core/utils/datatools.py)**：
   - 新增辅助函数 `resolve_bmac_data_path(exchange: str | None = None) -> Path`：从 `config` 读取 `bmac_data_path` 与 `getattr(config, 'exchange_data_paths', {})`；若 `exchange` 在 `exchange_data_paths` 中，返回 `Path(exchange_data_paths[exchange]) / bmac_data_path.name`，否则返回 `bmac_data_path`。
   - `check_bmac_update_flag(run_time, exchanges=None)`：按 `{resolve_bmac_data_path(ex) for ex in (exchanges or [])} or {bmac_data_path}` 构造所有需检查的 `spot_dict_{ts}.ready` 与 `swap_dict_{ts}.ready`。
   - **映射对齐修正（双路径同步）**：
     - `align_spot_swap_mapping`：`df.loc[df['symbol_swap'].eq(''), 'group'] = np.nan` 改为 `df.loc[df[column_name].eq(''), 'group'] = np.nan`。
     - `align_spot_swap_mapping_fast`：去掉对 `df['symbol_swap']` 的读取，将 `valid = swap != ''` 改为 `valid = col_ne`（即直接在 `col_ne = col != ''` 上计算每段连续非空区间的组内 `cumcount`），与更新后的 `align_spot_swap_mapping` 保持 100% 逐行逐列等价。
4. **[core/select_coin.py](../../core/select_coin.py)**：
   - `_selection_input_filters(strategy)`（约 L942）：`filters = [pl.col('是否交易') == 1]` 改为 `filters = []`。**保留** `KLINE_COLS` 中的 `'是否交易'` 列供截面因子使用。
5. **交易所客户端修复**：
   - [core/binance/standard_client.py](../../core/binance/standard_client.py) & [core/binance/portfolio_margin_client.py](../../core/binance/portfolio_margin_client.py)：合入 `fetch_transfer_history` 子账户转出改看 `fromAccountType` 修复；**严禁覆盖** `portfolio_margin_client.py` 本地定制（`update_balance` `recvWindow=30000`、`get_swap_usdt_balance`、`get_account_info`、`get_unimmr`、`get_statistics_overview`）。
   - [core/okx/base_client.py](../../core/okx/base_client.py) & [core/okx/portfolio_margin_client.py](../../core/okx/portfolio_margin_client.py)：合入 `v2.4.1` 全部 OKX 改动（`spot_inst_type = 'MARGIN'`、`_is_blocked_tradfi`、`0.97` 最小下单额折算、`acctLv='3'` 强校验、`0.3s` 仓位档位限频、订单 `tag`、`51138` 及 `0.999/1.001` 最新限价重试、`54000` 跳过、`set_fee_burn`、`fetch_transfer_history` 10 天 `bills` + `bills_archive` 去重汇总）。

#### A2. 账户单元、两阶段执行（`calc`/`trade`）、账户组与统计扩展
1. **[core/account_manager.py](../../core/account_manager.py)**：
   - 合入 `load_multi_accounts()`（过滤 `_` 开头文件与 `_` 开头组目录，全局重名抛 `ValueError`）、`load_account_units()`、`read_config_value()`、`read_hour_offset_from_config()`、`read_exchange_from_config()`、`run_account_exec(account_path, run_time, *args)`。
2. **[core/equity.py](../../core/equity.py) & [core/position.py](../../core/position.py)**：
   - `calc_equity` 增加默认参数 `ratio_folder: Path = runtime_folder`（保留关键字参数 `*, ratio_inputs=None, market_inputs=None, ratio_outputs=None`），当 `account_name` 非空且 `ratio_inputs is None` 时从 `ratio_folder` 读取 `df_{spot,swap}_ratio.pkl`。
   - `calc_target_position(account_config: AccountConfig, df_spot_ratio: pd.DataFrame | None = None, df_swap_ratio: pd.DataFrame | None = None, is_debug=False, scale: float = 1.0)`：
     - **向后兼容**：当 `df_spot_ratio is None` / `df_swap_ratio is None` 时自动从 `runtime_folder` 读取对应 `.pkl`，保证所有旧单参调用点与单测零破坏；传入 DataFrame 时直接取 `.iloc[-1]` 免读盘。
     - 合入 `equity_leveraged = all_equity * leverage * scale`，以及 `if scale > 1 and target_spot_ratio.any(): target_spot_ratio = target_spot_ratio / scale`（现货不加杠杆保护）。
3. **[core/real_trading.py](../../core/real_trading.py) & [core/account_exec.py](../../core/account_exec.py)**：
   - 拆分为 `calc_by_account(acct_conf, me_conf, run_time) -> bool` 与 `trade_by_account(acct_conf, me_conf, run_time, ratio_folder: Path = runtime_folder, scale: float = 1.0, *, preloaded_ratios: tuple[pd.DataFrame, pd.DataFrame] | None = None)`，单账户 `run_by_account` 顺序调用二者。
   - **归档红线**：`is_archive_round(run_time)` 仅当 `lookback_save_interval_hours > 0` 且整除时为 `True`；**仅当 `lookback_save_interval_hours > 0 and not should_save_archive` 时**才将 `conf.lookback_days` 置 `0`（当默认 `lookback_save_interval_hours == 0` 时绝不篡改账户自身的 `lookback_days`，且不执行 `save_runtime_data_archive`）。
   - **`dry_run` 防写全覆盖**：
     - `account_exec.py::prepare_account`：`is_simulate != 'dry_run'` 时才调 `reset_max_leverage()`，`update_account_info(is_operate=is_simulate != 'dry_run')`。
     - `trade_by_account`：`is_debug or is_simulate == 'dry_run'` 时跳过撤单；`is_simulate == 'dry_run'` 时跳过真实下单、`save_order_history`、`collect_asset`、`adjust_base_margin`，仍正常写出 `save_position_results`、`save_final_select_results(..., max_file_limit=24 * 3)`、`save_symbol_order`。
   - **`RebRobust40` 下单轮注册**：在 `account_exec.py::run_trade_stage` 中，若 `account_config.rebalance_mode` 模式名为 `'RebRobust40'`，调用 `positions.ModelRobust40Strategy.register_execution()`，确保账户组 `--stage trade` 独立子进程可直接构造 `LiveRebRobust40` 执行器。
4. **[core/account_group.py](../../core/account_group.py)、[accounts/_demo_group/](../../accounts/_demo_group/)、[tools/robust40/server.py](../../tools/robust40/server.py)、[tools/robust40/cli/server_robust40.py](../../tools/robust40/cli/server_robust40.py) & [startup.py](../../startup.py)**：
   - 新增 `core/account_group.py` 与 `accounts/_demo_group/{_manage.py, demo_a.py, demo_b.py}`（源自 `v2.4.1`）。
   - **让账户组原生兼容 `MODEL`（`server_init=True`）成员**：
     - `server_robust40.py` 的 `trade` 模式支持透传可选 `--stage {all,calc,trade}` 与 `--scale <float>` 给 `core.account_exec.main()`：
       - 当 `--stage` 为 `all`（默认）或 `calc` 时：执行完整修订预检、`replay` 事务、`finish_live` 状态提交与 `account_exec.main()`（`calc` 阶段额外写出 `组交接/` 权益与权重，不撤单不下单）。
       - 当 `--stage trade` 时：**不再重复跑修订预检或修改 `warmup.pkl`/快照状态**（因为同轮 `--stage calc` 已完成计算并提交快照），仅加 `account_lock`、注册 `RebRobust40` 并转发至 `account_exec.main()` 执行 `run_trade_stage(..., scale=scale)`。
     - `Coordinator.command(account, run_time, market, *extra_args)`：对非 `MODEL` 账户返回 `[sys.executable, account_exec.py, stamp, *extra_args], env`；对 `MODEL` 账户当 `'--stage'` 为 `'trade'` 时免重复 `.ready` 检查直接返回 `server_robust40.py trade ... --stage trade --scale ...`，否则走标准 `.ready`/快照检查并返回带 `*extra_args` 的 `server_robust40.py trade` 命令。
     - `account_group.py::run_group(group, run_time, *, dispatcher=None)`：当传入 `dispatcher`（由 `startup.py` 提供 `server_initializer` 包装回调，未传时默认回退 `run_account_exec`）时，组内 `MODEL` 与传统成员统一走 `dispatcher(path, run_time, '--stage', 'calc')` 及 `dispatcher(path, run_time, '--stage', 'trade', '--scale', repr(scale))`；若 `MODEL` 成员在 `calc` 阶段退出码为 `75`（触发后台重建），未写出本轮 `handoff.json`，`load_handoff_equity` 返回 `None`，自动命中组级熔断「整组本轮不下单并告警」，安全闭环！
   - `startup.py`：
     - 保留本地 `StatisticsTasks(statistics_config['max_workers'], logger.warning)` 有界异步统计池（不用上游无界 `ThreadPoolExecutor`）、保留 `clean_startup` / `warmup_plans` 与 `check_accounts` 的 `dry_run` 保护。
     - `server_initializer.prepare(...)` 按各账户所属交易所解析对应的 `resolve_bmac_data_path(exchange)`。
     - `run_statistics_async(run_time, random_time, account_path: Path)` 支持单账户与账户组（逐成员跑 `statistics.py`，组模式非 debug 再跑 `group_statistics.py`；提交键用 `account_path.stem` 或组目录名）。
5. **统计模块增量合入**：
   - [core/utils/statis_func.py](../../core/utils/statis_func.py)：新增 `net_fund(df)` 函数（供 `group_statistics.py` 与 `statistics_debug.py` 复用；保留本地现货移动成本等全部定制）。
   - 新增 [core/utils/group_statistics.py](../../core/utils/group_statistics.py)（源自 `v2.4.1`，注意兼容本地 `equity.csv` 的扩展列，按列名投影读取不报错）。
   - [core/utils/statistics.py](../../core/utils/statistics.py)、[core/utils/statistics_debug.py](../../core/utils/statistics_debug.py)、[delist.py](../../delist.py)、[monitor.py](../../monitor.py)：将 `init_system(profile.stem)` 更新为 `init_system(str(profile))` 以支持账户组子目录成员配置文件加载；`statistics.py` 其余单源快照与底图双列图例逻辑保持不变。

---

### 5.2 Phase B：消费端第二批（环节 5 + 6 + 7）协同加速设计

#### B1. 环节 5：跨进程 Worker 预热（`run_time - 10 s`）+ 去 `sleep(1)` + `spot` 先到先读

1. **消除 `check_flags` 冗余等待（[core/utils/datatools.py](../../core/utils/datatools.py)）**：
   - 上游生产端严格遵守「`.tmp + os.replace` 原子落盘完成后最后写 `.ready`」契约。将 `check_flags(run_time, flag_paths, poll_interval=0.1)` 中检测到全部 `flag_path.exists()` 后的 `time.sleep(1)` 删除，轮询间隔从 `1.0 s` 缩短至 `0.1 s`（非 `MODEL` 路径立即净省 `~1.5 s`）。
2. **`run_time - 10 s` 零争用预热调度（[startup.py](../../startup.py) + [tools/robust40/server.py](../../tools/robust40/server.py)）**：
   - 常量：`PREWARM_LEAD_SECONDS = 10.0`（在 `tools/robust40/initialization_config.py` 中定义，与 `RESOURCE_POLICY['yield_preempt_seconds'] = 10` 完全对齐）。
   - 时序：在 `startup.py::run_loop()` 解析出 `due_units` 非空后（此时因 `cheat_seconds=60..120` 处于 `run_time - 120s ~ -60s`）：
     - 若本轮首个待执行单元为单账户 `MODEL`（覆盖测试机 `25m` 单户与正式机 `5m/15m/25m/35m/45m/55m` 错峰 6 户 100% 常规场景！）：
       1. 主循环先睡眠至 `max(0.0, run_time.timestamp() - PREWARM_LEAD_SECONDS - time.time())`（即 `:X4:50`，恰在 `yield_preempt_seconds=10` 让路窗内，且早于 `:X5:00` 上游启动 `10 s`，足够容纳 `~2.2 s` 模块导入，整机 4 核 `0%` CPU）。
       2. 调用 `Coordinator.launch_prewarmed_trade(account, run_time, market)`：
          - 在 `:X4:50` 立即执行不依赖新行情的前置检查：`_reap()`、不在 `pending/jobs` 中、`recover_interrupted(destination)`、`inspect(destination, ..., stamp - 3600)` == `'ready'`、`check_dependencies` + `resume_capacity` 检查、以及 `release_demand`（若启用）。
          - 若前置状态就是 `'rebuild'`，则返回 `None` 回退到标准路径（等齐本轮 `.ready` 后登记 `request_rebuild`，不拉起无谓交易进程）；若状态为 `'ready'`，则在 `:X4:50` **立即通过 `subprocess.Popen` 拉起** `server_robust40.py trade --account <path> --stamp <stamp> --wait-ready`！
       3. 主进程 `process.wait()` 等待该预热子进程退出并交由 `Coordinator.handle_result` 处理退出码（`0` 正常提交统计、`75` 登记重建、`76` 等待 `.ready` 超时静默跳过本轮不抛异常、其他非零抛 `CalledProcessError`）。
     - 若本轮包含多个单元或传统账户/账户组：对首个单户 `MODEL` 走上述预热路径，其余单元继续走去除了 `sleep(1)` 的快速 `check_bmac_update_flag` + `Coordinator.command` 路径。
3. **子进程内无锁预导入 + 释放锁等 `.ready` + `spot` 先到先读流水线（[tools/robust40/cli/server_robust40.py](../../tools/robust40/cli/server_robust40.py) + [tools/robust40/handoff.py](../../tools/robust40/handoff.py) + [tools/robust40/telemetry.py](../../tools/robust40/telemetry.py)）**：
   - 当 `--wait-ready` 开启时，`server_robust40.py` 在 `:X4:50 ~ :X4:53` 空闲窗口内**不持 `account_lock`** 提前完成 `from core.account_exec import main`（连带完成 `pandas` / `numpy` / `pyarrow` / `polars` / `numba` / `ModelRobust40Strategy` 全量模块导入），随后仅短暂持 `server.account_lock(destination)` 读取 `(warmup_sig, original, old_inputs)`（`warmup_sig = (st_mtime_ns, st_size)`，`old_inputs = pd.read_pickle(destination / 'model/revision_inputs.pkl')`）并**立即释放 `account_lock`**——确保 `:X4:50 → swap.ready` 等待期间绝不阻塞后台初始化进程的 `install()`！
   - 随后进入 `handoff.wait_and_read_inputs_streaming(...)`（轮询间隔 `0.05 s`，超时截止 `stamp + DATA_READY_WAIT_SECONDS`）：
     1. **阶段 1（等待 `spot` 双 `.ready`）**：一旦 `spot_dict_{stamp}.ready` 与 `market_pivot_spot_{stamp}.ready` 同时就绪（不等 `swap`！），立即在后台线程池（2 线程）启动只读上游 `bmac_data_path` 的工作：
        - 校验 `spot` Arrow `required` 预计算列完备性 + 读取 `spot_trim = read_arrow_history_trim_date(spot_path)` + 执行 `read_arrow_tail(bmac_data_path, 'spot', run, end, start, indexed=True)`；
        - 执行单市场 `read_pivot_inputs(bmac_data_path, minute, end, start, kinds=('spot',))`。
     2. **阶段 2（等待 `swap` 双 `.ready` 并重新获取 `account_lock`）**：一旦 `swap_dict_{stamp}.ready` 与 `market_pivot_swap_{stamp}.ready` 同时就绪：
        - **此时刻记为消费端正式起算点 `ready_at = perf_counter()`**，调用 `telemetry.reset_timer(prewarm_s=..., wait_ready_s=..., spot_early_lead_s=...)` 重置 `_start`，保证 `total_s` 严格只计全部数据就绪后的真实消费耗时！
        - 进入 `with server.account_lock(destination):` 持有账户锁直至本轮结束；复核 `warmup.pkl` 的 `(st_mtime_ns, st_size) == warmup_sig`（若等待期间恰好有后台初始化新装入快照导致签名变化，重新执行 `inspect` + 同步 `handoff.prepare` 兜底，零竞态风险）。
        - 校验 `swap` Arrow `required` 预计算列完备性（若 `spot` 或 `swap` 缺列/损坏，返回 `REBUILD_REQUIRED_EXIT = 75`，与原 `Coordinator.command` 行为 100% 一致！）+ 校验 `spot_trim == swap_trim` 及 `check_upstream_history_trim_date`（若推进则 `server.begin` 并返回 `75`）。
        - 立即并发读取剩余的 `swap` Arrow（`read_arrow_tail(..., 'swap', ...)`）与 `swap` Pivot（`read_pivot_inputs(..., kinds=('swap',))`），并与已在后台跑完（或接近跑完）的 `spot` 任务汇合，主线程汇总 `revision_arrow_spot_s` / `revision_arrow_swap_s` / `revision_pivot_s` 遥测后直接进入 `first_revision(old_inputs, current)`！
     3. **超时保护**：若到达 `stamp + DATA_READY_WAIT_SECONDS` 仍未收齐 4 个 `.ready`，等待并回收已启动线程，不触碰 `warmup.pkl` 或任何快照文件，记录 `status='waiting_data_timeout'` 并返回 `WAIT_FOR_DATA_EXIT = 76`。

#### B2. 环节 6：`model/state.pkl` 隐藏预读 + `equity.py` 保存去重 + `process_equities` 并行读与非阻塞写后台化

1. **Step 6 期间后台隐藏预读 `model/state.pkl`（[tools/local_risk_model.py](../../tools/local_risk_model.py) + [core/backtest.py](../../core/backtest.py) + [core/real_trading.py](../../core/real_trading.py) + [positions/ModelRobust40Strategy.py](../../positions/ModelRobust40Strategy.py)）**：
   - 在 `tools/local_risk_model.py` 新增单槽位预读器：
     - `start_model_state_preload(path: Path) -> None`：若 `path.is_file()`，启动单后台守护线程，先取 `sig0 = (st.st_mtime_ns, st.st_size)`，调用 `pd.read_pickle(path)`，完成后再取 `sig1 = (st.st_mtime_ns, st.st_size)`；仅当 `sig0 == sig1` 时缓存 `(path.resolve(), sig1, bundle)`（任何异常静默置 `None`，留给正式路径正常读取报错）。
     - `consume_preloaded_model_state(path: Path) -> dict | None`：若存在匹配 `path.resolve()` 的预读线程，先 `thread.join()` 等待其收尾（避免差几毫秒未读完时发生双倍重读），清空全局槽位（立即释放引用！），在调用方已持有的 `lock(state)` 内校验当前文件 `(st_mtime_ns, st_size) == saved_sig`，一致则返回 `bundle`，否则返回 `None`。
     - `clear_preloaded_model_state() -> None`：`join`（带短超时）并清空槽位。
   - **精确触发点（防 `fork` 污染）**：
     - `core/real_trading.py::calc_by_account` 在进入 `with step6_market_handoff(model_state_preload_path=...):` 时登记待预读的 `state_path`（仅当 `enable_live_hooks` 且 `not initializing`）。
     - 在 `core/backtest.py::run_backtest_multi` 的 Step 6 中，**严格在 `with ProcessPoolExecutor(max_workers=pool_workers) as executor:` 已经提交完所有 `_step6_worker` 任务之后**（此时 `fork` 子进程已全部派生完毕！），调用 `start_model_state_preload(preload_path)`，使 `449 MiB` unpickle 完全落在主进程等待 `as_completed(futures)` 的 `~5 s` 空窗内！
     - `step6_market_handoff()` 的 `finally:` 块调用 `clear_preloaded_model_state()` 兜底防泄漏。
     - `ModelRobust40Strategy.prepare()` 在 `with lock(state):` 内优先调用 `consume_preloaded_model_state(target)`，未命中则回退 `pd.read_pickle(target)`，命中时记录日志 `MODEL仓管：命中Step 6后台预加载状态`。
2. **`core/equity.py` `save_s` 零差去重加速（[core/equity.py](../../core/equity.py)）**：
   - `create_ratio_snapshot`：删除 `pd.concat` 后第 3 次多余的 `ratio_df = ratio_df.reindex(columns=final_columns, fill_value=0)`（前两步已对 `ratio_df_0` 与 `ratio_df_1` 分别按 `final_columns` `reindex`，拼接结果列顺序已严格等于 `final_columns`）。
   - `calc_equity` 多空选币计数：将 4 次 `df_spot_ratio[df_spot_ratio > 0].count(axis=1)` 全表浮点掩码 DataFrame 构造替换为直接基于底层二维数组的 NumPy 计数：
     ```python
     spot_arr = df_spot_ratio.to_numpy(copy=False)
     swap_arr = df_swap_ratio.to_numpy(copy=False)
     with np.errstate(invalid='ignore'):
         long_cnt = (spot_arr > 0).sum(axis=1, dtype=np.int64) + (swap_arr > 0).sum(axis=1, dtype=np.int64)
         short_cnt = (spot_arr < 0).sum(axis=1, dtype=np.int64) + (swap_arr < 0).sum(axis=1, dtype=np.int64)
     counts = {
         'symbol_long_num': pd.Series(long_cnt, index=df_spot_ratio.index),
         'symbol_short_num': pd.Series(short_cnt, index=df_spot_ratio.index),
     }
     ```
     （IEEE-754 下 `NaN > 0` 与 `NaN < 0` 均为 `False`，与 pandas `.count()` 忽略 `NaN` 的计数在任意含 `NaN`/空列/全零输入下 **100% 精确相等**。）
3. **`process_equities` 多线程并行读盘 + 主进程非阻塞产物异步写盘（[core/model/backtest_config.py](../../core/model/backtest_config.py) + [core/real_trading.py](../../core/real_trading.py)）**：
   - 在 `core/utils/misc_kit.py`（或新增轻量 `core/utils/async_disk.py`）提供作用域绑定的单线程后台写盘器 `AsyncDiskWriter`：
     - 支持 `submit(fn, *args, **kwargs)`、`flush()`（等待队列排空并重抛首个写盘异常）、以及 `with AsyncDiskWriter() as writer:` 上下文管理。
   - **`process_equities` 并行读与异步写**：
     - 当 `inputs is None` 且 `len(configs) > 1` 时，用 `ThreadPoolExecutor(max_workers=min(4, len(configs)))` 并行执行每个子策略 `conf` 的「读 `资金曲线*.csv` → `transfer_equity_period` → 计算仓管策略因子列 → 读 `ratio_spot.pkl` 与 `ratio_swap.pkl`」，主线程按 `configs` 原顺序收集 `equity_dfs`、`ratio_dfs` 并计算全局 `start_time` / `end_time`（保证属性与异常顺序 100% 不变）。
     - 对 14 个 `equity_df.to_pickle(.../'equity_df.pkl')`：若传入了 `async_writer`（实盘 `calc_by_account` 传入，离线单测未传时保持原同步写），提交给 `async_writer` 后台落盘（传入裁切前独立 `equity_df` 引用，后续 `equity_dfs[idx] = df[...]` 为切片新对象不原地改写）。
   - **`calc_ratios` 与 `real_trading` 非阻塞写盘重叠 + 单账户 `ratio` 内存直传**：
     - `calc_ratios` 中的 `仓位比例-原始.csv` 与 `仓位比例.csv`：当 `async_writer` 存在时，对快照副本（`df_ratio.copy(deep=False)` 在 `smooth_ratios` 新表生成前引用安全，为稳妥对 `原始` 传 `.copy()`）提交至 `async_writer` 后台写 CSV。
     - 单账户 `run_by_account`（`stage == 'all'`）：`calc_by_account` 将算好的 `(df_spot_ratio, df_swap_ratio)` 缓存在 `me_conf._live_target_ratios` 直接内存交给 `trade_by_account(..., preloaded_ratios=...)`（省去 `trade_by_account` 的 2 次大表 `pd.read_pickle`），同时将 `runtime_folder/df_{spot,swap}_ratio.pkl` 与 `spot/swap_ratio_*.csv` 提交至同一个 `async_writer`，使这批落盘与 `trade_by_account` 的交易所拉资产/算手数/下单**全程并发重叠**，并在 `run_by_account` 退出前 `writer.flush()`！（注：账户组 `--stage calc` 无同进程 `trade_by_account`，在 `calc_by_account` 结束前立即 `writer.flush()`，确保 `save_handoff` 100% 读到已落盘的 `df_*_ratio.pkl`。）

#### B3. 环节 7：`MODEL` `state.pkl` 异步写盘重叠 + 崩溃恢复事务闭环 + Numba 预热

1. **延迟原子提交句柄 `DeferredAtomicSave`（[tools/local_risk_model.py](../../tools/local_risk_model.py)）**：
   - 新增类 `DeferredAtomicSave(path: Path, bundle: Any, lock_cm)`：
     - 构造并启动单后台线程：在 `path.parent` 下创建唯一临时文件 `<name>.<rand>.tmp`，执行 `pd.to_pickle(bundle, tmp)` + `os.fsync(fd)`（**不在后台线程执行 `os.replace`**！）。
     - `commit()`（幂等）：`join()` 后台写盘线程；若写盘异常，删除 `tmp`、释放 `lock_cm` 并原样抛出异常（此时 `state.pkl`、`revision_inputs.pkl`、`warmup.pkl` 均未改动！）；若写盘成功，在当前主线程执行 `os.replace(tmp, path)`，随后释放 `lock_cm`。
     - `abort()`（幂等，不抛异常）：`join()` 后台写盘线程，删除残留 `tmp`，释放 `lock_cm`。
2. **`MODEL` 接入与生命周期兜底（[positions/ModelRobust40Strategy.py](../../positions/ModelRobust40Strategy.py) + [tools/robust40/server.py](../../tools/robust40/server.py) + [core/real_trading.py](../../core/real_trading.py)）**：
   - 仅当 `prepare()` 处于实盘续算模式（`options.get('server_init') is True and not initializing`）时启用延迟提交；离线脚本与直接调用 `update_state()` / `initialize_state()` 的全部单元测试保持同步 `atomic_save` 100% 不变。
   - 在 `ModelRobust40Strategy.prepare()` 中：
     - 手动进入 `lock_cm = lock(state); lock_cm.__enter__()`；若 `_prepare` 或 `_update_state(..., defer_save=True)` 在构造 `DeferredAtomicSave` 之前抛错，立即 `lock_cm.__exit__(...)` 释放锁。
     - `_update_state` 完成 `advance(...)` 后启动 `pending = DeferredAtomicSave(state_path, updated, lock_cm)`，挂在 `strategy._robust40_pending_save = pending` 后立即返回主线程！
   - 主线程无等待继续执行：`step6_market_handoff` 退出 → `me_conf.calc_ratios()`（`~0.93 s`）→ `me_conf.agg_pos_ratio()`（`~2.01 s`）→ `validate_targets()`（`~0.7 s`）。
   - 在 `ModelRobust40Strategy.transform_targets` 调用的 `tools/robust40/server.py::finish_live(strategy)` **最开头**（在 `pending.replace('revision_inputs.pkl')`、`validate_snapshot` 与 `complete(..., complete=True)` **之前**！）：
     ```python
     pending_save = getattr(strategy, '_robust40_pending_save', None)
     if pending_save is not None:
         delattr(strategy, '_robust40_pending_save')
         pending_save.commit()
     ```
   - **异常安全兜底**：在 `core/real_trading.py::calc_by_account` 的 `try ... finally:` 中检查 `getattr(me_conf.strategy, '_robust40_pending_save', None)`——若因 `calc_ratios` / `agg_pos_ratio` / `validate_targets` 任一环节抛错未能走到 `finish_live()`，在 `finally` 中立即调用 `pending_save.abort()` 清理 `.tmp` 并释放 `.lock`，确保旧 `state.pkl` 与快照事务 100% 无损回退！
3. **Numba 离线预热子命令（[tools/robust40/cli/server_robust40.py](../../tools/robust40/cli/server_robust40.py) + [tools/run_deployment_tests.py](../../tools/run_deployment_tests.py)）**：
   - `server_robust40.py` `mode` 新增 `'warm-numba'`（`--account` 与 `--stamp` 在该模式下设为非必填）：用 4 行 × 2 币极小合成 `float64`/`int64` 数组调用 `positions.ModelRobust40Strategy` 的 `LiveRebRobust40.calc_lots` 与 `start_simulation`（含 `resume_state` 分支），在 `< 5 s` 内把全部 `@nb.njit(cache=True)` / `@jitclass` 编译落盘至 `__pycache__`，零网络、零行情依赖。
   - 在发布测试/生产同步代码后执行一次 `python tools/robust40/cli/server_robust40.py warm-numba`，彻底消除实盘首轮 `+29 s` JIT 编译毛刺。

---

## 6. 验证矩阵与留给实现者的自由度

### 6.1 必测验证项（全部基于 TDD 先 RED 后 GREEN）
1. **Phase A 专项测试（`tests/test_upgrade_241.py` + `tests/test_align_fast_equivalence.py`）**：
   - `align_spot_swap_mapping` 与 `align_spot_swap_mapping_fast` 新规则在 200 组随机序列及 `symbol_swap != symbol_spot` 边界上 100% 等价，且在模拟真实单币 `swap` 表（`symbol_swap` 非空常量、`symbol_spot` 空/非空交替重上市）上与旧版输出逐位一致。
   - `_selection_input_filters` 不再过滤 `是否交易 == 0`，而 `KLINE_COLS` 仍保留 `是否交易` 供截面因子正常计算。
   - `config.py` / `datatools.py` 支持 `exchange_data_paths` 按交易所解析 `bmac_data_path`、支持从非 `_` 账户组目录查找成员文件，且 `lookback_save_interval_hours` 默认 `0` 不篡改 `conf.lookback_days`。
   - `account_group.py`：`load_group` 校验、`calc_scales`（含 `max_scale` 截断）、`build_transfer_msg`（`absolute` vs `relative` 口径与 `FLOAT_EPS` 边界）、`run_group` 成员失败整组熔断、组内 `MODEL` 成员走两阶段 `calc`/`trade` + `dry_run` 零撤单/零下单、`position.py` `scale > 1` 现货除 `scale` 不放大而合约正常放大。
   - BN 子账户 `fetch_transfer_history` 转出按 `fromAccountType` 过滤、OKX TradFi 过滤 / `51138` 动态限价 `0.999`/`1.001` / `0.97` 最小下单额 / `fetch_transfer_history` 10 天去重、`statis_func.net_fund` 与 `group_statistics.py` 6 子图出图不报错（含单成员无 `transfer.csv` 场景）。
2. **Phase B 专项测试（`tests/test_consumer_batch2_pipeline.py`）**：
   - **环节 5**：`check_flags` 就绪后 `< 0.25 s` 即返回（无 `1.0 s` 硬睡眠）；`wait_and_read_inputs_streaming` 在 `spot` `.ready` 先出现、`swap` `.ready` 延后 `0.3 s` 出现时，证明 `spot` 读取在 `swap` 就绪前已完成，最终字典与同步 `read_inputs` **`check_exact=True` 完全一致**，且 `wait_ready_s` 独立记录不污染 `total_s`；缺列或 `history_trim_date` 推进仍准确返回退出码 `75`；等待超时返回退出码 `76` 且不修改 `warmup.pkl`。
   - **环节 6**：`start_model_state_preload` + `consume_preloaded_model_state` 命中缓存省去二次 `pd.read_pickle`，文件被外部改写（mtime/size 变化）时自动丢弃缓存重读，未消费退出时自动清空引用；`equity.py` 新计数在含 `NaN`、全 0、正负混合矩阵上与旧 `.count(axis=1)` Oracle 逐位一致；`process_equities` 并行读 + `AsyncDiskWriter` 与串行同步基线产出的 `equity_dfs`、`ratio_dfs` 及落盘 `.pkl`/`.csv` 逐字节/逐值一致，写盘异常在 `flush()` 准确抛出。
   - **环节 7**：`DeferredAtomicSave` 证明 `pd.to_pickle` 在后台线程与主线程计算并发重叠，`finish_live` 前目标 `state.pkl` 仍保持旧值、`finish_live` 后原子替换为新值且释放 `.lock`；当 `validate_targets` 抛异常触发中止时，`abort()` 清理 `.tmp`、释放 `.lock`、旧 `state.pkl` 与 `warmup.pkl` 完好无损；`server_robust40.py warm-numba` 退出码 `0`。
3. **既有核心套件全回归**：
   - 跑通 `test_phase5_optimizations`、`test_p4_optimizations`、`test_2b_*`、`test_server_*`、`test_removed_series_governance`、`test_rebuild_handoff`、`test_universe_expansion_revision`、`test_arrow_*`、`test_full_arrow_initialization`、`test_precompute_contract`、`test_checkpoint_live`、`test_upgrade_231`、`test_upgrade_233` 等全部关联测试 0 回归。

### 6.2 留给实现者（Builder）的合理裁量项
- `AsyncDiskWriter` 可放在新建 `core/utils/async_disk.py` 或现有 `core/utils/misc_kit.py`（实施计划将指定精确路径与签名以消除歧义）。
- 日志文案中的阶段耗时格式保持中文清晰可读，与既有 `STEP6_MARKET_PRELOAD` / `EQUITY_PROFILE` / `MODEL仓管` 风格一致即可。
