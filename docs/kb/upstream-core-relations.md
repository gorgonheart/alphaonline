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
