# MODEL运行工具

**策略不在本目录实现，策略参数也不在本目录维护。**

账户字段生效状态与E盘路径含义见 [参数说明](../../docs/model-robust40-parameters.md)。八个确认无效的历史字段已注释并排除比较；分钟偏移只取账户hour_offset，工具不维护第二份策略配置。

- 每个 `accounts/<账户>.py` 独立声明完整策略名、仓管参数、子策略池、S3日期和offset。
- 仓管实现集中于 `positions/ModelRobust40Strategy.py`，包含专家、季度模型、S5减仓、模拟/续算及仓管自身恢复点。
- 框架因子、截面、再择时仍分别在 `factors/`、`sections/`、`signals/`；本轮不调整。

用户已确认：初始化调度、快照事务、行情冻结和日志等运行工具留在tools。以下按功能维护，不再按仓管内部算法拆文件。

| 文件 | 功能边界 |
| --- | --- |
| `initialization_config.py` | **仅功能配置**：统一计算并发MODEL_JOB_NUM（初始化/续算/手工预热）、历史长度、ready等待、恢复点数量、修订输入范围；无策略名、权重或模型超参数 |
| `server.py` | 账户/offset分发、数据及状态就绪检查、snapshot安装/完成标记、初始化槽位 |
| `cli/server_robust40.py` | initialize/compute/trade进程入口和Windows spawn配置；调用框架及positions仓管 |
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
