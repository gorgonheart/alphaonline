# 初始化共享选币输入：转换、读取与有限并行

发布更新：用户已人工确认，2026-11-02 20:54已启用selection_block_hours=2016并发布alphatrade，alpha2经追加授权恢复完整Arrow。以下默认0/待审核描述保留为实验阶段记录，当前状态以[发布记录](2026-11-02-alphatrade-shared-release.md)为准。

## 本轮实现

仅优化自动初始化的选币输入组织，不改因子、S3、仓管或账户参数。上游alpha保持原样；正常4050续算和未启用工具的传统策略走原选币分支。

原候选先把全市场宽表转成按时间划分的Parquet，14个组合分别重复读取各块。现改为：

1. 从Kline时间列生成一次稳定排列，所有因子复用同一排列，空值/NaN和行对齐保持原样。
2. 一次只转换有限数量的列，写入LZ4压缩IPC缓存；每个record batch对应完整的2016小时时间范围及全部币种。不是上游Arrow发布格式，不修改alpha。
3. 每个时间块的公共列和所需因子只读一次，供整个策略池43个子策略共享；实际选币仍调用原core函数。
4. 转换和读取最多使用现有MODEL_JOB_NUM（当前4）；选币线程上限为min(2,MODEL_JOB_NUM)，原生Polars/BLAS仍1线程。不同阶段依次执行，不能把4×2理解为8个同时计算worker。
5. 全策略池在一个原框架mem_save_exe子进程内完成，退出时释放内存；没有恢复每组合回收进程的旧实验。

新缓存原子发布；缺列、行数不同或转换失败不发布完成目录。失败注入、null/NaN、不同row group、分类币名及完整时间截面已有测试。custom/entry_exit整个组合回退原全量选币，不尝试切断跨时间状态。

代码集中于tools/streaming_selection.py，旧Parquet重分区实现已从业务模块移除，历史实验源码保留在research/selection_before_20261102.py。旧生成缓存无需兼容；完整初始化使用独立workspace，新缓存不是snapshot的一部分。

## 最小core人工复核

| 文件 | 本轮差异 | 审核要点 |
| --- | --- | --- |
| core/backtest.py / run_backtest_multi | 选币阶段增加可选selection_pool_runner分支，调用既有mem_save_exe；原ProcessPool分支完整保留 | 仅处理选币阶段，聚合/模拟/异常传播和原默认路径不变；不新增模型名称判断 |
| core/select_coin.py / select_coins | 删除上一轮单组合selection_runner回调3行，恢复原函数直接调用 | 属于撤回旧工具入口，按最小原则恢复原实现 |
| tools/robust40/cli/server_robust40.py / apply_profile | 仅初始化且selection_block_hours>0时注入整池工具，配置既有并发上限 | 续算显式设置selection_pool_runner=None，选择线程恢复1；账户不增加策略参数 |

上一轮process_strategy_thread的可选factor_source/return_result继续使用，本轮未改其计算公式。工作区其他已有core差异不属于本轮新增审核，不能以此表替代之前的审核清单。

开关仍是initialization_config.INITIALIZATION.selection_block_hours：0关闭，2016为当前候选。尚未根据单次样本自动修改正式默认配置；正式alphatrade未部署或重启。

## 测试与计时口径

复用隔离副本17:55冻结因子输入与原14组合/43策略，后台上游照常运行；不访问余额、下单或发送通知。每轮首次计时前只清理由本实验生成的列缓存，源因子、冻结行情与基准保留。使用与此前相同的4.5GiB MemoryHigh / 5GiB Max（扣监督预留）、12GiB SwapMax及CPUWeight10。

| 实验 | 首次选币总耗时（含缓存生成） | 转换 | 采样匿名峰值 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 共享列缓存，串行转换/读取/选币 | 264.878秒 | 94.987秒 | 2.220GiB | 43份严格一致 |
| 紧接原路径对照 | 199.322秒 | 无 | 4.389GiB | 43份严格一致 |
| 转换/读取4并发，选币2线程 | 218.070秒 | 34.546秒 | 2.426GiB | 43份严格一致 |

以上计时不含单独对拍及解释器启动；cgroup总内存还包含文件缓存，不能等同匿名内存。输入IPC缓存实际约4.70GiB，较旧Parquet分区约4.04GiB增加约0.66GiB，清理时仍随初始化workspace处理。

