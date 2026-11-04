# 2026-11-04 新Ubuntu测试机：四offset MODEL干跑

## 授权与部署范围

用户指定ubuntu@43.173.122.28，SSH私钥E:/siteup/env/visibility-deploy.pem；确认四个账户使用当前MODEL策略，备份只提供密钥和微信链接。代码部署/home/ubuntu/alphatrade；该目录部署前为空。alpha继续运行，不修改其代码、配置、数据或服务。用户自行将swap扩至约20GB；部署只读取实际大小，不修改swap或系统swappiness。

源码来自当前D:/alphaonline工作区，不冒充某个Git提交。打包仅业务源码、工具及测试，排除.git、历史data/snapshot、私钥、账户备份、研究行情和Windows二进制；私有配置单独经SSH stdin传入，权限600。5m连接凭据来自accounts.local.json的同名覆盖，其他三个来自accounts backup。所有账户采用20m_robust_4h.py策略模板；已核对20/40/55三个模板除account_config/strategy_name外相同，仅改hour_offset和strategy_name中的分钟。

| 账户文件 | offset | 仓管/策略池 | 测试重点 |
| --- | --- | --- | --- |
| 5m-onheart1020.py | 5m | ModelRobust40Strategy，14组合 | 首次独立初始化、状态保存及下一小时4050衔接 |
| 15m-nheart1020.py | 15m | 同一模板，各自状态 | 连续至少两轮4050、目标仓位和干跑订单文件 |
| 25m-eart1020.py | 25m | 同一模板，各自状态 | 与alpha及其他账户初始化重叠时的资源压力、等待时间 |
| 45m_gonheart1020.py | 45m | 同一模板，各自状态 | 修订检测/自然回放、训练与复用日志；未发生回放不计通过 |

四账户均执行全部正常流程，表内只是分析重点；不同offset行情不同，不能当作相同数据的A/B性能对照。实际第一个初始化预计45m；验收测试已预热部分Numba缓存，因此首轮是无业务snapshot初始化，不等同完全冷编译，不预设5m一定冷启动。S3沿用2025-04-30，实际00:offset UTC；不改账户策略参数。初始化共用原单槽位，某账户超过下个offset时其他首次初始化可能排队，不并行启动四份全历史工作集。

## 功能和资源配置

- config.json：is_simulate=dry_run，shared_factor_pipeline=True，incremental_lookback_hours=0，realtime_data_path=/home/ubuntu/alpha/data，HTTP/HTTPS代理http://127.0.0.1:7897。
- 仅测试发布副本：INITIALIZATION.data_pipeline=True、factor_symbol_batch_size=32、selection_block_hours=2016、MODEL_JOB_NUM=4。本地默认False/16不变。
- 因子4进程/每进程数值库1线程；截面最多2个Polars线程；共享选币1个Python任务、Polars使用4线程。初始化原有MemoryHigh约4.5GiB/MemoryMax约5GiB、swap上限12GiB和nice10不变。实际系统swap以采样为准，不能把12GiB进程上限当作已经使用量。
- 新建消费者独立环境/home/ubuntu/alphatrade-env/alpha311，离线克隆现有alpha311；Python3.11.18，生产者环境不安装或升级依赖。pip check通过。Numba缓存仅在服务器本机生成。
- 新服务alphatrade-dryrun.service运行startup；原有PM2 realtime_data不动。观察服务alphatrade-observer.service只读采样24小时，不修改交易状态。

## 部署前发现并修复的dry-run缺口

原入口虽然跳过撤单/最终下单/资金归集，但startup.check_accounts仍可能改杠杆/持仓/保证金模式，account_exec仍以is_operate=True刷新账户，dust转换还在is_operate外。已补三个最小修改：

1. startup.py：dry_run自检只刷新时间，不调用账户设置接口。
2. core/account_exec.py：dry_run不重置杠杆，账户刷新传is_operate=False。
3. core/model/account_config.py：小额资产转BNB也受is_operate约束。

不关闭账户原有补BNB配置；正式none模式的设置/补BNB/划转逻辑保留。6个独立行为测试先3失败3通过，修复后相关批次53通过。测试机部署专项37通过；四个账户通过显式GET-only私有查询，未发订单或测试通知。四offset完整Arrow的BTC约14061–14062根、12个依赖列及full_history标记齐全。

## 运行和统计口径

报告目录/home/ubuntu/alphatrade/reports/ubuntu-20261104：

