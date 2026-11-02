# MODEL 模拟数据来源统一：最小改动与人工复核

## 范围与决定

按用户确认，参照本地 `2.3.1core/` 的职责划分：公共市场决定模拟选币范围，账户权限在真实执行层过滤。初始化和正常运行使用同一种公共客户端，子策略与 MODEL 的模拟单位统一读数据中心 exginfo。用户随后明确不做旧状态兼容，旧数据可以完整重算；本轮不实现旧手数转换。

本轮 **core/、startup.py、账户配置、sections/、alpha 全部零新增修改**。通过与本轮开始保存的文件逐字节比较确认 core/startup 未改变；仓库中这些文件已有的未提交修改保留。未改变真实交易的 minQty 解析、订单取整、资金预留、权限过滤或自动补 BNB。

## 开发改动与审核项

| 文件 | 改动 | 类型 / 审核要点 |
| --- | --- | --- |
| `tools/robust40/live.py` | MODEL 单位仅取 `read_bmac_exginfo().lot_size`；每个市场最多读一次；先合并 14 个子策略列名再归一化 | 取消账户 minQty 作为模拟单位来源、取消缺失时多源兜底；保留未知/非法单位报错；新旧单位不同时要求重算 |
| `tools/prewarm_robust40.py` | 两个预热入口共用 `prepare_public_market`；公共客户端使用独立实例市场缓存 | 保留账户 bStocks 名单刷新，公共客户端负责现货/合约市场列表；不调用账户余额、订单或 BNB 操作；防止 fork 继承账户权限币池 |
| `tools/server_robust40.py` | 初始化冻结公共选币规则供子进程使用，正常 MODEL worker 使用隔离缓存的公共客户端；取消 `_server_market_info` 和虚拟账户的 bn 字段 | 取消初始化专属的账户权限币池；改动只在 MODEL worker 的进程内生效，其他策略维持原框架 |
| `tools/rebuild_robust40_live_baseline.py` | 离线基准使用同一单位函数，不再读取旧 bootstrap 的单位 | 新计算不依赖旧模型包存在，不把旧单位带入新基准 |
| `tools/robust40/runtime.py` | 重叠合并时检查共同币种单位相同 | 避免其它调用入口绕开 live 的检查后，把新单位与旧手数混用；无单位转换兼容路径 |
| `tools/robust40/warmup.py` | 现有 settings 加入固定的模拟数据口径；已有 snapshot 校验同时核对子策略与 MODEL 单位 | 复用既有配置变更重建流程，无新注册表/哈希/额外每轮大文件扫描；单位核对复用原本已经读取的子策略模拟器状态 |
| `tools/robust40/server.py` 与 worker | 不再保存或要求 snapshot 的 `model/market_rules.pkl` | 此副本没有续算消费者，取消冗余文件依赖；初始化工作区的冻结规则仍供 spawn 使用 |

另有一个验证工具的两行调整：`tools/check_server_section_parity.py` 接受可选 origin，用 snapshot 中实际生效的 S3 日期对拍，避免仅验证工具自己推算的起始日期。共 7 个运行工具文件和 1 个对拍工具；无 core/startup 修改。

取消项均在 tools 的 MODEL 专用适配层；不存在需要恢复的 core/startup 功能变更。公共模拟币池并不授予实际下单许可，`core/position.py` 原有账户权限交集仍生效。bStocks 属于必须保留的过滤功能，不能因改用公共客户端而丢失。

## 状态与上线行为

- 新配置口径为 `simulation_data_policy=public_universe_exginfo_units_v1`，由工具生成，不要求维护账户中的第二套配置。
- 原包与新口径不符时，现有 `inspect` 返回配置变更，由原 Coordinator 按账户/offset 自动完整初始化；不复用旧 MODEL 手数，不强行修改 complete 标记。
- 本地 20/40/55m 旧包只读检查均返回 `rebuild / configuration changed`。本轮没有删除、覆盖或安装生产 snapshot；也没有启动 startup、交易、通知或重启服务。
- `clean_start` 原行为不变。初始化安装、备份、失败保留现场仍沿用既有代码，不增加另一套清理逻辑。
- 初始单位来自同轮计算使用的缓存 exginfo，续算仍检查它与已保存单位一致。若交易所单位变化，失败的计算仍按现有 incomplete 流程在后续重建，不在持仓手数上直接换单位。

## 验证记录

