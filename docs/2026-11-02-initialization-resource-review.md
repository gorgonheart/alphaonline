# 初始化资源窗口：实现与人工复核

状态：本地实现、相关离线回归和Ubuntu隔离资源组测试完成；未部署代码或重启生产服务。用户最新约束是每5分钟切换、独占可使用4～5GB、非独占初始化进程树以2GB为目标。本文使用GiB（1024³字节），不把回收目标写成瞬时内存保证。

## 运行规则

两套安装分别负责奇数/偶数5分钟offset，各自正常交易仍串行。已有初始化workspace隔离保留，55m初始化不会使用5m普通交易的cache/runtime。Linux两个安装使用同一用户的 `/run/user/<uid>/alphatrade-initialization.lock`，全机这两套程序同时只有一个重型初始化，其余监督进程只排队、不加载行情。

1. startup原有提前唤醒后、等待上游ready前，检查当前offset的MODEL小元数据。需初始化时启动prepare，预加载代码、账户及因子依赖配置；不读取行情、不创建正式workspace、不下单。
2. 同stamp四ready齐全，重新检查账户配置及snapshot；准备期间状态已更新则取消。复用准备得到的依赖配置冻结输入，之后仍启动独立compute进程绑定workspace。没有承诺所有计算模块的冷启动成本都被消除。
3. Linux初始化放在独立systemd服务，普通用户运行，位于 `user-<uid>.slice` 下，与现有PM2 session为兄弟资源组。新服务nice=10、CPUWeight=10，无CPU硬配额；无竞争时可使用四核。MODEL交易worker nice=3，上游原进程优先级不变。其他传统策略没有新增资源组/降优先级。
4. 每2秒只查进程命令名、四ready文件存在性及cgroup计数。每300秒首30秒、末30秒，或四ready未齐，或任一安装有account_exec / MODEL trade worker，均为忙时。只有其余时间可进入独占模式。这是保守判断，不认定整机完全无其他负载；0m没有发布时会保持忙时至后续完整轮次。
5. 忙时总预算目标2GiB，独占4.5GiB；扣除64MiB监督进程预留后设置MemoryHigh，MemoryMax固定为5GiB减64MiB。仅模式变化调用systemctl。忙时停止派发新因子批次及启动下一重计算阶段，已执行任务可完成。没有SIGSTOP，也没有4分钟计算截止。
6. 资源控制失败只停止本次初始化服务，不退化为无内存控制继续跑；OOM是初始化失败，沿用原snapshot安装/完整性流程，失败结果不安装。已运行的实盘、alpha和PM2父进程不被重置优先级或停止。

排队后若原stamp已不再是上游最新完整发布，沿用原规则拒绝它，等待之后合法offset重试；不会混合新旧发布或偷偷改变计算截止时间。两个安装应使用同一Linux用户及独立可写data路径；重复手动启动多个同根startup不属于受支持运行方式。

## 配置与场景区别

唯一功能配置在 `tools/robust40/initialization_config.py`，账户策略参数不变。

| 设置 | 自动初始化 | 正常续算/修订回放 |
|---|---|---|
| MODEL_JOB_NUM总上限 | 4 | 4 |
| 时序因子在途任务 | 最多4，批次前等待窗口 | 最多8，最多4同时运行 |
| 选币/聚合/模拟进程 | 最多1 | 最多3 |
| 选币内部线程/数值库线程 | 1 / 1 | 1 / 1 |
| 因子/截面分片 | 16列 / 1组 | 原主机设置 |
| Linux资源组和准入等待 | 启用 | 不启用；被识别为前台，初始化让行 |
| 输入、状态和计算公式 | 原完整PKL初始化 | 原4050 Arrow与S3/MODEL共同状态 |

手工prewarm沿用总上限及初始化阶段上限，但不自动建立Linux资源组；不要把手工命令当作本方案受控入口。Windows用于离线正确性验证，不提供systemd内存约束。

## 2GiB目标的实际边界

MemoryHigh触发回收/节流，不是RSS硬上限。切换前已经加载的匿名矩阵可能暂时保留；能否迅速回收到目标取决于工作集、swap及当前阶段，暂停新任务不会自动释放父进程数组。5GiB MemoryMax是最后防线，也不是整个8GiB服务器绝不OOM的保证。框架/子进程之外还要给上游、实盘、内核及文件缓存留空间。

