# MODEL_ROBUST40 部署与维护

更新：2026-10-31，已按用户纠正撤销“公共仓管参数文件”方案。每个offset完全可以不同，所有原账户配置继续在各自accounts文件中维护。当前方案是服务器完整PKL初始化、4050 Arrow续算、按账户/offset独立保存状态。

账户每个字段是否实际生效、E盘研究文件用途及部署依赖，见 [参数说明](model-robust40-parameters.md)。不要将历史声明误当成已经接通的配置开关。

本轮未部署、重启、下单、通知或删除生产数据。core、startup、signals、factors、sections、alpha均无本轮新增修改；既有框架定制保持。

## 1. 目录和配置归属

| 内容 | 唯一维护位置 |
| --- | --- |
| 策略名、完整仓管参数、子策略池、S3日期、offset、执行/BNB设置 | `accounts/<账户>.py`，参考15m-nheart1020.py的账户自包含写法 |
| MODEL仓管算法与实盘接口 | `positions/ModelRobust40Strategy.py`，单文件按11个功能段组织 |
| 选币因子、截面、再择时 | 原有 `factors/`、`sections/`、`signals/`，不迁移、不复制 |
| 初始化、快照、行情冻结、日志工具 | `tools/robust40/`，详见包内README |
| 工具功能配置 | `tools/robust40/initialization_config.py`：并发/历史长度/等待/恢复点等 |
| 主机路径、框架运行配置 | 根 `config.py` / 已有 `config.json` |

三个MODEL账户及三个离线示例都已恢复完整字典，不再调用model_parameters，不存在公共仓管参数默认文件。20m调整父权重不会传到40m或55m；也不要求与历史示例参数相同。14个子策略顺序仍需和本账户的仓管下标对应。根配置文件依框架原规则加载指定账户，不是新的策略参数来源。

仓管内部特征用于季度分类器，不是框架选币factor插件；它们和模型、专家、S5减仓、虚拟账户、续算、目标变换合在同一个positions文件内。原共享 `HybridRotationDriftStrategy.py` 保留，传统策略继续使用原实现。

工具只读取账户参数执行运行适配，没有新增策略参数配置。当前功能设置：统一MODEL_JOB_NUM=4（初始化、续算和手工预热共用，仅MODEL worker覆盖全局并发），完整历史下限14000，续算4050，ready等待240秒，恢复点25，修订输入168小时。这些是处理资源/窗口配置，不是收益权重或训练超参数。

## 2. 仓管单文件阅读顺序与调用

`positions/ModelRobust40Strategy.py` 文件头列出三个框架入口：prepare_live、calc_ratio、transform_targets。

| 功能段 | 内容 |
| --- | --- |
| 1–3 | 决策时钟/特征、父约束与专家组合、季度分类及S5减仓 |
| 4–6 | 减仓叠加、换仓死区和手数、虚拟账户模拟内核 |
| 7–8 | 完整/增量计算、输入合并和状态读写 |
| 9–11 | 执行扩展注册、实盘输入/目标校验、仓管自身恢复点 |

正常调用为：startup → tools的Coordinator/worker → core现有计算流程 → positions三个hook。离线对拍直接调用同一个positions实现。磁盘snapshot事务由tools负责；模型状态的恢复和重算由positions负责；S3数学仍调用原sections文件。

10个原 `tools/robust40/{features,parent,model,execution,rebalance,simulation,engine,runtime,live,live_execution}.py` 已归并后移除，没有旧路径兼容壳。外部研究脚本需更新导入为 `positions.ModelRobust40Strategy`；旧live.transform_targets的纯校验功能改名validate_targets，公开transform_targets仍含原完成标记hook。不能将纯校验调用换成会提交状态的公开hook。

## 3. 发布范围

发布同一套已审核的alphaonline代码：positions、tools、core、框架因子/截面/信号及根配置代码。不能只复制新的positions文件到原版框架，因为它仍依赖现有hook、运行工具及共享Hybrid实现。不要以第三方core整体覆盖本项目Arrow、续算和交易定制。

账户文件属于服务器配置：逐账户合并公开策略配置，保留服务器自身密钥、主机路径、通知设置。不要用开发机私有账户文件覆盖服务器配置。数据目录和日志独立于代码发布，不随代码镜像删除。

旧14个根tools命令已移至 `tools/robust40/cli/`，文件名不变。外部脚本同步路径；当前Coordinator两处路径已更新，startup不需修改。删除项仅是已迁移的代码文件；不是删除snapshot或行情。本轮不自动提交此前大量未提交工作，实际发布时需确保新增文件也在发布包内。

research、测试临时数据、E盘研究输入、Python/Numba缓存不作为运行数据发布。research_inputs、child_returns、conditional_risk_path等八个无效字段已注释，当前与旧状态都排除这些字段的比较；不需要保留路径字符串或部署E盘文件。删除这些历史字段本身不再触发重建。部分原声明如gross_cap/momentum_60并不是当前native实现的调参开关；本轮只归并代码，未扩展参数功能。

## 4. 环境与离线检查

