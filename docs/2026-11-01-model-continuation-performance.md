# MODEL续算性能优化与人工复核

2026-11-01。仅实施已确认的三项优化；不调整77秒时序因子路径。alpha Arrow只预计算并传递三个长周期因子，其他短周期因子仍由alphaonline计算，这两者不冲突。

## 实际改动

| 文件/入口 | 修改 | 保留的行为 |
|---|---|---|
| positions/ModelRobust40Strategy.py：start_simulation/simulate | 内核启用Numba磁盘缓存；入口传数组、标量和原恢复tuple，在native内构造原RebRobust40，不将jitclass实例类型放进跨进程签名 | 原模拟循环、手续费、资金费、换仓死区、总敞口及恢复状态公式未改；非fastmath |
| 同文件：prepare/_prepare/update_state/_update_state | 正常轮次第一次读取state时获取原state锁；准备、校验、合并、续算和原子保存期间保持锁，共用该bundle；离线update_state仍独立加锁读一次 | 账户/offset/版本/参数/重叠一致性检查保留；异常释放锁；初始化仍由initialize_state加锁且拒绝覆盖 |
| 同文件：advance/load_model_raw | raw采用延迟加载函数，普通小时及重复轮次不读；跨offset日、停机补算跨日、修订恢复后跨日才读；初始化直接读完整PKL | 每个offset自身日边界，原observations/quarterly_predictions/S5减仓逻辑；原始行情仍是本轮publication |
| tools/robust40/handoff.py：read_arrow_tail | 增加可选列投影和尾部行数，模型读取close、quote_volume、taker_buy_quote_asset_volume、funding_fee、first_candle_time、avg_price_1m及时间列；核对symbol元数据数量 | 默认修订读取仍按原时间窗口和列集；模型保留截止时刻之前最后4050行，不擅自改成4050小时窗口；无新行情副本或缓存协议 |

完整原始行情的首次/末次时间、重复币名及必要age字段校验仍在模型加载路径。日内即使不再准备未使用的原始行情，交易前handoff的168小时修订检测、框架Arrow读取、MODEL重叠校验仍执行。没有用“无修订”代替子策略/S3一致性检查。原有0/5/…/55分钟网格不变。

core/startup/全局config/accounts/因子/截面/alpha本轮无新增修改，无框架功能取消。不改变job_num，不开启incremental_lookback_hours，不采用Tail-Only，不并行启动多个账户状态写入者。

## 缓存与上线边界

Numba使用其原生缓存机制，无额外hash、版本注册表或门禁。正常默认缓存位于positions/__pycache__（若环境设置NUMBA_CACHE_DIR则用该目录）；所有内核依赖仍在同一策略文件，源文件修改会失效缓存。缓存不存在或不匹配时正常编译并保存，第一次仍可能较慢；新进程随后加载，不需要常驻进程或传递账户内存。服务器需要正常缓存写权限。不要将一次冷启动误认为优化失败，也不要在运行中清空缓存。

原snapshot结构和策略配置不变，无需重新初始化或删除旧数据。已运行的worker不热更新；上线遵循现有整体代码切换流程。本次没有启动/重启生产worker、下单、通知或改写生产snapshot。

缓存依据：[Numba官方缓存说明](https://numba.readthedocs.io/en/stable/developer/caching.html)。不能仅依据cache=True推断成功；这里已用两个全新Windows进程验证None恢复状态和tuple恢复状态两种签名，第二进程2次命中、0次miss。CPU/Numba/源代码变化后的冷编译是正常行为。

## 验证结果

- 三项新增合同先15项失败；实现后相关批次86项通过，含全5分钟offset原特征一致、初始化完整历史、普通小时不读取raw、跨日/季度/两日停机补算、重复轮次、回放共同恢复、季度模型复用、状态锁及失败保持。4条既有pandas FutureWarning未修改。
- 修改前策略保存在research/model_continuation_20261101/before.py，仅用作离线oracle；新旧模拟循环和原计算类逐AST比较一致，仅在native入口内部构造原calculator。
- 真实40m快照12992小时（2025-05-09 00:40至2026-11-01 07:40 UTC）：死区0.2和0.4两种完整模拟与修改前实现逐值一致，包括资金曲线全部列、最终状态和最近25个恢复点。不是重新生成golden替代核对。
- 真实40m原始Arrow的756个币种：新投影结果与原全读/转换/裁剪路径的模型输入列逐值相同，含avg_price_1m标签价格和first_candle_time。
- core/startup相对于本轮开始的diff未变化；没有复测未修改的C因子公式或真实交易接口。

## 性能证据与限制

| 项目 | 观测 | 解释 |
|---|---|---|
| 修改前生产专家/MODEL续算阶段 | 最近12次28.81–31.94秒，中位30.33秒 | 含每轮新进程编译；不是季度重训耗时 |
| 修改前离线profiler | 首次42.30秒，编译39.08秒；同进程再次3.21秒 | profiler有开销，不与生产绝对耗时直接相减 |
| 优化后全新进程、真实40m一小时advance | **1.778秒，cache hit=1、miss=0** | 无profiler；目标/账户状态与已有快照一致；不包含state读盘、Hybrid等外围步骤 |
| 跨日原始行情准备 | 原9.368秒，新6.271秒 | 单轮顺序对照、非稳定P95；全部输入列一致 |
| 普通小时raw | **不执行读取/转换** | 由调用次数合同验证；保留交易前修订检查 |
| 重复状态读取 | 由两次变一次 | 首次读取仍需执行；减少第二份大bundle的内存占用，未宣称固定节省值 |

不能把以上不同口径简单相加作为整轮提速承诺。框架其余因子仍需计算；实际下单前耗时、网络抖动和长期P95要看新worker日志。按需读取单独记录“按需原始行情读取完成，耗时…”，避免把之前的模拟耗时算作raw I/O。

证据及可重跑脚本：research/model_continuation_20261101/verify.py，oracle_result.json、cached_result.json、raw_result.json。脚本只读取生产输入，写入本research目录的结果与Numba缓存；不会运行startup/交易/生产初始化。

## 人工复核要点

1. 缓存签名仅新增死区和敞口两个标量，数值仍由原simulate调用点传入；未修改投资规则或另写一套模拟公式。
2. 正常prepare与离线update_state进入同一个_update_state，前者持有首次读盘时的原锁，不能直接在无锁业务路径调用私有方法。
3. lazy raw的触发依据是恢复后的previous.observation_day与当前offset日；跨日回放不能使用旧观察数据跳过刷新。
4. 默认修订检测读取行为不变，模型专用投影保留全部特征和标签所需列，初始化仍保留完整历史。
5. 保留既有不完整计算重建规则；未借性能优化将失败状态伪装成已完成。
