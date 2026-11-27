# 子项目 2B：生产端 `v2.0.2` 底座升级、存储解耦与双端零重建滚动设计文档

> **所属体系**：[统一开发入口](../../README.md) · [2026-11-23 Phase 5 与 v2.0.2 规划](../../2026-11-23-phase5-and-v202-migration-plan.md) · [开发规范与数据契约](../../kb/standards-and-contracts.md)
> **日期**：`2026-11-23`
> **基准提交**：生产端 `D:/alpha` `main=b5eb945`（`production/main` 同步，含 P4-4 `7c10b63` + REST 延后 `3.0 s`）；消费端 `D:/alphaonline` `main=2001075`（Phase 5 子项目 2A 已在测试机首轮验收，未发正式机）；上游参考底座 `D:/alpha/coin-realtime-data_v2.0.2`（只读参考）
> **项目规模**：**项目级（跨双仓架构升级）**——主工作在 `D:/alpha`（底座切换 + 暗雷治理 + 存储解耦 + 定制平移 + 峰值治理），配套小幅加固在 `D:/alphaonline`（Pivot 移除治理 + 滚动 S3 初始化起点 + `history_trim_date` 信号联动）
> **状态**：方案 A 已由用户确认；已完成独立构建者可实施性核查并修订全部 3 项核查发现，待最终人工批准后进入 `writing-plans`

---

## 1. 意图与背景（Why）

现行生产端 `D:/alpha`（`v1.1.12` 本地增量版）存在 4 类耦合痛点，仅靠项目 1 的 REST 延后 `3.0 s` 无法治本：

1. **纯 REST 准点抓取的尾部伪修订**：即使延后 `3.0 s`，仍有少量币种在 5m 边界后出现尾差修订，且每次采集耗时 `24–31 s`；上游 `v2.0.2` 引入 Binance WebSocket 闭合 K 线（`k["x"] == True`）+ `barrier` 轮询 `.ws_ready` + 仅补漏币的 REST 兜底，可将采集 + 重采样压至 `~6–10 s` 并从协议层根治伪修订 `replay`。
2. **`v2.0.2` 原版在 4c8g / 紧缺磁盘生产机上存在 5 处暗雷，不能直接照搬**：
   - **暗雷 1（`flat pkl` 磁盘/I/O 炸弹）**：`core/storage/writer.py:420` 用全量 `num_base_candles`（`180,000` 行）裁剪缓冲 `flat pkl`，满窗额外吃 `~15+ GiB` 并冲刷页缓存；
   - **暗雷 2（常驻进程内存膨胀）**：`runtime_supervisor.py:219, 279-306` 默认 `NUM_WRITERS=4` + 每 200 stream 起 1 个 `ws_runner` `spawn` 进程 = **10–11 个常驻 Python 进程**（`~0.9–1.4 GiB` RAM，现行仅 `212 MiB`）；
   - **暗雷 3（`resample` 8-spawn 并发炸弹）**：`update_cycle.py:277` 并发跑 `spot`/`swap` 两线程，各自在 `resample.py:323` 起 4 个 `spawn` 子进程 = 瞬间新增 **8 个 Python 进程**（叠加总计 15 进程）；
   - **暗雷 4（`WsLocalCache` SQLite 无限增长）**：`core/ws/ws_local_cache.py` 只有 `INSERT/UPSERT` 无任何清理逻辑，长跑会无限膨胀；
   - **暗雷 5（上游预处理 `4.54 GiB` 内存峰值）**：现行 `D:/alpha/core/preprocess.py:725-731` 用 2 线程同时跑 `spot` 和 `swap` 预处理导致双市场全历史并存内存，与交易端重叠时诱发 swap。
3. **重启误触发 6 户重建与 `2026-12-16` 历史撞线风险**：
   - 2026-11-23 重启正式机 `alpha2` 现场暴露：① `init_dirs()` 清空了整个 `preprocess_1h_resample/`，导致当年 Pivot 被删除重造、已下架币 `ANTHROPICUSDT` 列丢失，触发消费端 `handoff.py` `removed_columns` 强制 6 户串行重建（~84 分钟）；② 固定写死的 S3 `start_date = '2025-05-20'` 要求最早 K 线早于 `2025-03-31`，临时调高 `kline_count_1h=15000` 后将在 `2026-12-16` 再次撞线；③ `base_5m` 保留全部 `15000h` 在正式机独占 `12.1 GiB`（占满可用空间压力，剩余仅 `25 GiB`）。
4. **`v2.0.2` 分叉自 `v1.1.11`，缺失 6 项现行 `D:/alpha`（`v1.1.12` + 本地强化）关键能力**：
   - 缺失 `fill_gaps` 整数列 dtype 还原（遇缺口币会直接撞碎 Arrow schema）；
   - `app.py` 跳过非启用 offset 会废掉 10 分钟频次 24h 已结算资金费回补与空闲 `backfill.py`；
   - 缺失 `funding_backfill.py` / `funding_publication.py` 与 `history_data/funding.py` 并发限流（易撞 WAF 403）；
   - 缺失 `COIN_CAP_DTYPES` 严格 `float64` 解析与 `coin_cap_use_proxy` 隔离；
   - 缺失 `preprocess` 增量、4 个 C 扩展、Arrow + `arrow_demand`、P4-4 内存传 Pivot、`candle_origins` 与 `publication_sources`；
   - 入口改名为 `realtime_binance.py` / `config_binance.py`（破坏现有 PM2 `realtime_data.py` 与 `config.json`）。

---

## 2. 目标、非目标与反目标