前两轮研究入口仅明确设置资源组优先级；最终复核入口显式调用configure_process('compute')，并通过实际进程查询确认父子nice=10。最终轮还通过真实mem_save_exe传递可pickle的完整配置，并按框架实际的组合排序运行；不能把早期直接调用工具的峰值当作最终生产内存上限。

TDD首先看到新增接口/并行行为/调度分支失败，再实现；本地10项选币缓存/原路径测试通过，调度与profile 10项通过；Ubuntu相关组合31项通过（有重叠），6个原有H频率弃用警告。最后补齐“首次全量Kline读取前准入”和“上块释放后再等待压力”两个失败测试并修复，缓存专项本地9项、Ubuntu9项通过（Ubuntu2.90秒）。这些准入/对象寿命修复在下表性能采样之后部署，未再次重跑相同全量实验。本轮未运行无关全仓/E2E或实盘交易测试。

## 最终实际进程入口对照

两轮均为相同冻结输入、真实框架组合排序、父子nice10和同一资源预算；新路径通过mem_save_exe，原路径通过原ProcessPoolExecutor(max_workers=1)。是顺序共存负载实验，不是同负载重复统计或P95。

| 项目 | 新共享缓存 column_pool_hook_final | 原选币 baseline_hook |
| --- | ---: | ---: |
| 选币耗时，含首次转换/进程池生命周期 | 285.221秒 | 390.431秒 |
| 采样匿名内存峰值 | 3.157GiB | 4.144GiB |
| cgroup总内存峰值，含文件缓存等 | 4.438GiB | 4.438GiB |
| swap峰值 | 1.482GiB | 1.464GiB |
| 控制器总CPU时间，含加载/对拍 | 181.718秒 | 273.397秒 |
| 精确选币对拍 | 43/43 | 43/43 |
| OOM / OOM kill | 0 / 0 | 0 / 0 |

本次选币墙钟减少约27%，匿名峰值减少约24%，但swap未下降、总内存仍受相同MemoryHigh约束。新方案转换46.496秒、读块53.613秒、块内计算140.577秒；这些分项不包含全部落盘/回收/进程启动开销，不能与总计强行相加。单轮降低的是重复转换/读取和活跃数据规模，不能承诺每轮都快27%。

同日原直接入口还测到199.322秒与513.759秒，说明后台负载与缓存状态对墙钟影响明显。20:40短采样vmstat的I/O wait为19%～36%、swap-in约8～11MiB/s，CPU steal为0；user.slice无CPU配额节流。该证据解释当时等待，不能追溯断言每个慢阶段都由同一原因造成，也不能用“CPU低”判断已经结束。

新产物继续经过原聚合函数，14组合共28份spot/swap权重矩阵与原路径严格逐值一致，无容差放宽；aggregate_columns控制器总耗时100.777秒（含加载/对拍），无OOM。没有再次运行相同输入的完整MODEL/S3模拟、从零全流程或新一轮4050续算/实盘交易，因此这不是整轮上线验收。

## 发布与人工复核结论

- 当前代码已落地本地和隔离BENCH；正式alphatrade、alpha/alpha2本轮未修改或重启，未下单或通知。
- 收尾只读检查：本轮三个测试service均inactive/dead；PM2的realtime_data、alpha2、pmt-startup-alphatrade、pmt-startup-alphatrade1均显示online。因此不能沿用早期“正式alphatrade仍暂停”的状态判断，本轮没有操作这些进程。
- 默认selection_block_hours仍0。人工复核上方core入口后，可在隔离/指定初始化配置中设2016；只影响初始化，账户策略参数和正常4050选币分支不变。不能把旧672/2016 Parquet峰值直接套在新后端上。
- 不再缩小分块或增加新并发参数；目前已有一次排序、整池共享读取、4转换/读取与2选币线程的有限并行。后续优先观察多轮共存压力下的总耗时和I/O，再决定是否正式默认开启。
- 本轮测试任务已结束；只删除测试生成的4.70GiB IPC临时缓存，保留冻结源输入、43份选币、28份对拍结果、日志和已安装测试snapshot。服务器可用空间约11GiB；生产多offset容量仍需另算。

证据目录：`/home/ubuntu/alphatrade-bench-20261102-1755/reports/pressure_selection`，上述variant对应日志、jsonl采样、summary.json和result.json；最后专项测试为reports/column-cache-final-tests.log。复现实验入口在research/ubuntu_selection_pressure_worker.py及controller.py，早期不同入口/优先级记录保留，不混为同一基准。
