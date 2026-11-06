# 超恢复范围即时转后台重建：本地实施与人工复核

日期：2026-11-05。分支：codex/next-runtime-efficiency。用户批准设计并选择“立即后台重建，完成后下一合法offset交易”。本轮未提交、未部署、未重启、未下单、未删生产数据。保留同分支此前执行器缓存/模拟复用/Arrow联动等未提交修改。

## 实际规则

1. `replay.before/common_before`找不到严格早于修订时间的共同恢复点，抛`RebuildRequired(ValueError)`。CLI在交易前捕获，保留旧snapshot、写complete=False、返回75。失败回放副本先走原生命周期释放。
2. startup仅识别自身MODEL CLI的trade模式75，立即登记后台重建，跳过该失败轮统计，继续其他账户。普通非零结果继续抛原CalledProcessError；传统入口即使返回75也不接受为重建。
3. Coordinator最多一个待数据线程，每2秒检查待重建账户：小配置/状态、同offset四ready、两份Arrow的full_history及必要列。缓存未变化的Arrow头部；不加载行情矩阵，不创建冻结工作区，不占全局计算槽。
4. 仅全量数据就绪才启动原资源监督器。Linux先持账户dispatch锁，再等现有全局初始化槽；worker继续持初始化锁，使用snapshot锁处理状态。父进程重启不意味着锁自动被新父进程接管，已有owner仍阻止重复启动。
5. 取槽后重新选择最新完整同offset发布，再调用原`initialize`，共用冻结、因子/选币/S3/MODEL计算、验证及原子安装。排队日志文件名保留请求stamp；日志publication_stamp、工作目录和冻结输入使用实际计算stamp。
6. 发布切换/全量缺失返回76，回到等待；同一文件版本不反复启动worker。已计算失败的同配置/同实际stamp，在既有warmup.pkl中仅保存一条initialization_attempt；新的发布/配置才能重新评估。成功安装沿原complete元数据替换该标记。没有新增独立状态台账。
7. 外部任务已经完成初始化时，即便Arrow已缩回短长度，也会取消pending，不会覆盖其已释放的全量需求。恢复状态容量检查与正常交易一致，使用resume_capacity而非固定240小时。
8. 初始化完成仅保存状态并释放需求、回收自有工作区，不调用账户交易入口。后续仍等下一合法offset正常交易。

立即交接不等于立即拿到全量数据：如果上游仅在下一发布点生成完整Arrow，仍必须等待它。两端Arrow需求协议默认关闭，未开启时日志提示由上游配置全量发布；本轮没有启用生产联动或实现上游盘中强制重发。

## 异常与清理范围

自动交接明确限于无共同恢复点和既有inspect识别的无效状态。没有把任意ValueError、订单失败、ENOSPC、权限或导入错误按75吞掉。其他结构性错误仍报真实异常，不能声称所有历史数据损坏都会自动修复。inspect自身既有状态判定范围保持。

旧snapshot保留至新包安装，避免先删唯一可检查状态。沿用自有失败初始化/回放清理和最近24份日志，活跃或归属不明目录不删；现存同stamp工作目录仍按原防重规则保留/跳过，后续新发布的安全回收沿原实现。并非清空data或所有offset。峰值仍包含旧snapshot与当前初始化包，磁盘不足不靠扩大删除范围解决。

正常健康账户preflight会核查小元数据和固定文件；等待线程仅在pending非空时存在。残留事务恢复可能在互斥锁内做真实I/O，因此不承诺其他账户零等待。完整计算/清理耗时仍使用原日志，本轮没有加大状态扫描。

## 人工复核修改范围

| 文件 | 本轮功能改动 |
|---|---|
| startup.py | 原check=True三行改为四行结果分派；正常统计顺序不变，重建轮不统计 |
| tools/robust40/replay.py | 类型化无恢复点异常，保留ValueError继承；75/76结果常量 |
| tools/robust40/cli/server_robust40.py | 明确异常分类、返回码传播、重建入口重查发布、计算前保存单份尝试信息 |
| tools/robust40/server.py | 单一待数据线程、账户去重、启动接管、实际容量复查、外部完成取消；小元数据尝试记录 |
| tools/robust40/market_inputs.py | 缓存头部的full_publication检查；已知发布切换使用PublicationUnavailable |
| tools/robust40/resources.py | 排全局槽前持账户dispatch锁；rebuild沿用初始化资源策略 |
| tools/robust40/telemetry.py | 已显式设置rebuild_required/waiting_data/blocked的状态不再覆盖为ok |

本轮无新增core、positions、sections、alpha改动。startup属于新增恢复调度，并非功能取消，不回退到core2.3.1。未改变权重、选币、S3、MODEL数值内核、订单顺序或自动补BNB。

## 验证证据

TDD覆盖无点可回放、类型边界、无交易调用、状态标记、普通异常、缺全量不启动、短转全启动一次、换轮、失败去重、重启锁、元数据损坏、另一offset继续、外部完成、真实startup函数AST隔离执行。独立复核发现两项重要问题，均先复现失败再修复：实际续算容量不一致、外部完成后仍等待。另补了外部完成不得重新要求全量的回归。

最终同批：**146通过、19跳过，102.14秒**。新文件25项包含其中，不重复相加。日志`test/rebuild-acceptance.log`。覆盖checkpoint、初始化worker/completion/dispatch/reliability、资源、回放日志、scratch生命周期、初始化交接、MODEL和传统checkpoint、Arrow需求。19跳过包含Windows不具备的能力、缺失旧账户/真实历史oracle；3条pandas大写H弃用警告非本轮逻辑变化。

验证严格边界：初始化入口委派到同一原函数，现有恢复/数值测试通过；**没有执行新的真实14050小时完整初始化旧新对拍，也没有在Ubuntu启动服务验证systemd/cgroup队列**。传统真实snapshot/行情缺失的测试没有用新golden替代。不能把离线调度测试说成生产全流程验收。

本地性能记录`test/rebuild-cost/timing.json`（alpha311，Windows，热导入，实际14子策略账户配置+合成小状态/文件，不读真实模型，Arrow需求开关关闭）：

| 操作 | 样本 | 中位 | P95 |
|---|---:|---:|---:|
| pending缺发布检查，包含真实配置解析/小锁操作 | 30 | 34.75ms | 39.23ms |
| 健康账户preflight，小元数据及固定文件stat | 30 | 43.48ms | 47.69ms |
| 成功子进程结果分派 | 300 | 0.0087ms | 0.0091ms |

这不是Ubuntu性能，也不含冷导入、磁盘换页、冻结、完整计算或失败副本删除。2秒轮询为待恢复阶段成本；11账户同时pending不能视作单账户成本。没有为省几十毫秒引入配置缓存失效协议或第二份大数据。

## 部署前/后下一验证项（本轮未执行）

- 人工复核以上7文件diff，连同前任务core优化分别验收，避免混淆两批范围。
- 在获准测试机的可删除test目录，用受控旧snapshot和同轮完整输入触发一次超恢复范围，确认75交接、无订单、同一原初始化结果/S3/MODEL截止一致、工作区回收。
- Ubuntu验证排队后换轮、父进程重启、资源锁与配置覆盖；等待短Arrow时全局槽应空闲，另一个健康offset按原流程运行。
- 验证后台成功后不自动交易，下一合法offset接续成功。真实下单开关仍由用户决定。