### 目标（Goals）
- **G1（底座升级 + 5 暗雷拆除）**：`D:/alpha` 切至 `v2.0.2` 模块化底座并开启 `use_websocket=True`，拆除全部 5 处暗雷：常驻进程压至 `5` 个（`1 main + 2 writers + 2 ws_runners`，总 RSS `< 500 MiB`），`flat pkl` 压至 `~20h` 小窗，`WsLocalCache` 滚动清理（48h），`update_resample_from_base` 零额外 `spawn` 进程，预处理峰值从 `4.54 GiB` 压至 **`< 2.5 GiB`**。
- **G2（5m/1h 存储解耦 + 冷启自愈）**：`base_5m` 固定滚动保留 **30 天**（立省 ~11 GiB）；`resample_1h`、`batch PKL`、`funding` 以 `min_history_start_date = '2025-03-25'` 作为非破坏性保留下限且重启不清空（冷启从 ~15 分钟压至 **`< 2 分钟`**）；冷启动 / 新增 offset / 单币 1h 缺失时自动按需临时补拉 `5m` 重采样后再将 `base_5m` 裁回 30 天。
- **G3（重启零重建双保险）**：`alpha` `init_dirs()` 保留 `resample_1h/`、`preprocess_1h_resample/`（含 `market_pivot_*.pkl` / `.complete` / `.arrow-demand/` / `batch PKL`）与 `exginfo/`，配合 `update_pivot_rows` 天然保列；`alphaonline` `handoff.py` 将 F1 零持仓分级放宽 + `max(30, 8%)` 单市场熔断扩展到 `pivot/*` 的 `removed_columns`。
- **G4（`alphaonline` 滚动 S3 起点 + 计划内磁盘瘦身联动）**：在不改动任何现有实盘账户配置（`accounts/*.py`）、不改动 `ModelRobust40Strategy.py`（免 Numba 重编译）、不重启 `startup.py` 的前提下：① 异常全量初始化时自动按上游可用历史推算滚动 S3 起点（确保保留 ≥ 365 天完整 MODEL 训练窗），彻底消除 `2026-12-16` 撞线；② 未来 1–2 年后若运维主动在 `alpha/config.json` 设置 `history_trim_date` 并重启 `alpha`，`alphaonline` 在首轮预检即通过 Arrow metadata `b'history_trim_date'` 识别变更并返回退出码 `75`，由现有单槽位后台队列自动逐户重算切到新窗口。
- **G5（全量本地定制平移与向后兼容）**：完整保留现行 `D:/alpha` 全部定制能力、`realtime_data.py` + `config.py` + `config.json` 入口与 7 条跨项目数据契约；2B 上线当天对已有 `alphaonline` 快照实现 **0 因子漂移、0 全量重建**。

### 非目标（Non-Goals）
- **不启用 OKX 实盘**：保留 `v2.0.2` 的 `ExchangeAdapter` / `OkxAdapter` / `OkxProtocol` 架构骨架便于未来跟进上游，但本次仅围绕 Binance 生产链验证与发布，不启用 OKX 进程。
- **不启用 `snapshot_restore`**：保留 `v2.0.2` 的 `core/snapshot/` 模块，配置默认 `use_api['snapshot_restore'] = False`。
- **不修改 `positions/ModelRobust40Strategy.py`**：保留全部策略公式、168h `merge_context` 严格一致校验与既有 Numba 缓存。
- **不修改 `动量210401_双项异常隔离V2`、`CciV3`、`ILLQStdBbi45208` 任何因子数学公式**。
- **不做多级稀疏 Checkpoint（`F1b`）或 `O8` Replay 深度优化**（已独立归档）。
- **不触碰正式机并行旧实例 `/home/ubuntu/alpha` 与 `/home/ubuntu/alphatradetest`**。

### 反目标（Anti-Goals）
- **绝不在正常启动或 2B 首次上线时截断磁盘已有的 `resample_1h` / `batch PKL` 头部**：因 `动量V2(13608)` 是无截断 IIR（15,000 根后头部仍有 ~11% 权重且通过 `rolling(13608)` 极值窗口放大至 ~81%），任何非显式的头部截断都会导致老币 `动量V2` 过去 168h 漂移，触发 `merge_context` `Historical revision in S5 positions` 报错。
- **绝不引入任何新第三方依赖**：`v2.0.2` WS 使用既有 `aiohttp.ClientSession.ws_connect`，测试/正式机已装 `aiohttp 3.13.3`，严禁污染 `alpha311` 或消费者 `upgrade233/alpha311`。
- **绝不破坏原子发布顺序**：必须坚持「全历史算完 C 因子 → 原子写 `{spot,swap}_dict_{ts}.arrow` → 生成 `market_pivot_*` → 最后写 `{spot,swap}_dict_{ts}.ready`」。
- **绝不在非 `history_trim_date` 变更时误触发 6 户重建**：老快照无 `history_trim_date` 字段时默认视为 `None`（与 2B 上线默认 `history_trim_date=None` 一致），上线当天 0 重建。

---

## 3. 备选方案与已选决策（Approaches）

针对「1h / `batch PKL` 是否裁头滚动」与 `动量V2` IIR 残留权重的矛盾，对比了 3 种真实方案并经用户确认采用 **方案 A**：

| 方案 | 核心机制 | 磁盘轨迹（正式机 6 offset） | 关键取舍与结论 |
| --- | --- | --- | --- |
| **方案 A（已选）** | `base_5m` 30 天滚动（立省 ~11 GiB）；`1h`/`batch PKL` 以 `min_history_start_date='2025-03-25'` 作非破坏下限、默认不裁头（`history_trim_date=None`）；`alphaonline` 扩展 Pivot F1 放宽 + 异常重建滚动 S3 起点 + 未来显式 `history_trim_date` 触发计划内重建 | 首日净省 ~11 GiB；稳态 Arrow 恒定 ~8.6 GiB；之后缓慢增长 **~12 GiB/年**（`resample_1h` 3.6G + `batch PKL` 6.9G + `Pivot` 1.5G） | **最优**：零策略数学改动、零 `merge_context` 误报风险、2B 上线与日常重启均 0 重建、未来可一键做计划内磁盘瘦身 |
| 方案 B（弃用） | `1h`/`batch PKL` 硬锁 15,000h 滚动裁头 + 放宽 `alphaonline` `merge_context` 168h `S5` 校验 | 除 ~1.5 GiB/年冻结 Pivot 外几乎恒定 | 必须削弱 168h 严格历史一致守卫，且每次裁头对 `S5` 引入非平稳漂移 |
| 方案 C（弃用） | `1h`/`batch PKL` 硬锁 15,000h 滚动裁头 + 把 `动量V2` 改为有限窗口截断 | 同方案 B | 改变核心选币因子数学定义，破坏既有实盘与研究基线等价性 |

---

## 4. 环境与跨项目硬约束

1. **Python 解释器与测试规范**：
   - 本地唯一使用 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（`pandas 2.2.3` / `numpy 2.0.2` / `pyarrow 17.0.0` / `aiohttp 3.11.11`）；测试机/正式机生产者为 `/home/ubuntu/miniconda3/envs/alpha311/bin/python`（`aiohttp 3.13.3`）。
   - Windows pytest 统一传 `-p no:cacheprovider --basetemp=<short_path>`；离线测试禁止外网连接（monkeypatch socket）。
   - Linux 目标机必须通过 `bash tools/deploy_alpha.sh build` 独立编译 4 个 CFFI 扩展（`cci_build.py` GCC 关闭 FMA contraction）。
2. **机器私有配置与入口兼容**：
   - 保持 `D:/alpha/realtime_data.py`（PM2 `realtime_data` / `alpha2` 及 `tools/deploy_alpha.sh` 依赖该文件名）和 `config.py` + 机器私有 `config.json`（测试机 `proxy='http://127.0.0.1:7897'`、`coin_cap_use_proxy=True`；正式机 `proxy=None`、`kline_count_1h_spot/swap=15000`、`enabled_hour_offsets=6 offsets`）。
