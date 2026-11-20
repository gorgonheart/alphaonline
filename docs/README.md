# `alpha` / `alphaonline` 统一开发入口与全局摘要

> **所有后续开发对话（两仓通用）请从此文件开始。**
> 维护基准：`2026-11-20` · 交易端 `D:/alphaonline`（`main` 与 `production/main` 已完全同步至 `940c487`+文档，含 Phase 0–3 + F1 + Q1 + Q2）· 生产者 `D:/alpha`（`main=41686c3`，生产运行 `41686c3`）。
> 原则：历史设计/计划文档不等于已落地；遇到文档与代码冲突时，以**现行代码 + 可复现测试 + 生产只读核验**为准，并同步更新本知识库。

---

## 0. 开场必做与开发规范摘要

1. 读本文件对应摘要及专题子文档，检查 `D:/alphaonline` 与 `D:/alpha` 两端 `git status` / `git diff`（保留用户未提交修改）。
2. 本地 Python 唯一使用 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`；Windows pytest 传短路径 `--basetemp` 与 `-p no:cacheprovider`。
3. 因子、截面、模拟、恢复、订单逻辑改动必须 **TDD + 独立 Python oracle**，严禁重生成 golden 掩盖差异。
4. 默认离线验证，**未经明确授权严禁启动实盘/生产者、下单、重启服务或删除生产数据**；严禁将密钥、webhook 或账户凭据写入文档/日志/Git。
5. 变更完成后同步更新 [docs/DEVELOPMENT.md](DEVELOPMENT.md)（按时间倒序流水记录）与受影响的 `docs/kb/*.md` 专题文件。

详细规范与 7 条跨项目数据契约 → **[开发规范与数据契约](kb/standards-and-contracts.md)**

---

## 1. 本地 `alpha311` 环境

- **仓库**：`D:/alphaonline`（交易/消费端）与 `D:/alpha`（行情/生产端），双端通过共享目录的 Arrow + PKL + `.ready` 契约衔接。
- **解释器**：`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（Python `3.11.11`），锁定核心数值库 `pandas 2.2.3` / `numpy 2.0.2` / `polars 1.36.1` / `pyarrow 17.0.0` / `numba 0.60.0` / `lightgbm 4.7.0`。
- **注意**：共享本地 `alpha311` 仍为 `xbx-py11==0.1.23`、`ccxt==4.5.53`（2.3.3 依赖只在隔离目录验证）；禁止使用旧 `alpha` / `alpha3` 环境（会报 `numpy._core` 反序列化错）；Windows C 扩展需 MSVC x64 + SDK 构建 `.pyd`（不可复制到 Linux）。

完整环境与依赖对照 → **[三套环境与 Conda/进程拓扑](kb/environments.md)**

---

## 2. 测试服务器与 Conda 环境（`43.173.122.28`）

- **实例**：`/home/ubuntu/alphatrade`（PM2 `alphatrade-dryrun`，运行模式 `dry_run`）与 `/home/ubuntu/alpha`（PM2 `realtime_data`），非 Git 源码增量同步。
- **Conda 环境**：交易端使用独立 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`（`xbx-py11==0.1.24` / `ccxt==4.5.88`）；生产者使用 `/home/ubuntu/miniconda3/envs/alpha311`（Python `3.11.18`，固定 `chardet==5.2.0`）。
- **SSH 连接**：已配置本机免密别名 `ssh alphatest`（或 `ssh 43.173.122.28`，私钥 `C:\Users\Administrator\.ssh\visibility-deploy.pem`）。
- **基础设施**：`/swap.img` 已扩容至 **20 GiB**；Mihomo `v1.19.33`（日本 NTT AnyTLS）常驻 `127.0.0.1:7897`；已启用 `shared_factor_pipeline=True`、32 币分片、`ARROW_DEMAND_ENABLED=True`、`dynamic_burst_enabled=True`、`coin_cap_use_proxy=True`。**已于 2026-11-19~20 同步并验证 `edbbba3` + Phase 0–3 + F1 + Q1/Q2 统计全部改动。**

详细配置与历史记录 → **[三套环境与 Conda/进程拓扑](kb/environments.md)**

---

## 3. 正式服务器与 Conda 环境（`43.167.235.164`）

- **SSH 连接（严格只读优先，变更需明确授权）**：已配置本机免密别名 `ssh alphaprod`（或 `ssh 43.167.235.164`，用户 `ubuntu`，私钥 `C:\Users\Administrator\.ssh\alphaprod-oldserveragent.pem`，源 `D:\alphaonline\env\oldserveragent.pem`）。
- **主实盘双端**：`/home/ubuntu/alphatrade`（Git HEAD `940c487`+文档，与本地 `main` 0 差异；PM2 `startup.py` PID `464274`，模式 `none`）消费 `/home/ubuntu/alpha2`（Git HEAD `41686c3`，`realtime_data.py` PID `3634429`）；两仓通过 `/home/ubuntu/repos/{alphatrade,alpha}.git` 私有裸仓 fast-forward 更新，工作区 sparse checkout 排除根 `tests/`。
- **并行旧实例（严禁误动）**：`/home/ubuntu/alphatradetest`（PID `3983440`）消费 `/home/ubuntu/alpha`（PID `3982664`），独立运行，主线部署绝不重启或修改。
- **Conda 环境**：主实盘消费者独立运行 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`（Python `3.11.14`，`xbx-py11==0.1.24` / `ccxt==4.5.88`）；生产者与旧组件保持 `/home/ubuntu/miniconda3/envs/alpha311`（`ccxt==4.5.53`）。
- **PM2 陷阱**：PM2 遇到含 `/home/ubuntu`（含 `bun` 子串）的绝对 `interpreter` 路径会误用 Bun 容器导致语法错误，**必须**用短名 `python` + 独立 `PATH`。

完整进程、裸仓与环境表 → **[三套环境与 Conda/进程拓扑](kb/environments.md)**

---

## 4. 各个环境 `accounts/` 区别

- **正式主实盘（`alphatrade`）**：6 个 `MODEL_ROBUST40_V1_25m` 账户按 10 分钟错峰（`5m/15m/25m/35m/45m/55m`），模式 `none`，最小下单金额 **现货 15 / 合约 10**，各自独立快照且 `clean_start` 保留有效 MODEL 状态。
- **正式旧实例（`alphatradetest`）**：5 个传统账户按 10 分钟错峰（`10m/20m/30m/40m/50m`），与主实盘完全隔离。
- **测试服务器（`43.173.122.28`）**：主干整合后为单户 `25m-eart1106.py`（MODEL），模式 `dry_run`（已彻底禁止前置杠杆/持仓模式修改、自动补 BNB 及 dust 兑换），最小金额 **现货 10 / 合约 5**。
- **本地仓库（`D:/alphaonline/accounts/`）**：Git 跟踪 `20m/40m/55m_robust_4h.py` 及 `_` 开头模板；正式机在工作区删除这 3 个跟踪文件并用未跟踪实盘账户替代。因资金、门槛、历史采集不同，不可把测试与正式的自然权重直接当等价验收。

详细账户矩阵与配置分层 → **[各环境 `accounts/` 配置与差异矩阵](kb/accounts-matrix.md)**

---

## 5. 正式环境对存储、内存与运维的限制

- **硬件水位（2026-11-19 实测）**：根分区 `178 GiB` 已用 `146 GiB`、**可用仅 `25 GiB`（86%）**；RAM `7.5 GiB` + Swap `17 GiB`（4c8g）。
- **资源限额**：`MODEL_JOB_NUM=4`，全局后台初始化仅 **1 个槽位**（单户 ~14 分钟）；Linux 初始化 cgroup 限制 `MemoryHigh=4.5 GiB`、`MemoryMax=5.0 GiB`、`SwapMax=12 GiB`、`nice=10`；普通交易 `nice=3`。
- **存储红线**：① 生产所有测试/对拍/备份只能放在应用根目录 `test/`，整个 `test/` 必须随时可人工删除且不影响业务；② `data/server_initialization` 与 `data/model_replay` 属受控业务工作区（由 `lifecycle.py` 持锁自动清理，注意硬链接残留会阻止上游 `unlink` 释放物理块）；③ 上游 `arrow_snapshot_keep=1`（原子替换期间仍需预留 1 份峰值写入空间）；④ 禁开默认全量每小时采样。

完整资源参数、目录红线与发布/回退清单 → **[正式环境存储、内存与运维规范](kb/production-limits-and-ops.md)**

---

## 6. 历次主干修改记录

从 `2026-06`（增量模块 1–5、三大 C 扩展、Arrow mmap + Polars 截面）→ `2026-10`（2.3.1 升级、三因子异常修正与动量 V2 C 加速、MODEL_ROBUST40 v1 独立偏移移植、服务器自动初始化与共同 checkpoint 回放）→ `2026-11-01~06`（共享计算管线、局部 gen-0 GC、P1–P4 性能优化、Arrow 按需发布、超恢复范围退出 75 即时后台重建、可删除 `test/` 与工作区回收、1.1.12 全量合入、2.3.3 升级与现货成本、1h 损益匹配修复）→ `2026-11-19`（**新上币 universe 扩张误判重建修复 A1 + A′ + `merge_context` 回填**，正式上线 `edbbba3`）。

完整双端时间线与提交映射 → **[历次主干修改记录](kb/changelog-main.md)** · 逐条原始技术日志 → **[docs/DEVELOPMENT.md](DEVELOPMENT.md)**

---

## 7. 与外部第三方版本 `core` 的关系

- **当前集成版本**：交易端为邢不行框架 **`2.3.3`（`v2.3.3.20261030`）** 本地增量集成；生产者端已完整合入 **`coin-realtime-data_v1.1.12`**。根目录 `2.1.1core`～`2.3.3core` 与 `coin-realtime-data_v1.1.12` 仅作只读对照参考，不入库。
- **核心边界**：严禁整体覆盖 `core/`；必须永久保留 Arrow mmap + PKL 回退、Polars 截面、动量 V2 全历史回退、`entry_exit` 续算、`dry_run` 防写保护、MODEL 窄钩子与 26 点恢复、执行器 JIT 缓存、局部 GC、统计单源快照及资源治理。升级依赖须在独立 `upgrade233/alpha311` 环境完整解析并通过 `pip check`。

详细版本对比与不可丢失定制清单 → **[与外部版本 `core` 的关系及定制边界](kb/upstream-core-relations.md)**

---

## 8. 当前未合并主干的分支与多机同步状态

- **未合并分支**：`D:/alphaonline` 与 `D:/alpha` 两仓 `git branch --no-merged main` **均为空**（全部功能分支已合入各自 `main`）。
- **已合入但未发布差异**：
  1. `D:/alphaonline` 本地 `main`（`bac2443`）比正式机 `production/main`（`edbbba3`）多出 1 个未发布提交 **`6c02e58`**（`2026-11-07` 统计展示精简与多空分侧前 5）。
  2. 测试服务器 `43.173.122.28` 停留在 `2026-11-06`，尚未同步 `2026-11-19` 的 `edbbba3`。
- **遗留现场**：存在 1 条历史保护 `stash@{0}` 及 2 个附加 worktree（`s3-hotfix`、`research/coin-cap-proxy`），待确认后再清理。

完整分支、stash、worktree 与同步明细 → **[分支、未发布提交与多机同步状态](kb/branches-and-sync.md)**

---

## 9. 重要性能分析

- **现行线上常态（正式机 `25m-eart1106`，2026-11-19）**：`prepare_s ≈ 6.7–9.9 s`（`compare_s ≈ 0.75–0.96 s`，D1 5000 根联动使修订读取从原 `18.3 s` 降至 `5–8 s`）、`account_calculation_s ≈ 138–142 s`、`total_s ≈ 166–170 s`；2h 回放 `total_s ≈ 180–184 s`；全量单户初始化 `13–15 分钟`（单槽位）。
- **已兑现的大项收益**：动量 V2 C 快速路径（`30.3→2.88 ms`/币）、局部 gen-0 GC（映射全代 GC `13.13 s→2.3 ms`）、MODEL 原生日期（特征 `-60%`）、P1 `LiveRebRobust40` 可缓存 JIT（目标计算 `~15 s→~1.4 s`；注意修改该文件后首轮会一次性重编译 `~29 s`）、新上币扩张免全量重建（每波消除 `73–97 分钟` 停单）。
- **当前第一大剩余瓶颈**：**时序因子阶段（`FACTOR_BLOCKS`，低干扰 `~82–93 s`）**，CPU 热点集中于市值外部处理（`29.4` 核秒）、`ZfMeanQ`（`29.2`）、`Cci异常隔离`（`23.5`）、`111_输入隔离`（`17.0`）、`CorrBTC`（`16.3`）、`pandas→Polars`（`15.0`）；且与上游生产重叠窗口会引发数十秒 swap/IO 波动。

完整阶段剖面与热点数据 → **[重要性能分析与瓶颈剖面](kb/performance-analysis.md)**

---

## 10. 未完成或未尝试的任务方向

- **P0（近期）**：实施 [plan.md](../plan.md) 问题 B（24h 盈亏 `[未匹配]` 历史归属回填）；决策 `6c02e58` 是否同批发布；测试机同步 `edbbba3` 并在线上观察下一次自然新上币遥测；盘点正式机 86% 磁盘水位。
- **P1（中期）**：问题 A Phase 2（两周观测后再评估零持仓退市/移除序列放宽）；闭环 Windows vs Linux 21 个现货杠杆准入规则差异；按独立 oracle 对 `ZfMeanQ`/`Cci`/`111`/市值等时序因子热点单项优化；评估 P4 相邻 `ticker` 复用。
- **P2（条件推进）**：更广范围子策略模拟行情共享、修订 Arrow 读取再窄化、传统 45m/偶发聚合慢轮定位、1.1.12 历史映射账户影响对账、异步统计争用观察。

完整优先级路线图 → **[未完成与未尝试的任务方向](kb/roadmap-and-backlog.md)**

---

## 11. 效果不好的尝试与避坑清单

- **已撤回/暂缓**：市值 `log10` 去重求幂优化（完整进程中位反慢 `102.7→107.5 s`）、修订 Arrow IPC 投影（暖态反慢 `1.73→2.15 s`）、删除选币每组 GC（反慢 `112.8→117.9 s`）、资金曲线 CSV→内存旁路（`4.44e-16` 尾差破坏严格一致）、`pending 8→16`（无稳定收益）、`MemoryHigh 4.5→7 GiB`（无改善）、截面 2→4 线程（仅差 `0.06 s`）、直接发精确 4050 根 Arrow（破坏映射预热）、聚合备用 v2（索引名不匹配）、消费端重建 Pivot（已永久删除）。
- **明确不推进**：重写 Bias（仅 `0.048 s`）/ Hybrid（仅 `0.43 s`）、普通专家盲目并行（仅 `1.55 s`）、按年跳读 Pivot（仅 `1.66 s`）、Tail-Only、数据库替代状态、全局禁 GC、`restore_model` 另写 remap（A3）。

完整实测数据与避坑指南 → **[效果不好、已撤回或明确不采用的尝试](kb/rejected-and-failed-attempts.md)**

---

## 12. 历史 `docs/`、`research/` 与根目录 `.md` 归档索引

两仓现存 140+ 份历史评审/设计/发布文档与 `research/` 离线实验目录的分类导航 → **[历史文档与 `research/` 归档索引](kb/archive-index.md)**
