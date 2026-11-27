# 与外部第三方版本 `core` 的关系及定制边界

> 所属体系：[统一开发入口](../README.md) · 根目录对照参考目录（均已 `.gitignore` 忽略，仅供对照，不纳入提交）：`D:/alphaonline/{2.1.1core,2.2.1core,2.3.0core,2.3.1core,2.3.3core}/`、`D:/alpha/coin-realtime-data_v1.1.12/`。

---

## 1. 第三方框架演进与本地集成策略

本项目基于邢不行实盘框架（交易端 `alphaonline` + 数据中心 `alpha`），采用 **“只做增量窄合入、绝不整体覆盖 `core/`”** 的维护策略。

### 1.1 交易端（`alphaonline`）版本脉络

| 外部版本 | 集成状态 | 合入内容 | 本地保留/拒绝项 |
| --- | --- | --- | --- |
| `2.1.1` / `2.2.1` | 历史基线（`481cf95`） | 原始多策略、仓位管理与实盘执行主链 | 在此基础上建立 Arrow mmap、Polars 截面、`entry_exit` 续算、`dry_run`、本地归档禁用 |
| `2.3.0` → `2.3.1`（`20260911`） | **已完整增量合入**（2026-10-14） | OKX 路由、bStocks 过滤、maker_ratio（默认 0，建仓/清仓 IOC）、自动补 BNB、拒单容错、1h 成交额统计、策略归属列 | 未启用根目录 `delist.py`/`monitor.py` 独立服务；保留本地归档禁用与文件保留上限；详见 [2026-10-14-upgrade-core-2.3.1.md](../superpowers/specs/2026-10-14-upgrade-core-2.3.1.md) |
| **`2.3.3`（`v2.3.3.20261030`，当前现行）** | **已完整增量合入并上线**（2026-11-06，提交 `c49d6de`→`680f334`） | ① `AccountConfig` 每轮单币参考名义金额限额（默认 `{}`，显式 `None` 拒绝）；② bStocks 双来源 + 全半角括号兼容 + 部分失败保护；③ 统一账户单源 `balance` 钱包分配（防 100+20 错算成 140）；④ 手动 `加密apikey.py`（支持 OKX password）；⑤ `xbx-py11==0.1.24` / `ccxt==4.5.88`（仅部署于独立 `upgrade233/alpha311`） | 保留所有 Arrow/Polars/C 预计算、MODEL/S3 续算与恢复点、执行器 JIT 缓存、本地统计快照；**未采用**上游 2.3.3 `statis_func.py:497` 的 `{**backfill, **current}` 历史归属合并（即 `plan.md` 问题 B，待独立补齐） |
| **`2.4.1`（`v2.4.1.20261120`）** | **已在隔离分支 `feat/v241-and-consumer-batch2` 离线增量合入并通过 Carbon 终审**（2026-11-26，提交 `ee62015`→`d21d5a1`，待授权部署测试机/未合 `main`/未上线） | ① `exchange_data_paths` 多交易所按账户覆盖与 `check_bmac_update_flag(exchanges)`；② `lookback_save_interval_hours`（强制默认 `0`）；③ `align_spot_swap_mapping`（含快速路径同步修复）与选币预过滤放开 `是否交易`；④ BN 子账户转账方向修复、杠杆档位 `adjust_leverage_by_target_notional` & OKX 全部客户端升级；⑤ `core/account_group.py`、`calc`/`trade` 两阶段拆分、`group_statistics` 与 `MODEL` 成员组调度兼容 | 完整保留 Arrow/Polars/C 预计算、MODEL 26 点恢复、P1–P5 性能优化、`portfolio_margin_client.py` 本地定制、有界异步统计池、单户 `statistics_snapshot` 双列底图例及全链路 `dry_run` 保护；未启用根目录 `delist.py`/`monitor.py` |

### 1.2 数据中心端（`alpha`）版本脉络

