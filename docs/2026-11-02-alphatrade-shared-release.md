# alphatrade 初始化优化发布记录

## 授权与范围

用户确认本轮core复核并要求发布到alphatrade；预检发现alpha2在19:55仍发布5000根Arrow（full_history=0），用户追加确认恢复完整发布并重启alpha2。2026-11-02 20:54:44（UTC+8）完成代码切换及两个PM2服务重启。

- 目标：`/home/ubuntu/alphatrade`，PM2 `pmt-startup-alphatrade`。
- 生产者：`/home/ubuntu/alpha2/config.py`仅publish_kline_num从5000改0，重启`alpha2`；发布器已有完整历史能力，无需替换。
- alpha、alphatrade1保持原PID和配置；不改账户文件、机器config.json、仓管参数、S3日期、自动补BNB配置，不主动清理snapshot或安装测试snapshot。
- 初始化启用`selection_block_hours=2016`；4050续算仍原选币分支。资源/分批依赖一并发布，避免只更新整池回调造成缺模块。

## 实际代码范围

生产之前尚无前几轮初始化依赖，因此本次累计替换/新增18个Python文件，外加6份文档；不是只有最后一轮两个core入口。

| 范围 | 文件/作用 |
| --- | --- |
| core 7文件 | backtest.py（阶段资源及整池选币入口）、select_coin.py（严格长因子复用、分批分派、可选输入）、model/backtest_config.py（输入模式）、utils/arrow_reader.py（完整Arrow及裁行）、datatools.py（严格读取）、functions.py（输入准备分派）、section_state.py（既有S3优化） |
| startup.py | 同offset提前预检/准备入口3行 |
| sections 1文件 | VolumeMaxBias_进出场阈值分离官方.py，已测试S3低内存实现 |
| tools 9文件 | bounded_pool.py、streaming_factors.py、streaming_selection.py、robust40/resources.py、cli/server_robust40.py、cli/prewarm_robust40.py、initialization_config.py、server.py、market_inputs.py |

对应原审核范围见[完整Arrow](2026-11-02-full-arrow-initialization-review.md)、[资源调度](2026-11-02-pressure-selection-review.md)及[共享选币](2026-11-02-shared-selection-review.md)。暂存包中的每个Python文件与已验证BENCH逐文本比较一致；仅功能开关0→2016及其注释不同。

## 验证与运行状态

发布前相关离线检查62通过、1个生产者测试排除、8个已有警告；未放宽数值容差。此前43份选币与28份权重严格一致。发布时确认startup无活动子任务，再停止对应调度；逐文件原子替换后检查与暂存内容一致。

20:55:25检查：alphatrade已通过账户自检，20:55轮检查旧snapshot得到`incomplete calculation`，启动低优先级prepare，已预载代码并等待同轮四ready。alpha2处于重启后的历史K线初始化，尚未观察到新一轮完整Arrow；这时存在的5000根文件属于19:55旧发布，不能作为新设置未生效的结论。PM2 error日志尾部包含旧错误，需按更新时间及重启后增量区分，不能直接认定新错误。

20:58:52复核：alpha2已在20:57完成历史数据准备，调度进入21:00非启用offset等待；重启准备跨过20:55，错过该轮发布。当前仅启用55m，下一合法发布预计21:55，新完整Arrow尚未验证。两个服务PID稳定且online，错误日志mtime分别停在20:54:41/20:54:39（停止旧进程阶段），重启后没有新增错误日志；alpha及alphatrade1保持原PID。20:55 prepare按原240秒缺输入期限取消，保留snapshot并等待后续合法offset，不为赶本轮临时修改期限或复用旧行情。

发布完成不等同于全新初始化/交易成功；21:55首次新Arrow发布与初始化结果需后续日志确认。

## 回退

备份：`/home/ubuntu/alphatrade-backups/20261102-shared`。回退命令：

```bash
bash /home/ubuntu/alphatrade-backups/20261102-shared/rollback.sh
```

脚本停止本次两个服务、还原被替换代码及alpha2发布配置、删除仅本次新增的代码文件，再恢复两个服务。不会删除账户或运行数据；运行后生成的新snapshot不回滚，若后续已有新数据/新部署，执行前应再次评估。发布脚本执行失败会自动调用同一回退脚本。