3. **时间戳精度契约**：
   - `pandas 2.2.3` 下 WS（`pd.Timestamp(ms, unit="ms", tz="UTC")`）与 REST（`pd.to_datetime(..., unit="ms", utc=True)`）均产出 `datetime64[ns, UTC]`；`Writer` 入口显式统一 `candle_begin_time` 为 tz-aware UTC `datetime64[ns, UTC]`，确保 `TSManager`、1h 重采样与 Arrow schema 跨源完全一致（同时消费端 P5-A1 也已兼容任意 `s`/`ms`/`us`/`ns`）。
4. **7 条跨项目契约不可破坏**（见 [standards-and-contracts.md](../../kb/standards-and-contracts.md)）：特别是 `batch PKL` 永不落预计算因子、Arrow 先在完整历史算完因子再 tail 裁切、`candle_origins` 保持 `first_candle_time` 跨重启稳定、`test/` 目录纯隔离随时可删。
5. **默认离线开发与授权红线**：未经明确授权，不启动实盘/生产者、不重启测试/正式机服务、不删除生产数据。

---

## 5. `D:/alpha` 生产端详细设计（阶段 2B-1 & 2B-2）

### 5.1 包布局、入口兼容与配置体系

采用 `v2.0.2` 的清晰子包结构替代现行扁平 `core/`，同时对旧模块路径提供薄转发兼容层（保障既有 30+ 测试与外部脚本零断裂，具体是否保留或同步迁测试 import 见 §7）：

```text
D:/alpha/
├── realtime_data.py              # Binance 主入口（保留现名，调 core.app.run_realtime(config, create_binance_handle)）
├── config.py                     # 兼容现有全部 config.json 字段 + 2B 新增字段
├── core/
│   ├── app.py                    # 主循环（WS/REST 分流 + 非启用 offset 资金费/空闲补算 + defer_funding_backfill）
│   ├── handle.py                 # BmacHandle / MarketHandle（保留 spot/swap 分开配置 + 2B 存储参数）
│   ├── alert.py / version.py
│   ├── exchange/                 # v2.0.2 多交易所抽象 + BinanceFetcher 保留本地 funding 校验与限流钩子
│   ├── storage/                  # writer.py（小窗 flat pkl + 30d TSManager 裁剪）、ts_manager.py、candle_manager.py
│   ├── ws/                       # ws_kline_client.py、ws_local_cache.py（+48h prune）、ws_runner.py（单进程多连接）
│   ├── pipeline/                 # runtime_supervisor.py、update_cycle.py、barrier.py、data_update.py、
│   │                             # resample.py（+int_dtypes 还原 + 增量合并）、backfill.py、publication_sources.py
│   ├── history_data/             # history.py、candle.py（+30d/自愈双模式）、funding.py（保留本地并发4+450/300s）、utils.py
│   ├── ingest/                   # coin_cap.py（+COIN_CAP_DTYPES + coin_cap_use_proxy）、coin_cap_schema.py、
│   │                             # data_api_update.py、quantclass.py
│   ├── strategy/                 # preprocess.py（增量+C因子+P4-4+顺序spot/swap降峰值）、factor_calc.py、
│   │                             # arrow_publish.py（+history_trim_date）、arrow_demand.py、candle_origins.py、
│   │                             # funding_backfill.py、funding_hours.py、funding_publication.py
│   ├── snapshot/                 # v2.0.2 冷启动快照模块（默认关闭）
│   └── utils/                    # file_system.py（非破坏 init_dirs）、network.py、time.py、log_kit.py、log_bridge.py、...
```

**`config.py` 新增/保留配置项（均支持 `config.json` 覆盖与启动期类型校验）**：

| 配置项 | 默认值 | 含义与校验规则 |
| --- | --- | --- |
| `use_websocket` | `True` | `bool`。`True` 走 WS+REST 补漏；`False` 退化纯 REST 并启用 `rest_fetch_delay_seconds` |
| `rest_fetch_delay_seconds` | `3.0` | 非负数值。仅在 `use_websocket=False` 退化模式生效；WS 模式不附加此延迟 |
| `num_writers` | `2` | 正整数（建议 4c8g 固定 `2`）。替代 `v2.0.2` 硬编码 `NUM_WRITERS=4` |
| `ws_flat_pkl_bars` | `240` | 正整数（至少 `>= once_update_candles * 2`）。`Writer` 缓冲 `flat pkl` 独立滚动上限（~20h） |
| `base_5m_retention_days` | `30` | 正整数（至少 `>= 7`）。`TSManager` 原始 5m 月分区常态滚动保留天数（`30d = 8640` 根 5m） |
| `min_history_start_date` | `'2025-03-25'` | `None` 或 ISO UTC 日期字符串（如 `'2025-03-25'`）。1h / `funding` 非破坏性保留下限，绝不主动裁掉早于该日期的已有历史 |
| `history_trim_date` | `None` | `None` 或 ISO UTC 日期字符串。显式计划内瘦身开关：非 `None` 时在启动期裁剪早于该日期的 `resample_1h`/`batch PKL`/`funding`/过期旧年 Pivot，并将该值写入 Arrow metadata `b'history_trim_date'` |
| `kline_count_1h_spot` / `kline_count_1h_swap` | `15000` / `15000` | 正整数。兼容现有机器 `config.json`；若仅配置单字段 `kline_count_1h`，自动同时赋予 spot/swap |
| 其余既有本地配置 | 保持原默认 | `precompute_factor_configs`、`precompute_factor_workers`、`preprocess_num_batch`、`publish_kline_num`、`arrow_snapshot_keep`、`arrow_demand_enabled`、`arrow_demand_min_rows`、`coin_cap_use_proxy`、`enabled_hour_offsets`、`use_api`、`proxy`、`storage_path` |

**校验红线**：
- 若 `history_trim_date` 非 `None` 且 `min_history_start_date` 非 `None` 且 `pd.Timestamp(history_trim_date, tz='UTC') > pd.Timestamp(min_history_start_date, tz='UTC')`，启动期允许以 `history_trim_date` 覆盖有效下限并打印明确 WARNING（防止两字段冲突导致刚裁完又把历史下回来的死循环）；
- 若 `history_trim_date` 距当前时间不足 `14,400` 小时（600 天），启动期直接 `raise ValueError` 拒绝启动（防误操作把历史裁得过短导致下游无法初始化）。

### 5.2 阶段 2B-1：拆除 5 处暗雷与 5m/1h 存储解耦

