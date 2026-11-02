# MODEL_ROBUST40 参数含义与当前生效状态

核对日期：2026-10-31。所有账户继续独立维护有效策略参数。已将八个确认无效的历史字段注释掉，并从状态一致性比较中排除；分钟偏移只使用account_config.hour_offset。本轮改变配置解析/比较，不改变仓管数学或开启新的调参开关。

## 一、生效参数、历史字段与运行配置

| 字段 | 含义 | 当前MODEL行为 |
| --- | --- | --- |
| `rebalance_anchor_time` | 漂移组周期再平衡的时钟锚点 | **生效**。通过父参数传给共享Hybrid，周期模式/`both`使用；不规定行情、S3或训练起始日期 |
| `method='native'` | 旧研究分配器选择原生Hybrid权重，而非额外优化分配 | 当前无方法切换分支，**不读取此字段**；仓管流程由策略实现确定 |
| `gross_cap=2.5` | 同币净额后，总绝对目标敞口上限的历史声明 | 算法有2.5约束，但来自`solve_sleeves`默认值及`expert_views`内固定值，**未从账户字段读取**。改字段不会改实际约束 |
| `risk_definitions=['down','up']` | 旧研究选择BTC下跌/上涨条件下的beta风险维度 | 当前固定计算并使用up/down，**不读取此列表** |
| `momentum_60=True` | 将S5动量策略的基准权重设为0.60 | 当前代码固定执行`base[4]=.60`，**不读取此布尔值**；之后仍受敞口约束、再择时和模型减仓影响，并非最终账户恒定持有60% |
| `short_single_coin_max_ratio=0.05` | 字段名称表达空头单币5%上限的意图 | 当前MODEL及其调用的Hybrid没有对应读取。**不能认为该字段提供单币5%限制**；框架另行配置的限仓功能属于独立机制 |
| `research_inputs` | 旧研究冻结的每子策略、每小时、每币目标仓位数据目录 | 当前实盘不读取，也不检查该目录是否存在 |
| `child_returns` | 旧研究子策略小时收益率表 | 当前实盘不读取此文件 |
| `conditional_risk_path` | 旧研究预计算的条件beta文件 | 当前实盘不读取此文件 |
| `account_config.hour_offset='40m'` | 本账户的分钟偏移与决策网格 | **唯一配置来源**。runtime.minute已移除；内部minute按需派生，状态自身时间轴/offset校验保留 |
| `enable_live_hooks=True` | 框架调用仓管实盘扩展入口的开关 | True调用prepare_live和transform_targets；False跳过二者，仍运行calc_ratio。当前MODEL必须为True，关闭会报错，不是停单或禁止训练开关 |
| `runtime.server_init=True` | 服务器自动初始化、4050续算及完整状态交接模式 | **生效**，是运行模式，不是收益模型参数，也不表示每轮重训 |

当前MODEL内部时间轴为UTC-naive。因此`2021-01-01 00:00:00`锚点按UTC解释；不要求下载2021年以来行情。周期检查先计算锚点之后的理论周期点，再在本offset已有K线时间轴中找不早于该点的记录。例如3456小时周期使用锚点加整数倍3456小时，并保持该offset自己的K线网格。S3起点独立来自账户截面第四参数，季度训练窗口由仓管算法和可用历史决定。

其他仍生效的父仓管配置包括`factor_list`、`cap_ratios`、`direction_list`、`rotation_indices`、`dynamic_groups`，由共享Hybrid计算轮动和漂移基准。顶层factor_list用于框架准备仓管所需因子；parent.factor_list供Hybrid读取，当前配置保持一致。不要将上述旧声明字段与这些生效参数混为一谈。

## 二、三个E盘路径原来做什么

已只读核查本地原研究代码及目录：

1. `research_20261019_cross_strategy/inputs/`：含`index.pkl`时间轴、`columns.json`币种列、`positions_0.npz`至`positions_13.npz`稀疏目标比例、`retiming.pkl`再择时乘数等。原`CrossStrategyResearch1019._inputs()`读取这些冻结产物，分析跨策略持仓、净额和风险；不是交易所行情源。
2. `research_20261019_momentum_hedge/tables/CHILD_RETURNS.pkl`：子策略小时收益表。原`SharpeResearch1020`等离线分配研究读取后聚合日收益，用于收益、协方差或分配估计；其native分支本身就可不读取该收益表。
3. `research_20261020_sharpe65/inputs/conditional_beta.pkl`：预计算的风险时间轴及beta数组。原`ConditionalBeta1020`按`risk_definitions`选取相应风险矩阵进行多空权重约束。

