# 工作区生命周期与可删除test生产部署

## 2026-11-05 工作区清理部署及生产test隔离完成

部署e32f503至43.167.235.164:/home/ubuntu/alphatrade，保留账户、none模式、资源覆盖与有效snapshot；未启动交易。失败初始化/回放工作区释放和有界诊断生效；未改core/startup/数学。根tests与专用benchmark/parity工具、已知测试缓存/日志/部署包归入test/archive，生产sparse checkout排除测试文件。tools/run_deployment_tests.py从已提交Git生成test/verify-*，隔离空凭据、数据、缓存和日志；删除test不影响业务，下次verify重建。两次隐藏test后配置与业务导入均通过，TDD还验证实际删除并重建。

Ubuntu完整1459通过、77跳过，唯一缺LightGBM依赖失败补齐后对应文件6通过；补装lightgbm4.7.0、narwhals1.37.0，其他包未升级，pip check正常。所有新测试日志位于test/verify-i4hr7688及test/verify-bshdonik；未把重叠用例相加。首次宽范围迁移/历史rollback脚本改写被审批拒绝后，采用逐项确认测试资产的窄迁移，历史rollback.sh保持原样。部署细节见[生产记录](2026-11-05-lifecycle-production-deployment.md)。

## 配置与边界

主干源码e32f503，后续仅补文档。备份/home/ubuntu/alphatrade-backups/20261105-lifecycle-e32f503保留替换前运行源码，独立于test。只追加DIAGNOSTIC_LOG_COUNT=24，原INITIALIZATION/资源预算、机器config.json、accounts与accounts.local.json未覆盖。当前PM2只有alpha、alpha2、alphatradetest在线；没有为alphatrade注册或启动新实例。

日常离线维护命令：bash tools/deploy_alphatrade.sh verify。root/tests及专用测试工具被生产Git sparse checkout排除，测试由Git重新生成到test/verify-*/source；所有pytest临时数据、Python/Numba缓存、安装缓存及输出都在test。test/archive只是历史证据，业务及新验证入口没有指向它的引用。手动删除test会终止正在运行的测试，但不会删除业务snapshot、账号、解释器或运行代码。

运行初始化工作区data/server_initialization和有效data/snapshot属于实盘计算，不是test，可由新的生命周期机制清理自有失败现场，不能把test可删除承诺扩展到这些目录。

原部署源码备份中仅测试目录和专用测试工具迁入test/archive，业务回退源码及原rollback.sh保持。Git对象内的历史测试仍保留，这是重建测试的来源，不是生产执行路径。没有改写历史回退脚本。

## 验收

- 本地生命周期/共同恢复等106通过、2平台跳过；隔离入口21通过、3平台跳过（有重叠，不相加）。
- Ubuntu完整套件1459通过、77跳过、1缺依赖失败，575.66秒。缺少的是既有LOCAL模型的LightGBM；按原requirements补齐后整文件6通过，25.00秒，原失败已关闭。依赖检查通过；没有为结果放宽数值容差。
- 两次将整个test改名隐藏，运行tools/deployment.py check及config、core.real_trading、server/handoff/lifecycle/server_robust40与验证重建入口导入通过；未调用账户初始化/交易入口。
- 测试目录删除/重建由独立fixture实际验证，不只是静态路径检查。根运行文件不依赖test，入口可用Git版本重建。

本轮未改变策略参数或订单数学、未发送微信、未启动/重启alpha或交易；上游进程PID在本轮开始/结束一致。
