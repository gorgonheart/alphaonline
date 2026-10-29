# MODEL_ROBUST40 v1：独立分钟偏移移植

用户在 2026-10-29 确认：本地主干保留 10 月 22 日前基线；新分支移植 MODEL_ROBUST40，20m/40m/55m 分别计算，重建回测基准，不共享平移的 0m 父权重，不启用 Tail-Only。

基线 main=e6fc3bd，开发分支 codex/live-offset-parity，旧草稿 codex/robust4h-draft-20261029=5098bb7。没有部署或生产写入。

## 已实现范围

- 条件 beta、八个专家视图、九个虚拟账户、周度 robust_tail、季度 MODEL、每日评分与 S5 专属减仓。
- 20m/40m/55m 分别以自身行情、持仓、净值训练和计算；不再平移 0m 父权重。
- 三份完整独立回测配置和正式根入口回测。复用已有 14 个子策略缓存，重新计算父策略、模型、delta 和组合资金曲线；没有声称重新运行全部原始行情选币。
- 实盘显式准备/目标变换接口、按账户隔离的历史和虚拟账户续算、同输入交易链路对账。全历史聚合保留，不做 Tail-Only。
- 三份未启用账户示例在 `examples/model_robust40/`，不放入会自动发现的 `accounts/`，不含凭据。

## 固定研究规则

14 子策略使用三目录的 config_MODEL_ROBUST40_*.py；父配置使用原 G_M60_ASYM 的 Hybrid 参数、M60、2.5 净额后 gross 与 ±0.3 净敞口/条件 beta 限制。八个专家分别半减 S6–S13 并在同侧再分配，S5 不动，保留约束；九个虚拟账户以 20% 死区、2.45 执行 cap 生成收益。robust_tail 每周一使用此前最多 365 个日收益、至少 180 天，S5 日收益最低 20% 为尾部；正分前三个专家总权重最多 0.5，其余为基础组合。

MODEL 为 15 特征季度 LogisticRegression(C=.1,max_iter=500,solver='lbfgs')；365 日训练窗、48 小时间隔、至少 180 个采样日，标签未来 24h 收益低于 −5%。每日 00:mm 评分，逐小时按 S5 当前成员重新排名，百分位 >.8 时扣除该 S5 有效目标的一半，实际组合净多头最多减至零。最终执行死区 40%、cap=2.45。S5 再择时只应用一次。

新日键为 `(UTC时间−分钟偏移).normalize()+分钟偏移`。没有足够训练历史时沿用研究的无评分预热，不拿未来季度或其他偏移模型兜底。模型异常、历史缺口、错位与预热是不同状态，不能混为“没有风险”。

## 实盘接线与 core 修改

`process_equities → strategy.prepare_live → calc_ratios → agg_pos_ratio → apply_position_limits → strategy.transform_targets → calc_target_position`。

准备接口读取本账户、明确分钟的子策略净值/选币/再择时、原始 swap PKL 与已发布 pivot。内部时间统一 UTC，保留分钟偏移，要求最后一根等于本轮 UTC 触发时间减 1 小时。策略定义、父参数、模拟器配置、执行参数不同即停止。

父策略输出不提前乘再择时，框架 `calc_ratios` 仍只乘一次；S5 delta 自身包含 S5 的一次再择时。变换后与共享引擎全窗口目标再次对照。币种按名称对齐，虚拟账户的已有持仓、待执行目标、价格随新增列一起映射。新币补零，历史退市列保留。

按最小修改原则收敛后，相对 main 只有 **4 个 core 文件、+27/−4 行**。core 不导入 MODEL 模块，共享账户模型、父策略模型和调仓算法均恢复主干；没有替换框架已有函数或调仓模式。

| 修改位置 | 通用接口与默认兼容性 |
|---|---|
| `core/real_trading.py` | 通过已有 `core_funcs` 调用 prepare/transform；必须同时显式设置 `params.enable_live_hooks=True`。旧策略即使存在同名函数也不会被调用 |
| `core/position.py` | 执行器显式声明 `use_full_equity=True` 才使用完整权益；默认仍扣 1.001 和原现货预留 |
| `core/model/backtest_config.py` | 按配置实例传递可选 `required_history_start`；默认 None |
| `core/utils/datatools.py` | 仅该配置显式设置历史起点时读取完整保留 PKL、检查起点；其他策略继续原 Arrow、回退及尾窗路径 |

