# 2026-11-05 测试机即时重建功能部署

## 结果与范围

目标43.173.122.28，目录/home/ubuntu/alphatrade。2026-11-05 11:14:51（UTC+8）完成11文件补丁部署，用户确认将测试机由none切回dry_run。PM2沿用alphatrade-dryrun（id2），新PID613090；已pm2 save。上游realtime_data PID2731029保持，不改上游、生产43.167.235.164或账户参数。

部署前测试机实际上仅有25m-eart1020.py一个账户；进程名dryrun不能作为模式依据，config.json原值为none。本轮只将其is_simulate改为dry_run，其他配置保留。重启日志出现“[干跑模式] 跳过杠杆、持仓模式及保证金模式修改”，新调度进入11:15数据等待。

## 具体发布文件

startup.py及tools/robust40下cli/server_robust40.py、handoff.py、initialization_config.py、lifecycle.py、market_inputs.py、replay.py、resources.py、server.py、telemetry.py、arrow_demand.py，共11文件。7个本轮恢复文件加测试机缺少的前版清理/需求接口依赖。

本次**未发布前任务core/backtest.py、core/position.py、core/real_trading.py及positions/ModelRobust40Strategy.py的性能优化**，未把它们混入恢复补丁。服务器没有Git元数据，按只读源码备份逐文件核对，部署前再次检查源未变化，替换后直接比较文件内容；没有新哈希机制或大行情副本。

initialization_config保留远端data_pipeline=True、32币批次、MODEL并发4及原cgroup/内存/swap预算，仅加入ARROW_DEMAND_ENABLED=False、ARROW_CONSUMER_NAME=None、DIAGNOSTIC_LOG_COUNT=24。上游联动未启用，检查当时25m两份Arrow均full_history=1，现有全量发布可供初始化。

## 验证与状态保留

使用实际PM2解释器/home/ubuntu/alphatrade-env/alpha311/bin/python。候选验证基于测试机原源码加同一11文件补丁；空账户、独立config/data/cache/tmp、socket.connect审计禁网。

首次141通过、22跳过、2失败，63.27秒；两失败由打包漏掉公开examples/model_robust40示例引起，补齐后仅复测两项，2通过、11.28秒。合计143项通过，不把复测重叠用例重复统计。22跳过包含缺失传统历史/5m账户夹具及上游配套源码测试。没有修改策略数值容差或生成新golden。

在账户空闲窗口停止仅测试PM2，原子替换源码，切dry_run后禁止网络做业务导入检查，确认资源配置保留。所有账户文件字节不变，snapshot_time.txt字节和warmup.pkl大小/mtime在部署步骤前后相同。部署前当前有效包complete=True，截止2026-11-05 01:25 UTC，S3 origin=2025-04-30 00:25 UTC；.previous保留。没有清空data、手动制造修订或强制重训。

部署后PM2在线、启动检查成功。尚未完成下一25m整轮，更未触发真实“超恢复范围→完整初始化→下一offset续算”验收；143项是离线控制流/恢复等测试，不等于真实14050小时整轮初始化对拍。

## 磁盘与文件生命周期

测试资产全部放在/home/ubuntu/alphatrade/test/deploy-rebuild-20261105/，另有test/deploy-rebuild-probe.py。包括非敏感源码备份source-before.tar.gz、补丁、隔离candidate、验证日志、脚本和结果；config-before.json包含原机器配置，权限600，不输出或下载其凭据。业务源码是独立文件，不依赖test，删除test只会丢掉测试记录和本次回退备份，不影响服务导入/运行。

磁盘部署前后均约31GiB可用（df取整）；未复制全量行情或snapshot。没有新增定时采集器或通知任务。

## 后续核查

下一25m普通dry_run轮确认状态提交、正常选币/权重输出及不发送真实订单。实际遇到超范围修订时，检查MODEL_REPLAY rebuild_required、后台接管、完整Arrow等待、初始化成功清理和下一合法offset续算；不为了验证主动删除有效snapshot。
