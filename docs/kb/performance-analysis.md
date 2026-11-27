# 重要性能分析、阶段剖面与已落地收益

> 所属体系：[统一开发入口](../README.md) · 所有耗时均保留实测环境与口径，**禁止**将并行累计 CPU 秒当墙钟、将微基准线性外推整轮或将嵌套阶段相加。
> 后续优化规划与进度跟进：[2026-11-19 Ubuntu 4c8g 性能深度优化规划与跟进](../2026-11-19-performance-optimization-plan.md)（Phase 0–4） · [2026-11-23 Phase 5 低并发优化与生产端 `v2.0.2` 底座升级规划](../2026-11-23-phase5-and-v202-migration-plan.md)（Phase 5 2A & 2B）。

---

## 1. 普通 4050 续算全流程耗时剖面（Ubuntu 4c8g）

### 1.0 正式服务器 Phase 0–3 上线后稳态耗时（`43.167.235.164`，2026-11-20 `04:15–05:55 UTC` 10 轮采样，`8406c3f` / `940c487`）

| 阶段 / 指标 | 普通 `incremental` 轮（稳态） | 2h `replay` 轮（稳态） | 备注 |
| --- | --- | --- | --- |
| `FACTOR_BLOCKS`（时序因子 + 写块） | `40.2–42.4 s` | `40.2–42.4 s` | 较 Phase 1 前 `~63.4 s` 下降 `~34%` |
| `sections`（8 个截面因子 + S3 续算） | `20.8–22.0 s` | `20.8–22.0 s` | 主体为 S3 3.68M 行 pandas `MultiIndex` 续算（WP2 目标） |
| `selection`（14 策略流式选币） | `2.8–3.2 s` | `2.8–3.2 s` | 已充分优化 |
| `Step 6`（14 子策略模拟，含 `fork` 预读） | `11.0–13.5 s` | `11.0–13.5 s` | 预读 `4.1–4.9 s`，16/16 `reused_market=True` |
| `MODEL`（读态/读 Pivot/`merge_context`/续算/写态） | `14.8–16.5 s` | `14.8–16.5 s` | 含重复读 Pivot `~3.5–4.0 s` + `merge_context ~4.1–4.7 s`（WP3 目标） |
| `account_calculation_s`（主交易计算总计） | **`107.1–108.5 s`** | **`107.1–108.5 s`** | 较上线前 `138.4–141.7 s` 净省 `~31 s` |
| `total_s`（控制器全流程） | **`133.7–143.7 s`** | **`149.0–154.1 s`** | `replay` 含 `finish_live` 重读 `state.pkl`（`+4.1 s`） |

### 1.1 正式服务器历史基线耗时（`43.167.235.164`，2026-11-19 Phase 1–3 上线前 `25m-eart1106`）

| 阶段 / 指标 | 普通 `incremental` 轮（13:29–19:29） | 20:25 部署 `edbbba3` 首轮（含一次性 Numba 重编译） | 2h `replay` 轮（14:29 / 16:30） |
| --- | --- | --- | --- |
| `old_read_s`（读修订基线） | `0.39–0.48 s` | `0.34 s` | `0.40–0.49 s` |
| `input_read_s`（Arrow + Pivot 修订读取） | `4.90–8.27 s`（5000 根联动生效后，较原全量 `18.3s` 明显下降） | `5.95 s` | `5.45–5.74 s` |
| `compare_s`（`first_revision` 168h 逐值比对） | `0.75–0.96 s` | `0.945 s`（新逻辑零回归） | `0.85–0.97 s` |
| `prepare_s`（修订预检总计） | `6.73–9.90 s` | `7.74 s` | `21.46–22.73 s`（含 `copy_s ~7.6s` + `restore_s ~7.6s`） |
| `account_calculation_s`（主交易计算） | **`138.4–141.7 s`** | `169.83 s`（`+31s` 为 `ModelRobust40Strategy.py` 改动触发的一次性 Numba `.nbc/.nbi` 缓存重建） | `138.8–139.8 s` |
| `total_s`（控制器全流程） | **`166.1–170.4 s`** | `204.33 s` | `180.4–184.4 s` |

### 1.2 历史分阶段拆解参考（2026-11-04 18 轮 Ubuntu MODEL 中位数，P1/D1 上线前基线）

用于识别剩余结构性热点（完整数据见 [2026-11-04-whole-flow-performance-review.md](../2026-11-04-whole-flow-performance-review.md)）：

