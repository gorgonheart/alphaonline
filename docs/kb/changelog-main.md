# 历次主干修改记录（`alphaonline` + `alpha` 双端时间线）

> 所属体系：[统一开发入口](../README.md) · 按时间倒序梳理两仓已落地主干与正式发布的关键里程碑；完整逐条技术日志仍保存在 [docs/DEVELOPMENT.md](../DEVELOPMENT.md) 与 [D:/alpha/docs/DEVELOPMENT.md](../../../alpha/docs/DEVELOPMENT.md)。

---

## 1. 交易端（`D:/alphaonline`）主干演进

| 日期 | 代表提交 | 阶段 / 主题 | 核心变更摘要 | 线上状态 | 详细专题 |
| --- | --- | --- | --- | --- | --- |
| **2026-11-19** | `edbbba3`（生产）<br>`bac2443`（本地合并） | **新上币 universe 扩张误判重建修复（A1 + A′ + `merge_context`）** | `handoff.first_revision` 区分扩张与真实修订（新增序列/新增列不推 `revised_from`、不污染 `changed_fields`，移除保持保守）；`expand_result` 按 symbol 重映射 26 个 checkpoint（保留 Numba dtype）；独立审查拦出并修复 `merge_context` 新币 overlap 价格/资金费回填缺陷，消除 T+1 轮与扩张前回放 `ValueError`。双端 122 项测试全绿。 | **正式已上线**（`20:22`，PID `464274` 未变，20:25 `25m` 首轮加载通过）；测试未同步 | [plan.md](../../plan.md)、[DEVELOPMENT.md](../DEVELOPMENT.md) |
| **2026-11-07** | `6c02e58` | 统计展示精简与按多空分侧排行 | 1h 账户损益仅展数值；期初持仓价格影响按 `min_qty` 过滤小持仓，现货/合约合并后按多头/空头各取绝对值前 5；现货 `cost_status` 去“（未计手续费）”。 | **仅本地 `main`，未发布** | [DEVELOPMENT.md](../DEVELOPMENT.md) |
| **2026-11-06** | `c217ca1` / `414cb05` | 1h 损益匹配修复 | 统一权益/持仓两层历史 1h 筛选，匹配上一计划轮次（±10 分钟容差、严格早于当前、缺轮不借旧轮）。 | **正式 + 测试已上线** | [2026-11-06-hourly-statistics-deployment.md](../2026-11-06-hourly-statistics-deployment.md) |
| **2026-11-06** | `c49d6de` → `680f334` / `67ef4ad` | **2.3.3 增量升级 + 现货移动加权成本 + 双线主干整合** | 集成 2.3.3（每轮单币限额、bStocks 双来源/全半角、统一账户单源钱包分配、OKX 加密工具、`xbx-py11=0.1.24`/`ccxt=4.5.88`）；新增 `tools/spot_cost_estimate.py` 移动加权成本；切换独立 `upgrade233/alpha311` 环境；补齐 26 恢复点边界与 231 文件全量审计。 | **正式 + 测试已上线** | [2026-11-06-main-consolidation.md](../2026-11-06-main-consolidation.md)、[2026-11-06-upgrade-core-2.3.3-implementation.md](../2026-11-06-upgrade-core-2.3.3-implementation.md) |
| **2026-11-05~06** | `f4361a6` / `1dae6a2` / `2827d27` | **后半程性能（P1/P2/P3/P4）+ 超恢复范围即时后台重建 + Arrow 需求联动 + S3 覆盖热修复** | P1 `LiveRebRobust40` 可缓存 JIT（~13s→0.4s）；P2/P3 再择时行情与 `state0` 复用；P4 执行分段计时；超恢复范围退出码 `75` 交 `Coordinator` 单槽位后台重建（缺全量时轻线程等 ready）；开启 `ARROW_DEMAND_ENABLED` 按需发布；修复 S3 `validate_initial_history` `max→min`（解决 GUNUSDT 误拦截）。 | **正式 + 测试已上线** | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md)、[2026-11-05-rebuild-handoff-implementation.md](../2026-11-05-rebuild-handoff-implementation.md)、[2026-11-05-s3-listing-coverage-hotfix.md](../2026-11-05-s3-listing-coverage-hotfix.md) |
| **2026-11-05** | `e32f503` / `7177978` | 临时工作区生命周期与可删除 `test/` 隔离 | 自动释放失败初始化/回放工作区（释放 26GiB 硬链接残留）、有界 64KiB 诊断与 24 份日志；生产 sparse checkout 排除根 `tests/`，引入 `tools/run_deployment_tests.py`。 | **正式已上线** | [2026-11-05-lifecycle-production-deployment.md](../2026-11-05-lifecycle-production-deployment.md) |
| **2026-11-04** | `9451bdd` 合并 | 共享 I/O 优化、局部第 0 代 GC、MODEL 原生日期加速、`dry_run` 防写修复 | `read_history` 预筛选；局部 gen-0 GC 将初始化映射释放由 13.13s 降至 2.3ms；`coin_features` 原生日期快路（-60%）；禁止 `dry_run` 修改杠杆/补 BNB/dust 兑换。 | **正式 + 测试已上线** | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md)、[2026-11-04-shared-io-optimization-review.md](../2026-11-04-shared-io-optimization-review.md) |
| **2026-10-29~11-03** | `debac03` → `3d6f809` | **MODEL_ROBUST40 v1 独立偏移移植、服务器自动初始化、共同恢复点回放与共享计算管线** | 20/40/55→5/15/25/35/45/55 独立偏移 MODEL；`core/` 窄隔离接入；服务器 140xx 完整历史初始化 + 4050 Arrow 续算；S3/子策略/MODEL 共同 checkpoint 回放与原子事务；共享时序/截面/选币 IPC 分块管线与 cgroup 限额；统计 BN 单源快照与价格影响报告。 | **正式 + 测试已上线** | [MODEL_ROBUST40.md](../MODEL_ROBUST40.md)、[2026-10-30-server-initialization-review.md](../2026-10-30-server-initialization-review.md)、[2026-10-31-checkpoint-live-review.md](../2026-10-31-checkpoint-live-review.md) |
| **2026-10-14** | `e6fc3bd` 前后 | **2.3.1 增量升级 + 三因子异常修正** | 集成 2.3.0/2.3.1（OKX、bStocks、maker_ratio、成交额统计、策略归属）；三因子修正（`111_输入隔离`、`Cci异常隔离168_因果版`、`动量210401_双项异常隔离V2`）。 | **已上线** | [2026-10-14-upgrade-core-2.3.1.md](../superpowers/specs/2026-10-14-upgrade-core-2.3.1.md) |
| **2026-06~07** | `481cf95` 等 | Arrow mmap 读取、Polars 截面、2.2.1 升级、去冗余 Pivot 重建 | 消费端 Arrow 优先 + PKL 回退 + 向量化映射对齐；两个回撤截面因子 Polars 化；删除消费端从 Arrow 重建 Pivot；`entry_exit` 续算与 `dry_run`。 | **已上线** | [2026-06-27-consumer-mmap-arrow-read-design.md](../superpowers/specs/2026-06-27-consumer-mmap-arrow-read-design.md) |