#### A. 暗雷 1 + 5m/1h 三层存储解耦（`writer.py`、`handle.py`、`file_system.py`、`history_data/candle.py`、`resample.py`）
1. **`flat pkl` 独立小窗裁剪（`core/storage/writer.py`）**：
   - `Writer.__init__` 拆分为两个独立窗口参数：`flat_pkl_max_bars: int`（默认 `max(once_update_candles * 2, 240)`）与 `partition_retention_bars: int`（由 `base_5m_retention_days * 24 * num_offsets` 计算，30 天 = `8640` 根 5m）。
   - `Writer._merge` 只用 `flat_pkl_max_bars` 裁剪 `flat pkl`，彻底杜绝 `180,000` 行全量重写；入口同时校验并统一 `candle_begin_time` 为 `datetime64[ns, UTC]`。
   - `Writer._update_tsmanager` 每天（`PARTITION_TRIM_INTERVAL = timedelta(days=1)`）用 `partition_retention_bars` 裁剪 `TSManager` 全部早于 `required_first_begin` 的旧月分区（**注意修正 `v2.0.2` bug**：原 `writer.py:464` 只裁 `partitions[0]`，若跨月切换或从旧 15000h 首次迁移存在 20 个旧月分区，需遍历 `partitions` 裁除所有早于 `required_first_begin` 的分区，空分区由 `TSManager.write_partition` 自动 `unlink`）。
2. **1h / `funding` 历史深度与固定时间戳保留契约（杜绝隐式 `.tail(N)` 裁头）**：
   - 设 `effective_floor = max(min_history_start_date, history_trim_date)`（任一为空取另一方，均空则无日期下限）；`trim_ts = pd.Timestamp(history_trim_date, tz='UTC')`（当 `history_trim_date` 非 `None` 时）。
   - `handle.effective_kline_count_1h(trade_type, run_time)`：当 `effective_floor` 非空时，`anchor_hours = math.ceil((run_time_utc - effective_floor).total_seconds() / 3600) + 48`，返回 `max(configured_kline_count_1h[trade_type], anchor_hours)`。
   - **关键区分（彻底消除 `resample_1h` / `funding` 与 `batch PKL` 脱节）**：
     - `effective_kline_count_1h` **仅用作回补深度目标**（驱动 `full_backfill_symbols` 冷启/缺盘拉取深度、`async_download_funding_rates` 首次全量拉取起点、以及 `calc_resample` 的冷启 `theoretical_start`）。
     - **运行期保留不再使用滑动 `.tail(num_candles)`**，改为**固定时间戳下限 `min_ts`**（由 `history_trim_date` 驱动）：
       - 当 `history_trim_date is None` 时：`update_resample_candle`、`save_funding_hours`、`gen_batch_data_incremental`、`data_api_update` 均传 `num_candles=None / depth=None / min_ts=None`（**完全不裁头部**，正式机现有 `2025-03-08` 与测试机现有 `2025-04-16` 的 `resample_1h`/`batch PKL`/`funding` 头部永不漂移，`动量V2` 逐位 0 差异）；
       - 当 `history_trim_date` 非 `None` 时：上述 4 处统一按固定时间戳过滤 `candle_begin_time >= trim_ts`（**仍不做逐小时滑动 `.tail(N)`**，因此完成一次计划内重建后，新起点同样长期固定在 `trim_ts`，`动量V2` 再次保持 0 漂移）。
3. **非破坏性 `init_dirs()`（`core/utils/file_system.py`）**：
   - 启动时**保留** `base_candle_dir`、`resample_dir`（所有 offset 的 1h PKL）、`preprocess_dir`（所有 offset 的 `market_pivot_*.pkl` / `.complete` / `.arrow-demand/` / `batch PKL` / 既有快照）、`funding_mgr`、`coin_cap_mgr`、`exg_mgr`、`data_api` 目录（`remove_exist=False`），**绝不再 `shutil.rmtree`**；
   - 仍在 `init_dirs()` 开头对每个 offset 调用 `preserve_published_origins(mgr)`，保障 `preprocess_1h_resample_origins/` 始终与最近完整发布一致。
4. **显式计划内裁头（仅 `history_trim_date` 非 `None` 时触发）**：
   - 在 `init_history` 内新增 `apply_explicit_history_trim(handle, trim_ts)`：
     - 扫描所有启用 offset 的 `resample_1h` 与 `preprocess_1h_resample` `batch PKL`、`funding`、`data_api`，仅裁剪 `candle_begin_time < trim_ts` 的行；
     - 删除 `year < trim_ts.year` 的旧年份 `market_pivot_{spot,swap}_{year}.pkl` 与 `.complete`；
     - 仅当至少裁剪了 1 份文件时，顺手删除受影响年份的 `.complete` 标记，让首轮预处理平滑重建。
   - 当 `history_trim_date is None` 时该函数为纯 no-op（保证 2B 上线首日对测试机与正式机已有磁盘 0 截断、0 `动量V2` 漂移）。
5. **重启增量补齐 + 冷启/新增 offset/单币缺失自动自愈（`history_data/candle.py` & `pipeline/resample.py`）**：
   - 启动期对每个市场（`spot`/`swap`）在校验 `base_5m` 前，先扫描已启用 offset 的 `resample_1h` 覆盖情况，得到每个 symbol 的需求集合 `full_backfill_symbols`：
     - **仅**满足以下两个明确缺盘条件之一时，才将该 symbol 加入 `full_backfill_symbols`（**严禁对已有完整 `resample_1h` 的现网/测试机实例隐式前置补拉旧历史**，否则会前移已有 `batch PKL` 头部导致 `动量V2` 漂移触发 `merge_context` 报错）：
       1. 任一已启用 offset 缺失 `{symbol}.pkl` 或文件为空/损坏（覆盖：全新冷启动、新增 offset、单币损坏、新上市币）；
       2. 任一已启用 offset 的最新 bar 早于 `run_time - timedelta(days=base_5m_retention_days - 2)`（停机超 28 天，30 天 `base_5m` 无法无缝衔接）。
     - **常规快路径（不在 `full_backfill_symbols` 中）**：`base_5m` 目标起点 = `run_time - timedelta(days=base_5m_retention_days)`（30 天）；`verify_exist_history` 仅保留最近 30 天分区，`RecentDownloader` 只补停机几分钟缺口，`HistoryDownloader` 直接 0 请求完成；`resample_history` 从 30 天 `base_5m` 重采样后，**对已存在 `resample_1h` 的币种丢弃截断窗口首根不完整 1h bar**（与 `_resample_one_symbol:257` 同逻辑），通过 `update_resample_candle(..., min_ts=trim_ts)` 增量合并写入（**绝不用 `set_candle` 覆盖全历史！**）。
     - **自愈路径（在 `full_backfill_symbols` 中）**：`HistoryDownloader` 按 `effective_kline_count_1h`（已含 `min_history_start_date` 兜底）目标起点拉满 5m 历史（遇到新币短页自动停止），`resample_history` 为其完整构建缺失/断档 offset 的 `resample_1h`（已有完整历史的其他 offset 仍走增量合并），**`resample_history` 完成后立即将这批币的 `base_5m` 月分区裁回 `base_5m_retention_days`（30 天）**，释放临时磁盘。
     - **说明（测试机 `alphatest` 首次上线行为）**：测试机既有 `resample_1h` 起步于 `~2025-04-16` 且各启用 offset 均已存在，移除隐式市场级深度回补后，测试机 2B 首次重启直接走常规快路径（`< 2 min`、0 头部前移、0 `动量V2` 漂移、0 全量重建）；门禁 5 的冷启/缺盘自愈能力在隔离 `test/` 目录或模拟缺盘场景独立验证 `<= 2025-03-25` 覆盖。
     - **并发与可观测性**：保留现行 `D:/alpha/core/history_data/candle.py` 的 `asyncio.Semaphore(50)` 并发上限（防打穿代理）与 30 秒节流 `INFO` 进度日志，叠加 `v2.0.2` 的 `return_exceptions=True` 单币失败隔离与整批 429 冷却。