| 阶段 | 历史中位秒（11-04） | 现行状态与定位 |
| --- | ---: | --- |
| **时序因子阶段** | `93.59 s`（范围 `90–121s`；同机低干扰 `~82s`） | **当前最大单一耗时项**（详见 §3） |
| **截面阶段（共享路径 + S3 状态）** | `22.05 s` | 已分块 + 2 线程默认（4 线程暖态无收益） |
| **修订检查输入读取** | `18.33 s`（全量 Arrow 时期） | 开启 D1 按需 `5000` 根后，2026-11-19 正式实测已降至 **`5–8 s`** |
| **子策略模拟（14 组含 2 再择时）** | `17.74 s` | 已多进程；P2/P3 已消除同再择时 `state0→state1` 重复读盘 |
| **目标持仓计算** | `15.01 s`（其中 `LiveRebRobust40` 重复 JIT 占 `~13s`） | **已由 P1 消除**（新进程命中缓存后降至 `~1.4s` / 纯函数 `~0.4s`） |
| **MODEL 准备/续算/保存** | `10.67 s`（读 `2.82` + 输 `2.09` + 并 `2.20` + Hybrid `0.43` + 数值 `1.55` + 存 `1.57`） | 2026-11-19 实测总计 `~10.6s`（数值仅 `1.58s`），不是主要瓶颈 |
| **选币 / 父权重 / 聚合** | `2.88 s` / `3.42 s` / `1.64 s` | 偶发聚合慢轮（曾达 `26.39s`）与传统 45m 聚合（`22.43s`）待观察 |

---

## 2. 已落地且经验证的有效优化