MODEL 自身在 `positions/ModelRobust40Strategy.py` 加载时，通过 `tools/robust40/live_execution.py` 向现有按名称查找的调仓表增加独立 `RebRobust40` 名称。这是进程内的附加注册，不覆盖内置模式；重复同类注册允许，名称冲突报错。核心默认导入不加载 MODEL。私有执行器包装冻结回测算法，独自处理价格检查、lot size 类型和完整权益声明。`target_equity_mode` 账户字段已移除。

core 外的 `config.py` 允许显式账户覆盖 `simulator_config` 和 `required_history_start`；两项每次加载均恢复默认，防止从 MODEL 切回旧账户时残留。已扫描当前 accounts，没有账户定义这两个新增模块级配置。MODEL 示例只放 examples。

2026-10-30 按用户要求统一共享 section：`TopKTo2K` 的公开 pandas 入口复用原 Polars 实现，统一 2.1 倍退出边界与市场分组；`VolumeMaxBias_进出场阈值分离官方` 全量初始化补零，空截面也生成结果列，增量分支仍由历史 merge 提供结果列。所有引用这两个原名称的策略均生效，旧策略走 pandas 时边界选币可能改变。删除两个 Robust40 专用 section 文件、示例别名和研究定义别名转换；MODEL 使用原名称，直接逐项核对研究定义。此次不修改 core。

只在 position 层修改无法保证输入历史、父权重和再择时正确，也不能让回测模拟器自动采用同一规则。数学和策略约束集中在 `tools/robust40/`，core 仅提供默认关闭的通用调用点。

账户的 `rebalance_mode` 必须放在 **account_config 内部**，不设成模块级全局变量，否则也会改变 14 个子策略的模拟器。示例保留子策略默认 RebAlways，子策略模拟费用/最小订单与研究一致（现货 10、合约 5），组合才用 40% 死区。

## 初始化历史与续算

S3 的状态因子固定从 2025-11-28 初始化，并要求此前至少 720 小时；5000 根当前尾窗不足。示例要求数据中心保留至 2025-10-28 00:mm 或更早，启用时完整 PKL 路径取代短 Arrow。新币以真实 first_candle_time 判断，不要求上市前虚构数据。S5 动量 V2 声明完整历史预计算参数，保留现有缺列回退行为。历史要求只保证最低长度，不能保证生产者与研究数据逐值相同。

`runtime_bootstrap.pkl` 保存每偏移历史、模型及 9 个虚拟账户和最终组合的续算状态。状态路径必须在 `data/cache`、`data/runtime` 及 runtime_folder 外；每账户使用不同路径，第一次运行绑定账户名，后续拒绝跨账户复用。原始 bootstrap 不覆盖。

新输入跳过头部 1000 根初始化区，取末端至少 168 根重叠验证：选币、再择时、行情、子策略净值/因子须一致；子策略 OHLC 允许一个恒定权益缩放，验证容差 atol=1e-8、rtol=1e-6。历史更改、缺口、错误偏移、过期数据不静默拼接。模型按新偏移日刷新；没有 180 天训练历史属于无评分预热，模型/输入错误则报错。

状态使用互斥锁和原子替换；计算失败保留旧状态。异常退出可能留下 `.lock`，须确认没有同账户进程后由操作人员处理，程序不自动抢锁。现有主流程在计算前可能已经撤销挂单；新策略失败会阻止新目标下单，不能把它描述成整轮没有任何账户操作。

## 验证结果

正式回测共同区间：2025-11-28 00:mm 至 2026-10-27 20:mm，共 8,013 根。共享引擎另保留一根到 21:mm，用于目标/续算；不将未共同覆盖的最后一根拿来比较收益。

| 偏移 | 正式回测 vs 共享引擎最大误差（权益/成交额/费用/敞口） | 最近 2,000 根实盘聚合目标误差 | 末根目标手数不一致数 |
|---|---:|---:|---:|
| 20m | 6.55e-11 | 0 | 0 |
| 40m | 2.18e-11 | 0 | 0 |
| 55m | 2.91e-11 | 0 | 0 |