使用已验证的conda alpha311、Python3.11；Windows解释器通常为 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`。requirements.txt当前仅固定xbx-py11版本，不是完整环境锁文件；新服务器需匹配现有numpy/pandas/numba/scipy/scikit-learn/pyarrow/交易依赖，不在目录整理时升级。MODEL分类器用LogisticRegression，不需要为MODEL安装LightGBM；其他策略依赖另计。

框架主机配置realtime_data_path指向真实alpha数据目录。需能读取各offset完整PKL/pivot/同轮四个ready、对应Arrow预计算列、共享exginfo及策略依赖的辅助输入。首次约140xx完整历史，之后4050续算；本轮不操作alpha生产者。

MODEL继续要求incremental_lookback_hours=0，使用共同恢复点；普通策略原功能保持。S3第四参数由各账户人工设置UTC日期，实际起点是该日00:offset，初始化与续算同源。不是上线后改成最近4050小时。当前20/40/55m日期保持2025-04-30；详细安全覆盖规则见 [S3说明](2026-10-31-manual-s3-input-freeze.md)。

以下命令用于隔离发布目录/本地离线验证，不启动实盘：

```powershell
Set-Location D:\alphaonline
$python = 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe'
Remove-Item Env:ROBUST40_SERVER_WORKER -ErrorAction SilentlyContinue
Remove-Item Env:ROBUST40_WARMUP_WORKER -ErrorAction SilentlyContinue
Remove-Item Env:X3S_TRADING_ACCOUNT -ErrorAction SilentlyContinue
$env:X3S_USE_FILE_LOGGING = '0'
& $python -X utf8 -B -m tools.robust40.cli.server_robust40 --help
& $python -X utf8 -B -m pytest tests/test_robust40_layout.py tests/test_server_initialization_worker.py tests/test_server_runtime_reliability.py -q -p no:cacheprovider
```

测试依赖pytest，可在同环境隔离目录完成，不要求安装在生产机。需要数据对拍时，使用cli下的check_server_factor_parity/check_server_section_parity/check_server_model_parity，先看--help，输出到验收目录。示例目录和旧prepare_robust40_live_configs仍是历史研究工具，不能直接覆盖生产账户。

## 5. 人工上线与回退

1. 停止目标alphaonline调度并等待初始化/交易子进程结束，再整体更换代码，避免混用新旧模块；不操作alpha或其他实例。
2. 留存旧代码和各服务器配置，保留完整data/snapshot。按审核后的范围应用新代码和各账户配置，更新外部命令/导入路径。
3. 验证账户hour_offset正确（不再填写runtime.minute）、S3合法、执行与BNB配置保持；运行离线检查。
4. 使用原服务管理器/计划任务启动唯一目标实例；工作目录是项目根，解释器alpha311。不要同时再启动另一份startup，也不要手工拼trade/compute环境变量。生产服务环境移除测试用X3S_USE_FILE_LOGGING=0，恢复正常日志。
5. 检查目标offset初始化/下一轮续算及下单日志。本次操作未实际执行。

本轮账户展开值和状态格式未改变，不因文件归并强制重建。仍由现有状态检查判断是否续算/初始化。函数所在文件改变，首次运行可能重新生成Numba缓存；不需要删除旧行情或训练状态。

既有流程：同轮ready → 检查snapshot → 有效包4050续算；缺失/配置变化/覆盖不足等按既有逻辑初始化。初始化worker本身不下单，安装后由后续匹配offset进入交易。有效计算已开始后无四分钟截止，四分钟仅限等待缺失输入。

clean_start=True仍保留server_init账户完整snapshot及对应备份路径；传统账户维持原清理行为。完整包包括子策略state0/state1、S3、model状态/修订输入、恢复点及元数据，不能只备份model.pkl。

纯目录归并回退：停目标实例且等子进程结束，恢复旧整套代码及对应账户声明，保留当前完整状态通过原检查。将来如改数学/状态协议，需对应完整代码/配置/状态回退或明确重建；不能仅覆盖几个小时前的模型文件。

## 6. 日志和新增offset

初始化日志在data/server_initialization/<账户>/<stamp>.log；正常worker与服务stdout沿用原位置。从根目录汇总：

```powershell
& $python -X utf8 -B -m tools.robust40.cli.summarize_replay_logs 'data/server_initialization/40m_robust_4h/*.log' 'logs/server_robust40*.log'
```

观察正常/回放/初始化比例、阶段P95、训练/复用次数、已提交但交易失败及未完成尝试。模块归并不增加每轮全量读取或检查。

扩展5分钟offset：从已验收账户复制公开定义，独立维护完整仓管参数/子策略参数及凭据；设置唯一账户文件名和策略名，仅用account_config.hour_offset配置分钟偏移。上游必须发布对应offset，新账户自行初始化，不能复制别的offset状态。配置识别测试覆盖0/5/…/55，但不同参数、完整历史和实际交易仍需分别验收。

## 7. 人工审核与证据

| 修改点 | 人工审核内容 |
| --- | --- |
| accounts三个账户、三个历史示例 | 完整参数字典恢复，各自独立，原数值/BNB/子策略/S3不变 |
| positions/ModelRobust40Strategy.py | 10文件及模型恢复函数归并，三个公开hook行为保持，validate_targets不提交状态 |
| tools调用点 | 导入positions；行情冻结/公共市场准备独立于历史CLI；仅功能配置留tools |
| core/startup及其他策略模块 | 本轮零新增修改，不取消原功能 |

前次目录归并证据为research/model_module_review/result.json：49个仓管函数/类在导入/路径规范化后逻辑AST一致，7个行情工具函数逻辑不变。随后配置清理证据为config_identity_result.json：六份清理前后配置按有效口径相同，已有40m离线12968小时state父参数仍匹配。Windows spawn、配置派生/比较、预热及生命周期专项通过；目录归并时缺失旧账户测试跳过，不计通过。完整140xx回测、C扩展真实数据对拍及服务器生产启动未在本轮重跑。

人工审核需包括effective_parent_params的8项排除清单、effective_settings的比较范围、hour_offset唯一来源以及server_init=False说明。有效/未知参数仍受比较约束，不忽略配置变化。清理前后完整字典不再相等属于预期；不能用旧的整字典相等判断要求重建。

本指南替代此前“公共仓管参数config.py/薄positions入口”的整理方案。工具模块详表见 [工具导航](../tools/robust40/README.md)。

## 2026-11-01 因子进程内存峰值修复

用户提供的20m 09:20轮次日志显示multiprocessing.connection接收任务时MemoryError，随后BrokenProcessPool；差异预检正常、计算未提交。此前只有初始化限制4进程，正常续算继承config.job_num=os.cpu_count()，本机20。初次修复添加LIVE_JOB_NUM=4，现已统一为tools/robust40/initialization_config.py中的MODEL_JOB_NUM=4；worker的apply_profile在框架导入前设置共同并发，Windows spawn子进程同样执行。该数值是资源设置，不是策略参数，不改变数学或因子列；但会减少并行度，实际延迟需观察。其他共享job_num的MODEL计算阶段也受此设置影响。

core、startup、全局config、传统策略、alpha均无本次修改。不取消失败后的不完整状态检查；本次失败可能已部分写入子策略/S3，不能仅把complete改为True继续交易。按现有规则下一轮自动完整初始化，不手工删除snapshot。资源调整自身不要求重建。新worker加载新值，已运行进程不会热更新；本次没有重启或下单。

人工复核最小范围：initialization_config仅保留MODEL_JOB_NUM；cli/server_robust40.py的两种模式与cli/prewarm_robust40.py均读取该值。原INITIALIZATION.job_num、LIVE_JOB_NUM和手工预热min(job_num, 2)均删除，不保留重复配置。原有Windows spawn离线测试增加config及core.select_coin实际并发断言，先正常模式失败/初始化通过，修复后两模式通过。未在生产行情上施加内存压力或执行整轮交易，不能承诺4进程在任何共存负载下绝不OOM。

统一后，初始化/续算默认仍为4，历史手工预热由最多2变为4；后续调整仅修改MODEL_JOB_NUM一处。它控制框架job_num计算并发，不承诺限制全部Python进程/底层库线程总数；调度槽位和mem_save_exe的单进程隔离不属于第二套计算并发配置。传统策略继续使用全局config.job_num。三个实际spawn路径离线检查通过，未启动生产预热、重启或下单；无需因并发配置变化重建状态。

## 2026-11-01 续算性能三项落地

仓管内核缓存、原始行情按需读取、state单次锁内加载已实现；无新的账户配置、core/startup改动或状态迁移。代码更新后的首次冷编译正常，后续新进程复用本机Numba缓存；无需删除snapshot或重新初始化。需同步positions/ModelRobust40Strategy.py和tools/robust40/handoff.py。人工复核、离线对拍和具体性能口径见[续算性能说明](2026-11-01-model-continuation-performance.md)。

## 2026-11-01 Hybrid缓存与Ubuntu部署补充

本次额外同步positions/HybridRotationDriftStrategy.py和positions/ModelRobust40Strategy.py：生产使用正常模块导入复用原Numba缓存；共享Hybrid只调整类型导入，公式及参数不变，离线参考加载器仍不写缓存。真实12994小时逐值一致；独立新进程热缓存Hybrid约0.727秒（Windows单次对照，非Ubuntu承诺）。

Ubuntu应使用本机alpha311、可写本地NUMBA_CACHE_DIR及同一服务用户，在新进程验证hit/miss；不要部署Windows缓存/.pyd，不在worker运行中清缓存。源文件改变后首次编译正常，编译缓存不属于snapshot，不需要清理业务数据。具体命令及人工审核见[Ubuntu缓存复核](2026-11-01-hybrid-cache-ubuntu-review.md)。

**Ubuntu整套流程尚未验收**：时序池没有显式mp_context，Python3.11 Linux fork与Polars组合需另行审核显式spawn及配置继承；本轮没有修改core/startup。这与Hybrid缓存改造分开验收。逐因子热点与后续最小修改清单见[时序因子审计](2026-11-01-timeseries-factor-audit.md)。