| 优化项 | 所在侧 | 实测收益（带口径） | 参考文档 |
| --- | --- | --- | --- |
| **动量 V2 C 扩展快速路径** | `alpha` | Windows 合成 14060 根单币调用 `30.32 ms → 2.88 ms`（C 核心 `13.34 → 2.65 ms`） | [DEVELOPMENT.md](../DEVELOPMENT.md#L981-L1015) |
| **局部第 0 代 GC（`temporary_gc`）** | `alphaonline` | 初始化映射全代 GC `13.127 s → 0.0023 s`，映射读转释放 `20.662 s → 8.110 s` | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **MODEL `coin_features` 原生日期快路** | `alphaonline` | 69 币种 ABBA 特征均值 `2.574 s → 1.027 s`（`-60.1%`，针对初始化与跨日，非普通每小时） | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **P1 `LiveRebRobust40` 可缓存 JIT** | `alphaonline` | Windows 新进程冷 `11.148 s →` 缓存命中 `0.401 s`；Ubuntu 实测目标计算由 `~15 s` 降至 `~1.4 s` | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md) |
| **P2/P3 再择时行情与 `state0` 复用** | `alphaonline` | 合成 14050×256 阶段 `0.460 s → 0.334–0.355 s`（行情读盘减半，`RSS +29 MiB`） | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md) |
| **D1 Arrow 按需发布（5000 vs 全量）** | `alpha` + `alphaonline` | 正常轮修订读取由 `~18.3 s`（14060 根）降至正式实测 `5–8 s`（5000 根），且大幅减少网络/磁盘页缓存压力 | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md) |
| **新上币 universe 扩张不再触发全量重建** | `alphaonline` | 每波新上币消除 **6 户 × ~14 分钟（合计 73–97 分钟停单）** 的串行全量初始化 | [plan.md](../../plan.md) |
| **上游 5m 采集提前启动与 4 线程因子池** | `alpha` | Windows spawn 采集晚启动 `3.96 s → 2.3 ms`；360 币增量预处理 4 线程 `22.92 s → 20.35 s` | [2026-10-31-dispatch-prewarm.md](../../../alpha/docs/2026-10-31-dispatch-prewarm.md) |
| **Phase 1 `FACTOR_BLOCKS` 热点核 + 直建 Polars（已上线生产 `8406c3f`）** | `alphaonline` | 测试机（E5 v4）零干扰 1260 币 / 461 万行：`106.6 s → 60.9 s`（`-42.9%`）；正式机（Xeon 8255C）`2026-11-20 14:15` 首轮实盘 369 万行 **`63.4 s → 47.44 s`（`-25.2%`）**，`swap_out_pages=1` | [2026-11-19-performance-optimization-plan.md](../2026-11-19-performance-optimization-plan.md) |
| **Phase 2 初始化 `BURST ↔ YIELD` 动态治理（已上线生产 `8406c3f`）** | `alphaonline` | 同切点 `10:25` 全量初始化：生产基线 `867.7 s`（14.46 min） vs 测试机 **`629.5 s`（10.49 min，`-27.5%`）**，峰值内存 `4545 MiB` 持平、0 OOM；正式机已启用 `dynamic_burst_enabled=True` + `foreground_max_age_seconds=3600`（过滤遗留僵尸 `account_exec`） | [2026-11-19-performance-optimization-plan.md](../2026-11-19-performance-optimization-plan.md) |
| **Phase 3 `P3-1` Step 6 跨 14 子策略只读 Pivot 共享（已上线生产 `8406c3f`）** | `alphaonline` | 测试机 14 子策略 ABBA `16.00 s → 5.38 s`（`-66.4%`，14/14 `check_exact=True`）；正式机 `14:15` 首轮实盘 `STEP6_MARKET_PRELOAD 4.08 s` + `16/16 reused_market=True`（单次 `pivot_read 0.001–0.006 s`），整轮 `account_calculation_s` `138.4–142.0 s → 113.91 s`、`total_s` **`165.0–184.0 s → 133.68 s`** | [2026-11-19-performance-optimization-plan.md](../2026-11-19-performance-optimization-plan.md) |
| **Phase 4 WP1–WP3 + P4-4（已上线正式机 `06c8b15` / `alpha2 b5eb945`，2026-11-23）** | `alphaonline` (`06c8b15`) + `alpha` (`7c10b63`) | 测试机 21 轮普通自然轮中位数：`FACTOR_BLOCKS` `48.0–51.1 s → 41.89 s`、`MODEL merge_context` `3.4–3.8 s → 1.08 s`（`-70%`）、`MODEL` 累计 `14.8–16.5 s → 7.10 s`、`account_calculation_s` **`115.7–121.2 s → 94.78 s`**、`total_s` **`144.3–154.1 s → 128.09 s`**；正式机 `55m` 首轮实盘 `FACTOR_BLOCKS 38.97 s`（`swap=0`）、`sections 15.06 s`、`merge_context 1.10 s`、命中 Step 6 Pivot 复用 | [DEVELOPMENT.md](../DEVELOPMENT.md) |
| **Phase 5 子项目 2A（`P5-A1` + `P5-C1` + `P5-D1`，测试机首轮自然轮验收通过，分支 `feat/phase5-2a-low-concurrency` 暂未合 `main`）** | `alphaonline` (`52e5c86..2001075`) | 测试机 `alphatest` `2026-11-23 14:25 CST` 自然轮（2h `replay`）对比当日升级前 14 轮中位数，3 项全部刷新当日最低：`input_read_s` **`8.27 → 6.12 s`**（`-26.0%`，三路并发重叠省 `4.00 s`）、`sections` **`14.51 → 9.95 s`**（`-31.4%`）、`完成仓位限制` **`6.11 → 3.16 s`**（`-48.3%`，含 `agg_pos_ratio`）、`account_calculation_s` **`97.84 → 86.58 s`**（`-11.26 s`）、`total_s` **`130.22 → 116.10 s`**（`-14.12 s`）；`swap_out_pages=0`、`validate_snapshot` PASS。原 `P5-B1`/`P5-B2`（`restore_s` 未加速，实测 `8.43 s`）已拆分为独立推后需求 `O8`；跨机核验（`2026-11-23 22:45–23:00`，与正式机 `06c8b15` 同切点 7 轮）：`total_s` 测试 `123.29` vs 正式 `127.51`（**−3.3%**），但 `account_calculation_s` 测试慢 `4.87 s`（弱 CPU 因子块）；`select_coin` 258/258 组零差、目标仓位方向一致，差异仅在资金规模与既有仓位（[对比详情](../2026-11-23-25m-prod-vs-test-comparison.md)） | [2026-11-23-phase5-and-v202-migration-plan.md](../2026-11-23-phase5-and-v202-migration-plan.md) |
| **Phase 5 子项目 2B 生产端 4 核并行（一期 `3e8972b..f06b8f1`）+ 计算-IO 重叠与零修订重启（二期 `f44d7ee..0d64e9d` 已上测试机）** | `alpha` (`feat/v202-migration-2b`) | **一期线上 5h 实测（`alphatest` E5 v4，`03:45–08:48 CST`）**：`25m` 轮 `.ready` 中位数 **`+149.1 s → +74.0 s`**（`-50.4%`），活跃 7 父策略 `157/157` 选币与 `538/538` 合约方向 **100% 一致**。**二期 + P0/P1 线上实测（`0d64e9d`，`20:11` 重启后 `20:25`/`20:45 CST`）**：SQLite WAL 直通 + 覆盖索引精确检索使 WS 屏障降至 **`0.13~2.37 s`**（`missed: spot=0 swap=0`）、`Resample` 内存合成耗时从 `16~21 s` 降至 **`5 s`**；`25m` 主轮 `spot_dict.ready = +45.72 s`、**`swap_dict.ready = +62.96 s`**（较正式机 Xeon `+114 s` 快 **`51 s`**），非交易偏移 `45m` 对齐 `publish_kline_num=5000` 后 `.arrow` 减半至 `1.45 GB`、`.ready` 从 `+122.47 s` 降至 **`+64.41 s`**；跨进程 `.ready` 零泄漏（稳定 `14` 个文件）；生产端重启后阶段 2 零 REST 覆盖且紧接首轮 `alphatrade-dryrun` 直接命中 **`mode="incremental"`（`0` `changed_series`）**，彻底消除重启 7h 回放 | [2026-11-25-producer-compute-io-overlap.md](../superpowers/plans/2026-11-25-producer-compute-io-overlap.md) |
| **消费端第二批（环节 5+6+7）+ 11-26 双端对比收口优化（`fork` 前释放修订缓冲/`vm.swappiness=15` + `spot` 确定性优先批次 + `T-22s` 余量/空档资金费节流 + `bStocks`/杠杆档位磁盘缓存）** | `alphaonline` (`main=5cab8f8`) + `alpha` (`main=5694033`) | **已合入双仓本地 `main` 并经 `alphatest` 过夜自然轮实测验收（`2026-11-27 00:25–03:25 CST` 连续 4 轮 `25m`）**：① 生产端 `spot` 100% 在第 1 波完成（`spot market_dict = 19.9–23.8 s` vs `swap 37.1–44.9 s`），消费端 `spot_early_lead_s` 4/4 轮稳定命中 **`16.83–21.37 s`**（零 `0.005 s` 竞态退化），`input_read_s` **`1.41–1.59 s`**、`prepare_s` **`2.65–2.92 s`**；② 消费端 `fork` 前释放修订缓冲 + `vm.swappiness=15` 使 `FACTOR_BLOCKS` `swap_out_pages` 从 `114k–203k` 骤降至 **`0–25,949`**（`02:25` 轮 `0` 换出），`FACTOR_BLOCKS` 墙钟降至 **`42.17–50.40 s`**，`account_calculation_s` 降至 **`85.57–100.18 s`**（中位 **`86.2 s`**），全流程 `total_s` 降至 **`90.50–105.26 s`**（中位 **`91.3 s`**，整轮 `XX:27:30~27:46` 完成，较正式机 Xeon `126–135 s` 快 **`35–43 s`**）；③ 启用偏移 `update_meta` 于 `:45~:49` 完成（留足 `11–15 s` 余量），非启用 5m 空档资金费 `保存=0.000s`；④ `bStocks` 局部脏行容错修复后稳定过滤 **118** 个股票代币，磁盘缓存命中 `<0.1 ms` | [2026-11-26-prod-vs-test-comparison.md](../2026-11-26-prod-vs-test-comparison.md) · [DEVELOPMENT.md](../DEVELOPMENT.md) |

