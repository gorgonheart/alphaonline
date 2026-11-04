# 当前版本发布收尾与监控清理

本轮范围：读取旧发布记录、清理线上临时验证监控、维护文档、提交本地main。没有发布生产业务、重启PM2、改变账户、删除行情或snapshot。用户已接受25m差异归因作为阶段验证，并自行切换测试机实盘模式；这不替代实际成交记录。

## 已核对的发布基线

- Git main起点cee71bc；最近正式alphatrade的功能发布见2026-11-02-alphatrade-shared-release.md，18个Python文件逐文件更新，因此生产HEAD仍cee71bc但工作树有修改。
- 后续11月3–4日的因子multi接口、共享因子/截面/选币、GC及日期优化，在43.173.122.28完成离线和实际轮次验证。不能只用11月2日的18文件清单发布本版。
- 当前本地账号为20/40/55m；用户已删除本地5m传统账户文件，本轮不恢复。传统测试使用公开fixture，不把测试账户放回运行扫描目录。
- 两台服务器实际都只保留25m-eart1020.py运行账户。生产43.167.235.164是dry_run、数据源alpha2；测试43.173.122.28是none、数据源alpha。账户差异和模式由维护者管理，不随本次提交覆盖。
- 测试服务器是源码副本，没有.git；不能将其当已提交Git版本或直接运行git update。

## 监控清理结果

测试机alphatrade-observer.service原PID2859996，已停止；unit归档到research/monitor_cleanup_20261104/alphatrade-observer.service.disabled，原单元not-found/inactive。历史日志及研究证据保留。

清除两个已失败的live25-upstream旧版隔离单元、四个robust40-speed实验单元的failed状态；不存在对应运行任务或定时器。生产机未发现仍运行的live25采集器、资源观察器或关联cron，无需停止业务。

测试PM2 alphatrade-dryrun PID28034、alpha PID2731029在清理前后保持；名称仍叫dryrun不代表运行模式。pm2-logrotate保留。研究启动脚本research/live25_restart_20261104/run-dryrun.sh仍被PM2引用，内容只exec startup，因此保留，禁止清空整个research。

Codex旧25m自动任务此前已取消，本地automation目录没有25m配置。其他项目的自动任务不属于本轮，不改动。

保留业务statistics_config、通知设置、MODEL_REPLAY与必要阶段耗时、初始化资源限制及恢复机制；这些是正常运行功能，不作为临时监控删除。两个一次性live25采集/比较测试移至ignored research归档，不作为正式部署测试依赖。

## 本次累计发布内容

1. 因子：17个旧因子新增signal_multi_params，保留signal及旧索引/脏数据语义。
2. core：共享FactorBlocks、因子有界调度、截面与S3状态、选币必要列及原报告接口；初始化增加可选内存输入，默认旧接口可用。
3. tools/positions：完整Arrow初始化、统一初始化数据编排、资源控制、共同恢复与观测；MODEL日期转换优化。未实施刚审计出的目标手数JIT提速。
4. dry_run入口：跳过账户设置、杠杆等操作；none原交易功能保持，自动补BNB不关闭。
5. 测试/文档：包含共享流程、传统策略、GC、日期、Arrow、资源与状态回归；不携带临时监控、行情、测试snapshot、私有账户或Windows缓存。

主干自带默认仍保守：config.py的shared_factor_pipeline=False；INITIALIZATION.data_pipeline=False、factor_symbol_batch_size=16。本轮只提交既有功能，不静默替全部账户启用新路径。

已在Ubuntu验证的开启组合：机器config.json中shared_factor_pipeline=true；tools/robust40/initialization_config.py中data_pipeline=True、factor_symbol_batch_size=32、selection_block_hours=2016、MODEL_JOB_NUM=4。MODEL正常续算4050；上游完整Arrow发布用于首次初始化。不要只启用其中一个开关就宣称运行的是相同测试路径。未知custom/entry_exit等策略保持既有回退。

## 正式服务器下一次升级方式

当前生产目录有受跟踪修改和运行账户改名，旧deploy脚本会拒绝update，这是正确行为；本轮不添加新的生产验证/保护逻辑。

