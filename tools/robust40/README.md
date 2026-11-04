# MODEL运行工具

**策略不在本目录实现，策略参数也不在本目录维护。**

初始化数据重构（本地实现与验收完成，未部署）：`tools/initialization_pipeline.py`只负责编排，
`tools/initialization_data.py`管理临时块的读取、投影和释放，时序/截面与选币分别复用
`tools/streaming_factors.py`、`tools/streaming_selection.py`。开关位于本目录的
`initialization_config.py`。实测完整中位耗时772.8→622.5秒，提交峰值却升至7.69—8.03GiB；
当前保留False/16币，Ubuntu隔离验证后才切换已测组合True/32币。不改变正常4050入口和snapshot协议。
完整取舍与四个core接口见[实施复核](../../docs/2026-11-03-initialization-data-refactor-review.md)。

自动初始化使用完整Arrow，长因子直接复用；通用分批输入/时序计算位于 `tools/streaming_factors.py`，批次大小在 `initialization_config.py`。正常4050续算保持原路径。两端配套更新及新增core人工复核见[全长度Arrow说明](../../docs/2026-11-02-full-arrow-initialization-review.md)。

账户字段生效状态与E盘路径含义见 [参数说明](../../docs/model-robust40-parameters.md)。八个确认无效的历史字段已注释并排除比较；分钟偏移只取账户hour_offset，工具不维护第二份策略配置。

- 每个 `accounts/<账户>.py` 独立声明完整策略名、仓管参数、子策略池、S3日期和offset。
- 仓管实现集中于 `positions/ModelRobust40Strategy.py`，包含专家、季度模型、S5减仓、模拟/续算及仓管自身恢复点。
- 框架因子、截面、再择时仍分别在 `factors/`、`sections/`、`signals/`；本轮不调整。

用户已确认：初始化调度、快照事务、行情冻结和日志等运行工具留在tools。以下按功能维护，不再按仓管内部算法拆文件。

| 文件 | 功能边界 |
| --- | --- |
| `initialization_config.py` | **仅功能配置**：MODEL_JOB_NUM总上限、初始化/续算阶段上限、资源预算、初始化分片/历史长度/ready等待/恢复点；无策略参数 |
| `resources.py` | Linux自动初始化资源组、实际内存/IO压力准入、跨安装初始化互斥、批次准入与子进程优先级；不实现策略计算 |
| `server.py` | 账户/offset分发、数据及状态就绪检查、snapshot安装/完成标记、初始化槽位 |
| `cli/server_robust40.py` | prepare/initialize/compute/trade进程入口和spawn配置；调用框架及positions仓管 |
| `market_inputs.py` | 同轮ready、行情/辅助数据冻结、公共市场信息准备；生产与历史预热复用同一组函数 |
| `handoff.py` | 修订输入读取/比较、共同恢复的磁盘事务及失败恢复；仓管回退计算调用positions |
| `replay.py` | 共同时间点选择、现有section状态接口的离线调用；不再实现MODEL计算 |
| `warmup.py` | 读取账户配置、校验完整snapshot、历史预热包交接；不保存策略参数 |
| `offline.py` | 指定公开赋值提取、币名规范化及研究数据适配 |
| `telemetry.py` | 将耗时、回放及季度训练记录到现有日志 |

server中的S3起点/4050依赖检查是现有运行适配约束，使用各账户的实际参数。本轮未修改它们的数学/判断行为。仓管仍复用 `tools/local_risk_model.py` 的 `lock/atomic_save` 两个既有IO函数，不调用该模块的其他策略训练逻辑；未扩大修改其他策略。

## 命令入口

每个命令功能一个文件，位于 `cli/`。从项目根目录使用alpha311：`python -m tools.robust40.cli.<模块名>`。旧 `tools/<模块名>.py` 路径已迁移。

| 类别 | 模块 |
| --- | --- |
| 当前生产worker（由startup调度） | `server_robust40` |
| 日志汇总 | `summarize_replay_logs` |
| 离线因子/S3/仓管对拍 | `check_server_factor_parity`, `check_server_section_parity`, `check_server_model_parity` |
| 研究验收 | `build_robust40_reference`, `verify_robust40_integration`, `verify_robust40_continuation`, `check_robust40_dependencies`, `benchmark_model_checkpoints` |
| 历史人工预热 | `prewarm_robust40`, `rebuild_robust40_live_baseline` |
| 历史配置导出 | `prepare_robust40_backtest`, `prepare_robust40_live_configs` |

历史预热/导出不是当前服务器部署步骤。旧live配置导出器含旧bootstrap路径及BNB默认值，不用于覆盖现有账户。生产worker已不再从历史预热命令导入冻结函数。

部署、回退与人工复核见 [部署指南](../../docs/model-robust40-deployment.md)。历史文档和research脚本旧模块路径需按新归属调整后再运行。

最新资源实现：[实际压力调度](../../docs/2026-11-02-pressure-selection-review.md)。自动初始化固定4.5GiB回收目标、5GiB最后防线（扣监督预留），允许12GiB swap；不再每5分钟强行降至2GiB。按真实压力延缓新批次，低优先级与前台共存。`selection_block_hours=2016`已人工确认并发布alphatrade，设0可恢复原选币。[整池共享列缓存](../../docs/2026-11-02-shared-selection-review.md)一次时间排序、有限列转换、每块供全部策略共享，转换/读取最多MODEL_JOB_NUM并发，选币最多2线程。首次仍有转换及约4.70GiB临时磁盘成本；旧Parquet分区及其内存数据不再代表当前实现。仅初始化注入selection_pool_runner，正常4050续算不启用。[发布及回退记录](../../docs/2026-11-02-alphatrade-shared-release.md)列明生产状态；历史适配清单保留在[原资源窗口说明](../../docs/2026-11-02-initialization-resource-review.md)。