---

## 3. 当前剩余第一大瓶颈：时序因子阶段（`FACTOR_BLOCKS`）剖析

基于 2026-11-06 在测试机与正式机进行的同输入交叉对照实验（详见 [2026-11-06-factor-block-causal-review.md](../2026-11-06-factor-block-causal-review.md) 与 [2026-11-06-factor-release-readiness.md](../2026-11-06-factor-release-readiness.md)）：

1. **已排除的误判**：
   - 当前账户依赖的 32 个时序因子**全部**已实现 `signal_multi_params()`，不存在漏用多参数接口。
   - 正式模块与测试模块在同机同输入下，924 任务 / 3,681,943 行 / 81 列 / 87 块**逐字节一致**，完整耗时无稳定 20 秒代码差距（`81.6–92.6s` vs `82.5–85.9s`）。
2. **CPU 热点分布（924 任务诊断，存在嵌套，不可简单相加）**：
   - 市值因子含外部处理：`~29.45` 核秒（其中磁盘 unpickle 仅 `1.96` 核秒，主体为逐行对齐/倍率处理）
   - `ZfMeanQ`：`~29.23` 核秒
   - `Cci异常隔离168_因果版`：`~23.52` 核秒
   - `111_输入隔离`：`~16.99` 核秒
   - `CorrBTC` 含外部数据：`~16.29` 核秒
   - `pandas → Polars` 转换：`~15.00` 核秒；`BiasV1`：`~13.63` 核秒；父进程分区写块：`~13.8–18.1 s` 墙钟。
3. **宿主并发与上游重叠干扰巨大**：
   - 同代码同输入（`nice=12` 实验）：与上游生产重叠窗口 `136.95 s`（swap out `+838 MiB`） vs 主要生产结束后 `92.58 s`（swap out `0.016 MiB`），波动可达数十秒。
   - 这说明优化因子阶段必须区分**纯 CPU 热点**与**上下游 IO/换页争用**，避免在单样本噪声下误判代码收益。

---

## 4. 全量初始化耗时参考

- 单账户完整 140xx 历史初始化（单槽位 cgroup `MemoryHigh=4.5GiB`）：正式机实测 **13–15 分钟**（~780–900 s，其中时序因子 ~210 s、截面 ~95 s、选币 ~100 s、MODEL 全算 ~74–150 s 等）。
- 跨日普通 MODEL 续算（含日级特征按需读原始行情）：约 `53 s`（含 `8.1 s` 原始读取），不可与普通非跨日小时 `10–14 s` 混算。

---

## 5. WebSocket 采集链路实测：为什么没有带来显著收益（2026-11-23）

