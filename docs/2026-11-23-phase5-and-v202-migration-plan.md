# Phase 5 交易端低并发优化与生产端 `v2.0.2` 底座升级规划

> **所属体系**：[统一开发入口](README.md) · [路线图与待办](kb/roadmap-and-backlog.md) · [性能分析](kb/performance-analysis.md) · [开发规范与数据契约](kb/standards-and-contracts.md)
> **制定日期**：`2026-11-23`
> **基准版本**：`D:/alphaonline` 本地 `main=06c8b15`（Phase 4 已通过测试机 23 轮自然轮验收，待发正式机；正式机运行 `940c487`）、`D:/alpha` 本地 `main=7c10b63`（P4-4 内存传 Pivot，待发测试/正式；正式机运行 `41686c3`）；上游新底座参考 `D:/alpha/coin-realtime-data_v2.0.2`（只读参考，未合入）。
> **实施状态**：本文档记录已核实的实测诊断、架构评估与分阶段规划；**项目 1** 为近期低风险止血与发布计划，**项目 2（Phase 5 + `v2.0.2` 底座升级）** 待人工审核后从新对话按阶段执行。

---

## 0. 实测诊断与架构评估沉淀

### 0.1 测试机 Phase 4 实时 CPU / 内存 / 耗时剖面（`43.173.122.28`，4c8g）

硬件：4 vCPU Xeon E5-26xx v4 / `7.5 GiB` RAM / `19–20 GiB` Swap；空闲基线 `MemAvailable ≈ 6.61 GiB`（常驻进程合计 `~1.07 GiB`，含 `realtime_data.py ~212 MiB`）。采集自 `2026-11-22 23:25` 自然轮（`25m-eart1106`，`stamp=1795361100`）高频只读采样及 23 轮自然运行遥测：

| 环节（按执行顺序） | 耗时（典型/中位） | 并发度 & CPU 利用 | 进程树 RSS（`ps` 累计） | 真实物理内存净增（最低 `MemAvailable`） | Swap 增量 | 定位与现状 |
| --- | --- | --- | --- | --- | --- | --- |
| **0. 上游预处理** (`realtime_data.py`) | `~114–116 s`（采集 `24–31 s` + 预处理 `75–85 s`） | 1 主 + 1–2 子进程，单核峰值 `~102%` | **`4.54 GiB`**（单子进程） | **`~4.3 GiB`**（余 `2.3 GiB`） | 低 | **整机最高内存峰值点**；与交易端重叠时诱发 swap，拖慢 `FACTOR_BLOCKS` `+9–10 s` |
| **1. `preflight` 修订预检** | 普通 `~10–15 s`；`replay` **`~27 s`** | 单核串行 | `~0.8–1.5 GiB` | `~1.0–1.5 GiB` | `0` | **端到端第二大耗时项**（`input_read_s 9–14 s` + `copy_s ~7.7 s` + `restore_s ~7.8 s`） |
| **2. `FACTOR_BLOCKS`** | **`41.9 s`** | 1 主 + 4 worker，**4 核吃满 `317%–415%`** | `2.00–2.95 GiB` | `~1.70 GiB`（余 `4.91 GiB`） | `0` | 高饱和段，Phase 4 已降 ~7–9 s |
| **3. `sections`**（截面 + S3 续算） | **`13.6 s`** | 1 主 + 2 worker，**仅 `~1.1–1.2 核`** | `1.25–2.35 GiB` | `~1.16 GiB`（余 `5.45 GiB`） | `1 页` | **低并发段**：外层按截面族完全串行，闲置 ~2.8 核，内存极宽松 |
| **4. `selection` + 权重聚合** | `~5.4 s`（选币 `3.5 s` + 聚合 `1.9 s`） | 1 主 + 4 worker，峰值 `~3.0 核` | `1.70–2.85 GiB` | `~1.32 GiB`（余 `5.29 GiB`） | `0` | 健康 |
| **5. `Step 6`**（14 子策略模拟） | **`8.6–10.4 s`**（预读 `2.6–3.2 s`） | 1 主 + 4 `fork` CoW worker，`~2.7 核` | `4.79–4.97 GiB`（含 4 份 CoW 重复计费） | `~2.05 GiB`（余 `4.57 GiB`） | `0` | P3-1 CoW 共享健康 |
| **6. `MODEL` 仓管** | **`6.8–7.1 s`**（已腰斩） | 单主进程，1 核串行 | **`2.67 GiB`** | **`~3.48 GiB`**（最低余 `3.13 GiB`） | `~809 页`（`~3.2 MiB`） | **交易端真实物理内存峰值点**，100% 复用 Step 6 Pivot，暂不盲目并行 |
| **7. 仓位聚合 + 仓位限制 + 目标仓位** | **`~8–9 s`**（仓位限制段 **`5.9–6.7 s`**） | 单主进程，1 核串行 | `1.64–1.86 GiB` | `~1.8–2.7 GiB`（余 `3.93–4.85 GiB`） | `~251 页` | **高性价比清理项**：`agg_pos_ratio` + `redistribute_excess_weight` 存在明显 pandas 逐列碎片化反模式 |

