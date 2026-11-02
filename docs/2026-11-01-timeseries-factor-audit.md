# 时序因子逐项审计、优化顺序与 Ubuntu 技术路线

## 范围与结论

本轮因子部分只读分析，不修改公式、参数、账户、core/startup或alpha。实际落地仅为[Hybrid缓存](2026-11-01-hybrid-cache-ubuntu-review.md)。

当前活动账户是20/40/55m MODEL及5m-onheart1020；只解析账户公开策略声明，没有导入账户凭据或运行交易。它们的时序因子参数集合相同，S3起始日期等差异仍独立保留。当前目录没有此前15/25/45账户，不能宣称完成这些账户真实运行验证。

递归展开截面依赖后：30个时序因子、72个参数列；每个MODEL账户另有4种截面因子、8个参数列，因此日志中的总数80包括截面列。跨账户合并S3日期会多出一项截面配置，不是同一账户同时计算81列。30个时序模块中，3个/12列由alpha C预计算并通过Arrow传入，剩余27个/60列正常由alphaonline计算。因子目录共93个Python文件，本次逐项审计覆盖活动依赖及111包装器依赖，不将未使用文件宣称为全部验收。

**仍有优化价值，首先减少既有Pandas路径的重复工作，再处理真正的滚动内核。不要直接把所有因子重写成C/Polars，也不要通过缩短历史或放宽修订检查换速度。**

## 生产日志与抽样方法

40m 2026-11-01 18:40轮次（logs/server_robust40.log:58901起）：

| 阶段 | 时间 | 边界 |
|---|---:|---|
| 时序因子总阶段 | 78.077秒 | 整体计时 |
| 读取all_candle_df_list.pkl | 1.61秒 | 在整体阶段内 |
| 多进程计算 | 68.91秒 | 包含池启动、传输、公式、pd→pl、返回和退出，不是纯公式 |
| Arrow/Polars拼接 | 0.04秒 | 已很低，不优先 |
| 分片保存 | 4.30秒 | 写出及裁切；与以上共处同一轮 |

924份币种/市场输入、80列、1个因子分片、MODEL_JOB_NUM=4。其余阶段时间不能全部归因到序列化，日志未进一步分解。因子分片与生产者PKL batch是两个概念：当前因子仅1片，增加分片会重复进程池、输入传输和部分前处理；不能当作提速手段。

离线采样：同轮40m Arrow `1793529600`，BTC/ETH/BNB/SOL/XRP/DOGE/ADA/LTC各现货、合约，共16帧，每帧4050行。前一次预热，两次计时，调用现有`core.factor.calc_factor_vals`；复制测试输入在计时外，框架内部copy、extra_data读取和计算在计时内。3种预计算列实际存在，未将其重算混入正常耗时。没有执行选币/下单/生产初始化。

这是主流币、当前Windows环境的抽样，不代表新币、下架币、异常行情或Ubuntu全市场P95；各因子非同时测量，也没有把毫秒乘924后宣称实际可节省时间。27项每帧全部参数合计平均约207.69毫秒，仅用于定位。另独立执行cProfile，避免把其开销混进下面的表。

证据与可复跑入口：

- `research/audit_timeseries_20261101.py`：静态清单及可选小样本profile；无生产写入。
- `research/timeseries_audit_20261101/inventory.json`：全部账户/参数/截面依赖。
- `research/timeseries_audit_20261101/profile.json`：每项初次/平均/中位/最大、样本来源、版本、错误列表。
- `research/timeseries_audit_20261101/detail.txt`：六个热点的独立cProfile。

## 逐因子审计

下表毫秒为**单帧全部活动参数**的抽样均值，不能按单参数理解；“优化”都是待开发候选。排序/筛选改变属于策略变化，不能隐藏在性能改造中。