> 对象：测试机生产者 `/home/ubuntu/alpha`（`v2.0.2` 底座，`use_websocket=True`，WS 经 `127.0.0.1:7897` Mihomo 代理）。对照：正式机生产者 `/home/ubuntu/alpha2`（`b5eb945`，无 `use_websocket`，纯 REST + `rest_fetch_delay_seconds=3.0`，日志 0 条 `barrier`）。

### 5.1 单轮实测时间线（`22:25` 轮，与消费端 `25m` 同切点）

| 阶段 | 时刻 | 相对 5m 边界 |
| --- | --- | ---: |
| 5m bar 闭合 | `22:25:00.0` | 0 s |
| `barrier` 启动（spot / swap 并行） | `22:25:02.0` | +2.0 s |
| spot ready 齐 `520/520` | `22:25:11.9` | +11.9 s（`9.87 s`） |
| grace 到点计算 missed | `22:25:12.1` | **spot 0 / swap 229** |
| REST 兜底补 229 只 swap | `22:25:15.4` | `3.3 s` |
| swap ready 齐 `767/767` | `22:25:16.1` | +16.1 s（`14.07 s`） |
| Resample spot 完成 | `22:25:40.6` | **+40.6 s（`24.5 s`）** |
| Resample swap 完成 | `22:25:46.6` | +46.6 s（`6.0 s`） |
| Arrow publication | `22:25:47.3` | +47.3 s |
| 预处理 + pivot 全就绪 | `22:27:22.6` | **+142.6 s** |

设计预期为“采集 + 重采样 `6–10 s`”，实测采集链（barrier + REST + resample）**47 s**，全程（含 pivot 发布）**143 s**。

### 5.2 四个抵消收益的原因

1. **固定 `ws_wait_sec=10.0 s` 把下限钉死**（`core/pipeline/barrier.py` 签名默认值）。`ws_grace_passed` 一到点，`update_cycle` 立即按 `.ws_ready` 缺失发起 REST 兜底，不看 WS 还差多少。实测该时刻 swap 仍缺 `229/767`（30%）。
2. **WS 并没有减少工作量，而是变成双写**。本轮最终 `767/767` 全部有 `.ws_ready`、`gap` 标记为 0，说明 WS 后来把这些币全收到了；但 REST 已在 `22:25:12–15` 补过同一批 229 只。同一根 bar 被两个源各写一次，多付 `3.3 s` 与一轮带宽。
3. **`resample` 占了大头且串行**：spot `24.5 s` + swap `6.0 s`，合计 `30.5 s`，超过整条链路一半。这段 WS 完全没有触及（`update_resample_from_base` 从 base 存储重建），是当前真正的瓶颈。
4. **尾部延迟的主因是 writer 落盘队列，不是代理**（2026-11-24 逐 symbol `mtime` 采样后修正）：WS 客户端在 `+1.1 s`（spot）/ `+3.7 s`（swap）已收完整轮（246 轮统计），`ws_runner` 入队计数在 `+3~4 s` 停止增长，但 `.ws_ready` 到 `+8~10 s`（中位）才出现——中间 8–11 s 是 2 个 writer 排空 1,287 个 symbol（约 `86 条/s`）。代理只贡献 swap 比 spot 晚约 `2.5 s`（`3.66` vs `1.09`），是次要项。详见 [grace 参数取值分析](../2026-11-24-grace-tuning-analysis.md)。

### 5.3 对消费端轮次耗时的实际影响

消费端 `25m` 轮触发读的是**上一小时** `cutoff`（`22:25` 触发读 `21:25` 的 bar，上游 `21:27` 即已就绪），因此采集链路的 10–20 s 差异**不会进入 `total_s`**。`total_s 127 s` 中与输入相关的只有 `input_read_s 7.23 s`（正式机）/ `6.25 s`（测试机），其余是 `FACTOR_BLOCKS` 约 `35–46 s` 与账户计算约 `88–92 s`。**WS 采集提速在消费端轮次耗时上结构性不可见。**

### 5.4 与修订面的关系（24h 数据后修正 11-23 的初步判断）

11-23 单日样本曾显示：测试机 `changed_series 31–69`（WS + REST 双源）而正式机 `3–12`（纯 REST 单源），据此推测“双写可能抬高修订面”。**该推断已被 11-24 全量数据推翻，以本节为准。**

`2026-11-23 15:25 → 11-24 10:25 UTC` 连续 20 轮：

| 机器 | 上游采集 | 执行模式 | `changed_series` |
| --- | --- | --- | --- |
| **测试机** `alpha` | WS + REST 补漏 | **`incremental` 20/20** | **0（无修订）** |
| 正式机 `alpha2` | 纯 REST + `3.0 s` 延后 | 6 户 119 轮中 113 轮 `replay` | 中位 `13–55`，最高 `146` |