### 0.2 关键根因发现：现行纯 REST 准点抓取导致每小时伪修订 `replay`

- **实测事实**：测试机近期 **100% 每小时触发 2h `replay`**，正式机 `alphaprod` 最近亦有 **4/7（57%）** 轮触发 `replay`。
- **日志铁证**：`MODEL_REPLAY` 的 `revision_detected` 显示，`revised_from` 几乎全部精准等于**上一小时最后一根闭合 bar**，变更字段为 `['close', 'high'/'low', 'volume', 'quote_volume', 'trade_num']`。
- **根因**：现行 `D:/alpha/realtime_data.py:72` 在 `run_time`（`XX:25:00.000`）毫秒准点立即并发扫全市场币安 REST `/klines`，拉到了交易所撮合引擎尚未定稿的最后一根 5m K 线；下一小时再拉时数值定稿变化，迫使下游 `preflight` 白白多花 **`copy_s ~7.7 s + restore_s ~7.8 s = ~15.5 s`** 做状态回滚重放。

### 0.3 `alpha` 与 `alphaonline` 保持双程序解耦的架构裁决

**结论：继续保持 `alpha`（行情生产者）与 `alphaonline`（交易消费者）双进程解耦，不合并为单体程序。**

1. **1 对 N 扇出拓扑**：正式机 `alphaprod` 上由 1 个生产者 `alpha2` 服务 6 个错峰账户（`5m/15m/25m/35m/45m/55m`），旁边并存独立旧实例 `alphatradetest`（5 账户）。合并会导致 6 户互相牵连或采集/内存乘 6。
2. **生命周期与内存回收特征相反**：`alpha` 是长驻 IO 服务（正式机已连续稳定运行 16 天）；`alphaonline` 是短命批处理子进程（跑完 ~90 秒退出，OS 瞬间 100% 回收 ~3.5 GiB 内存，避免 Python/glibc arena 碎片常驻）。合并为常驻单体后，4c8g 上更容易把堆碎片吊在 4–5 GiB 诱发 OOM。
3. **发布、依赖与安全隔离**：交易端高频迭代并依赖独立 `upgrade233/alpha311`（`xbx-py11==0.1.24 / ccxt==4.5.88`），不中断生产者；生产者不接触交易 API Key。
4. **替代“合并”的更优工程解法**：
   - 跨进程零拷贝：Market Dict 已采用 **Arrow IPC `memory_map`**（共享 Linux page cache）；Pivot 已在消费端实现 Step 6→MODEL 复用。
   - 消除 `4.54 GiB + 3.48 GiB` 重叠：压低上游 `preprocess` 峰值（`spot`/`swap` 生命周期拆分 + `gc.collect() + malloc_trim(0)`，目标 `< 2.5 GiB`）+ 非紧急后台任务错峰避让活跃 `account_exec`。

### 0.4 资金费逻辑三版本对照（`v2.0.2` vs `v1.1.12` vs 现行 `D:/alpha`）

`v2.0.0–2.0.2` 分叉自 **`v1.1.11`**，尚未合入 **`v1.1.12`** 才新增的运行期 24h 已结算历史资金费持续回补：