#### B. 暗雷 2 + 暗雷 4：进程瘦身与 SQLite 清理（`runtime_supervisor.py`、`ws_runner.py`、`ws_local_cache.py`）
1. **`NUM_WRITERS` 可配置且默认 `2`**（由 `cfg.num_writers` 注入 `RuntimeSupervisor`），按 `zlib.crc32(symbol) % num_writers` 确定性路由。
2. **同市场多连接收拢至单进程多 `asyncio` 协程**：
   - 每个市场（`spot`、`swap`）仅起 **1 个** `ws_runner` `spawn` 子进程（进程名 `bmac-{exchange}-ws-{trade_type}`，共 **2 个** WS 进程）。
   - 子进程内接收该市场全量 `symbols`，按 `protocol.max_streams_per_conn`（Binance 为 `200`）切分为多个 chunk；每个 chunk 持有一个独立的 `WsKlineClient(url=protocol.ws_url(trade_type, chunk_idx), ...)` 协程并发运行在同一事件循环内（仍严守单连接 ≤ 200 streams，防止 Binance 5m 边界丢帧），共享**同一个** `WsLocalCache(ws_cache_dir / f"{trade_type}.sqlite")` 实例（单线程事件循环无并发写冲突）。
   - 保留 `RuntimeSupervisor` 的冷却防抖（`60s`）、优雅退出（共享 `stop_event`、绝不 `kill` 队列生产者）、延迟重启收尾（`resume_pending_ws_restarts`）与 `health_check` 告警。
3. **`WsLocalCache` 48h 滚动清理（`core/ws/ws_local_cache.py`）**：
   - 新增 `prune_before(cutoff_ms: int) -> int`（`DELETE FROM kline WHERE begin_time_ms < ?` + `PRAGMA wal_checkpoint(PASSIVE)`）。
   - 在 `WsRunner` 内每小时触发一次 `prune_before(now_ms - 48 * 3600 * 1000)`，启动重放后亦清理一次，确保 SQLite 恒定在 `~10–20 MiB`。

#### C. 暗雷 3：消除 `update_resample_from_base` 8-spawn 并发炸弹（`pipeline/resample.py`、`pipeline/update_cycle.py`）
- `update_resample_from_base` 仅处理最近 `once_update_candles = 99` 根 5m 闭合 K 线并合并写入 1h PKL，属于轻量文件 I/O + 小表 resample。
- 将 `resample.py:323` 的 `ProcessPoolExecutor(max_workers=4, mp_context="spawn")` 替换为 **`ThreadPoolExecutor(max_workers=2)`**（`spot`/`swap` 两路合计最多 4 线程、**0 额外 `spawn` 子进程**），完全消除 4c8g 上的 8 进程启动抖动与内存拷贝，同时在多线程进程内绝对无 `fork` 死锁风险。
- 在 `core/pipeline/resample.py::fill_gaps` 中**完整补回 `int_dtypes` 还原**（`D:/alpha/core/resample_candle.py:92, 117-118`），杜绝 gap 填充后 `trade_num` 变 `float64` 撞碎 Arrow schema。

### 5.3 阶段 2B-2：本地定制完整平移与 `4.54 GiB` 峰值治理

1. **预处理主链平移与 `4.54 GiB → < 2.5 GiB` 峰值治理（`core/strategy/preprocess.py`）**：
   - 完整迁入现行 `D:/alpha/core/preprocess.py` 的全部能力：
     - `gen_batch_data` / `gen_batch_data_incremental`（含 `apply_symbol_match` 保留历史已验证配对、`changed_price_years` / `invalidate_price_years`、`FundingPublication` 实时回填、原始批次 PKL 永不落预计算因子、P4-4 `year_pivot_out` 内存传 Pivot）；
     - 4 个 C 扩展预计算因子（`动量V2`、`ILLQ`、`CciV3`、旧动量）与 4 线程 `factor_executor`；
     - `arrow_publish.py`（新增可选 `history_trim_date` 写入 schema metadata `b'history_trim_date'`）+ `arrow_demand.py` + `cleanup_old_snapshots(keep=1)`；
     - `candle_origins.py`（`load_origins` / `remember_ready_origins` / `preserve_published_origins`）；
     - `gen_pivot_incremental` + `update_pivot_rows`（append-only 保列合并，保留历史已下架币列，新时间填 `NaN`、`funding_rate` 填 `0`）+ 精确 epoch-grid `mark_pivot_complete_if_closed`。
   - **峰值治理改造（3 步，预计峰值 `< 2.5 GiB`）**：
     1. `preprocess_offset_data` 将原 2 线程并发跑 `spot` + `swap` 改为**顺序执行 `spot → 释放 → swap`**（单市场内 ~400–600 币仍完整使用 4 线程 `factor_executor`，CPU 利用率几乎无损，内存直接砍半）；
     2. `preprocess_type_data` 在调完 `publish_arrow_snapshot_for_type` 后，先提取轻量 `origins` 字典（每币仅 2 个时间戳），立即 **`del market_dict`**，再执行 `gen_pivot_incremental`（错开全历史因子表与 Pivot `concat/pivot` 两个峰值），最后写 `{trade_type}_dict_{ts}.ready` 与 `remember_ready_origins`；
     3. 每市场结束后 `del year_pivot_data` + `gc.collect()` + Linux 下调用 `ctypes.CDLL("libc.so.6").malloc_trim(0)`（失败静默忽略，兼容 Windows）。
   - **防御性兜底**：`preprocess_type_data` 若发现本轮 `gen_pivot_incremental` 目标年份已存在旧 Pivot 文件（因 `init_dirs()` 不再删除），自动走 `update_pivot_rows` 增量保列合并；即使在极端情况下 `gen_pivot` 全量路径被直接调用，也先检查旧 Pivot 文件存在时保留已下架币历史列，确保两路径均 100% append-only 保列。

