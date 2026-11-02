# 服务器初始化链路可靠性复查

日期：2026-10-30。范围严格为D:/alphaonline。沿现有“完整PKL初始化→snapshot→4050增量”的协议修复启动、日志、失败恢复问题；不更改选币、仓位、因子数学、交易窗口、BNB设置或账户参数。本轮没有修改或写入D:/alpha。

## 修复及改动范围

仅两个运行实现文件：

| 文件/路径 | 问题与处理 |
| --- | --- |
| tools/server_robust40.py / configure_output | initialize外层此前未走profile的编码设置。统一所有CLI入口及spawn导入阶段的UTF-8设置，子进程继承，按行刷新，避免GBK异常或长时间无可见日志。 |
| tools/server_robust40.py / initialize | 冻结行情、冻结市场规则并启动完整计算时输出阶段；冻结期间配置变化直接报错。恢复旧S3日期改用容错读取，损坏旧元数据不会让重建流程再报同一个错。 |
| tools/robust40/server.py / previous_origin、inspect | 只保留同账户/分钟、可解析、未超过截止的旧起点；无有效旧起点则按既有full_history规则重新解析。缺失/非法S3起点、实际截止分钟错误触发重建，仍仅做小元数据及文件stat检查。 |
| tools/robust40/server.py / Coordinator | 新后台监测线程在初始化退出时立即记录完成/失败，失败只读最多8KB日志尾部；不等待下一匹配offset。创建进程失败关闭日志、保留同轮重试能力。启动成功后父句柄立即关闭，子进程继续持有自己的输出描述符；原同轮去重、独立offset目录、单初始化槽位和240秒交易窗口保留。 |
| tools/server_robust40.py / trade | core.account_exec在账户未就绪时提前返回None，此时尚未计算，恢复原完整元数据；导入错误也不会提前作废snapshot。计算抛异常仍保留未完成标记，按已有流程重建。 |

本轮新增core/startup/config改动为零；它们此前尚待人工复核的差异不因本轮测试而视为已获审核。完整协调器改动需重启startup加载，本轮没有执行重启、实盘或生产数据删除。

## 验证证据

- 真实GBK输出重定向先复现与40m一致的UnicodeEncodeError，修复后框架图标/中文stdout和stderr均通过。
- 故障测试先失败：进程创建失败的句柄/重试、损坏旧元数据、账户未就绪误作废snapshot。非法S3起点额外先复现4项失败，修复后进入重建。
- 真实后台进程成功/报错退出，不再调用下一轮command也能立即收到主日志通知；错误日志尾部读取量受限。
- 真实Windows spawn父/子进程均加载40m的14子策略，输入模式、snapshot/结果/日志路径留在临时workspace，中文/图标输出正常；无交易所调用。
- 最终218项相关回归通过，包含S3独立oracle、MODEL运行时/主链路、旧预热、空仓缺口和5/15/20/25/45真实旧策略公开配置。日志`research/server_reliability_final_tests.log`，287项为既有pandas FutureWarning。git diff --check通过。
- 已保存20m旧版本snapshot的50次快检：小元数据11702字节，中位14.89ms、最大21.02ms。当前账户策略池已改，检查正确返回configuration changed，而不是强行恢复旧包。测量记录`research/server_reliability_inspect_timing.json`。

## 仍未满足的实盘验收条件

三账户CCI参数现已由用户改为2000，TopK窗口168，基本依赖为2×2000+168−2=4166根，仍超4050；持仓周期及缺失小时补算还需要额外余量。本轮保留用户参数，不放宽检查。旧20m snapshot需按新策略池重建，40/55m也需完成各自初始化。

218项通过证明本轮实现和已覆盖路径的正确性，不代表当前不兼容参数的4050全币池新增选币/手数已通过。修复C扩展后新Arrow与PKL的实际全链路验收也不能由本轮测试替代；本轮没有操作alpha或发布数据。
