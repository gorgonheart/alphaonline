# 2026-11-05 后半程性能及Arrow联动测试部署

## 已完成

目标为测试43.173.122.28：/home/ubuntu/alpha和/home/ubuntu/alphatrade。2026-11-05 11:32:44（UTC+8）完成补充部署并重启PM2 realtime_data和alphatrade-dryrun，新PID分别638072、638081，已pm2 save。交易端保持用户确认的dry_run，账户文件与snapshot_time逐字节核对未改变；没有访问或部署生产43.167.235.164。

这是11:14即时重建补丁之后的追加发布。本次已补齐此前未发布的执行器缓存、再择时行情/曲线复用、执行分段日志和验证工作区释放工具；CSV旁路仍未启用。之前即时重建及清理补丁继续保留。

## 文件与配置

交易端追加6文件：core/backtest.py、core/position.py、core/real_trading.py、positions/ModelRobust40Strategy.py、tools/run_deployment_tests.py、tools/robust40/initialization_config.py。最后一个文件仅将已部署ARROW_DEMAND_ENABLED切为True，保留data_pipeline=True、32币批次、并发4和现有资源预算。没有修改策略池、S3日期、账户密钥、BNB设置或统计报警配置。

上游追加4文件：core/preprocess.py、core/arrow_publish.py、core/arrow_demand.py、config.py。测试机比本地基线少了单轮Arrow保留/失败tmp释放实现，一并补齐接口，避免新preprocess调用不存在的current_epoch参数。config.py基于远端原文件只增加功能默认值与JSON允许项，未覆盖原机器配置/凭据/5、15、25、45m偏移。config.json增加arrow_demand_enabled=True、arrow_demand_min_rows=5000、arrow_snapshot_keep=1。publish_kline_num原值0保留，C因子代码和扩展未变，无需本次重新编译C。

审查时全机只有alphatrade这一个startup和一个25m-eart1020账户消费测试上游，其他安装/运行服务未发现活动交易消费者。正常25m需求为5000，最终交易仍4050；其他offset没有请求，沿原publish_kline_num=0发布全量。任一消费者请求全量会优先全量。源PKL、Pivot和长因子计算历史不裁剪；清理过期Arrow使用正常发布生命周期，不手工删除有效snapshot。

## 隔离验证

两个候选副本均基于测试机实际原源码叠加本次补丁，放各项目test/deploy-efficiency-20261105下；空凭据/独立路径和socket.connect审计禁网。未把本地整仓包覆盖服务器。

- 上游：test_module8_arrow_publish、test_shared_factor_pool、test_arrow_single_snapshot，54项通过，3.72秒。包含spot/swap同轮共享深度、发布保留及失败清理。
- 交易端：执行器旧版本独立oracle、两新进程缓存、再择时恢复状态、已有MODEL运行合同、verify残留释放及24份日志、跨项目实际publisher/reader映射和多消费者协议，共43项通过，85.85秒。112条既有pandas弃用警告，未更改容差。
- 两端生产路径禁网导入检查通过；确认dry_run、联动开关、上游偏移、原交易资源参数。
- 上一批自动重建143通过/22跳过属于之前补丁验证，不与本批相加冒充完整全仓回归。

## 已部署路径的缓存实测

使用/home/ubuntu/alphatrade-env/alpha311/bin/python，在实际运行代码上以合成输入调用LiveRebRobust40，禁止网络，两个独立进程输出逐值相同：

| 调用 | 计算调用耗时 | cache hits | cache misses |
|---|---:|---:|---:|
| 新函数首次冷编译 | 10.1647秒 | 0 | 1 |
| 下一新进程命中 | 0.2671秒 | 1 | 0 |

缓存实际位于/home/ubuntu/alphatrade/positions/__pycache__，没有依赖test；删除test不删除运行缓存。本测量不含解释器/模块导入，不代表整轮实盘快了相同比例，也不代表真实订单接口耗时。再择时当前仅有严格状态对拍，未在服务器新旧相同输入下重做完整阶段性能基准。

## 联动登记与真实发布边界

上游重启按既有逻辑清除了旧ready，第一次登记脚本因此在latest_ready处停止，没有修改snapshot或需求。登记脚本改用当前合法offset截止核查已有snapshot，再调用既有release_demand接口；这只是一次部署登记，未修改业务ready门槛。

实际登记：offset25，state status=ready，stamp1793849100，rows=5000；独立上游解析器读取该控制文件也返回5000。已有snapshot已完成11:25轮的计算，因此没有强制全量初始化。新控制协议按下一次发布生效，**尚未观测新25m Arrow实物和随后的完整dry_run计算**；不能把解析器/合成对拍通过说成该轮实际发布已经完成。初始化失败→全量保持→成功后缩短的真实整轮流程也未通过删除有效状态来强行制造。

## 磁盘与维护

本次源码备份、补丁、日志、验证脚本和结果均在两端test/deploy-efficiency-20261105。结束后仅删除本次自建candidate/pytest/numba/tmp/offline-data目录，删除前确认路径边界及无活动进程引用，不触碰业务数据或正常运行Numba缓存。上游保留约4.3MiB，交易端约88KiB；原机器config备份及含config源码的回退包权限600。

终测df空闲约72GiB。部署开始时约31GiB，但本次测试清理量远小于该变化，**不能将约41GiB空间变化归功于测试清理**；两端没有执行人工业务数据删除命令，正常生产者重启/保留策略按原流程运行。

交易测试安装没有Git元数据，tools/run_deployment_tests.py的prepare_run仍要求Git来源，本次采用独立源码包验证。已测试并发布其finish_run清理/24日志实现，不声称该非Git安装可直接使用Git克隆型verify入口。生产Git安装的适用范围未变。本次不为了工具运行新增整仓Git历史或修改生产布局。

## 后续观察

下一合法25m发布检查spot/swap一致5000、full_history=0、原长历史PKL/Pivot保持；正常dry_run结果和状态提交完成后，比较新增执行分段日志。遇到需重建时观察全量请求和后续完整发布，不手工伪造ready、不清有效状态来缩短等待。当前只完成测试部署及上述隔离/缓存/控制协议验证，尚未升级生产。