| 时序因子 | 活动参数 | 毫秒 | 逻辑、风险与建议 |
|---|---|---:|---|
| CirculatingMcap | 1 | 25.80 | 公式只是流通量×close，主要成本在coin-cap读取/对齐/倍数修正。优先将data_bridge逐行log10/round向量化；保留23小时发布时间、merge_asof、众数倍数、NaN及symbol映射。 |
| ZfStd | 24 | 3.09 | pct_change符号×振幅后rolling.std；可用多参数只读入口减少临时列。保留pct_change缺值语义和ddof=1。 |
| BiasV1 | 2760 | 13.43 | rolling.apply每行Python调用argmax，复杂度随窗口增长；候选Numba单调队列。必须保留相同高点取最早位置、窗口首段、NaN、dropna/reset_index及距离+1。 |
| ILLQStdBbi45208 | 96/264/1104/2184/2320 | Arrow | 5列已预计算；最长6n=13920，不能在4050上回退重算。初始化Python完整历史路径与生产者C路径需继续对拍；零振幅/Inf、滚动std是核心边界。 |
| 111_输入隔离 | 13/26/36 | 12.27 | 3重滚动组合；包装器深拷贝因原111会覆盖首根成交额/主动买额。保留隔离，可只复制所需5个价格/成交字段，并复用同参数成交额均线、重复比值。不能直接删除copy。 |
| 新币 | 1 | 1.28 | 以当前输入最早时间计龄，非first_candle_time；小成本暂缓。缩短窗口会改变值，不得以真实上市时间悄悄替换。 |
| ZfMeanQ | 72/120/192/240/312/360/432/576/816 | 33.66 | 振幅均值再rolling rank，9组重复振幅和临时列。先做signal_multi_params；原生rolling rank仍占大头，不承诺多参数化消除全部耗时。 |
| QuoteVolumeMean修正 | 50/168/202/504 | 1.23 | 已multi，rolling默认完整窗口；保持min_periods=n，不改成1。暂缓。 |
| VolumeMaxBias | 720 | 0.61 | volume/rolling.max，已multi；S3底层时序输入。很快，不动。 |
| CorrBTC | 60/344/892 | 17.30 | 每币读取同一offset BTC、去重排序对齐，3参数重复计算收益。先multi复用收益，再评估worker内本轮BTC原表复用；对齐结果仍按每币时间轴。 |
| 双均线 | 336/432 | 3.38 | floor(n/3)与3倍窗口均线差；multi去临时列即可，不能将除3舍入规则改掉。 |
| SpotSwapBoth | 1 | 1.42 | 逐行现货/合约映射非空判断。轻量，可只读数组输出；不能用最后一行映射覆盖历史。 |
| 资金费率绝对值MAX | 48 | 3.51 | 0替换缺值、ffill、rolling.max后abs。注意不是abs后max，零也不是任意可丢弃缺失；ffill可能依赖窗口前的最近非零值。 |
| BiasQ | 420 | 3.38 | Bias再滚动百分位，已multi。rank约定及NaN敏感；除非复用排名内核有实测收益否则暂缓。 |
| 动量210401_双项异常隔离V2 | 3509/5421/12600/13608 | Arrow | 4列预计算，EMA/异常历史/两项重置/168小时冷却，显式requires_full_history；初始化本地_clean_anomalies仍cache=False，仅影响初始化路径，不是正常78秒热点。 |
| CciV3 | 1176/5208/10248 | Arrow | 3列预计算，多层ewm_fixed(window=15000)，短窗重新起算不等价。保留原公式及C/Python完整历史对拍。 |
| MinMax | 240/360 | 5.40 | TP滚动min/max归一；multi复用TP、避免临时列；平价0/0保留原NaN。 |
| Atr | 168/3552 | 14.59 | 两参数重复TR、DataFrame行max及大量增删列；优先multi，TR只算一次，skipna语义须保留，不能直接用传播NaN的np.maximum代替。 |
| Bias (2) | 240/360 | 3.28 | close/均线−1；可multi，小收益。与仓管Bias公式不同，不合并命名。 |
| HoursSinceSpotAndSwap | 1 | 7.09 | 找输入内首次双市场有效点并计算小时数；datetime重复转换和表复制可减少。索引/时区与多次上下架须验证。 |
| Cci异常隔离168_因果版 | 240/816/1536 | 17.22 | 异常检测+分段CCI+168小时冷却，已multi。先消除重复包装，若优化内核需保留reset位置、零md、真实小时冷却；历史需求不能只按n估算。 |
| 新币落九天用的 | 1 | 7.10 | 从输入首行计时，对无双市场映射置NaN；减少datetime转换/临时表。不能改成首次上市时间且宣称等价。 |
| PctChangeMax | 12 | 1.29 | 已multi，但会写入输入临时pct列。局部只读Series可改善接口隔离；公式快，优先级低。 |
| Cci过滤无现货删数据 | 1000/1440/1800 | 9.98 | 双市场TP掩码、两层rolling mean；multi复用TP/掩码。不能启用文件底部被注释掉的旧multi，它缺少双市场掩码，会改变TopK结果。 |
| Funding_fee_sum | 24/168 | 0.66 | 已multi，完整窗口求和；与下面Qps的min_periods=1不同，不直接合并。 |
| 浪Max23 | 398 | 6.65 | 振幅均值排名与volume均值排名取max；实际multi用np.maximum，不能改成skipna的DataFrame.max后说等价。 |
| 当前回撤 | 39/53/93/109 | 3.83 | VWAP/滚动高点−1，已multi复用；保留内置LUNA屏蔽和零volume语义。它同时服务两个截面功能。 |
| QpsSeqFundingSum | 24/336/938/1079 | 1.18 | 已multi、min_periods=1资金费率和；快，暂缓。 |
| QpsSeqDropHigh | 336 | 0.78 | close相对滚动高点回撤，已multi，保留分母1e-12。暂缓。 |
| SelectCoin | BTC/ETH/SOL/BNB | 8.31 | 4参数重复对整列symbol去横杠；multi一次标准化，再比较4次。不能假设一帧历史symbol永不变化而改成末值广播。 |

