# FACTOR_BLOCKS 聚合统计与测试发布

## 范围

用户确认增加统计并部署测试服，要求使用 `dry_run`。初查config.py字面值为none，进一步检查发现机器config.json已覆盖为dry_run；安装时显式保留dry_run并以实际导入值验收。只改 `core/select_coin.py`、`core/utils/factor_data.py`，新增 `core/utils/factor_metrics.py`；不修改策略数学、参数、输入窗口、并发、排序、持久数据格式、alpha 或资金费率逻辑。不发布上轮已撤回的市值优化，也不夹带尚待独立发布的历史映射修正。

共享因子块入口覆盖初始化、普通续算、修订回放和正式交易计算；未走因子块的传统路径不新增汇总。`config.factor_block_profile` 缺省 True，设 False 可关闭聚合采集，不改变计算路径；关闭后仍有少量空上下文调用，非零成本。

## 日志口径

沿用现有日志，每阶段追加一条 `FACTOR_BLOCK_PROFILE` JSON，原 `FACTOR_BLOCKS` 总耗时行保留。无独立文件、逐币日志、额外行情读取或结果二次序列化。父进程只保存每任务三个浮点标量及最多十个慢任务标识，不保留额外因子表。

- account/run_time/input_mode/status/error_type/tasks/rows：轮次与结果归属。失败时 tasks 只表示已收到的任务，不表示已提交或已计算总数。
- worker：跨进程累计 CPU/墙钟；Arrow reader/切片、转 pandas、merge、factor_prepare、转 Polars。factor_prepare 包括原预处理 hook、因子数学和最终 pandas 表构造；外部数据读取包含在其中，不是独立不重叠项目。Arrow 的 mmap 打开和导入不在 arrow_read_slice 内，而在 worker 总计中。
- parent_submit_s：生成输入、调度门等待、提交之和；auto 路径包括父进程读行情，初始化可包括资源门等待，不能称纯调度CPU。
- parent_wait_s：有序 future.result 等待，包含未完成计算/回传；ready_at_consume 只说明观察时 future 已完成，不表示结果此前等了多久。
- parent_concat/parent_append/parent_clear：串行消费；parent_partition/parent_write 是 append 子项，不能再次累加。write 包含去除分块辅助列及编码写文件，不是纯磁盘服务时间或 fsync 完成时间。
- first_result_s：从阶段开始到首个结果消费，包含创建进程、导入、计算、回传；不是纯 spawn 时间。
- pool_exit_s：包括错误场景等待进程池退出的时间。
- completion_to_consume：同机 monotonic 时钟，worker准备返回到父进程消费的间隔，包含序列化、回传、排队及有序等待，不能称纯IPC耗时。
- worker_wall/worker_cpu/completion_to_consume：任务分布 p50/p95/max，不是多个自然轮的阶段P95；slowest 为墙钟最慢十个已消费任务。

worker累计与父墙钟相互重叠，不相加分摊历史20.81秒。失败日志生成失败也不替换原计算异常。仅两条阶段日志，无独立生命周期或清理机制。

## 验证

- TDD：缺统计接口失败；成功/失败任务、背压边界、三种输入模式统计开关输出逐值一致；真实spawn Polars返回值验证。
- 独立复核补充：失败进程池退出计时、报告异常不覆盖原业务异常，均先失败后修复。
- 本地相关53项通过；包含共享因子、数据所有权及初始化/截面续算相关测试。
- Ubuntu候选隔离32项通过（5.79秒），网络调用由audit hook阻断；候选仅有上述三个运行文件。
- 合成全成本统计开销、安装和自然轮结果见后续执行记录。合成128币×4050行×80个Bias参数，4个spawn进程、32币写块；不代表真实全市场因子性能。

## 2026-11-06 执行结果（香港时间）

- 三组实际旧版/候选版交替完整进程：原版13.013/12.738/12.586秒，候选11.790/11.574/11.337秒；包含导入、创建进程、构造因子、回传、写文件、删除因子块及进程退出。原版中位12.738、候选11.574秒，样本小且有缓存/宿主负载影响，不宣称统计使算法提速。
- 同一候选统计关闭/开启完整进程11.463/11.403秒，计算加释放9.596/9.557秒；未见明显附加开销，不能据单对测量声称零成本或P95已验收。
- 八次实际运行均产出518272行、381730900字节IPC；逐值等价由前述回归验证，此处行数/字节数只作运行完整性补充，不替代数值对拍。每轮因子块均已由原close释放。
- 开启样本只新增一条2271字节JSON。128任务真实4进程计时可解析：parent_wait7.288秒，partition0.704秒，write0.732秒，pool_exit0.573秒；completion_to_consume P50 0.040秒/P95 0.318秒。仅说明观测可用，不用于解释自然轮20.81秒。
- 初次benchmark误将项目路径改到无配置目录，配置检查提前正常退出；该组六个约0.04秒结果无BENCHMARK标记，全部排除。修正脚本并增加必须抵达计算/统计标记的断言后才记录上述数据。
- 测试机11:04:03安装完成，PM2 `alphatrade-dryrun` PID2066058，实际导入is_simulate=dry_run、profile_enabled=True；11:04:08和11:05:01进入正常调度。alpha `realtime_data` PID1421306保持不变。
- 安装第一步的部署探针曾因嵌套字符串换行错误失败，自动恢复旧运行文件并保持交易服务停止；修正探针语法后重试成功。PM2停止时日志的KeyboardInterrupt来自调度sleep，并非统计计算异常。未跨过25m账户交易轮次。
- 三个安装文件与已验证候选逐字节相同；启动后未发现新异常。测试占用约11.83MB，服务器空闲44.25GiB，未留计算块或复制行情。业务不依赖test目录。
- 本轮截至11:05未到下一25m自然计算，因此自然轮统计及20.81秒归因仍待后续日志；不宣称自然初始化、修订回放或实盘成交已验证。

## 资金费率及发布边界

发布前只读比较：本地alpha、测试alpha、正式alpha2的funding_backfill、funding_hours、funding_publication、history_data/funding四文件内容一致。本次不写alpha，已上线资金费修正保持。测试源码、临时数据、备份及报告均在 `/home/ubuntu/alphatrade/test/factor-metrics-20261106/`，可删除且无业务依赖。生产服务器不部署、不重启。