| 阶段 / 机制 | `v2.0.2`（分叉自 `1.1.11`） | `v1.1.12` 原版 | 现行 `D:/alpha`（基于 `1.1.12` 强化） |
| --- | --- | --- | --- |
| **启动期全历史资金费** | ✅ 有（`history_data/funding.py`，启动一次性分页拉全历史；重启往回退 8h 补拉；修复 OKX 分页漏记录与交割合约 `1970-01-01` 脏行） | ✅ 有（`history_data/funding.py`） | ✅ 有 + 启动后 `defer_funding_backfill()` 5 分钟冷却防限流 |
| **运行期实时资金费** | 每轮拉 `/fapi/v1/premiumIndex` 预估值直接落盘 | 仅保留 `next_funding_time >= run_time` 的未来预估值 | 时间戳 `floor('h')` 对齐 UTC 小时桶，仅写 `>= run_time` 未来预估值 |
| **运行期 24h 已结算历史回补** | **❌ 无**（长跑不重启时永远停留在结算前预估快照，且网络抖动会漏期） | ✅ 有（`funding_backfill.py`，8s 预算 + 300s/450 次限流，50 并发） | ✅ 有且更严谨（并发 4 防 429 + 同小时多笔求和 + 原子写盘 + **`FundingPublication` 实时回填进增量 batch PKL / Arrow / 年度 Pivot**） |

> **关键结论**：迁移 `v2.0.2` 时**必须**把现行 `funding_backfill.py` + `funding_publication.py` 一并迁过去，否则会破坏 `S13-Funding0021NegativeDomain` 与 `MODEL` 模拟账本精度。

---

## 项目 1（近期优先）：现行 `alpha` REST 延后 2~3 秒过渡实验 → 合并主干并发布测试机与正式机

> **定位与价值**：在暂不引入 OKX、也不触碰 `v2.0.2` 两大暗雷（`flat pkl` 磁盘炸弹 + 10 常驻进程内存膨胀）的前提下，用极小、零架构风险的改动，先在现行 `D:/alpha` 上消除币安准点未定稿 5m K 线引发的每小时伪修订 `replay`（用 `+2–3 s` 等待换回交易端省去 `~15.5 s` 的 `copy + restore`，净赚 `~12–13 s`），随后与已验收的 Phase 4（`alphaonline 06c8b15` + `alpha 7c10b63`）一起按规范合并主干并发布至测试机与正式机。

### 1.1 代码修改点（`D:/alpha`）
- **目标文件**：`D:/alpha/realtime_data.py`（L68–L75）及对应配置/单元测试。
- **改动方案**：
  - 新增可配置常量/参数（如 `rest_fetch_delay_seconds = 2.5` 或 `3.0`，默认 `2.5`–`3.0` 秒，支持配置关闭设为 `0`）。
  - 在 `update_all()` 中，保留 `run_time` 作为本轮逻辑时间戳不变（不改变 K 线边界、`.ready` 文件名、offset 判断与下游契约），仅将 `sleep_until_run_time(run_time)` 及采集 worker 预热唤醒时刻改为 `run_time + timedelta(seconds=delay)`，使币安 REST `/klines` 在跨过 5m 边界 2–3 秒后再发起抓取。
  - 注意：仅影响 `update_all` 实时增量抓取，不影响 `init_history` 与 `update_binance_meta`（仍提前 15s 刷新 exginfo 与资金费）。

### 1.2 测试机验证流程（`alphatest` `43.173.122.28`）
1. 本地 `alpha311` 编写单元测试验证 `run_time` 契约不变、仅唤醒时刻延后 `delay` 秒，全量 `D:/alpha` 回归测试通过。
2. 备份测试机 `/home/ubuntu/alpha/realtime_data.py`（至 `test/`），同步延后 2–3 秒改动 + `7c10b63`（P4-4 内存传 Pivot），在空闲窗口重启测试机 `realtime_data`（待授权）。
3. 连续观察测试机 **≥ 4–6 轮自然 `dry_run`**，核对以下验收指标：
   - **`MODEL_REPLAY` 模式分布**：`mode=replay` 占比是否从 `100%` 显著降至接近 `0%`（仅保留真实资金费修正或偶发真修订）；
   - **`changed_fields` 检查**：上一小时最后一根 bar 的 `close/volume/quote_volume/trade_num` 伪修订是否消失；
   - **端到端耗时对比**：`preflight` 是否免去 `copy_s ~7.7 s + restore_s ~7.8 s`，`total_s` 是否从 `~128 s` 降至 `~110–115 s`；
   - **产出一对一对拍**：与正式机同切点 `S1/S3/S12/S14` 选币结果保持 100% 一致。

