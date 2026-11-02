# 服务器初始化实现与人工复核

日期：2026-10-30。框架实现及可执行的离线验证已完成，待人工审核。后续按用户“先在本地服务器跑通”要求，已切换本地20/40/55m的服务器初始化开关，排除startup对过期预热包的恢复；未代用户启动交易循环或下单。正式4050新增信号验收仍受CCI长依赖参数限制，不能把初始化接入解释为已具备上线条件。最新用户参数及启动可靠性修复见[本轮复查](2026-10-30-server-runtime-review.md)。

## 接入方式与边界

账户仍是唯一策略配置。本地 MODEL 账户 `strategy_config.params.runtime` 现为`minute, server_init=True, s3_origin='full_history'`，旧bootstrap/warmup/state路径已移除。由startup调用`tools/server_robust40.py`分发，worker设置snapshot内路径；不要直接运行core/account_exec绕过前置检查。自动补BNB设置未改。本次接入仅改账户runtime和测试/文档，core/startup没有新增差异。

`tools/robust40/initialization_config.py` 只保存输入/资源差异：完整 PKL 至少14000根合格长历史，实际不裁短生产者提供的140xx数据；正常输入4050；初始化计算 job_num=2；交易执行窗口240秒。框架原有阶段4/5/6池仍各最多4进程，并非整个初始化严格只有2进程。协调器一次最多运行一个独立初始化，正常已就绪账户继续串行交易。

初始化冻结同轮四个ready对应的raw PKL/pivot/exginfo，以及策略实际声明的外部因子目录；复制前后比较文件大小/mtime。生产初始化只读获取账户对应的可交易市场/合约类型/bStocks规则，传给隔离计算worker；不更新账户资金，不补币，不撤单或下单。规则查询与小时行情不是交易所原子快照，需在正式部署时复核实际过滤集合。

S3统一按合格长历史起点、上市过滤预留、实际S3参数n解析日期；向上对齐00:offset。已有日期仍可覆盖则保留，否则新建基准。实际参数使用ISO基本格式，如 `20250423T004000`，与 `2025-04-23 00:40:00` 等价且不破坏Windows因子缓存文件名。初始化与增量使用相同解析日期，正常4050滑窗不会移动它。

完整计算通过子策略/MODEL截止时间、末根权重及框架聚合对拍后，安装账户独立snapshot。包括14个子策略、sections状态、model/state.pkl、市场规则和warmup.pkl公开元数据。无旧bootstrap也能初始化。

成功的生产初始化会删除本协调器创建的scratch（严格验证root/账户/时间戳路径），保留外部日志与安装后的snapshot，避免每offset永久保存全量行情/因子缓存副本。失败目录和research验收目录保留，不自动清理用于复核的产物。

正常轮次只读取小元数据并stat必需文件；不重复加载大MODEL/ratio。先标记计算未完成，目标聚合对拍通过后提交完成标记。下单失败保留已完成计算，下一轮重新获取真实账户状态。计算中断的多文件状态不视为原子提交：下次自动全量重建；没有引入每小时整包备份或大量core写入替换。目录重建安装保留一份previous，切换失败可回滚；它不是每个增量小时的完整备份。此处是相对于原计划“保留每轮上一完整状态对”的明确收敛，需要人工审核。

同一账户使用OS文件锁，进程退出自动释放；账户/分钟/配置/共同截止不匹配、缺文件、Arrow缺列或超出补算能力触发重建。完整输入不ready则等待；不先删活动数据。相同发布/原因不重复启动。初始化耗时跨过当轮窗口后，下一合法offset自动续算；不会发历史目标。当前实现不抢占下一offset之间的空档交易。

## core最小修改清单（逐项人工审核）

| 文件 | 本轮用途 | 默认行为 |
|---|---|---|
| core/model/backtest_config.py | 传递input_mode和section_state并验证模式 | auto、False |
| core/utils/datatools.py | full_pkl在完整输入补算全部已声明预计算列；arrow_strict缺列禁止短窗补算；忽略旧固定历史起点裁切冲突 | 原auto及固定历史读取保留 |
| core/select_coin.py | pandas、Polars两个截面调用点转通用状态适配器 | 未开启仍调原signal/signal_polars |
| **新增 core/utils/section_state.py** | 通用signal_with_state协议、账户snapshot保存 | 只有显式开关且因子提供接口才启用；无MODEL引用 |