结论反转：**WS 的收益不在耗时，而在“不再 `replay`”**。测试机因 5m 数据在边界后 ~16 s 内闭合落盘，消费端读取时（1 小时后）已无待修订项，`copy_s` + `restore_s` 整段被省掉（`prepare` 中位 `25.86 → 7.59 s`）；正式机仍因 REST 尾部伪修订持续 `replay`。11-23 当天的反向数据应视为 2B 首日过渡态（双写、`grace 10 s`、单轮掉线叠加），不足以支撑“WS 抬高修订面”的结论。

剩余待解释项：测试机 WS 每轮 `missed swap 164–285`（占 `767` 的 `21–37%`），即仍有近三成依赖 REST 补写，但未造成 `replay`——说明**补写发生在同一根 bar 且数值一致**，与“跨 bar 尾部伪修订”是两回事。

### 5.5 可执行项（按收益/风险排序）

1. **把 WS 底座推广到正式机 `alpha2`（优先级最高，收益已量化）**：正式机 6 户 119 轮中 113 轮 `replay`，每轮 `copy+restore` 约 `14–16 s`；若复制测试机的“零修订”效果，单户每小时省 `14–16 s`、6 户合计约 `90 s/小时` 的纯冗余 IO，并消除 `replay` 的重复续算。**但不要再用 `total_s` 衡量采集侧改造**——`cutoff` 是 `candle_begin_time`，消费端读的是 `run_time` 刚闭合的那根 1h bar，轮次会阻塞等 `swap_dict/market_pivot_swap` 就绪（实测正式机 `+113 s`、测试机 `+143 s`）；`total_s` 从 worker 启动才起算所以看不见这段，但**下单时点会被直接推后**（同 mode 完成 `+226.9` vs `+251.8`）。评价采集侧请用「就绪滞后 + `total_s`」。
2. **`ws_wait_sec` 10 → 14（判定点 `+16 s`）**（2026-11-24 修正原"10 → 3"的建议）：实测 3 轮逐 symbol 到达 `max` 为 `12.7 / 14.4 / 14.9 s`，`+12 s` 判定点只覆盖 swap 的 `66–87%`，每轮产生 `~290` 个假缺口（83 轮无一零缺口），REST 抓完又往同一 writer 队列追加重复条目、把排空拖长 `2–4 s`。改到 `+16 s` 后常态轮次 REST 请求降为 0，barrier 结束 `15–16 s`（现为 p50 `16.0 s` / p90 `21.2 s`），掉线轮次 `~34 s` 仍 < `45 s` 软截止。**原建议"降到 3 s"是把 55–77% 的 swap 推给 REST，方向相反，撤回**。详见 [grace 参数取值分析](../2026-11-24-grace-tuning-analysis.md)。
3. **消除同 bar 双写**：REST 兜底完成后不再覆盖 WS 已落盘值，或统一“以 REST 为权威”但改为 REST 完成后一次性写入。
4. **`resample` spot/swap 并行**：两者目录独立、无相互依赖，可省约 `6 s`；spot 的 `24.5 s` 是当前最大单项，应单独剖析（base 存储读取 vs 重采样计算）。
5. **WS "覆盖不足"已定因，不是丢包**（2026-11-24）：`missed swap 21–37%` 是 `.ws_ready`（写盘完成）滞后造成的假缺口，WS 侧在 `+4 s` 前已全部入队；近 24 h 随机 120 只 × 300 根 bar 的 `_source` 标记为 spot `100% ws`、swap `99.82% ws`（仅 `04:25` 一轮 51 根为 `rest`）。真正的待办是 writer 排空能力（`num_writers=2`，~`86 条/s`），不是订阅重建。

### 5.6 grace 参数取值结论（2026-11-24 专项）

| `ws_wait_sec` | 判定点 | swap 最差覆盖 | 每轮 REST 请求 | barrier 结束（swap） |
| ---: | ---: | ---: | ---: | ---: |
| `10`（现行） | `+12 s` | 66% | 290（实测 83 轮中位） | p50 `16.0 s` / p90 `21.2 s` |
| `14`（建议） | `+16 s` | 100% | 0 | `15–16 s` |
| `3`（原建议，已撤回） | `+5 s` | 23% | ~1,050 | 被 REST 拖到 `20 s+` |

判定信号应从 `.ws_ready`（落盘完成）改为 ws_runner 接收侧标记，届时 grace 可回到 `3–5 s` 且 REST 只在真缺口触发。完整数据（83 轮 missed、3 轮逐 symbol 曲线、writer 排空速率、`_source` 溯源）见 [grace 参数取值分析](../2026-11-24-grace-tuning-analysis.md)。

### 5.7 WS 链路 vs 老纯 REST：差在写入语义，不在 grace（2026-11-24）

同切点实测（prod `20:55` 纯 REST / test `20:45` WS）：Arrow 发布 `+28.6 s` vs `+47.8 s`，swap pivot `+102.5 s` vs `+145.3 s`——**WS 更慢**；但修订面 REST `64/72` 轮 `replay`（`changed_series` p50 `25`）、WS `0/20`——**WS 更稳**。