另有仓管净值上的 `Bias(48/144/216)`，不属于这924份市场K线的主因子阶段。该Bias是`(close/MA).pct_change(n)`，也不能与`Bias (2)`合并。再择时signals亦在子策略模拟后，不应混入本表排序。

## 四个截面依赖不能遗漏

| 截面模块 | 时序依赖 | 优化边界 |
|---|---|---|
| VolumeMaxBias_进出场阈值分离官方 | VolumeMaxBias(720) | S3排名、延迟队列和持仓状态必须共同恢复，不能当成一根独立信号。 |
| TopKTo2K | Cci过滤无现货删数据(1000/1440/1800) | 先CCI完整依赖，再168/202根历史的曾入选状态；保留2.1退出边界和市场域。 |
| 固定回撤阈值触发币数占比开仓门槛 | 当前回撤(39/93) | 分母包含符合条件的币数，不能以加速为由先筛掉未触发币。 |
| 动态回撤阈值 | 当前回撤(53/109) | 当前实现按时间对输入域做均值/std，没有按最终交易市场先裁剪；会影响跨市场优化的合法性。 |

当前TopK参数已不是历史讨论中的2600；本次读取到最大1800。其双层CCI加TopK窗口须一起核算，不能沿用旧2600结论判定当前配置失败。Cci异常隔离还涉及异常识别与分段边界，未完成全市场完整历史/4050对拍，不宣称所有因子都已证明截断等价。

年龄因子的长/短历史值本来可能不同。当前过滤使用新币>168、双市场年龄>10或>0、落九天年龄<490；很老的币即使数值不同也可能得到同样布尔结果。这不是允许任意改变日期算法的依据，需对新币、重新上市和断档分别验证。

## 用profile确认的优化抓手

六项独立profile仅用于归因，以下秒数包含profile开销，不与无profile表直接相减：

- 市值：16帧0.562秒，其中load_coin_cap约0.499秒，Series.map约0.286秒；64,800次逐元素lambda。优先向量化倍数计算，并保持原众数选取；不是市值乘法慢。
- BTC相关性：16帧0.506秒，额外数据约0.205秒、48次signal约0.259秒。数据读取/对齐和参数重复都值得优化。
- ZfMeanQ：16帧0.724秒，144次rank约0.390秒；多参数化能去掉144次signal中的重复表达式/列写入，但不能消除全部rank成本。
- BiasV1：16帧有64,800次窗口argmax回调；这是适合专用数值内核的明确热点。
- ATR：大量临时列插入/删除，比两次rolling mean更值得先处理。
- 111：多层Series算术和rolling居多，保留首行输入隔离；不能把全部时间归因于copy。