### 1.3 合并主干与正式机发布流程（需明确授权后执行）
1. **合并主干**：测试机验证通过后，提交 `D:/alpha` 改动并合入 `main`，整理 `D:/alphaonline` 文档提交，推送至正式机私有裸仓 `/home/ubuntu/repos/{alphatrade,alpha}.git`（`fast-forward`）。
2. **正式机 `alphaprod`（`43.167.235.164`）空闲窗口发布**：
   - 先备份受影响文件至各自 `test/release-phase4-202611xx/backup/`；
   - 发布 `alphaonline`（Phase 4 `06c8b15`）：`git merge --ff-only origin/main` → 离线预热 Numba → 运行 `tools/run_deployment_tests.py` 隔离验证（**不重启 `startup.py` PM2**，下一整点自然生效）；
   - 发布 `alpha2`（`7c10b63` + REST 延后 2–3 秒）：按需在非活跃窗口平滑重启 `alpha2`（不触碰并行旧实例 `alpha` / `alphatradetest`）；
   - 跟踪首轮及连续 6 个错峰账户的自然实盘日志，确认 `status="ok"`、`committed=true`、`复用Step 6预加载市场Pivot` 命中、伪修订 `replay` 显著下降，并更新 [docs/DEVELOPMENT.md](DEVELOPMENT.md)。

---

## 项目 2（人工审核后，从新对话按阶段执行）：Phase 5 交易端低并发优化 + 生产端 `v2.0.2` 底座升级

> **执行边界**：项目 2 分为 **2A（中短期，交易端低并发优化）** 与 **2B（中期，生产端 `v2.0.2` 底座升级）** 两个独立阶段，均需在新对话中按 TDD + 独立 Python Oracle + 测试机长周期验证流程推进。

---

### 子项目 2A（中短期）：Phase 5 交易端低并发优化（测试机首轮自然轮已验收，`feat/phase5-2a-low-concurrency` 暂保留未合 `main`）

> **实施与验收更新（2026-11-23 `14:50 CST`）**：
> - **当前状态**：已在 `D:/alphaonline` 分支 `feat/phase5-2a-low-concurrency`（`52e5c86..2001075`，7 提交）完成实现与测试机 `alphatest`（`43.173.122.28`）部署，**未合入 `main`、未触碰生产机 `alphaprod`**。本地 16 文件回归 **249/249 通过**，测试机专项 **20/20 通过**（全套 248/249，唯一失败系非 git 部署目录缺 `examples/` 夹具），真数只读对拍 `P5-D1`/`P5-A1` 均 `check_exact=True`。
> - **测试机首轮自然轮实测验收（`14:25 CST` / `05:25 UTC`，`25m-eart1106`，`mode=replay`、`changed_series=13`，对比当日升级前 14 轮中位数）**：
>   - **`P5-D1`（`D1-A`，`agg_pos_ratio` 向量化）**：`完成仓位限制` **`6.11 s → 3.16 s`**（`-2.95 s / -48.3%`，扣除 `calc_ratios` `0.79 s` 后净耗 `5.29 s → 2.37 s`），刷新当日最低；保留 `columns.name='symbol'`（`2001075`），不动 `ModelRobust40Strategy.py`。
>   - **`P5-C1`（`C1-A`，`sections` 3 线程 + 实盘 Polars 放宽至 `budget`）**：`initialization.sections` **`14.51 s → 9.95 s`**（`-4.56 s / -31.4%`，较 `13:25` `15.06 s` 降 `5.11 s`），刷新当日最低；后台初始化（`compute_gate` 非空）维持串行 + `min(2, budget)`；不做 `P5-C2`。
>   - **`P5-A1`（`preflight` 三路并行 + 多精度整型二分）**：`input_read_s` **`8.27 s → 6.12 s`**（`-2.15 s / -26.0%`，三路 `spot 6.12 / swap 2.39 / pivot 1.62`，并发重叠省 **`4.00 s`**），刷新当日最低；零开销免疫 2B WS `ms` 精度时间列。
>   - **端到端**：`account_calculation_s` **`97.84 s → 86.58 s`**（`-11.26 s`）、`total_s` **`130.22 s → 116.10 s`**（`-14.12 s / -10.8%`），均刷新当日最低；`swap_out_pages=0`、`status="ok"`、`validate_snapshot` PASS、产物完整。
> - **范围收口裁决（`P5-B1` / `P5-B2` 推后为独立需求）**：首轮实测 `restore_s` 为 `8.43 s`（基线中位 `7.83 s`），现场拆解确认为 `model/state.pkl`（`449.2 MiB`）+ `sections/*.pkl`（`130.7 MiB`）串行读写及 14 子目录 pickle GIL/写盘带宽竞争所致。经用户确认，**`P5-B1` 与 `P5-B2` 整体推后并拆分为独立需求（[roadmap-and-backlog.md](kb/roadmap-and-backlog.md) `O8`），本轮不再继续关注**（分支内 8 行 `4c532bb` 正确无损，合主干前可按需决定保留或回退）。

