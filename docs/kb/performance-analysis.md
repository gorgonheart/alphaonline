# 重要性能分析、阶段剖面与已落地收益

> 所属体系：[统一开发入口](../README.md) · 所有耗时均保留实测环境与口径，**禁止**将并行累计 CPU 秒当墙钟、将微基准线性外推整轮或将嵌套阶段相加。
> 后续优化规划与进度跟进：[2026-11-19 Ubuntu 4c8g 性能深度优化规划与跟进](../2026-11-19-performance-optimization-plan.md)。

---

## 1. 普通 4050 续算全流程耗时剖面（Ubuntu 4c8g）

### 1.1 正式服务器现行典型耗时（`43.167.235.164`，2026-11-19 `25m-eart1106` 实测）

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