## 优化顺序及预计改动边界

| 顺序 | 工作 | 改动范围与收益判断 |
|---|---|---|
| 上Ubuntu前 | 显式spawn、服务缓存/依赖/权限验收 | 进程池共享路径需人工复核；是跨平台安全准备，不能承诺比fork快。 |
| 第一批 | ATR、ZfMeanQ、CorrBTC、Cci过滤、SelectCoin的multi入口；111局部输入复制/重复表达式复用 | 首选只改对应factors，复用已有框架接口，无新状态、无新技术栈。按热点逐项提交，不将所有因子一次重写。 |
| 第一批并列候选 | coin-cap倍数向量化 | core/data_bridge.py一处纯计算替换，收益证据明确；需人工审核，尤其零/负/NaN/Inf、舍入边界、众数并列。 |
| 第二批 | BiasV1缓存Numba argmax内核 | 因子文件内独立kernel，保留最早并列最大值和缺失行为；禁止fastmath，测试冷/热及多进程。 |
| 第二批 | BTC原表在worker内按本轮/来源/offset复用 | core/data_bridge.py的小范围缓存候选。必须区分初始化冻结目录与公共目录、文件更新及offset，不持久化第二份行情；缓存原表，不缓存所有币的对齐结果。 |
| 条件推进 | 减少任务序列化、仅一次传入小配置、少量币成组且限制在途任务 | core/select_coin.py进程池调度；先测IPC/启动/RSS，再决定组大小。小分组不等于整市场一任务；避免负载倾斜和已有OOM。 |
| 暂缓 | 全量改Polars/原生扩展、常驻多轮池、GPU、持久化全部因子增量状态 | 改动跨越公式、类型、数据生命周期和修订回放；当前尚无收益证明支撑复杂度。 |

优先以小改动解决有证据的重复工作，不承诺总阶段降至某个固定秒数。每项优化前保存旧路径为只读oracle，先失败测试，再新实现；验证通过才做同输入/同进程数性能对照，不另建golden掩盖差异。

## 并行、数据传输和可进一步减少的工作

1. `calc_factors`每币submit一份DataFrame和conf，收回Polars；Arrow mmap并不意味着后面的multiprocessing传输也是零拷贝。当前80列只有1片，简单调小factor_col_limit会重复开池/传输，不作为速度方案。
2. `MODEL_JOB_NUM=4`只约束框架池，未限制BLAS/OpenMP/Polars内部线程。Ubuntu基准应固定总CPU配额，比较2/4 worker及底层线程设置；不能同时把进程数和每个线程池都开满。`POLARS_MAX_THREADS=1`可能损害后续单进程截面阶段，需整阶段对照而非默认全局写死。
3. 减少完整表复制：旧signal路径每个因子一份copy，multi路径不保证只读（111原版和PctChangeMax是具体例子）。不能将全仓legacy copy直接取消，先逐因子落实输入不变合同。
4. 按市场只算必要因子有潜力，但必须从实际选币域和**截面依赖**构建保守集合。S3是mix_swap；动态回撤z-score当前可能使用两个市场输入。仅凭最终是合约下单而跳过所有现货因子会改变结果。这个方案不列为第一批最小改动。
5. `FactorHub.get_by_name`在缓存命中前仍设置locale，并且调用端/dispatch各查一次。可作为小开销清理候选，但profile未证明其为主瓶颈，不为省少量调用重构模块系统。
6. 常驻池能省导入/JIT，但增加跨账户配置、offset、外部数据和异常恢复的生命周期风险；优先已有磁盘编译缓存及单轮initializer，不先引入常驻服务。

## 技术栈选择