| 外部版本 | 集成状态 | 合入内容 | 本地保留/定制项 |
| --- | --- | --- | --- |
| **`coin-realtime-data_v1.1.12`** | **已完整合入并上线**（2026-11-05~06，提交 `b9cdd13`→`494d22a`） | ① 24h 已结算资金费回补；② 精确同名/唯一归一化现货合约映射与历史刷新；③ 新币首根闭合与空行情保护；④ 重叠价量修订贯通；⑤ Binance 优先/DataAPI 兜底固定来源；⑥ 年度 Pivot `.complete` 标记；⑦ 市值七列 `float64` 严格解析 | 完整保留本地增量模块 1–5、4 个 C 预计算扩展、单轮 Arrow 原子发布（`keep=1`）、`arrow_demand` 按需发布、`preprocess_1h_resample_origins` 策略起点持久化和 `coin_cap_use_proxy` 开关 |

---

## 2. 升级外部 `core/` 时绝不可丢失的本地核心定制清单

任何未来升级第三方 `2.3.x+` 时，**逐文件 diff 对照以下定制，严禁整目录替换**：

1. **数据读取与缓存**：`core/utils/arrow_reader.py`、`core/utils/datatools.py`（Arrow mmap 优先 + PKL 回退 + 向量化对齐 + `exginfo` 本地防 EOF 快照 + 两参数 Pivot PKL 读取 + `required_history_start`）。
2. **因子、截面与选币**：`core/select_coin.py`（预计算复用 + 动量 V2 全历史回退 + Polars 截面优先 + 分块并发写盘 + 跨小时空仓续算时间轴修复 + 显式截面状态接口 `core/utils/section_state.py`）；`core/model/strategy_config.py` + `core/utils/ee_continuation.py`（`entry_exit` 续算）。
3. **回测、模拟与执行**：`core/backtest.py`（进程池 LPT 调度、局部第 0 代 GC `temporary_gc`、再择时行情与 `state0` 局部复用、`ratio_inputs`/`market_inputs` 内存交接）；`core/equity.py` + `core/utils/checkpoints.py`（26 恢复点与扩张对齐）；`core/real_trading.py` + `core/position.py`（MODEL 显式钩子 `prepare_live`/`transform_targets`、`RebRobust40` 外部注册、执行分段耗时日志、`dry_run` 防写保护）。
4. **统计与成本**：`tools/statistics_snapshot.py`、`tools/spot_cost_estimate.py`、`core/utils/statis_func.py`、`core/utils/statistics.py`（单源只读 overview 共享、1h 计划轮次匹配、现货移动加权成本、异步统计并发上限）。
5. **调度与 MODEL 恢复体系**：`startup.py` + `tools/robust40/*` + `positions/ModelRobust40Strategy.py`（账户预选、75 退出码交接后台初始化队列、168h 修订判定区分扩张与真修订、26 点共同恢复回放、cgroup 资源限额、`lifecycle.py` 临时工作区自动回收）。

---

## 3. 依赖隔离红线