- preflight-tests.log：Ubuntu部署/干跑/共享入口专项结果。
- recovery-tests.log：独立共同恢复/S3/MODEL修订oracle结果，不等同实际账户自然回放性能。
- account-preflight.log：无凭据/余额输出的四账户与完整Arrow预检。
- resources.jsonl：每5秒整机CPU、MemAvailable、实际swap总量/占用、换页计数、磁盘余量、CPU/内存/I/O PSI；消费者/生产者PID及CPU/RSS/IO；初始化和干跑cgroup的anon/file/current/peak/swap/cpu/events。
- events.jsonl：增量提取原MODEL_REPLAY和INIT_PHASE，保留account/stamp/attempt及阶段边界；不重复读取行情。重启观察器按原事件身份去重。
- summary.json：按账户、kind、mode区分成功/失败、总耗时P50/最大值、季度训练/复用；不足20个成功样本不输出P95。无事件明确表示未覆盖。
- ../startup.log：完整调度、账户请求、计算、干跑目标/订单、统计日志。
- ../../data/server_initialization/<account>/<stamp>.log：初始化等待、冻结、阶段、资源准入及结束记录。

分析时区分ready等待/初始化排队与计算耗时；通过阶段时间与resources.jsonl对齐定位峰值。RSS相加含共享页，不能替代cgroup memory.current；Windows提交量不能与Linux anon/file直接比较。记录各轮Numba准备耗时及已预热条件，不能将首次业务初始化直接标为冷Numba；统计/接口等待和前半程计算分开。自然修订按event/changed_fields/restored_at/replay_hours等实际字段统计，没发生不能写成回放已验收。

本次先部署真实调度干跑，不人为改alpha行情、不破坏账户snapshot、不暂停某账户制造7小时缺口。受控缺口/价格修订的性能对照须使用单独状态与行情副本，不能与主样本混记；当前通过离线oracle验证其行为，真实整轮性能若尚无事件标为未覆盖。

## 运行维护

查看：sudo systemctl status alphatrade-dryrun.service alphatrade-observer.service；tail -f /home/ubuntu/alphatrade/reports/startup.log。24小时后观察服务正常退出，干跑服务继续；再次启动观察器沿用现有记录并去重。初始观察样本少，不承诺P95或长期稳定性。

停止测试：sudo systemctl stop alphatrade-dryrun.service alphatrade-observer.service。初始化使用独立robust40-init/robust40-supervisor服务；若正在运行，按日志中的本测试unit逐个停止，不能通配停止其他应用。无需停止或重启alpha，不清空有效snapshot。

执行状态和首次启动/初始化结果在下方补充；部署成功与整轮计算成功分别记录。

## 首次执行记录

2026-11-04 00:34:42（UTC+8）两个服务均active。干跑主服务PID2860001，启动复查NRestarts=0；已明确输出“干跑模式，跳过杠杆、持仓模式及保证金模式修改”。旧版本横幅仍按is_debug=False打印“正式模式”，不代表本次允许交易；有效config.is_simulate=dry_run及相应分支已经验证。

Ubuntu部署专项37通过/2条原警告（18.93秒）；共同恢复/S3/MODEL修订专项40通过（63.77秒）；两批结果分别保留，不与Windows53项简单相加。四个账户GET-only私有查询均成功；依赖检查无损坏项。实际系统swap已约20GB，记录于resources.jsonl；剩余磁盘约85GiB。消费者环境独立克隆，未改生产者Python环境。

服务启动先检查00:35这个框架5分钟调度点，上游仅启用5/15/25/45m，35m无ready应按原规则跳过；下一账户触发点预计00:45（以上游同轮四ready为准）。当前尚未将任何账户完整初始化/续算标为成功。观察器每5秒产生资源样本，已有主服务cgroup和生产者/消费者进程统计；其结构化解析也用已有实际初始化日志验证。后续成功、失败和自然回放由summary.json及原始日志判定，不因服务active就算性能验收完成。

本次新增core功能修复清单仅core/account_exec.py、core/model/account_config.py，加startup.py的dry-run自检分支；此前15个共享计算源文件按用户部署授权一并发布。未改alpha、未修改账户的正式补BNB参数、未下实际订单、未发送人工测试消息。

最终启动复核：39个资源样本已记录，实际swap_total=20.0GiB，包含alphatrade-dryrun.service的cgroup以及producer/consumer两个角色；私有配置均600。alpha PID仍2731029，与部署前相同。尚无MODEL完成事件时summary.rounds为空，符合未覆盖定义。