注意"更慢"的落点：`cutoff` 是 `candle_begin_time`，消费端读的就是 `run_time` 刚闭合的那根 1h bar，并且会阻塞等 `swap_dict/market_pivot_swap` 就绪（今日 12 轮 worker 启动滞后中位：正式机 `112.7 s`、测试机 `148.0 s`）。这段等待在 `total_s` 之外，但直接推后下单：同 `incremental` 口径完成时点 `+226.9`（正式机）vs `+251.8`（测试机）。

根因是 `once_update_candles=99`（`core/handle.py:96`）：REST 每轮抓 99 根且**全量重写**（每轮 ≈ `127k` 行、约 `12 MB`），8 h 窗口内任一根 bar 与库里不同就记一次修订；WS 只推闭合 K、只写 1 根、永不回看（每轮 `1.29k` 行）。**因此调 `rest_fetch_delay_seconds` 只能压低最新 bar 的漂移，不能让 `changed_series` 归零**；要等效必须把 REST 改成 write-once（抓取窗口保留、写入只提交库中不存在的 bar）。该结论与 grace 取值、机器性能无关。详见 [WS 链路与老 REST 的差别](../2026-11-24-ws-vs-rest-path-comparison.md)。

**"修订"是否真实存在已核验（`2026-11-24 21:35–22:00`）：不存在。** 92 标的 × 25.5 h × 9 列共 5.5 万对 bar，WS 一次写入值与 REST 纠正后值**逐值零差**；`13:40` 全量落库对比显示 swap `8/767`（1.04%）不一致且方向单一（正式机**只少不多**，少 1–20 笔），spot `0/520`；同一根 bar 的 REST 轮询为 `+4 s` 未定型、`+9.3 s` 定型后 `181 s` 不变。即：**没有历史被更正，是 REST `klines` 端点在闭合后数秒内仍在补聚合，而 WS 的 closed kline 是定型后推送**。证据链见 [修订根因定论](../2026-11-24-revision-root-cause.md)。

### 5.8 全链路 4 核并行与尾部增量加速离线落地（`2026-11-25`，`D:/alpha` `3e8972b..f06b8f1`）

针对 §5.2–5.7 定位的测试机上游就绪慢（`+137~146 s` vs 正式机 `+105~113 s`）全部瓶颈，结合实测窗口事实（`XX:25:00 → .ready` 期间下游 `alphatrade` 空等 `0%` CPU、可用内存 `~6.3 GiB`，原 `< 2.8 GiB` 单核串行限制不必要），已在 `D:/alpha`（`feat/v202-migration-2b`，`3e8972b..f06b8f1`）离线落地四层加速并通过 `410/410` 单测与跨仓契约验证：

1. **采集落盘与兜底（`3e8972b`）**：默认 `num_writers=4`、`ws_wait_sec=14.0`；`WsRunner → Writer` 改传轻量原生 `kline` 字典；`Writer` 完成 `flat_pkl` 原子替换与 `.gap` 更新后**立即写 `.ws_ready`**（将慢速 `TSManager` 月分区写移出屏障等待路径）；双市场屏障 100% 齐且无 `.gap` 时通过 `cancel` 事件即时终止 REST 兜底线程（真漏网单根仅拉 `limit=5`，有 `.gap` 才拉 `limit=99`）。
2. **重采样层（`33d0854`）**：反转读取优先级为 **240 行轻量 `flat_pkl` 优先、月分区回退**，接入 `RuntimeSupervisor` 常驻 4 进程 `spawn` 计算池并发重采样，并内存直传尾部 `12` 根 1h K 线至预处理层。
3. **Pivot 矩阵化（`1ea9b30`）**：`update_pivot_rows` 改为单次时间索引定位 + NumPy 二维 `float64` 矩阵批量赋值，消除每轮 `3,068` 次 pandas `.loc` 开销。
4. **预处理核心层（`f06b8f1`）**：`gen_batch_data_incremental` 增加前缀精确校验的**尾部增量富集快路径**（免除每轮对 1.5 万行全表重复 join/sort/reindex，异常/修订自动回退全表路径）；`swap || spot` 双市场恢复并发（`spot` 先完先发 `.ready`），8 个 Batch 在常驻 4 进程池内完成全历史真多核 C 因子计算后当场切 `.tail(publish_kline_num)` 序列化为 Arrow IPC `RecordBatch` 缓冲并立即释放大表。
5. **合成基准实测（Windows `alpha311`，240 序列 × 6,000 根 = 144 万行，8 Batch + 6 列 C 因子 + 1000 行 Arrow + 2026 Pivot）**：旧 `2B`（`f97005a`）`resample 4.10 s + preprocess 12.00 s = 16.10 s` → 新 4 核流水线（`f06b8f1`）**`resample 1.45 s`（`2.83x`）+ `preprocess 3.52 s`（`3.41x`）= `4.97 s`（`3.24x`）**，全产物 `check_exact=True` 逐位零差。待授权同步测试机实盘验证自然轮就绪时点。



