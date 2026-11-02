# statistics第一阶段实施与人工复核

范围：本地 `D:/alphaonline`，性能优先、离线验证。未启动startup、下单、调用私有接口、发送通知、重启或部署；未修改alpha、账户配置、历史账本或生产snapshot。第二阶段的成交分页、费用、可信成本重建未实施。

## 默认行为与开关

`config.py` 的 `statistics_config` 独立于is_debug、交易、自动补BNB：

| 选项 | 默认 | 作用 |
| --- | --- | --- |
| enabled | True | 启用交易后的统计；False不影响交易必需查询和补BNB |
| notify | True | 基础净值、持仓图、策略状态/权重通知；False只保存统计数据 |
| charts | False | 可选资金曲线、划转查询、子策略曲线加载；与基础持仓表不同 |
| details | False | 可选本地订单回执成交额；UniMMR已恢复到基础通知；不调用旧历史成交重建 |
| price_impact | False | 可选1h/24h期初持仓价格影响估算，不是真实交易盈亏 |
| max_workers | 2 | 全局统计在途上限；账户重复/容量已满直接跳过并记录，不排队 |

首次加载默认提供基础报告。开启高级选项仍可能遇到现有网络问题，未改变交易所全局重试/超时。保留原下单后的统计等待（默认120秒或random_time），移除成功任务末尾60秒空等；120秒不是本轮CPU计算耗时。

模拟统计入口仅兼容enabled/notify/charts、定向账户筛选和独立图片，不移植实盘账户查询。其原模拟计算逻辑保持，尚未做与实盘统计相同的曲线缓存优化。

## 计算与保存口径

- 指定账户先按文件stem筛选，再核对账户名/offset，之后才校时和读取一次账户overview。其他账户不初始化、不查询；不调用具有交易副作用的update_account_info。一次进程的公共校时保留，overview内部的多次HTTP调用不计为“一次HTTP”。
- 基础持仓与权益使用同一次overview。现货已有数量/仓位价值反推价格，不重复拉ticker；不再读取旧逐币成本CSV。ETH数量0.6294843、价格2407.22对应市值1515.307196646 U，展示1515.31 U。
- `pos_u`统一为当前市值；新增显式`market_value`。现货均价、未实现盈亏和change置空，`cost_status=待对账`。旧CSV是净投入，不论数量是否匹配都不能直接证明剩余成本；原CSV不修改、不删除。
- 合约仍使用交易所均价/标记价/持仓盈亏。持仓数量和价格按原精度保存，只有展示副本对金额取两位小数。缺价格为空，不用0代替。
- 保存交易前snapshot仍由原交易流程调用：同一run_time替换该轮全部币种，避免旧币与EMPTY并存；保留7日历史，增加observed_at。统计不向交易前history追加交易后观察值。
- 统计的pos_spot/pos_swap、equity.csv、pnl_history按账户目录与run_time幂等；同时记录实际observed_at。时间沿用框架本地时间约定，epoch用本地datetime.timestamp转换，不再将UTC-naive按本地时间转换而偏差8小时。旧错误epoch不迁移、不伪造修正。
- 1h/24h估算使用实际观察时刻，基线必须不晚于目标，最大向前容差10分钟；展示实际起点/终点。历史不足或旧格式缺market_value则明确未知。全清仓仍计算期初仓位假设不交易的价格影响；缺现价不生成伪零或完整合计。只有有效基线里的已平仓币种需要额外行情，各市场至多一次批量ticker。
- spot/swap历史各读一次，1h/24h复用；策略归属映射一次、持仓计算一次；14个子策略资金曲线在All/30d/7d间共用14次读取。参考主曲线由逐段plot改为一次plot，时间保留20/40/55等分钟偏移。
- 账户级OS锁排除同账户重复手动统计进程；启动调度限制在途任务并观察子进程失败。原文件以同目录临时文件+replace更新；图片为每次独有临时路径，发送完成清理。锁文件保留但锁由OS在关闭/进程退出时释放。
- 基础持仓和净值先落盘，再执行额外接口/图表/通知；这些失败各自记录并继续。基础概览失败则本轮失败，不使用默认零权益冒充成功。在线路径完全不调用旧get_all_order_info，不因任何异常扫描全市场历史。

## 需要人工关注的限制

1. 未知的5.010667 ETH差额未自动解释。可信成本仍需要第二阶段完整成交/费用/划转核对；本阶段仅停止错误展示。
2. 现有客户端部分划转查询失败会被内部吞掉，调用方无法识别空表是“无划转”还是“查询失败”。charts默认关闭；开启后标题和消息注明划转未核验，equity.pkl附`transfer_history_status=unverified`，不能用它作已对账收益凭证。部分交易所UniMMR也有兼容默认值，基础报告只展示客户端结果；严格成功语义需第二阶段单独审核客户端。
3. 基础事实来自多次底层接口组成的overview，并非交易所原子快照。首次新格式历史不足1h/24h时不会沿用旧成本格式来凑数据。
4. 原历史成交工具函数保留但不从实盘run调用，供第二阶段独立修复；不要手动调用旧函数重建可信成本。
5. 原账户订单、BNB、MODEL、S3、Arrow和因子逻辑未改。本轮不改变签名/全局重试，也不能消除代理TLS故障。