维护者发布时：先留存生产现有源码、config.py及其业务统计设置、config.json、accounts目录和accounts.local.json；只停止目标安装并等待worker/统计退出，其他实例不动。将当前main导出到独立暂存目录，核对全部新增运行模块后应用源码；现有25m账户及其名称、模式、凭据、S3日期、BNB、代理、行情路径原样保留。不要把本地20/40/55账户展开到生产accounts，也不要执行git reset --hard或git clean消除差异。

现存snapshot/data保持，不部署测试机snapshot，不因本次共享计算更新主动重建。原子状态/共同恢复协议不变，但完整候选上游同输入同状态及真实成交的覆盖边界仍按下文事实保留。若未来希望恢复纯Git fast-forward维护，先独立整理服务器专有账户及有意配置差异，不能由本次提交自动覆盖。

本轮只合入本地main，不push生产SSH仓库、不执行update或PM2重启。源码导出可用git archive当前提交；禁止将含历史私密配置的完整Git历史作为公共发布包。

## 阶段验证和未决事项

此前共享四场景、传统45m回归与Ubuntu离线对拍见2026-11-03-shared-compute-review.md、2026-11-04-speed-followup-review.md。四币主要分歧来自行情跨阈值/并列排名/等权分母与历史选择延续；用户接受为阶段验证。实际旧/新MODEL共同输入共同起点12项精确一致，不扩大成整套上游/S3/订单已同源通过。

2026-11-05 00:01完成最终Windows alpha311全量离线测试：1441通过、77跳过、0失败，406.79秒；610项既有弃用/设置警告未为通过而消除或放宽。使用配套D:/alpha源码，短研究临时路径。缺真实输入的skip单独列出，不视为通过。真实成交与账户变更由用户手工none测试验证；本轮未采集成交或声称成交通过。

后续性能建议见2026-11-04-whole-flow-performance-review.md：重复目标手数JIT约13秒最值得优化，但本版没有实现；不把审计建议写成发布功能。

## 本轮提交前修复

首轮全量：1446通过、77跳过、7失败。3项为Windows长临时路径导致Numba/Arrow创建文件失败；3项为旧测试未同步已批准的dry-run小额资产操作保护、初始化inputs参数和arrow_full_strict入口；1项为MODEL离线PKL读取run_time=None时错误探测Arrow文件。最后一项仅增加空值条件，不改变live分支或数值公式，原失败测试覆盖完整4100行及上市时间字段校验。

更新旧测试时只允许明确的单个AST保护条件差异，其余源码保持精确比较；传统45m公开fixture的strategy_config/strategy_pool与用户备份AST相同。基准脚本固定旧提交cee71bc，合并后不会变为HEAD自比。独立复核通过上述范围。归档采集测试换短路径后12通过（1.95秒），不加入正式发布套件计数。

没有新增生产业务core/startup功能变更；本版累计已有改动按上方范围提交。125文件包括历史开发记录、测试和原有用户删除的5m账户，不能将文件数全部解释为本轮新增运行逻辑。

## 累计core/startup文件复核索引

| 文件 | 已有改造职责 |
|---|---|
| core/account_exec.py | dry_run账户只读初始化 |
| core/backtest.py | 共享因子/截面/选币阶段及原报告衔接 |
| core/equity.py | 初始化可选内存输入，默认原模拟接口 |
| core/model/account_config.py | is_operate=False时禁止小额资产兑换 |
| core/model/backtest_config.py | 完整Arrow模式、资金曲线/权重可选内存输入 |
| core/select_coin.py | 有界因子调度、分块截面及共享选币，保留旧分支 |
| core/utils/arrow_reader.py | 完整历史与严格输入读取 |
| core/utils/datatools.py | Arrow读取/合并适配 |
| core/utils/factor_data.py | 新增共享块所有权、投影与局部GC |
| core/utils/functions.py | 共享数据准备、现有记录接口衔接 |
| core/utils/section_state.py | S3状态分块/续算 |
| startup.py | offset预准备及dry_run自检跳过账户变更 |

本轮没有追加这些文件的业务修改，清单用于说明相对cee71bc的累计版本范围；不要将累计范围误认为本轮监控清理新增代码。之前人工审核与逐场景测试按专题文档保留。


最终Git格式检查通过（保留Windows原行尾，cr-at-eol口径），无未暂存业务改动、无遗漏未跟踪发布依赖。125文件纳入本地main，准确提交号以git log为准；未推送、未正式部署。