基于 §0.1 实时剖面，各优化项定位与最新落地状态如下：

| 优先级 | 编号 | 优化内容与代码定位 | 预估 / 实测收益 | 内存与风险评估 | 状态与验收口径 |
| --- | --- | --- | --- | --- | --- |
| **P1** | **P5-D1** | **仓位聚合 `agg_pos_ratio` 向量化（`D1-A`）**<br>• [core/model/backtest_config.py](file:///d:/alphaonline/core/model/backtest_config.py) `_agg_side_ratios`：求 14 子策略列并集，列连续 `(n_cols, n_rows)` `float64` 单块累加，替代 14 次 `DataFrame.add(fill_value=0)`；`ModelRobust40Strategy` 禁止 `symbol_ratio_limit`，`redistribute_excess_weight` 不可达故不改动。 | 预估 `~5–6 s`<br>**实测**：本地合成 `5.35→1.17 s`；测试机真数 `1.91→0.79 s`；自然轮 `完成仓位限制` **`6.11→3.16 s`（净降 `2.95 s`）** | **风险极低**；无并发复杂度，峰值内存下降 | **【已完成 · 测试机首轮验收通过】**<br>独立旧版 oracle `check_exact=True`，覆盖 NaN/0 列侧/缺索引 `KeyError`/`columns.name='symbol'` |
| **P2** | **P5-C1** | **`sections` 跨截面族 3 线程并行 + 实盘 Polars 放宽（`C1-A`）**<br>• [core/select_coin.py](file:///d:/alphaonline/core/select_coin.py) `_calc_section_blocks` + [core/utils/section_state.py](file:///d:/alphaonline/core/utils/section_state.py) + [core/backtest.py](file:///d:/alphaonline/core/backtest.py)。<br>• 实盘（`compute_gate is None`）3 线程并行计算 `signal_block` 截面族并放宽 `_polars_threads=budget`，主线程按声明顺序提交与原子落盘；初始化保持串行 + `min(2, budget)`。 | 预估 `~7–8 s`<br>**实测**：自然轮 `initialization.sections` **`14.51→9.95 s`（`-4.56 s / -31.4%`，较 `13:25` `-5.11 s`）** | **风险低**；该阶段 `MemAvailable` 充裕，实测 `swap_out_pages=0` | **【已完成 · 测试机首轮验收通过】**<br>串行 vs 并行 `check_exact=True`、异常原子性无残留、`validate_snapshot` PASS |
| **P3** | **P5-A1** | **`preflight` 三路并行读取 + 多精度整型时间二分**<br>• [tools/robust40/handoff.py](file:///d:/alphaonline/tools/robust40/handoff.py) `read_inputs` & `read_arrow_tail`。<br>• `spot` / `swap` / `pivot` 三路并行且主线程汇总遥测；循环外按 Arrow `TimestampType.unit` 做精确整型缩放 + 零拷贝二分，保留原生 `datetime64[{unit}]` 并零开销免疫 2B WS `ms` 精度。 | 预估 `~5–8 s`<br>**实测**：自然轮 `input_read_s` **`8.27→6.12 s`（`-2.15 s`；三路并发重叠省 `4.00 s`）** | **风险低**；内存无明显增加，完全保留 `first_revision` 与遥测字段契约 | **【已完成 · 测试机首轮验收通过】**<br>真实 1,294 帧 100% `check_exact=True`、`ns/ms/us/s` + 时区/空值回归全过 |
| **P4** | **P5-B1 + B2** | **`replay` 硬链接 CoW 克隆 + 并行 `restore_child`**<br>• [tools/robust40/handoff.py](file:///d:/alphaonline/tools/robust40/handoff.py)。<br>• `B1` 硬链接 CoW 暂缓；`B2` 4 线程并行 `restore_child`（`4c532bb`）在 `14:25` 首轮实测 `8.43 s`（基线 `7.83 s`），因 `model/state.pkl`（`449 MiB`）+ `sections`（`131 MiB`）仍串行且子目录 pickle 受 GIL/写盘带宽竞争未见净加速。 | 预估 `~11–13 s`<br>**实测**：`B2` 单改无净加速（`7.83→8.43 s`） | `B1` 涉及长期防原地写红线；本轮不再投入 | **【已推后为独立需求 `O8`，本轮不再关注】**<br>后续视 `v2.0.2` WS 上线后真 `replay` 频率再单独立项评估 |

> **子项目 2A 实际兑现**：测试机 `14:25 CST` 首轮 `account_calculation_s` 从 `97.84 s`（中位）降至 **`86.58 s`（`-11.26 s`）**，2h `replay` 轮端到端 `total_s` 从 `130.22 s` 降至 **`116.10 s`（`-14.12 s`）**；后续普通 `incremental` 轮（无 `copy+restore ~15 s`）预计端到端可落至 **`~100 s`** 区间。

---

### 子项目 2B（中期）：生产端 `v2.0.2` 底座升级 + 5m/1h 解耦与重启零重建（阶段 2B-1 & 2B-2 已离线落地，待授权启动 2B-3）

> **实施与离线验证更新（2026-11-23 `21:00 CST`）**：
> - **当前状态**：阶段 **2B-1 & 2B-2** 已按 [2B 设计文档](superpowers/specs/2026-11-23-subproject-2b-v202-migration-design.md) 与 [2B 实施计划](superpowers/plans/2026-11-23-subproject-2b-v202-migration.md) 在两仓隔离分支离线落地（**均未合入 `main`、未部署/重启测试机或正式机**）：
>   - `D:/alpha` `feat/v202-migration-2b`（`f3d70ff..f97005a`，13 提交）：拆两暗雷 + 5m/1h 解耦与自愈 + 非破坏性 `init_dirs()` + 显式 `apply_explicit_history_trim()` + 本地 `preprocess`/C 扩展/Arrow `history_trim_date` metadata/Pivot 保列/`funding_backfill` + `spot→swap` 降峰值 + WS 非阻塞周期 + 跨仓端到端契约测试 `tests/test_2b_cross_repo_contract.py`（已入 `tools/deploy_alpha.sh::verify()`）。
>   - `D:/alphaonline` `feat/phase5-2b-consumer-hardening`（`35a8cb1..6cd263b`，3 提交）：`pivot removed_columns` 零持仓放宽与熔断、PyArrow 扫描最早 K 线与滚动 S3 起点（`≥ 365d` 守卫）、上游 Arrow `history_trim_date` 退出码 `75` 重建联动；`positions/ModelRobust40Strategy.py` 与 `accounts/` `git diff` 严格为空。
> - **离线验证结论**：`D:/alpha` 全量回归 **`397 passed, 0 failed`**（`50.88 s`）；`D:/alphaonline` 2B + 核心消费端回归 **`192 passed, 0 failed, 2 skipped`**（`56.63 s`），跨仓 shim 关联 4 文件 **`33 passed, 0 failed`**。
> - **下一步**：待用户明确授权后，按下方 §阶段 2B-3 放行门禁启动测试机 `alphatest` 24 小时自然轮验收。

> **迁移方向与精简裁决（2026-11-23 更新）**：
> 1. **底座方向**：**以 `v2.0.2` 为新底座，将本地 `D:/alpha` 定制模块平移到新分支**（不把 `v2.0.2` 反向塞回 `1.1.12`）。开启 `use_websocket=True` 后，WS 协议自带 `k["x"]==True` 闭合标志 + 内置 `GRACE_WAIT_SEC=2s` + `barrier` 轮询 `.ws_ready` + 10s 后 REST 兜底，**不再需要项目 1 的 3.0 秒人工等待**（仅 `use_websocket=False` 退化模式保留）。
> 2. **精简融合（取消重型前置阶段，全部收敛到 `D:/alpha` 一端）**：
>    - 2026-11-23 正式机重启 `alpha2` 暴露了两个问题：① 当年 Pivot 重建删掉已下架币 `ANTHROPICUSDT` 列（`removed_columns: 4`）触发 6 户串行重建；② `kline_count_1h=14060` 随日期推移晚于 S3 固定起点 `2025-03-31`（临时调至 `15000` 后将于 **`2026-12-16`** 再次撞线）。
>    - 无需改造消费端 `alphaonline` 引入复杂的 `baseline_anchor` 或稀疏 Checkpoint（保留为独立选做项 `F1b`）；只需在 **`2B-1`** 将 5m（30天滚动恒定）与 1h（按 `min_history_start_date='2025-03-25'` 自动兜底）解耦，并在 **`2B-2`** 平移 `preprocess.py` 时顺手加 ~10 行**年度 Pivot append-only 保列**，即可零消费端改动解决核心痛点。
>    - **时间兜底提醒**：若 `2B` 未能在 **`2026-12-16`** 前上线正式机，届时只需提前把正式机 `alpha2/config.json` 的 `15000` 临时上调一次过渡即可。

#### 阶段 2B-1：拆除 `v2.0.2` 两大源码暗雷 + 5m/1h 存储解耦（可控磁盘、永不撞线）
1. **暗雷一（磁盘/I/O 炸弹）与 5m/1h 存储解耦合一改造**：
   - 位置：`D:/alpha/coin-realtime-data_v2.0.2/core/storage/writer.py:396-429`、`core/handle.py:504`、`core/pipeline/resample.py`。
   - 风险：原版 `Writer` 每 5 分钟既写 `TSManager` 月分区，又全量读写按 `num_base_candles`（`12 × 15000 = 180,000` 行）裁剪的 `flat pkl`，满窗口将额外吞噬 **`~15+ GiB` 磁盘**（威胁正式机剩余 `25–28 GiB` 根分区）并冲刷页缓存；且 5m 原始历史随 `kline_count_1h` 无限膨胀。
   - **必改方案（三层存储解耦）**：
     - **`flat pkl`（WS/REST 合并缓冲）**：独立裁剪上限为小滚动窗（如 `max(once_update_candles * 2, 240)` 根 5m K 线，仅最近 ~20 小时）。
     - **`base_5m`（`TSManager` 原始 5m 月分区）**：**固定只滚动保留最近 `30 天`（`720h` / `8640` 根 5m）**，自动裁剪超过 30 天的旧月分区——体积永久恒定在 **~2–3 GiB**（砍掉目前 90% 的无用 5m 历史）。
     - **`resample_1h` & `batch PKL`**：新增配置 **`min_history_start_date = '2025-03-25'`** 自动兜底（`effective_kline_count_1h = max(configured, ceil((now - min_history_start_date)/1h) + 48)`），**永不撞穿 S3 固定起点 `2025-03-31`**；因 1h 体积仅 5m 的 `1/12`，多保留一整年全市场 1h 也仅多占 **~1–2 GiB**，无需人工清理；且 `alpha` 重启时**不再清空已有 `resample_1h`**，仅对最近 30 天增量补齐校验（冷启从 ~15 分钟压至 **`< 2 分钟`**）。
2. **暗雷二（4c8g 常驻进程内存膨胀）**：
   - 位置：`core/pipeline/runtime_supervisor.py:219, 279-306` & `core/exchange/protocol.py:54`。
   - 风险：默认 `NUM_WRITERS=4` + 每 200 stream 开一个 `ws_runner` `spawn` 进程（Binance 现货 ~400 + 合约 ~600 = 5–6 个 `ws_runner`）+ 主进程 = **10–11 个常驻 Python 进程**，光常驻就吃 **`~0.9–1.4 GiB` RAM**（现行仅 `212 MiB`）。
   - **必改方案**：4c8g 上将 `NUM_WRITERS` 调为 `2`，并将同市场多个 WS 连接收拢至单进程多 `asyncio` 协程内（只留 `spot` / `swap` 2 个 `ws_runner`），将常驻内存压回 **`~350–450 MiB`**。

#### 阶段 2B-2：本地定制平移 + Pivot 保列防重建 + 内存峰值治理
1. **替换 `core/strategy/preprocess.py`**：
   - 完整迁入本地增量 `gen_batch_data_incremental`、4 个 C 扩展预计算因子（`动量V2` / `ILLQ` / `CciV3` / 旧动量，Linux 独立编译且 GCC 关闭 FMA contraction）、4 线程 `gen_factor_data`、`arrow_publish.py` + `arrow_demand.py`（5000 / 全量按需）、以及 `P4-4` 内存传 Pivot（消除临时 `*_for_pivot_batch*_{year}.pkl` 死 I/O）。
   - **顺手加入年度 Pivot append-only 保列合并（~10 行）**：重建当年 `market_pivot_{spot,swap}_{year}.pkl` 时，若旧 Pivot 文件存在，保留其中已下架币种的历史列（新时间戳填 `NaN`），绝不因重启抹除当年历史列，从源头杜绝下游因 `removed_columns` 触发 6 户全量重建。
2. **迁入 4 项核心治理模块**：
   - `candle_origins.py`（维持 `first_candle_time`，防止重启/WS 补齐后触发下游 168h 伪修订）；
   - **`funding_backfill.py` + `funding_hours.py` + `funding_publication.py`**（补齐 `v2.0.2` 缺失的运行期 24h 已结算历史资金费持续回补及增量 batch/Pivot 实时回填，保护 `S13` 与 `MODEL`）；
   - `publication_sources.py` + `backfill.py`（Binance 优先来源锁定 + 空闲 offset 历史补算）；
   - `coin_cap_use_proxy` 开关（适配测试机 `QUANT` 代理分流）。
3. **顺带治理上游预处理 `4.54 GiB` 内存峰值**：在 `preprocess_offset_data` 内拆分 `spot` 与 `swap` 生命周期（`spot` 写完 Arrow/Pivot 后立即 `del + gc.collect() + malloc_trim(0)` 再算 `swap`），目标峰值压至 `< 2.5 GiB`。

#### 阶段 2B-3：双端联动检查点与验收门禁（测试机 24 小时自然轮）
- **契约核对**：
  1. 严格保持原子顺序：**全历史算完 C 因子 → 原子写 `{spot,swap}_dict_{ts}.arrow` → 生成 `market_pivot_*`（append-only 保列）→ 最后写 `{spot,swap}_dict_{ts}.ready`**；
  2. 依赖隔离：`v2.0.2` 新依赖（如 `websockets`）仅安装在生产者环境 `/home/ubuntu/miniconda3/envs/alpha311` 并通过 `pip check`，绝不污染消费者 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`；
  3. 数据目录与 `bmac_data_path` 保持向后兼容，且 `P5-A1` 已原生兼容 `v2.0.2` WS 的 `ms` 精度时间戳。
- **测试机 `alphatest` 24 小时自然轮放行门禁（全部达标后方评估切正式机）**：
  1. 连续 24 小时零崩溃、无 WS 死锁、无 `.gap` 无限重试告警；
  2. 生产者发 `.ready` 耗时从 `~114–116 s` 降至 `~85–95 s`（采集 + 重采样 `~6–10 s`）；
  3. 下游 `25m-eart1106` 24 轮自然 `dry_run` 中，非资金费修正轮均稳定走 `mode=incremental`；
  4. **重启零重建演练通过**：在测试机人为重启 `v2.0.2` 生产者，确认冷启 `< 2 min`、当年 Pivot 保留已下架历史列、下游 **0 全量重建**且 `resample_1h` 覆盖 `<= 2025-03-25`；
  5. **资源水位达标**：`flat pkl` + `base_5m`（30 天）恒定不膨胀；常驻内存 `< 500 MiB`，整机运行期 `MemAvailable > 2.5 GiB`，无显著 swap；
  6. 与正式机 `alphaprod` 同切点多轮对拍 `S1/S3/S12/S13/S14` 选币及仓位比例一致。