2. **资金费双路径完整平移（修复 `v2.0.2` 回退）**：
   - `core/exchange/rest/binance_fetcher.py`：保留现行 `D:/alpha/core/binance_fetcher_async.py` 的 `get_realtime_funding_rate`（显式数值校验）和 `get_hist_funding_rate(symbol, *, _reserve_request=None, **kwargs)`（保留原始毫秒时间戳、校验 symbol/时间范围/行数、不误 round）。
   - `core/history_data/funding.py`：对 Binance 路径采用现行 `D:/alpha/core/history_data/funding.py` 的 `FUNDING_MAX_CONCURRENCY = 4` + 滚动 `450/300s` 限流 + `settled_funding_hours` 同小时求和 + `save_funding_hours` 原子写盘（OKX 保留 `v2.0.2` 逻辑）；全局起始时间改用 `handle.effective_kline_count_1h('swap', run_time)`。
   - `core/pipeline/data_update.py`：在 `update_exchange_meta`（Binance 路径）完整迁入现行 `update_binance_meta` 的并发 `funding_task` + 8s 预算 `history_branch`（`backfill_recent_funding_rates`）+ `save_funding_updates`；`core/app.py` 在 `init_history` 成功后立即调用 `defer_funding_backfill()`（5 分钟冷却防 WAF）。

3. **非启用 offset 调度、`publication_sources`、`backfill` 与 `coin_cap` 平移**：
   - `core/app.py::update_all`：非启用 offset **不再 `time.sleep(60)` 跳过**，改为在 `run_time - 15s` 跑 `update_meta`（维持 10 分钟频次资金费回补，确保测试机单 offset 也能每小时扫完 ~600 币）+ `update_symbol_match` + `supervisor.refresh_ws_subscriptions` + `supervisor.health_check`，到 `run_time` 后执行 `maybe_backfill(handle)`。
   - `core/pipeline/update_cycle.py` & `core/pipeline/barrier.py` & `core/pipeline/publication_sources.py`：
     - **WS 模式（`run_ws_cycle`）——保留非阻塞发布拓扑 + 精确区分未开盘新币与 REST 失败**：
       1. **非阻塞并发拓扑（对齐 `test_publication_source_coverage.py` 契约与门禁 2 `~85–95 s` 预算）**：`task_data_api`（仅 `handle.data_api is not None`）与 `task_coin_cap`（仅 `handle.coin_cap_handle is not None`）在 `aux_exe`（`spawn`）中异步运行，**绝不在 `_run_preprocess` 前阻塞等待**。Binance `barrier + update_resample_from_base` 完成后立刻调 `choose_publication_inputs(..., data_api_done=False)`：若 Binance 已覆盖全部成熟币种，**立即启动 `_run_preprocess`**，等 `_run_preprocess` 结束后再回收 `task_coin_cap` 与 `task_data_api`；仅当 Binance 存在缺失成熟币且启用了 `data_api` 时，才等待 `task_data_api` 完成并二次调用 `choose_publication_inputs(..., data_api_done=True)` 做补全或抛 `Incomplete current market coverage`。
       2. **REST 兜底逐币确认状态与 barrier 提前退出（修复 `v2.0.2` 新币未开盘卡满 45s 缺陷）**：`_rest_fallback_worker` 在 `rest_state` 中记录逐币结果——`submitted[tt]`（REST 返回非空并已投递 writer）、`confirmed_empty[tt]`（REST 请求**成功**且返回空表 `df.empty`，证实为尚未开盘新币）、`failed[tt]`（REST 异常未确认）。`wait_for_ready_barrier` 接收 `rest_confirmed_empty`：当 `rest_done.is_set()` 且已过 `ws_wait_sec`（10s）后，若 `pending` 中剩余 symbol 全部属于 `rest_confirmed_empty`（且本地无历史）或均不在 `rest_submitted` 中（REST 已结束且不会再补），**立即退出 barrier 而不白等至 `timeout_sec=45s`**；若仍有 `pending & rest_submitted`（writer 仍在落盘），仍继续等待至写完或 `hard_timeout_sec=180s`。
       3. **`choose_publication_inputs` WS 适配**：仅当缺失币种满足 `symbol in confirmed_empty[tt]` 且本地 `resample_mgr` 无非空历史时，才允许作为未开盘新币跳过；REST 失败（`not rest_state["ok"]` 或不在 `confirmed_empty`）的无历史币绝不误跳过，必须等 DataAPI 或告警不发布残缺快照。
     - **REST 退化模式（`run_rest_cycle`）**：保留 `_fetch_wake_time(run_time)`（应用 `rest_fetch_delay_seconds`）、进程池预热与非阻塞 `choose_publication_inputs`，且 `update_binance_base_candles` 按 `base_5m_retention_days`（30 天）裁剪所有旧分区。
   - `core/pipeline/backfill.py`：适配 `v2.0.2` 的 `MarketHandle`（`get_market_handle`）与 `effective_kline_count_1h`，复用现有 `local_resample_offset` 增量合并逻辑（因 30 天 `base_5m` 只补齐近期，丢弃截断窗口首根不完整 1h bar）。
   - `core/ingest/coin_cap.py` & `core/ingest/quantclass.py`：补回 `COIN_CAP_DTYPES`（CSV 与 API 双路径严格 `float64`）与 `coin_cap_use_proxy` 独立代理开关（仅 `coin_cap` 按该开关决定是否走 `handle.proxy`，`data_api` K 线不受影响）。

---

## 6. `D:/alphaonline` 消费端配套设计（轻量加固，零账户改动、零 `ModelRobust40Strategy.py` 改动）

### 6.1 Pivot `removed_columns` 零持仓分级治理 + 单市场熔断（`tools/robust40/handoff.py`）