---

## 2. 生产者端（`D:/alpha`）主干演进

| 日期 | 代表提交 | 主题 | 核心变更摘要 | 线上状态 | 详细专题 |
| --- | --- | --- | --- | --- | --- |
| **2026-11-06** | `b9cdd13` → `494d22a` / `41686c3` | **1.1.12 完整合入 + Arrow 按需发布 + 市值代理整合** | 精确同名/唯一归一化现货合约映射与历史刷新、新币闭合首根/空行情安全处理、重叠价量修订贯通 batch/长因子/Arrow/Pivot、Binance 优先/DataAPI 兜底固定来源、年度 Pivot `.complete` 标记、市值七列 float64；`core/arrow_demand.py` 消费者按需发布；`coin_cap_use_proxy` 开关（正式关闭、测试开启）。 | **正式 `alpha2` + 测试 `alpha` 已上线** | [2026-11-06-alpha1112-factor-block-implementation.md](../2026-11-06-alpha1112-factor-block-implementation.md)、[2026-11-06-main-consolidation.md](../2026-11-06-main-consolidation.md) |
| **2026-11-05** | `9e5e15d` / `ef4af11` / 双线补丁 | 单轮 Arrow 保留 + 可删除 `test/` + 已结算资金费回补 + 重启策略起点保留 | `arrow_snapshot_keep=1`（释放 16GiB）；可删除 `test/` 与稀疏检出；最近 24h 已结算资金费回补且逐 offset 一次发布；`preprocess_1h_resample_origins` 持久化 `first_candle_time` 避免重启伪修订。 | **正式 + 测试已上线** | [2026-11-05-arrow-single-snapshot.md](../../../alpha/docs/2026-11-05-arrow-single-snapshot.md)、[2026-11-05-candle-origin-dual-release.md](../2026-11-05-candle-origin-dual-release.md) |
| **2026-11-02~03** | `dc12fdc` / `d9c8371` | Linux 部署自动化、GCC/MSVC CCI 一致性、全量 Arrow 发布 | 配置迁入 `.gitignore` 的 `config.json`；CCI 在 GCC 关闭 FMA contraction 消除跨平台差异；支持 `publish_kline_num=0` 全量 Arrow 与 `full_history` 元数据。 | **正式 + 测试已上线** | [D:/alpha/docs/DEPLOYMENT.md](../../../alpha/docs/DEPLOYMENT.md) |
| **2026-10-29~31** | `b13f899` 等 | 采集端日志轮转、资金费分页、`backfill` 空档补算与预热调度 | 多进程 worker 分离日志 + `SafeTimedRotatingFileHandler`；`fundingRate` 信号量限流与 `startTime` 游标；重启后 5m 本地重采样补算缺失 offset + 共享 `exchangeInfo` 复用；4 线程共享因子池。 | **已上线** | [2026-10-29-funding-rate-api.md](../../../alpha/docs/2026-10-29-funding-rate-api.md)、[2026-10-30-backfill.md](../../../alpha/docs/2026-10-30-backfill.md) |
| **2026-10-14~15** | — | 动量 V2 C 扩展与性能优化 | 新增 `动量210401_双项异常隔离V2` 独立 CFFI 扩展；快速路径去诊断帧分配与 IEEE-754 位检查，14060 根单币调用由 30.3ms 降至 2.88ms（约 10×）。 | **已上线** | [DEVELOPMENT.md](../DEVELOPMENT.md#L981-L1015) |
| **2026-06** | `faf5a14` → `2175e16` | 增量模块 1–5、三大 C 扩展、Write-Once + Arrow 发布 | 重采样/更新/batch/pivot 全增量；旧动量/ILLQ/CciV3 C 扩展；batch PKL 不落因子，内存嵌入后原子发 Arrow 并后置 `.ready`。 | **已上线** | [2026-06-27-producer-io-and-arrow-publish-design.md](../../../alpha/docs/superpowers/specs/2026-06-27-producer-io-and-arrow-publish-design.md) |