- TDD：单位来源/初始化 20 项失败后修复；状态单位不一致 2 项失败后修复；无用规则文件检查 1 项失败后修复；bStocks 保留 1 项失败后修复。日志见 `research/model_source_*red.log`。
- 主专项 182 项通过：MODEL 单位、Windows spawn、账户/offset 路由、snapshot 生命周期、输入模式、S3，以及 5/15/20/25/45m 旧策略加载和目标仓位对照。
- 后续针对状态检查、完整包、跨日模拟和全部 0/5/…/55m 时间偏移的专项 87 项通过；bStocks/初始化/路由及快检专项 49 项通过。不同批次有重叠，不累计为独立测试总数。
- 20m、40m 实际 Arrow 对完整 PKL 因子重算各 78 组通过，比较末 4050 行及 NaN 分布，涵盖配置的动量、ILLQ、CciV3 参数。对象为 BTC、ETH、0G 的现货/合约样本，不声称所有币都完成了对拍。
- 55m 当前数据中心没有 Arrow 发布，不计入 Arrow 验收；不生成替代 Arrow 冒充 C 发布。
- 真实 40m 的 14 个子策略完整重算、MODEL 初始化、包校验通过。MODEL 共 13,065 小时，2025-05-04 00:40 至 2026-10-30 08:40 UTC；S3 实际 origin 为 2025-04-25 00:40。ALLUSDT 的新 MODEL 单位为 1，与子策略一致，fixture 中账户式 minQty=10 不再参与模拟单位选择。
- 与完整结果相比，末 48 小时分两次、各用 4050 行窗口续算后，parent/delta/account 最大绝对差均 0；9 个虚拟账户及最终账户的手数/待执行手数等模拟状态校验通过。独立逐子策略加权并施加再择时/S5 调整，现货/合约目标最大差均 0。证据：`research/model_sources_40m_v1/result.json`。
- 按各 snapshot 的实际 S3 origin（20m: 2025-04-25 00:20；40m: 2025-04-25 00:40；55m: 2025-04-26 00:55），三个 offset 的长历史与 4050 续算各比较 64,800 个值，全部相等。每个 offset 采用 8 个真实币种样本，证据：`research/model_sources_s3_effective_origins.json`。
- 公共缓存隔离补充 TDD 后专项 32 项通过；最终初始化/正常续算两种 profile 的真实 Windows spawn、加载入口绑定、账户缓存不被污染及初始化调用限制共 5 项通过。Linux fork 的继承缓存条件由单测模拟，未部署 Linux 服务。
- 新真实 snapshot 删除冗余 market_rules 副本后快检仍为 ready。新包仅留在 research，带 offline_fixture 的输入不能通过工具安装进生产 snapshot。
- 性能：1269 个元数据币种、14 个子策略列并集、两份元数据读取，20 次暖文件缓存测量中位数 12.59ms，最大 13.84ms，无网络请求。完整重算及对拍用时约 1027.5 秒，子策略链约 673.1 秒；这是本机本次验证耗时，不是线上每轮耗时保证。已清理本次新建的 91 个中间缓存文件（7,859,789,924 字节），保留原始输入、元数据、子策略结果及验证 snapshot，未清理生产数据。

## 验证边界及后续事项

40m 重算使用保留的真实长历史行情、当前账户策略配置，工作区和安装目标均在 research 内。公共市场 API 响应由保留的 exginfo 经过原版公共规则解析器构造为明确标注的离线 fixture，并刻意保留 ALL 的 minQty=10 / stepSize=1 差别；它不是当时真实 API 回包，不能代替联网市场规则验收。计算子进程禁止网络，fixture 包受现有检查约束，不能安装到生产 snapshot。

全量与续算对拍验证数值一致性，不以重新生成的结果自动充当正确 golden。另用子策略矩阵、父权重和再择时独立加权验证最终目标；包校验核对每个子策略 state0/state1 的模拟单位。

48小时4050窗口对拍属于MODEL层，使用完整计算得到的子策略输入；因子和S3另做上述样本对拍。本轮没有把全部币种、14个子策略的完整实盘Arrow工作流与真实账户接口做端到端比较，也没有生成20m/55m的全新生产基准。生产三个offset需在加载新代码后分别依正常初始化流程计算，不能直接安装此次离线fixture。

本轮不修改原版下单 minQty 与 stepSize 混用的问题。真实执行规则的修复会影响所有策略，需要另列最小改动及人工复核。元数据共享文件的一致性和历史单位变化的转换方案也没有混入本轮；本轮对单位变化采取重算，不兼容转换。

独立审核补丁：`research/model_sources_review.patch`，只包含本轮 tools 增量，不混入此前统计、时间戳、S3 或 startup 修改。