## 本阶段最小修改范围（人工审核项）

| 文件 | 本轮修改及审核重点 |
| --- | --- |
| core/utils/statistics.py | run账户筛选/只读overview/阶段隔离；持仓和权益先保存；曲线缓存/一次plot/保留分钟；短择时历史防越界。旧订单重建函数不改。 |
| core/utils/statis_func.py | 现货市值替换错误成本；共享快照保持精度/同轮替换；历史窗口与估算；原子写入/独立图片；compute_recent_volume新增strict=False向后兼容参数。注意该文件被交易前snapshot调用。 |
| core/utils/statistics_debug.py | 仅独立图片、正确epoch、选项兼容、目标文件筛选和移除末尾空等；不复制实盘查询。 |
| startup.py | 本阶段只有5处行替换：导入调度器、导入开关、创建上限2调度器、统计enabled守卫、按账户submit。既有MODEL初始化/清理改动不属于本阶段。 |
| config.py | 新增独立statistics_config与说明4行；现有is_simulate、clean_start等改动保留。 |
| tools/statistics_runtime.py | 小型通用工具：临时路径、原子保存、非阻塞OS账户锁、无积压统计任务调度。无交易/策略依赖。 |

core改动仅上述3个统计文件。行数较多主要是替换重复的旧仓位/盈亏和账户处理分支，删除多于新增；不能把本阶段描述为“几行修复”。没有整体覆盖core或扩大修改账户模型/交易所客户端。

专用差异：`research/statistics_phase1_review.patch`。该文件排除了此前startup初始化和config的既有未提交修改，包含本阶段依赖的新工具；用于人工复核，不需要在已经修改的工作区再次套用。

人工复核建议逐项确认：

- [ ] 默认基础报告及charts/details/price_impact关闭符合当前运行需要。
- [ ] 现货历史成本/盈亏暂不显示、价格影响与真实盈亏分离的口径。
- [ ] 共享snapshot完整精度、新字段、同轮替换及旧格式不参与可信估算。
- [ ] startup仅统计分发改变；自动补BNB和交易查询保持。
- [ ] OS锁、原子文件替换、独立图片及队列满时跳过的行为。
- [ ] charts启用后只能视作划转未核验参考；真实联网单轮验收待人工审核后执行。

## 离线验证与性能

TDD初始19项失败，修复后新增边界和并发/IO测试。完整专项92项通过，覆盖统计、2.3.1交易回归、主链路及时间戳重试；日志`research/statistics_phase1_final_tests.log`。校时顺序复核后32项统计测试通过，日志`research/statistics_phase1_postreview_tests.log`；最终有效历史按需ticker优化5项通过，见`research/statistics_phase1_history_final.log`；补充真实账本数量匹配样本、snapshot保存精度及所有可选项同时失败的6项通过，见`research/statistics_phase1_failure_final.log`。git diff --check与6个运行文件语法检查通过。

覆盖全部0/5/10/…/55偏移、ETH三种账本与净投入样本、不改输入、数量精度、空仓/全清仓/缺价格/旧格式/历史不足、同轮幂等、目标筛选/身份不符/零权益/接口失败、两份历史读取、14份子曲线读取、图片隔离、写入失败保留旧文件、真实子进程OS锁、跨工作目录脚本导入、worker异常观测、短择时历史及模拟开关。所有网络与通知由离线边界阻断/替换。

| 指标 | 旧路径 | 本阶段验证 |
| --- | --- | --- |
| 指定账户overview | N+1次 | 1次；其他账户0次 |
| 14个子策略曲线 | 42次CSV读取 | 14次 |
| 1h/24h持仓历史 | 4次PKL读取 | 2次 |
| 不合格历史的已平仓币种行情 | 可能仍查询 | 0次额外ticker |
| 旧逐币成本CSV | 每币1次且重复调用 | 0次 |
| 全市场历史回退 | 异常可触发 | 在线0次 |
| 单账户并发 | 无约束 | 1；全局默认2、不积压 |

`research/benchmark_statistics_phase1.py`从当前HEAD提取旧实际函数与新函数对比，用临时合成CSV、7次调用取中位数，无生产数据/网络写入。1币旧11.91 ms、新3.38 ms；100币旧363.87 ms、新3.05 ms。结果`research/statistics_phase1_benchmark.json`。这仅是现货表函数的CPU/本地IO微基准，不外推为整轮实盘加速倍数或私有接口延迟。

复测命令（不启动实盘）：

```powershell
& "C:\Users\Administrator\miniconda3\envs\alpha311\python.exe" -X utf8 -m pytest tests/test_statistics_phase1.py tests/test_upgrade_231.py tests/test_main_chain_parity.py tests/test_binance_timestamp_retry.py -q -p no:cacheprovider
& "C:\Users\Administrator\miniconda3\envs\alpha311\python.exe" -X utf8 research/benchmark_statistics_phase1.py
```
