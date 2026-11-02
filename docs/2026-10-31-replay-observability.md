# 回放日志与后续优化优先级

2026-10-31：代码已增加，离线验证；未重启服务、初始化生产账户、下单或修改生产状态。延续“匹配修订后的完整回测”口径，不增加忽略阈值，不改变恢复/训练判定。

## 日志位置和事件

使用现有 `core.utils.log_kit.logger`，固定前缀 `MODEL_REPLAY ` 后接单行 JSON。正常本地 worker 的日志通常在 `logs/server_robust40.log`（以及每日轮转文件）；实际文件名取决于进程首次初始化框架 logger 时的入口。PM2 或 `X3S_USE_FILE_LOGGING=0` 时使用已有 stdout 日志，不另建指标文件。后台初始化 stdout 还在 `data/server_initialization/<account>/<stamp>.log`。应保留覆盖完整观察时段的日志。

每次 worker 有 `start` / `end`，同一账户/轮次重试以 `attempt` 区分；账户和 publication 的 UTC epoch 为 `account` / `stamp`。kind 区分 trade、initialize、compute，初始化父进程与 compute 子进程分别记录，不相加成两次初始化。

- 正常无修订：`mode=incremental`。
- 检测到真实修订：`revision_detected`，记录最早 `revised_from`；数值变更统计 changed_cells、changed_series、changed_fields，新增/消失历史序列统计 added_series/removed_series。这些是被比较输入表的计数，raw/Pivot可能重复表示同一价格，不能当成独立交易或唯一修订币数。
- 恢复成功：`restored`，记录 `restored_at` 和 `replay_hours`。小时数从恢复点到本轮截止，包含新到K线，不等于额外浪费小时数。
- 无法恢复：最终 `mode=rebuild_required`，failed_phase、error_type、recovery_reason；后续按原流程初始化，不表示初始化已经成功。
- `calculation_committed`：目标校验和计算状态保存已完成，发生在下单前。后续交易报错，end 可以是 status=error 且 committed=true，不能算作回放计算失败或推断订单成功。
- 提前返回：account_skipped=true。进程被强制终止可能只有start或commit没有end，汇总单列unfinished，不默认为成功。
- `quarterly_scoring`：scoring_calls 是季度评分函数调用数，trained_quarters/reused_quarters 是实际训练/复用数量；quarter_actions 标记每季 reused、uncached、training_changed（包括特征或流水线配置变化）。同日不调用时两项为0，不代表模型不存在。完整初始化训练发生在compute日志，父initialize只计全程耗时。

## 耗时口径（秒，perf_counter）

| 字段 | 范围 |
|---|---|
| old_read_s | 保存的168小时输入文件反序列化 |
| input_read_s | 当前Arrow/Pivot读取和有界输入整理 |
| compare_s | 差异检查及发生差异时的字段计数 |
| copy_s / restore_s | 仅修订时完整包复制 / 共同状态恢复及校验 |
| prepare_s | 上述预检整体，含裁剪及恢复元数据保存 |
| input_write_s | 本轮pending输入序列化及写入 |
| account_calculation_s | account main开始至finish_live提交，包括原账户准备、BNB和计算，不是纯模拟内核 |
| calculation_s | worker trade日志开始至计算提交，包含预检、复制、恢复；不含后续正常目标下单 |
| account_flow_s | 完整account main，包括后续交易/统计等原流程 |
| fit_s / scoring_s | 实际fit累计 / 完整季度评分函数（含比较、预测；冷启动可含导入） |
| total_s | worker函数全程，含事务退出、后续交易；不含调度等待及启动前导入 |

这些范围有嵌套，不能全部相加。缺字段表示没有执行/没有完成到该处，不能自动填0。错误的failed_phase保留最内层。错误全文留原traceback；结构化末行不重复可能包含签名的交易所异常。

不额外读行情或snapshot，不额外复制数据；计时常数开销，只有检测到差异时才统计已有布尔矩阵。每个阶段不逐币打印，正常trade通常start、commit、end三行，跨日多一条季度评分。日志写入异常不改变计算结果；日志缺失不能用作交易/计算成功凭证。

## 明天的统计

提供只读工具，支持通配符、轮转日志及多个来源，按attempt去重（同一记录同时出现在文件与stdout不重复统计）：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' -X utf8 -B tools/summarize_replay_logs.py 'logs/*.log*' 'data/server_initialization/*/*.log'
```

如使用PM2，将实际stdout日志路径作为额外参数。可加 `--since-stamp <UTC秒数>` 按行情发布轮次筛选。工具输出各账户/kind/mode的尝试数、不同发布轮次数、修订数、提交数、错误数、跳过数、训练/复用数，以及各耗时样本量、中位数、P95、最大值；另列unfinished和损坏行数。不读取生产状态、不联网。

统计先看trade各组：修订尝试占已完成检测尝试的比例，同时按account+stamp合并重试观察实际受影响轮次。初始化、缺数据、未完成检测和未结束进程单列，不混入“无差异轮次”。1天每offset最多约24个正常样本，P95可能接近最大值，只能作为初步判断。初始化未接入此前日志的旧进程不在统计覆盖内；新启动worker才会加载本次代码。

## 后续值得推进的事项

1. **优先：正常轮次输入检查。** 旧测量读取约4.95秒、比较0.67秒，每轮都有成本。先用本次日志确认P95和实际占比，再评估同一publication内复用已有Arrow/Pivot读取或减少重复转换，保持数值和覆盖范围不变。不要先引入跨轮共享缓存、哈希或缩小检测范围。
2. **按频率决定：复制/恢复成本。** 若修订频繁且copy_s占比高，优先缩小事务复制范围，但必须保留失败恢复和提交之后交易失败的语义；不能对后续原地写的状态直接硬链接。
3. **有条件推进：仅重算受影响模块。** 先看changed_fields分布。如果多数只有Pivot close变化，研究复用不受影响的选币/S3输出；收益要与依赖识别和跨模块一致性的复杂度比较。不能把raw close变化等同于所有因子不受影响，也不能跳过S3子账户收益重算。
4. **暂缓：完整初始化复用旧季度模型。** 已测4季度训练117ms、复用95ms，收益很小；跨初始化保留并验证旧模型需要额外状态管理。目前不值得优先实施。若未来fit_s明显增长再考虑。
5. **按失败原因决定：恢复点保留长度。** 若主要因修订早于25点而重建，可评估增加小时点；先同时核对4050因子预热/补算能力与币种布局，增加点数不能解决输入覆盖不足。

本轮仅tools运行文件、专项测试与文档；core、startup.py、sections、账户和alpha无新增修改。此前未提交修改保留。

## 验证

新增日志测试先4项失败，随后实现。首批日志/完成流程/共同恢复数学专项17通过；最终日志、完成流程、服务器可靠性18通过（32.10秒，批次有重叠），覆盖正常/修订分支、阶段失败、提交后异常、训练复用计数、重复日志去重、缺失结束事件及日志输出失败不影响计算。CLI --help和git diff --check通过。本轮未重复大规模行情对拍，因为未改变计算、比较、训练或恢复规则；未测实盘频率或独立日志开销。新worker加载后才有实际观测数据。