三偏移各自暂停 240 小时后继续，父权重、delta、资金曲线与全量计算误差均为 0。最终代码另对三偏移各做 24h 断点复核，三项误差仍均为 0。验证包含未执行订单、费用和持仓的保存恢复。交易链路对账实际调用本仓库 `calc_ratios`、`agg_pos_ratio`、`calc_target_position`，使用相同权益、持仓、价格、币种准入和最小手数；未调用交易所。

初版全仓离线结果为 305 passed / 4 skipped / 100 warnings；本次隔离重构后的结果见 [策略隔离审查](2026-10-29-strategy-isolation.md)。新增五个指定账户配置加载、三种原调仓模式与主干目标手数对照、显式 hook 开关、配置重载、框架共享模块恢复测试；共享因子已于 10 月 30 日按用户要求统一修正。三个偏移重新经过实际聚合和目标手数接口，目标误差及不一致手数仍为 0。MODEL 因子边界保留独立 oracle；动量 V2 数学和预计算/完整历史声明不变。

原 Hybrid 的 0/20/40/55m、1/4/6H 与首尾完整/不完整周期 48 组合亦已离线对照独立 OHLC oracle 和回测函数一致；MODEL 本次正式数据验收仅覆盖 20m/40m/55m。

## 产物与重现

- `research/model_robust40_v1/{20m,40m,55m}/`：parent、delta、季度模型、训练窗、资金曲线、bootstrap、完整回测配置、正式结果与日志。
- `research/model_robust40_v1/integration_report.json`：三偏移对账数值。
- `tools/build_robust40_reference.py`：由原子策略缓存生成独立父策略/模型，不覆盖已有目录。
- `tools/prepare_robust40_backtest.py`：复制原缓存至新输出目录，生成完整独立回测配置；只重定位已有 CacheGuard 输出路径，不另建哈希协议。
- `tools/verify_robust40_continuation.py`：全量/续算检验并生成 bootstrap；`--verify-only` 保留已有 bootstrap。
- `tools/verify_robust40_integration.py`：真实数据通过实盘聚合和目标手数映射。
- `tools/prepare_robust40_live_configs.py`：从研究定义生成三份未启用账户示例。

统一使用 alpha311，例如：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' -B -X utf8 tools/verify_robust40_integration.py
```

正式回测使用各 E:/quant 偏移目录的根 `backtest.py`，`--config` 指向上述新配置，`--reuse-children-from OV1028B_<分钟>m_ORIGINAL --cache-mode positions --job-num 2 --factor-col-limit 40 --no-plot`。所有业务输出在 D:/alphaonline/research 新目录；E 端业务代码和旧结果未改。

## 上线前尚需验证的外部条件与代价

本次未部署、未重启、未下单，未将线上 Hybrid 账户换成 MODEL。**当前结论是相同输入下的模型、聚合和目标手数一致，不是服务器生产行情到实际成交全链已一致。** 线上 alphatrade1 原有账户是 5/15/25/35/45/55m；20/40m 还需数据中心明确供数。

启用前需要针对生产当轮做离线输入复验：初始化历史是否保留、14 子策略定义/费用/币种过滤是否匹配、完整动量预计算历史是否一致、原始字段/pivot 是否齐全。现有研究子策略缓存与生产冷启动结果若不满足重叠验证，须用同源生产输入重建该偏移子策略和父策略基准，不能删除校验强行通过。本次没有以原始生产数据重跑全部 14 子策略，也没有验证交易所实时准入、实际费用、滑点或成交。

三偏移 bootstrap 每份约 344 MB，历史随时间增长；运行内存还包括矩阵/原始行情和多份对齐副本。全量重建实测约 80–90 秒/偏移，日切特征/模型刷新约数十秒；常规小时续算复用虚拟账户，不重跑全部历史模拟，但仍有完整历史读取、验证、Hybrid、beta、聚合和状态写盘成本，尚无生产延迟验收。冻结规则未重调，回测结果不是收益保证。

没有完整 Git 中间快照，旧基线是根据本地文档及来源重建的功能基线。详细差异与限制见 [差异报告](2026-10-29-live-backtest-differences.md)。