### 5.9 同版本双机（正式机 `5cab8f8`+`5694033` vs 测试机）与正式机 CPU/内存效率（`2026-11-27 10:47–11:16`，只读）

来源：[2026-11-27 正式机 vs 测试机数据对比与资源效率分析](../2026-11-27-prod-vs-test-and-resource-efficiency.md)，留底 `docs/evidence/2026-11-27-prod-vs-test/`。

1. **性能差的主因从"代码/上游"转为"修订模式"**：正式机今日 `63/64` 轮为 `replay`（`changed_series` p50 `37`、范围 `9–171`、无 0），测试机 `11/11` 轮 `incremental` 且 `changed_series` 恒空。同账户（`45m`）实测 `replay` 比 `incremental` 多 **`+28.1 s/轮`**（`prepare 24.0→4.5`、`input_read 6.7→1.2`、`calc 113.1→85.2`）。**注意这与 §5.7 "WS 0/20 replay" 的结论冲突**：正式机已在 `v2.0.2` WS 底座上仍每轮判修订，需要单独立项复查 `compare` 基线来源。
2. **同模式可比口径**：正式机 `incremental`（`45m`）`calc 85.23 s` / `account_calculation 80.44 s` vs 测试机（`25m`）`92.92 / 89.78 s`——正式机在同时跑 6 账户 + 一套旧栈的情况下仍快 `8–10%`（Xeon 8255C vs E5-26xx v4）。
3. **上游已反超**：同批次正式机 `barrier 0.13/1.39 s`、`market_dict 17.5/32.9 s`、`pivot 2.2/4.8 s`；测试机 `0.34/19.83 s`、`20.9/38.8 s`、`2.0/3.7 s`。正式机 `v2.0.2` 相较 11-26 的 REST 口径（`40.5/63.2 s`）快 `23–30 s`。
4. **CPU 效率（`sar` 全天 67 点）**：`%user 22.92 / %nice 6.10 / %system 9.16 / %iowait 6.19 / %idle 55.63` → 日均 **1.78/4 核（44.4%）**；无轮基线 `busy 4.7%`。轮内 1 s 采样 `busy 86–99%`（`3.47–3.95` 核），其中 MODEL 4 个 `spawn_main` worker 占 `88–97%`。**4 路并行已饱和，提高 `MODEL_JOB_NUM` 无收益**；剩余可压缩空间在串行段（修订 `prepare` 与回放 `calc`）。
5. **内存效率**：`%memused` 均值 `11.25`（峰 `17.05 @09:40`）、`kbcached` 均值 `2.69 GB`；`pswpin/pswpout` 全天均值 `396 / 424 页/s`（`~1.55/1.66 MB/s`，日均 ~135 GB）；轮内 1 s 采样 `MemAvailable` 最低 **`710 MB`**（11:11）、swap used 峰值 **`4,353 MB`**。峰值与 **旧栈 `alphatradetest` 4×`account_exec`（各 ~1 GB）** 重叠强相关；`MemoryHigh=4.5 GiB` 仅作用于 `systemd-run` 初始化 scope，常态轮次与旧栈都在 `session-23.scope` 下无上限。


### 5.10 单轮分环节资源画像（正式机 vs 测试机，`2026-11-27 12:32-12:40`，1 Hz 采样）

来源：[最近一轮分环节资源画像](../2026-11-27-latest-round-resource-profile.md)。

1. **同口径单轮**：正式机 `35m_t1104`（cutoff 03:35，`total 96.20 s`，`calc 81.16`）vs 测试机 `25m-eart1106`（cutoff 03:25，`total 91.12 s`，`calc 89.26`），两者均 `incremental`、1301 帧。
2. **算力等价**：并行因子段核秒 `149` vs `152`，MODEL 段 `39` vs `37`，峰核 `3.86` vs `3.90`（均 4 核）。正式机 `calc` 反而快 8.1 s。
3. **差异来自同机邻居**：正式机并行段 `trade_spawn 2.67 核 + legacy 0.36 核`；`alphatradetest` 4×`account_exec`（各 1.33 GB）轮内合计 3.8 核；旧生产者 `alpha/realtime_data.py` 峰值 5.1 GB、常驻 1.0-1.3 核。
4. **内存**：`MemAvailable` 地板正式机 **1,461 MB** vs 测试机 **4,218 MB**；swap 常驻 1,437 对 998 MB；轮内换出峰值 14,106 对 10,957 页/s，落盘段主缺页 3,430 对 653 次/s（page cache 被匿名页挤空）。
5. **磁盘非瓶颈**：轮内写 47-56 MB/s、读 12-65 MB/s、`util` 峰 79-85%、写 `await` 3-4 ms。
