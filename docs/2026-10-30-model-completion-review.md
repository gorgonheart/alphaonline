# MODEL 慢轮次完成与保存

2026-10-30。用户明确要求取消四分钟计算截止，允许算完并正常保存。本条覆盖历史初始化方案、运行可靠性文档中“240秒执行窗口”的约定。

## 22:40 实跑定位

主日志显示四个同轮ready于22:41:40齐全；账户准备后，子策略流程22:42:08开始，22:43:55.777结束，累计107.599秒。随后整理净值和prepare_live仓管计算，到22:44:45.640进入仓位比例，间隔49.863秒；聚合结束后finish_live因超过22:44:00抛异常。

本次异常之前complete已经保存。只读核查40m snapshot：complete=True，共同截止2026-10-30 13:40 UTC，S3起点2025-04-25 00:40 UTC，当前配置inspect返回ready，无需因此重新初始化。

对该snapshot离线剖析：状态读取1.372秒，Hybrid基础权重5.499秒，条件beta1.184秒，完整最终账户模拟首次36.357秒、同进程再次1.053秒；剖析总计Numba编译累计40.346秒（包含Hybrid和模拟）。这说明新进程冷编译是主要来源，并非重新训练所有季度模型或程序sleep。测量包含cProfile开销，不应直接把各项当作线上精确分段；新增阶段日志供后续实跑定位。

同输入完整最终账户重放与已保存账户全部数值通过rtol=1e-10、atol=1e-8比较，生产state文件大小/mtime前后不变。脚本及日志为research/profile_40m_model_latency.py和同名.log；未调用交易入口、交易所或修改生产状态。

## 实际改动与人工复核

本轮core和startup.py新增改动均为零，未修改alpha、账户参数、因子公式、BNB设置或模型数学。

| 文件 | 本轮修改 |
| --- | --- |
| tools/robust40/server.py | 同轮四个ready已齐时允许超过四分钟继续分发；finish_live只完成既有状态提交，不因耗时抛异常。 |
| tools/server_robust40.py | 移除trade启动时间截止和不再使用的execution_stamp注入；仍校验profile、账户、配置、历史覆盖和snapshot。 |
| tools/robust40/initialization_config.py | 将EXECUTION_WINDOW_SECONDS改名为DATA_READY_WAIT_SECONDS=240，仅供等待缺失输入；不是计算或下单时限。初始化job_num=4保持用户当前值。 |
| tools/robust40/live.py、runtime.py | 增加仓管输入、历史合并、Hybrid、MODEL续算和状态保存的阶段耗时日志；默认回调为空，离线调用兼容。 |

计算超过offset+4分钟时，继续同一轮计算、目标一致性校验、保存并进入账户正常后续流程；不会自动切换至另一轮输入。正常后续流程是否真实下单仍由原有运行模式控制。计算及保存真正失败仍抛异常，不把坏状态标为完整。

缺失输入时的等待仍有上限：共享core.utils.datatools.check_flags及MODEL四个ready等待保持offset+240秒。数据已齐不受该等待截止影响；此限制没有作用于已经开始的计算。后台完整初始化仍保存成功后由下一合法offset续算，不在初始化进程补发订单。

首次模拟的编译开销本轮没有优化或隐藏。取消时限解决的是完成流程被时间检查打断，不代表计算本身加速；未引入Tail-Only、常驻进程或新的并行机制。

## 验证及生效

先加入回归复现ready分发及worker启动的四分钟拦截，修正测试夹具后，最终50项专项通过、4项既有pandas FutureWarning。覆盖开始前/开始后超过240秒仍保存新截止并进入后续调用、保存失败不被吞掉、缺数据等待、账户未就绪、旧策略分流、snapshot检查及MODEL运行时。日志research/server_completion_final.log；git diff --check通过。

下一次新建worker读取磁盘上的改动，不再在finish_live抛出本次错误；已运行startup中的Coordinator需按正常操作重启才能加载新的分发行为。未代用户重启、下单或删除数据，实际下一轮完成及交易结果仍待运行验证。