- 现状：`handoff.py:121-190` 仅对 `raw/{spot|swap}/*` 实施了 F1 零持仓放宽与 `max(30, 8%)` 单市场熔断；对 `pivot/{spot|swap}/{open|close|vwap1m|funding_rate}` 的任何 `removed_columns` 均无条件 `earliest.append(a.index.min())` 触发全量重建。
- 改造：
  1. **Pivot 熔断前置**：在 `first_revision()` 顶部 `raw/*` 熔断旁，同步统计每个市场 `pivot/{kind}/*` 的并集移除列 `removed_pivot_spot` / `removed_pivot_swap`（分母为旧 `pivot/{kind}/close` 列数）；任一市场超过 `max(removed_raw_min_limit, int(removed_raw_ratio_limit * old_pivot_count))` 时，记录遥测 `circuit_breaker='removed_pivot_columns_surge'` 并抛出 `UpstreamUniverseDropError`（阻断下单且不破坏快照、不触发重建）。
  2. **阈值内零持仓放宽**：当 `key.startswith('pivot/')` 且存在 `removed_columns` 时：
     - 懒加载 `resolved_held`（复用既有 `held_symbols` 回调）；
     - 若所有被删列均不在 `resolved_held`（按 `(kind, canonical(col))` 匹配），记录 `removed_columns` / `removed_column_names` 遥测，**不**追加 `a.index.min()`（保持 `incremental`）；
     - 若任一被删列处于持仓状态，记录 `removed_held_symbols` 遥测并保守追加 `a.index.min()`。
  3. **非 `pivot/` 键（如 `raw/*` 缺失 OHLCV 架构列）**：保持原保守行为不变，绝不放宽。

### 6.2 滚动 S3 初始化起点（`tools/robust40/server.py`、`tools/robust40/cli/server_robust40.py`）

- **目标**：彻底消除写死 `start_date = '2025-05-20'` 导致的 `2026-12-16` 撞线，同时 **0 修改现有 6 个实盘账户文件**、**0 修改 `ModelRobust40Strategy.py`**。
- **核心纯函数**（新增于 `tools/robust40/server.py`）：
  ```python
  def resolve_initialization_s3_origin(
      configured_origin: pd.Timestamp,
      earliest_bar: pd.Timestamp,
      n: int,
      min_kline_num: int,
      end_bar: pd.Timestamp,
      *,
      min_model_days: int = 365,
  ) -> pd.Timestamp:
  ```
  1. 提取 `minute = configured_origin.minute`，计算满足 `VolumeMaxBias` 预热要求的最早安全时刻：`raw_safe = earliest_bar + pd.Timedelta(hours=min_kline_num + n)`。
  2. 对齐到 `00:{minute}` UTC 日网格（与 `configured_s3` / `decision_days` 一致）：`earliest_safe = (raw_safe - pd.Timedelta(minutes=minute)).ceil('D') + pd.Timedelta(minutes=minute)`。
  3. `resolved = max(configured_origin, earliest_safe)`。
  4. 校验剩余有效模拟区间：`if end_bar - resolved < pd.Timedelta(days=min_model_days): raise ValueError(...)`（默认 365 天，确保 `robust_weekly_mix` 与 `quarterly_predictions` 均拿到完整 365 天训练窗口）。
- **接线点**：
  1. `server_robust40.py::_initialize()`：在 `freeze_inputs(..., full_arrow=True)` 之后，以 PyArrow 仅读时间戳列（零 pandas 转换，`< 0.1 s`）从冻结的 `swap_dict_{stamp}.arrow` 扫描满足 `num_rows >= INITIALIZATION['minimum_history_bars']` 的币种最小时间戳作为 `earliest_bar`，调用 `resolve_initialization_s3_origin(...)` 得到 `origin` 并写入 `ROBUST40_SERVER_WORKER` 载荷（日志打印实际使用的 `S3 origin`，若推进于配置值则显式注明原因）。
  2. `server_robust40.py::apply_profile()`：将严格等式校验 `cutoff(origin) != configured` 放宽为**单调前向校验**——要求 `cutoff(origin) >= configured` 且位于合法 `00:{minute}:00` UTC 日网格上（拒绝早于配置日期或错位分钟的损坏状态）。`apply_profile()` 既有代码会自动把解析后的 `origin` 注入 `config.strategy_pool`，从而 `ModelRobust40Strategy.py:1557` 零改动即可通过 `validate_initial_history`。
  3. `server.py::inspect()`：对已有快照保持读取 `meta['origin']`，日常增量续算永不改变已固化 `origin`。

### 6.3 `history_trim_date` 显式计划内瘦身联动（`server.py`、`server_robust40.py`）

- **契约**：
  - `warmup.pkl` 新增可选字段 `history_trim_date: Optional[str]`（旧快照缺省等价 `None`）。
  - `server.complete(..., history_trim_date=None)` 与 `server.finish_live()` 原样保留该字段。
  - `server_robust40.py::compute()` / `_initialize()` 在全量初始化冻结 Arrow 后，校验 `spot` 与 `swap` 两个 Arrow 的 `b'history_trim_date'` 一致并随 `ROBUST40_SERVER_WORKER` 传给 `server.complete()` 写入新快照的 `warmup.pkl`。
  - `server_robust40.py::_trade()` 在进入 `handoff.prepare()` 前，仅读当轮 `spot`/`swap` Arrow header（`< 1 ms`）：
    - 若 `spot` 与 `swap` 的 `b'history_trim_date'` 不一致 → 抛错跳过本轮（不破坏快照）；
    - 读取 `upstream_trim`（缺失则为 `None`）与快照 `saved_trim = original.get('history_trim_date')`；
    - 仅当 `upstream_trim is not None and upstream_trim != saved_trim`（且 `saved_trim is None or pd.Timestamp(upstream_trim, tz='UTC') > pd.Timestamp(saved_trim, tz='UTC')`）时：
      - 调用 `server.begin(destination)`（将 `complete` 置为 `False`），记录遥测 `mode='rebuild_required', recovery_reason=f'upstream history_trim_date changed: {saved_trim} -> {upstream_trim}'`，直接返回 `REBUILD_REQUIRED_EXIT`（`75`）交由 `Coordinator` 单槽位队列自动逐户重建，**全程无需重启 `startup.py`**！
    - 当 `upstream_trim is None`（含 2B 上线首日与常规运行）或 `upstream_trim == saved_trim` 时：完全跳过，0 开销、0 重建。

---

## 7. 工程师裁决与留给实施阶段（Builder）的细节