- 消费者 2.3.3 所需 `xbx-py11==0.1.24` 与 `ccxt==4.5.88` 仅安装在独立环境 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`，**不得覆盖生产者共享 `alpha311` 环境**。
- 未来更新依赖必须在目标独立环境完整解析 `requirements.txt` 并通过 `pip check`，**禁止 `--no-deps`**；保持 NumPy `2.0.2`、pandas `2.2.3`、Polars `1.36.1`、PyArrow `17.0.0`、Numba `0.60.0` 不变以免破坏 C 扩展或数值精度。

---

## 4. 修订检测与回放（replay）是本地能力，不是上游功能

**上游（`2.1.1`–`2.3.3` core、数据中心 `1.1.12`）没有这套机制。** 上游只有"资金曲线续算"：`core/equity.py` 用 `simulate_to` 接着上次快照算，配合常量 `incremental_lookback_hours`（`2.3.3core/config.py:76`，默认 `0`，"最大支持更新最近多少小时资金曲线，建议不超过 24"）——即**固定回退 N 小时**，粗放且不判断历史到底变没变。`checkpoint_hours` / `revised_from` / `replay` 在各版本 `core` 中零命中。

**本地把它升级成"按需回放"**，因为本项目把跨轮续算从"只续资金曲线"扩展到**选币输出、子策略账户、S3 持仓与延迟 rank 队列、MODEL 历史与训练状态**。历史一旦变了，这些状态全部失效，而上游没有检测手段。本地新增：`checkpoint_hours=25`（MODEL 专用，传统账户默认 `0`）、`core/utils/checkpoints.py`（26 点，+33/−4 行、默认关闭）、`tools/robust40/handoff.py`（检测、共同点选择、事务）。

### 4.1 每轮做什么

| 步骤 | 动作 | 失败/分支 |
| --- | --- | --- |
| 1 检测 | 读最近 `168 h` 的基础行情、成交量、交易资格、映射、Pivot 模拟输入（`Arrow` 裁列裁行后转 pandas，不引入哈希），与上轮 `model/revision_inputs.pkl` 逐值比较 | 无差异 → `mode=incremental`，直接续算 |
| 2 定位 | 差异统计 `changed_cells / changed_series / changed_fields`，取最早受影响时刻 `revised_from` | 仅"历史扩张"（新增序列）不算修订 |
| 3 回放 | 取**严格早于** `revised_from` 且 14 子策略 + 2 再择时 + S3 + 9 专家 + MODEL 主账户**都有**的共同点，恢复到该点，再跑框架原计算流程到 `cutoff` | 无共同点 / 早于 25 点窗口 / 币种布局变了 → `rebuild_required`（退出码 75）走后台完整初始化 |
| 4 事务 | 隔离副本计算，目标对拍与 `cutoff` 校验通过才 `complete=True`；旧包留 `.replay-previous` | 计算异常还原原包；提交后交易所异常保留新计算 |

日志字段：`cutoff`（`candle_begin_time`）、`revised_from`、`restored_at`、`replay_hours`、`copy_s`（完整包复制）、`restore_s`（状态恢复）、`prepare_s`（步骤 1–3 预检整体）。

### 4.2 实测（正式机 `2026-11-24` 全天 `75` 轮）

| 观测 | 值 |
| --- | --- |
| `mode=replay` / `incremental` | `67` / `8` |
| `cutoff − revised_from` | **`1.0 h` × 67/67** |
| `replay_hours` | **`2.0` × 67/67**（修 1 根 = 回放 2 小时） |
| 命中列 | `close` `quote_volume` `trade_num` `volume` 各 67/67，`taker_buy_*` 64，`high/low` 仅 7 |
| 耗时中位 | `prepare 25.6 vs 9.7`、`copy 7.0`、`restore 8.4`、`total 134.8 vs 113.6` |

**结论：这 67 次不是"历史被更正"，而是最新一根未定型。** 修订点恒在上一个小时、命中列恒为成交量/成交笔数——REST 每轮 99 根全量重写、`rest_fetch_delay_seconds=3.0` 时最新 bar 尚未结算完，下一轮再读就不同。

### 4.3 根因已定量证实（`2026-11-24 21:35–22:00` 专项核验）

| 证据 | 结果 |
| --- | --- |
| 终值对比（`11-23 12:00` → `11-24 13:30`，92 标的 × 9 列，5.5 万对） | **零差异**：WS 一次写入值 == REST 纠正后值 |
| 同一根 bar REST 多点轮询（`13:40`） | `+4.2 s` 未定型（BTCUSDT 少 61 笔），`+9.3 s` 定型，此后 `181 s` 不变 |
| 全量落库对比（`13:40`，767 swap + 520 spot） | swap `8/767`（**1.04%**）不一致，且**全部是正式机少成交**（1–20 笔）；spot `0/520` |
| 抓取发起时刻 | 边界 `+3.0 s`（`21:45:03.001`），正好落在未定型窗口 |

因此 `replay` 的成因是**自伤**：本轮把未定型值写进 base，下一轮重写纠正，检测器记为"历史修订"。WS 的 closed kline 是服务端定型后推送，一次写入即最终值，故天然零修订——**它没有"漏读修订"，是没有修订可读**。

边界：`once_update_candles=99` 意味着两边都只重读最近 `8.25 h`，更早的历史即使被交易所修订，两条链路同样看不见（共同盲点，非 WS 特有）。完整证据见 [修订根因定论](../2026-11-24-revision-root-cause.md)。