| 技术 | 本项目适用点 | 判断 |
|---|---|---|
| pandas/NumPy向量化 | multi参数复用、去临时列、coin-cap标量map | 第一选择；已经部署，不改变数据框协议。 |
| Numba | BiasV1专用argmax/状态机；已有MODEL/Hybrid内核 | 第二选择；数组/标量稳定签名、cache=True、验证新进程命中。不要给已经原生实现的全部rolling机械加JIT。 |
| Bottleneck | 某些移动统计可试验 | 非即插即用：move_argmax并列取右侧，而原numpy.argmax取左侧；move_rank也不是直接的pandas pct rank约定。未经oracle不能替换。[官方语义](https://bottleneck.readthedocs.io/en/latest/bottleneck.move.html) |
| Polars | 成组表达式、整批列计算与现有截面/存储衔接 | 已有concat只有0.04秒；时序入口仍是pandas，迁移需改适配层。保留分组顺序、NaN/null、ddof、rank、窗口min_periods及类型；不以框架名推断收益。 |
| C/C++/Cython或Rust | 排名/专用滚动热点在前两批后仍明显时 | 目前alpha已有C扩展经验，可复用测试方法；新增构建、Ubuntu wheel/.so、ABI与两端部署成本。仅投入被profile证明值得的内核。 |
| Arrow mmap/shared memory | 大输入跨进程传输 | Arrow提供共享读取基础，但pandas对象列/转换仍可能分配；先看pickle/IPC占比。需要更改输入协议时不是最小改动。[Arrow内存说明](https://arrow.apache.org/docs/python/memory.html) |
| GPU/cuDF | 大批量长期驻留显存的另行研究 | 当前每币4050行、分支/排名/状态机混合，传输和适配成本未测；不列Ubuntu首次上线依赖。 |

Pandas官方同样建议先减少Python层重复工作，再评估Numba/Cython；这不是保证任何单一改写都更快。[pandas 2.2性能指南](https://pandas.pydata.org/pandas-docs/version/2.2/user_guide/enhancingperf.html)

## Ubuntu与正确性验收

Python 3.11 Linux进程默认fork，Polars建议spawn。当前core进程池没有显式指定；建议最小候选是仅在该池传入`multiprocessing.get_context('spawn')`，不要全局force启动方式。要验证MODEL环境配置在Linux spawn子进程同样生效，并覆盖传统策略入口。尚未修改/在Ubuntu执行。[Python 3.11](https://docs.python.org/3.11/library/multiprocessing.html) · [Polars multiprocessing](https://docs.pola.rs/user-guide/misc/multiprocessing/)

后续每批必须具备以下与改动相称的证据：

- 原公式独立oracle：全历史、4050、短历史、空/单行、NaN/Inf/零价格/零成交、并列值、异常点、参数顺序和输入不变；涉及两个入口的因子分别验证真正生产调用路径。
- 检查所有原时间行的值/NaN掩码，而非只末行；排名/过滤边界与选币集合单独核对。浮点内核若不能逐位相同，需说明误差来源和阈值影响，不能直接扩大通用容差。
- 涉及预计算时，验证原列名/参数、完整PKL初始化、C→Arrow复用与缺列行为。MODEL strict模式缺列应触发完整初始化，不把短窗重算当成功回退；传统auto路径保留原协议。
- 涉及状态/输入范围时，验证S3共同截止点、近期修订回放、跨日/季度/断档及恢复点。保持incremental_lookback_hours=0，不引入Tail-Only。
- 性能同固定输入比较冷/热、单/多进程、耗时及峰值RSS；无生产API/通知。真正整轮P50/P95需上线后持续样本，目前16帧不能替代。

## 人工审核最小core候选清单（未实施）

| 文件 | 可能修改 | 是否功能取消 |
|---|---|---|
| core/select_coin.py | 因子池显式spawn；随后若实测有收益再做单轮initializer/有界分组 | 否，保留原计算范围、结果顺序、Arrow/Polars与异常传播 |
| core/data_bridge.py | coin-cap向量化；BTC源表单轮复用 | 否，保留数据来源、发布时间/对齐规则和缺数据行为 |
| core/factor.py、core/utils/factor_hub.py | 当前无需修改；shift=0复制/缓存前locale只是后续低优先级候选 | 不应为速度取消legacy隔离或缺列补算 |
| startup.py | 本分析无需修改 | 无 |

若后续方案确实取消某个定制功能，先对照上版/core2.3.1恢复相应局部代码；以上候选不是取消功能，不应借此整体回退core丢失Arrow/Polars/续算定制。alpha保留只读边界，新增生产者预计算属于另行授权范围。