### 本设计已拍板的工程默认值（供人工审阅核对）
1. **单市场 WS 进程数固定为 2**（`spot` 1 进程 + `swap` 1 进程，进程内按 200 stream 分片多协程），`NUM_WRITERS` 默认 `2`，合计 5 个常驻 Python 进程。
2. **`WsLocalCache` 滚动清理周期**定为每小时清理一次，保留最近 `48h`（防止 1 天停机重放丢失且体积锁死 `< 20 MiB`）。
3. **自愈阈值**：`base_5m` 停机衔接冗余取 `base_5m_retention_days - 2`（即 30 天保留对应 28 天无缝衔接上限）；`min_history_start_date` 计算 `effective_kline_count_1h` 附加 `+48h` 安全余量；显式 `history_trim_date` 最低距离当前 `>= 14,400h`（600 天）。
4. **`alphaonline` 滚动 S3 起点最低模型窗**定为 `min_model_days = 365`（精确匹配 `ModelRobust40Strategy` 的 365 天 `weekly_mix` 与季度训练窗）。
5. **非破坏上线与固定时间戳保留（经构建者核查强化）**：`history_trim_date` 默认 `None`，且运行期 `resample_1h`/`funding`/`batch PKL`/`data_api` 一律改为**固定时间戳下限**（`None` 时完全不裁头、非 `None` 时按 `>= trim_ts` 过滤，绝不做滑动 `.tail(N)`），同时移除对已存在完整 `resample_1h` 实例的隐式前置补拉——保证 2B 上线首日在正式机（`2025-03-08`）与测试机（`2025-04-16`）均走 `< 2 min` 快路径、0 截断、0 前置、0 `动量V2` 漂移、0 重建。
6. **WS 非阻塞发布 + barrier 提前退出（经构建者核查补齐）**：保留现行 `D:/alpha` 非阻塞发布拓扑（Binance 100% 覆盖后不等 `task_data_api`/`task_coin_cap` 立刻进 `_run_preprocess`）；`_rest_fallback_worker` 产出逐币 `confirmed_empty`，消除未开盘新币导致 `barrier` 白等 45s 的上游缺陷并守住 `choose_publication_inputs` 覆盖校验红线。
7. **测试机验证顺序**：`D:/alpha` 与 `D:/alphaonline` 改动均在独立特性分支完成离线 TDD + Oracle 全量回归，先部署测试机 `alphatest` 跑满 24h 自然轮门禁并全部达标后，再经单独授权安排正式机发布。

### 留给实施计划（`writing-plans`）决定的细节
- 旧路径 `core/preprocess.py`、`core/candle_origins.py` 等是保留薄 re-export 兼容文件，还是同步迁移 `D:/alpha/tests/` 导入路径并只对外部入口保留兼容。
- `WsRunner` 内多 `WsKlineClient` 协程的任务管理细节（单 chunk 重连独立退避、互不阻塞其他 chunk、优雅取消顺序）。
- `D:/alpha/tools/deploy_alpha.sh` 与 `tools/run_deployment_tests.py` 的新测试清单增补。
- 单元测试夹具与离线合成行情/mock WS 服务的具体构造方式。

---

## 8. 分阶段落地与测试机 24 小时自然轮放行门禁（阶段 2B-3）

### 实施顺序（严格 TDD + 原子提交）
1. **阶段 2B-1（`D:/alpha` 底座 + 暗雷拆除 + 5m/1h 解耦）**：
   - 在 `D:/alpha` 创建隔离特性分支（如 `feat/v202-migration-2b`），合入 `v2.0.2` 模块骨架；
   - 落地 5 处暗雷拆除、3 层存储解耦、非破坏 `init_dirs()`、`apply_explicit_history_trim`、重启快路径与冷启/缺盘自愈、`int_dtypes` 还原；
   - 编写离线单元测试与 mock WS 集成测试（覆盖：flat pkl 240 行上限、30 天全分区裁剪、WS 多协程单进程、SQLite 48h 清理、重启保留 `resample_1h`+丢弃截断首根、冷启缺失 offset 自愈后裁回 30 天、无 8-spawn 进程）。
2. **阶段 2B-2（`D:/alpha` 本地定制平移 + 峰值治理 + `D:/alphaonline` 配套加固）**：
   - `D:/alpha`：平移 `preprocess` / 4 C 扩展 / Arrow + `history_trim_date` / `arrow_demand` / P4-4 / `candle_origins` / `funding_backfill`+`funding_publication`+`history_data/funding` 限流 / `publication_sources`+`backfill` / `coin_cap` 代理与类型；落地顺序 `spot→swap` + 先释放 `market_dict` 再建 Pivot + `malloc_trim(0)`；跑通 `D:/alpha` 全部既有 + 新增离线回归；
   - `D:/alphaonline`（独立分支，基于本地 `main=2001075`）：落地 Pivot `removed_columns` F1 治理、滚动 S3 初始化起点与 `history_trim_date` 预检联动；编写 TDD 测试覆盖零持仓/持仓/熔断、滚动起点计算与 365 天守卫、老快照默认 `None` 零误触发与显式推进返 `75`；跑通全量回归。
3. **阶段 2B-3（测试机 `alphatest` 部署与 24 小时自然轮放行门禁，需部署前授权）**：
   - 备份测试机 `/home/ubuntu/alpha` 与 `/home/ubuntu/alphatrade` 至各自 `test/release-2b-202611xx/backup/`；
   - 同步代码、Linux 编译 4 个 C 扩展、跑通 Ubuntu 离线全量回归（0 失败，且未改动消费者 `upgrade233/alpha311` 依赖）；
   - **全部 7 项门禁必须 100% 达标后，才评估正式机发布**：
     1. **24h 稳定性**：连续 24 小时零崩溃、无 WS 死锁、无 `.gap` 无限重试、无 429/403 资金费告警；
     2. **生产者提速**：发 `.ready` 端到端耗时从 `~114–116 s` 降至 `~85–95 s`（采集+重采样 `~6–10 s`）；
     3. **下游消除伪修订**：测试机 `25m-eart1106` 24 轮自然 `dry_run` 中，非真实资金费修正轮均稳定走 `mode=incremental`（`replay` 接近 0），`validate_snapshot` PASS；
     4. **重启零重建演练**：人为重启测试机 `v2.0.2` 生产者，确认冷启 `< 2 min`、当年 Pivot 保留历史列、下游 **0 全量重建**且 `动量V2` 无漂移报错；
     5. **自愈与计划内裁头演练（隔离 `test/` 验证）**：① 模拟缺失某 offset 或单币 1h，确认自动补拉 5m 重采样并裁回 30 天；② 在隔离目录验证设置 `history_trim_date` 后 `alphaonline` 正确返回退出码 `75` 并用滚动 S3 起点完成初始化；
     6. **资源与磁盘水位**：`base_5m` 降至 `~1 GiB` 且恒定、`flat pkl` `< 50 MiB`、`ws_cache` `< 30 MiB`；常驻内存 `< 500 MiB`，预处理单进程峰值 `< 2.5 GiB`，整机 `MemAvailable > 2.5 GiB`，无显著 swap；
     7. **跨机一致对拍**：与正式机 `alphaprod` 同切点多轮对拍 `S1/S3/S5/S12/S13/S14` 选币及仓位比例一致。

### 2026-12-16 时间兜底预案
若子项目 2B 未能在 `2026-12-16` 前完成正式机上线，仅需在 `2026-12-14` 前临时将正式机 `/home/ubuntu/alpha2/config.json` 的 `kline_count_1h_spot` / `kline_count_1h_swap` 从 `15000` 上调至 `16000` 过渡即可，不影响 2B 节奏。