此前select_coin空仓小时补齐14行保留；既有real_trading两个可选钩子、position完整权益声明保留，本轮未增加交易入口core回调。新增helper是第四个core文件，不能在审核时遗漏。

startup：替换全snapshot删除为保护已开启账户的逐账户清理；分发前调用协调器，旧账户仍使用account_exec。config：新增通用默认字段、账户加载字段，reload回到auto/False。旧warmup路径保持兼容，新模式不再搬运旧外部预热包。

## 共享因子修改（独立于core审核）

1. 原VolumeMaxBias section增加显式状态接口：保存持仓、最多n%11个有效截面的延迟排名、4050小时标记和已处理截止。空交易截面不会清仓或推进排名队列。原全量入口继续可用。
2. S3并列排名固定为时间、symbol、is_spot顺序，补仓排名用稳定排序。独立失败测试证明旧算法会随输入行顺序改变持仓。**这会改变旧策略在并列排名处的结果**；需要统一重建基准，不能混用修改前的S3状态。
3. 生产者 `D:/alpha/factors/c_ext/illq_core.c`：滚动方差跳过±Inf，并以带误差补偿的中心矩递推替换平方和相减，消除高数值/低波动的精度损失。计算顺序核对[pandas 2.2.3实现](https://github.com/pandas-dev/pandas/blob/v2.2.3/pandas/_libs/window/aggregations.pyx#L291)。两类独立oracle均先失败再通过；仅在research目录编译验证，未替换生产扩展。构建脚本显式UTF-8读取源码，解决Windows GBK解码失败。原二进制/已发布Arrow不会因源码修改自动修复，需要目标平台编译、正常发布新数据后再验收。
4. 旧ILLQ golden文件未改；它记录旧C累加顺序，新算法相对其最大差约2.2e-14。测试不再把旧C位级结果当正确性oracle，改为独立Python比较，并收紧原Python比较的rtol到1e-12；另保留旧golden与Python的兼容性核对。实际全币池对拍的rtol/atol均保持1e-10，没有放宽。

## 验证记录（持续更新）

- 40m自动初始化实跑在第一条框架debug图标日志处因GBK编码退出，实际未进入策略计算；手工20m验证时环境显式UTF-8，未覆盖该Popen条件。修复tools/server_robust40 main的输出/继承环境及协调器Popen环境，core/startup零新增修改。强制GBK的真实重定向子进程先复现同堆栈后通过；初始化与分流12项通过。失败日志与目录保留，新epoch自动重试，未代用户重启/下单。
- 修复55m实跑的ready竞态：19:57:16进入协调器时dict已ready，但同轮swap pivot到19:57:18才发布。原先静默跳过整轮，现仅在tools/server协调器等待四个同轮ready，最长到原240秒截止；补齐所有跳过原因及初始化日志路径，core/startup不新增改动。晚到/超时测试先失败，修复后分流专项10项通过。需重启startup加载代码，未代用户重启或下单。
- 本地接入后实跑20m初始化通过（非offline_fixture）：真实市场规则、完整PKL、14子策略→S3→MODEL及聚合/状态配对校验，已安装账户snapshot。共同区间2025-05-04 00:20至2026-10-30 09:20 UTC，S3起点2025-04-25 00:20，78个必需文件，1.179 GiB，inspect=ready；成功scratch已清理。日志`research/local_startup20_initialize.log`、结果`research/local_startup20_result.json`。启动接入9项检查通过；没有新增core/startup差异，没有运行交易循环或下单。40/55本轮只切换配置及验证分流，未完整重算。ready状态后的4050分流仍被5366根依赖拦截，未绕过。
- 最终相关回归195项通过：`research/server_initialization_final_tests.log`。包括旧预热与5/15/20/25/45账户，使用accounts backup中的真实公开配置，不再跳过这50项。新增成功scratch清理后，生命周期/初始化入口补验8项通过：`research/server_initialization_followup_tests.log`。`git diff --check`通过。
- 真实Arrow→PKL全历史重算，20/40/55各78组（spot/swap的BTC、ETH、0G，13列）：40m全部通过；20/55各5组ILLQ失败，均为spot 0G。报告`research/server_factor_parity_*m.json`。这是抽样，不是全币池验收。
- 进一步扩大到40m**全币池1269个市场/币种组合、16497组**：旧Arrow有1797组ILLQ失败，其余10152组通过。仅跳过Inf后仍有141组ILLQ不通过，补偿方差修复后6345组ILLQ全部通过；原已发布Arrow仍保留1797组失败，工具没有把它改成通过。`research/server_factor_parity_40m_all.json`、`server_factor_parity_40m_stable_all.json`。C扩展独立oracle及生产者因子入口6项通过。
- S3真实8币、约14064小时，20/40/55各以4050短窗续算48小时，最终同轮ready检查下**194400个持仓标记一致**。`research/server_s3_real_parity.json`。合成独立oracle另覆盖所有5分钟offset、空截面、重复截止及并列排名。
- 40m完整14子策略、S3、MODEL从零初始化成功，框架聚合对拍及snapshot配对检查通过：`research/server_init40_v2.log`。实际S3起点2025-04-25 00:40 UTC，共同模型区间2025-05-04 00:40至2026-10-30 08:40，13065小时，4个季度模型。v1暴露Windows含冒号缓存名错误，修复后v2重跑。测试使用离线公开市场规则fixture，没有调用交易所账户接口；不能据此声明真实账户市场过滤已验收。
- 同一完整MODEL基准取末48小时前缀，再两次以4050输入续算：parent/delta/account最大差均0；目标与9个虚拟账户/组合模拟器状态均通过。`research/server_model_parity_40m.json`。这使用已经计算好的child历史验证MODEL衔接，**不包含不兼容参数下4050重新选币的虚假验收**。
- 实测snapshot 1.373 GiB，其中MODEL 0.556 GiB；元数据11659字节，78个必需文件，50次快检中位13.84ms、最大15.97ms。`research/server_snapshot_measurement_40m.json`。按这一个40m样本替换旧状态体积，新增11offset的常驻预算约90.4 GiB（其他行情项目沿用上轮估算），初始化暂存另计；110–120 GiB总预留建议仍保留，不能把它当其他offset实测。

## 人工复核清单

- [ ] 审核上述三个core修改文件及新增section_state helper；保留此前已审核的14行空仓补齐，确认默认旧策略路径不变。
- [ ] 审核startup逐账户清理、后台初始化、240秒执行截止、同账户OS锁及失败状态全量重建。
- [ ] 确认共享S3并列排序的行为改变与基准重建；确认ISO起点表达、full_history策略适用于目标账户。
- [ ] 审核生产者ILLQ两项数学修复、构建编码修复和oracle测试变更；目标平台重编译并重新发布数据后复核。
- [ ] 调参验证CCI2600替代方案，再运行4050新增小时的完整选币/手数验收，最后确认正式交易可用。本地server_init已按用户要求开启用于初始化验证；BNB保持原配置。

## 仍需完成的上线条件

- 用户明确选择暂不调参。`Cci过滤(2600)→TopK168`基本依赖5366根，尚不满足4050；代码保留参数并拦截。考虑持仓周期后还须留出缺失小时补算余量，不能只比较最大单因子参数。
- 修复后的C扩展需要在目标平台构建并由生产者重新发布；旧20/55 Arrow仍有已证实的错误。不能用扩大容差或生成golden覆盖。
- 正式4050新小时因子→截面→子策略→MODEL→目标手数全链路验收，必须用调参后的唯一账户配置和新Arrow；当前参数下不能宣称通过。
- Linux恢复、真实市场过滤、12offset资源与实际下单时序需部署前人工确认。本次仅离线，不启动任何实盘服务。