当前alphaonline已改为：框架本轮子策略资金曲线/仓位/再择时 → `build_context` → 本offset行情计算`conditional_betas` → 专家模拟及季度模型；状态保存在本账户snapshot。三个路径已注释，既不读取文件，也不作为配置一致性依据；对应注释可以直接删除。

**部署不需要这些E盘文件，也不需要建立同名空目录、复制研究模型或将路径替换成服务器目录。** 服务器仍需要真实alpha行情、预计算Arrow、共享exginfo、当前子策略依赖的BTC/市值等辅助数据，以及现有snapshot或足够的初始化完整历史。这些是真正的数据依赖，不能与旧研究路径混淆。

## 三、原问题原因与现在的比较规则

原来warmup.settings比较整个配置字典，prepare又直接比较保存的整个parent字典，将历史说明字段与计算参数混在一起。因此文件不需要存在，但删除路径字符串却会触发配置变化。这是比较口径过宽，不是算法或pickle文件的必需依赖。

现由positions中的effective_parent_params明确排除method/gross_cap/risk_definitions/momentum_60/short_single_coin_max_ratio及三个研究路径；tools中的effective_settings同时排除冗余runtime.minute。当前配置和旧snapshot使用同一口径，旧元数据和模型文件不改写，不做状态格式迁移。新初始化保存有效parent参数。

仅这八个已确认无效字段被排除：有效字段、未知字段、账户hour_offset、server_init、子策略池、S3和其他原来比较的内容继续严格比较；状态时间轴和账户身份检查保持。删除旧路径/历史声明或冗余minute不会单独要求重建，有效策略变化仍按原规则重建/报不一致。实现只浅拷贝少量配置字典，不增加大文件读取、行情扫描或哈希。

若将来接通gross_cap等参数，必须同步从排除清单移除并验证所有计算/状态路径。当前取消注释也不会让它们成为调参开关；不能把此项配置清理当作算法功能扩展。

## 四、server_init=False会怎样

- True：由MODEL Coordinator选择完整初始化或4050 Arrow续算，配置worker profile、恢复点/修订回放及计算完成标记；clean_start为这类账户保留对应snapshot。
- False（或未配置）：Coordinator使用普通core/account_exec.py入口，不建立MODEL自动初始化profile，也不提供这条自动恢复流程。**不是暂停交易、不是禁止重训，也不是切换回另一套普通仓管策略**；name仍然是ModelRobust40Strategy。
- 旧手工预热路线可另配bootstrap_path/state_path，必要时配warmup_path。但当前三个生产账户只有自动模式配置，单独改为False会在需要状态路径等输入时失败，不能自动转换成可用的手工模式。
- clean_start=True时，不再按server_init账户规则保留其snapshot；旧warmup_path路线只有在正确配对、预检及恢复都成功时才提供自己的交接。不要用False作为临时停单开关。

本轮保留这一既有False分支，未取消功能或新增启动门禁；在账户旁标明其含义。

维护职责保持：accounts写各账户完整参数与生效说明；positions保存完整仓管实现，在固定规则和接口处说明来源；tools只维护初始化/快照/数据/日志功能及功能配置。注释与实际读取行为同步更新，避免注释声称存在未实现的开关。

验证覆盖旧字段删除后的配置相等、有效/未知参数变化仍识别、全部5分钟offset派生、False原调度分支、初始化profile及Windows spawn。未启动/重启、重算生产数据或下单。部署步骤见[部署指南](model-robust40-deployment.md)。

## 五、enable_live_hooks的True与False

True：框架整理子策略资金曲线后，调用prepare_live准备输入、计算或续算模型状态；仓位聚合及框架限仓后，调用transform_targets核对目标并应用S5修正，server_init开启时还完成状态交接。它与server_init职责不同：前者接通策略扩展入口，后者选择自动初始化/续算运行模式。

False或省略：core/real_trading.py跳过这两个扩展入口，但仍执行calc_ratio和后续交易流程。当前MODEL的calc_ratio需要prepare_live写入的结果，普通入口会报MODEL_ROBUST40 requires prepare_live before calc_ratio；直接调用prepare的工具路径也会因该开关为False而拒绝不一致配置。因此当前MODEL账户必须保持True，不能用它暂停交易或关闭重训。其他不依赖扩展入口的传统仓管无需开启。

三账户和三历史示例已补充True/False注释，保留原值。本次只改注释及文档，六份配置修改前后AST相同，无运行开销或状态重建要求。