`INIT_RESOURCE`输出模式、预算、实际cgroup内存、上一阶段采样峰值；回收后输出 `busy_target_reached_s`，超目标每30秒输出 `busy_target_pending_s`。计数包含组内进程和被计入组的缓存，与各进程RSS求和不同；监督进程在组外，留64MiB预算，不能只看cgroup数就称测量了整个进程树。2秒采样峰值可能漏短暂尖峰。

仍需人工在全市场初始化及下一5分钟上游/实盘重叠时采样：组memory.current/memory.peak、memory.swap.current、memory.events、监督进程RSS、整机MemAvailable及实盘耗时。若忙时长期超过目标、swap抖动或初始化被OOM，应继续拆分高峰阶段/缩小任务批次；不得靠降低因子精度、改S3参数或中止半成品状态检查达标。本批不声称这些全量验收已经完成。

## 本批新增core/startup人工复核项

| 文件 | 新增修改 | 对传统策略/原功能的影响 |
|---|---|---|
| core/backtest.py | 可选compute_gate在读取、因子、截面及选币/聚合/模拟阶段入口等待；私有_step5_worker不再回传已保存的大矩阵 | 未配置gate直接通过；公共step5返回接口、落盘、异常传播均保留。取消的是无人消费的私有进程间回传，没有取消原交易功能 |
| core/select_coin.py | MODEL初始化有界因子队列增加批次准入；可选select_coin_inner_threads | 普通策略无gate，原线程数公式保留；因子公式、输出列和精度不改 |
| startup.py | ready等待前新增3行MODEL准备入口 | 普通策略不启动prepare，正常账户交易仍subprocess.run串行；新增功能，不是需恢复旧版的功能取消 |

上一批已确认的backtest_job_num、有界队列、core/utils/section_state.py投影和S3分块保留；本批没有再改section_state或S3数学。不能把git总diff的所有行都称为本批新增，也不能用上一批确认替代本表人工复核。无alpha、accounts、positions、signals或factors修改。

运行工具变更：server.py增加prepare调度/生命周期锁，cli/server_robust40.py增加prepare及阶段profile，resources.py负责资源控制，bounded_pool.py新增可选before_submit。配置在tools，不进入仓管算法或账户策略参数。

## 验证证据与部署前检查

- TDD资源首轮9项失败；实现后相关组合74项通过（3条既有pandas警告）。随后增加控制器切换/失败测试，资源专项18项通过；两批有重叠，不相加。
- 覆盖普通策略/其他offset跳过、ready前不创建工作目录、超时取消、配置/状态变化取消、准备配置复用、忙锁防重复、批次准入、保存仍调用及异常仍传播、只控制自己的资源组。
- 既有Windows四核真实输入抽样中，初始化时序1/3/4进程63.78/44.61/46.43秒；4050为19.55/11.62/11.58秒。六组完整因子输出相同。不是新调度整轮收益或Ubuntu全市场峰值证明。
- Ubuntu systemd255、cgroup v2隔离小测试：原用户uid1000，父子nice均10；组MemoryHigh从128→320→128MiB切换，忙时准入等待3.003秒，峰值108.32MiB，swap0。只验证控制/继承，无需生产行情；工作目录 `/tmp/alphatrade-resource-review.Ng76oE`。该测试没有超过忙时阈值，**未验证大堆回收至2GiB的速度**。
- Ubuntu两个独立进程争用隔离临时锁：第二进程等待，第一进程故意异常退出后第二进程取得槽位；通过。测试将runtime目录替换到/tmp，未争用生产初始化锁。

服务器需要systemd、cgroup v2 memory/cpu控制器、可用的用户runtime目录、非交互sudo运行systemd-run/systemctl，以及Python环境已有psutil。本机目标已验证上述权限/服务创建；其他服务器必须先验证，不静默绕过。临时服务以原uid/gid执行Python，不把账户密钥放到systemd命令参数；运行日志在原账户server_initialization目录。不要直接把整套PM2放入低优先级组。

人工先审核本表的core/startup diff，再部署新worker并观察至少一轮完整初始化和一次跨5分钟重叠；本批尚未执行这一步。资源参数变化不改变模型配置比较，不要求为了本次调度改动清空已有snapshot。
