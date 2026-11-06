# alpha 1.1.12 遗留与 FACTOR_BLOCKS 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 用户已批准，原生就地实施。下面保留原验收清单；实际状态以执行记录为准，不能将尚未运行的自然/历史账户验收勾为通过。

## 2026-11-06 执行状态

详见[实施、测量及人工复核](../../2026-11-06-alpha1112-factor-block-implementation.md)。未部署、重启、提交两仓已有脏依赖或改生产数据。

| 任务 | 当前证据 | 未完成的验收 |
| --- | --- | --- |
| 0 基线 | 两仓分支/HEAD、相关函数源码基线、已有功能来源已核对 | 无独立可运行提交基线；保留未提交依赖，暂不提交碎片 |
| 1 映射 | 同名/唯一映射、保留历史更新及单边边界实现，oracle通过 | 真实账户全历史选币/仓位影响；单独发布 |
| 2 新币/空行情 | 首根、空分区/空年份、真实空文件新币已实现并测 | 自然上市事件观察 |
| 3 价量传播 | 中断重试、完整历史C长因子/Arrow/Pivot对fresh精确对拍 | 候选自然发布后S3/MODEL联动观察；有额外发布成本 |
| 4 成熟币首因 | 两端原始5m重采样等于持久化源，发布Arrow未跟进 | 初次HTTP响应时间/内容未留存，不能追溯证明早期采集原因 |
| 5 来源覆盖 | 固定来源、可控Future顺序/异常及新币覆盖通过 | 线上双源等待成本观察 |
| 6 年度完成 | 十二offset、2h、缺失、无变化、修订/失败恢复通过 | 自然跨年停机观察 |
| 7 市值类型 | 两CSV入口及已存在代理功能合入，专项通过 | 生产部署未授权 |
| 8/9 因子诊断/优化 | 热点锁定市值倍率；1258币精确对拍通过；固定顺序三组完整中位102.678→107.477秒，未达门槛，运行候选撤回 | 没有可发布的因子提速补丁；两机20.8045秒差距尚未完整归因 |
| 10 联合验证 | 上游335，下游相关421与57通过；独立审查修复；磁盘复核 | 真实成交/季度/传统45m自然新样本、同源账户发布验收不由单测替代 |

裁定：不增加常规逐币计时，详细性能只在隔离工具；不用新manifest/hash；测试临时数据释放，保留小报告。后半程既有优化只回归，不重复开发或计收益。生产原始小样本导出遭自动审批拒绝后改为服务端聚合比较，没有绕过或扩大采集。

**Goal:** 修复行情发布正确性缺口，完成 1.1.12 遗留移植，并在全成本、严格等价证据下优化因子块。

**Architecture:** alpha 负责同轮可信行情及 batch/Arrow/Pivot一致发布；alphaonline保持计算语义，局部优化共享因子入口。两仓独立变更、联合验收，初始化和续算复用计算实现，状态与交易边界保持。

**Tech Stack:** Python 3.11 / alpha311、pandas、NumPy、PyArrow、Polars、既有C/Numba；不新增数据库或运行服务。

**Spec:** [设计与边界](../specs/2026-11-06-alpha1112-factor-block-design.md)

## Global Constraints

- 保留已有未提交代码；不整体覆盖 core、不自动部署/重启/清生产数据。
- 资金费和历史起点视为已解决，仅回归；市值代理与至少5000的Arrow需求协议必须保留。
- 映射修正完整保留历史，在离线副本验收后单独发布；不修改历史订单。
- 性能修改严格相同输入、相同状态、相同输出；所有新落盘点纳入空间和释放审核。
- Python命令统一 `& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' -m pytest ... -q`，在对应仓库运行；服务器使用对应alpha311。
- 每项执行失败测试→最小实现→同组通过→审查diff→单项提交；提交不得夹带其他未提交工作。依赖尚未入库时先明确基线，不提交不可运行的碎片。

## Review Focus

- 已写batch但未写Pivot/Arrow就异常：Task 3验证重试仍修正旧时间戳。
- 新币无行情与成熟币获取失败：Task 2/5分别验证，不将失败伪装成可忽略空币。
- 历史映射变为空匹配：Task 1验证清除旧字段及双方历史有效区间。
- 完成年份仍收到价量/结算修订：Task 6验证穿透冻结且无变化不写旧年。
- 因子有状态/删行、worker乱序/失败：Task 8/9验证原行序、参数顺序、背压和清理。

## 顺序与交付

### Task 0：统一候选基线与证据入口

**Files:** 两仓 `docs/DEVELOPMENT.md`；现有缺口报告及本设计；真实部署文件只读核对。
**Interfaces:** 产出当前功能矩阵、补丁来源和待测列表，供所有后续任务引用。

- [ ] 保存两仓当前分支、差异范围；逐函数确认已部署资金费/起点、独立市值代理、Arrow联动和下游优化的候选来源。
- [ ] 将已有8轮因子证据整理到repo文档，保留测量边界，不重复抓取全行情；只登记所需冻结输入、外部市值/BTC输入及起始状态位置。
- [ ] 确认alpha写入权限与现有Git状态；隔离测试禁止网络/交易，产物在test内，无生产清理。

### Task 1：现货／合约映射及完整历史影响验证

**Files:** `D:/alpha/core/symbol_match.py`、`core/preprocess.py`；新增 `D:/alpha/tests/test_symbol_match_1112.py`。
**Interfaces:** 保持 `get_spot_swap_matches(spot_symbol_list, swap_symbol_list) -> DataFrame[spot,swap]`；历史修复使用 `calc_match` 的有效区间，沿用原字段名。

- [ ] 写独立期望测试：1000CATUSDT精确同名；唯一缩放；多对一/一对多均未匹配；输入乱序/重复/空集合；对侧无行情；旧映射变空后不能残留。
- [ ] 运行 `tests/test_symbol_match_1112.py`，确认旧代码在歧义样本失败；移植精确优先规则并修正全量/增量历史字段应用。
- [ ] 运行该组及 `tests/test_module10_preprocess_pipeline.py`；离线比较完整保留历史、映射时间区间、选择和目标合约。新旧差异逐项解释，未知历史规则显式标注。
- [ ] 输出独立历史修正/发布清单，不写生产；通过后提交该项。

### Task 2：新币首根闭合与无行情边界

**Files:** `D:/alpha/core/binance_update.py`、`core/preprocess.py`；新增 `tests/test_new_listing_1112.py`。
**Interfaces:** 保持 `fetch_update_resample(...)` 返回结构；`update_base_candles(...)`空分区安全；全空批次返回既有三元结构，不制造年份。

- [ ] 用手工5m OHLCV聚合期望写失败测试：取满窗口丢不完整首小时、新币短窗保留首小时、旧部分首根后续闭合覆盖、空返回、空分区、全空批次、有效与空币混合。
- [ ] 运行新增组验证失败；最小修改取满判断及空分区/空年份分支，不改变未闭合基础K线过滤。
- [ ] 运行新增组与 `tests/test_module1_update_resample_candle_incremental.py`、`tests/test_module3_gen_batch_data_incremental.py`；只证明采集/空数据行为，传播由Task 3完成。
- [ ] 审核修改和提交，保留自动补BNB、传统/模型计算行为不变。

### Task 3：重叠价量修正传递至发布产物

**Files:** `D:/alpha/core/preprocess.py`；必要时同文件私有小函数；新增 `tests/test_price_revision_publication.py`，沿用 `tests/test_funding_publication.py`。
**Interfaces:** `gen_batch_data_incremental`/`gen_pivot_incremental`保留调用者合同；仅必要时增加默认None的关键字上下文，按币/年携带当前轮待更新行，不建立长期修订库。

- [ ] 失败测试：相同时间戳新价量覆盖旧值；无新增时间戳仍更新；首根/末根/跨年；中途batch已写后重试；无变化不重写旧年；first_candle_time和资金费不退化。
- [ ] 修改追加逻辑为对可信重叠区间更新，补齐所有价量派生字段，长因子仍按完整源重算。Pivot按当前轮可信重叠行更新，不能仅依赖本次batch差异。
- [ ] 运行新增组、资金费/起点及增量Pivot测试；逐值对照独立全量重建的 raw batch、Arrow尾部、Pivot及长因子。
- [ ] 下游以同输入跑旧/新发布读取、S3及MODEL修订识别；有影响的输入修正允许触发既有回放，不放宽比较容差。统计正常轮额外开销后提交。

### Task 4：成熟币最后闭合价量的首处分歧

**Files:** 新建 `D:/alphaonline/test/alpha1112/closed_candle_probe.py`（诊断工具）；修复文件由实证指向，不预设sleep。
**Interfaces:** 输入市场、币种、offset、stamp；输出原始5m行及请求/返回时刻、重采样、发布对照。凭据/签名URL不落日志。

- [ ] 优先用已有差异币小样本；缺什么仅收什么，不采全账户/全行情。确认源响应是否已闭合、是否完整覆盖最后一小时。
- [ ] 将首处分歧转为离线失败用例：采集过早、重采样错误、源优先级或发布缓存，分别归因。
- [ ] 只修有证据的层；若Task 3已解决传播则不重复实现。不能修复时明确缺失证据，后续独立任务可继续但该项不标通过。
- [ ] 以同一稳定闭合基础输入验收下一轮发布修正、长因子及回放；保存小报告，释放临时数据。

### Task 5：DataAPI覆盖与确定的发布来源

**Files:** `D:/alpha/realtime_data.py:update_all`、`core/preprocess.py:read_candle_data`及其调用；必要时 `core/data_api_update.py`返回覆盖信息；新增 `tests/test_publication_source_coverage.py`。
**Interfaces:** 本轮内存来源选择供预处理读取，旧未传来源入口保持可用；不新增磁盘来源清单。Binance优先、DataAPI兜底，避免遍历时动态换源。

- [ ] 用可控制完成顺序的Future替身测试：DataAPI先成缺币/全覆盖，Binance先成，单源超时/失败，上市无K线，成熟币本轮缺失，预处理期间另源完成。
- [ ] 固定每币当轮来源及覆盖边界后一次派发；禁止缺币时因DataAPI总体成功早发ready。Binance覆盖完成不被DataAPI失败阻塞发布。
- [ ] 运行新增组、`test_dispatch_prewarm.py`、`test_realtime_recovery.py`；确认采集预热、补算、资金费更新和市值任务仍运行，ready后不重写同轮。
- [ ] 记录来源决定耗时、等待耗时和缺币数；检查不新增每币全历史重复读取。提交独立调度补丁。

### Task 6：上一年Pivot完成后跳过，修订可穿透

**Files:** `D:/alpha/core/preprocess.py`；新增 `tests/test_pivot_year_completion_1112.py`。
**Interfaces:** 同一组年份完成/跳过辅助函数供批次拆年和Pivot生成调用，复用 `.complete`；使用handle配置周期和offset。

- [ ] 测试12个offset、跨年停机、最后bar缺失、稀疏索引、旧文件无标记、标记有但文件无、完成后价量/资金费修订及落盘失败。
- [ ] 替换一月前七天启发式；完成标记在Pivot成功后写，修订年份可更新。无修订/无新数据不重写历史Pivot。
- [ ] 运行新增组、增量Pivot、资金费、价量修订组；对照全量重建数值及写文件调用次数；记录少读写的实际收益并提交。

### Task 7：市值七列统一数值类型

**Files:** `D:/alpha/core/quantclass_async.py`、`core/coin_cap.py`；可新增 `core/coin_cap_schema.py`共享七列dtype常量；新增 `tests/test_coin_cap_types_1112.py`。
**Interfaces:** CSV解析及原返回结构不变；先合入已验证市值代理接口，保留配置true/false行为。

- [ ] 独立CSV测试两个入口的七列float64、缺失、科学计数、非法字符串；失败不覆盖旧PKL且不删除CSV；代理/直连请求参数保持。
- [ ] 运行新增组确认失败，补共享dtype约束；不悄悄coerce非法文本或改变去重、日期处理。
- [ ] 运行新增组和已有代理专项（Task 0确定其路径）；核对市值因子值与合法输入旧版一致，提交。

### Task 8：FACTOR_BLOCKS同机定位与全成本基准

**Files:** 新建 `D:/alphaonline/test/factor_blocks/profile_pipeline.py`；默认先在隔离工具包装现有函数，必要聚合埋点才改 `core/select_coin.py`；新增 `tests/test_factor_profile_contract.py`。
**Interfaces:** 工具 `--input-root --account --run-time --output-root --workers --repeats`，输出小型阶段统计与差异摘要，禁网络。全量/短输入实验在同一原始发布派生，使用同一外部辅助数据。

- [ ] 固定父配置顺序、相同全市场输入、5000尾部及4050结果；新增测试验证计时开/关结果、行序和异常传播一致。
- [ ] 补自然轮低成本聚合计时；逐因子详细CPU计时仅在隔离测量开启，不每币写一条常规日志。
- [ ] 同机4worker先运行3组交替对照，拆workerCPU/墙钟和父阶段，计入创建、传输、写入和释放；噪声大再增加样本，不以3次声称P95。
- [ ] 分离Arrow长度、机器差异、外部因子输入、数值/转换、队首等待和写盘贡献；没有精确证据不把20.8045秒强行分摊。
- [ ] 输出热点排序及下一任务候选；只有热点证据支持才比较3/4worker、pending或32币分片，不改生产并发。

### Task 9：按热点依次实施因子块优化

**Files:** `core/select_coin.py`、`core/utils/factor_data.py`；只有证据指向时改 `core/factor.py`或单个factor；测试 `tests/test_shared_factor_stage.py`、`test_factor_data_ownership.py`及候选专用测试。
**Interfaces:** 保持 `process_descriptor`返回 `(index, pl.DataFrame)`、FactorBlocks列/行合同及`ordered_results`有界顺序。具体候选在Task 8报告锁定后再补精确实现接口，不能拿本任务当任意重构授权。

- [ ] 首选低风险候选逐项试：worker只读reader/小型辅助数据复用、配置一次传递；若无收益停止该候选。
- [ ] 对热点转换/中间数组/有界调度分别先写失败oracle，再实现；保留BiasV1删行、旧索引、可变资金费及参数顺序。
- [ ] 跑相关专项并按Task 8完整计时；每个候选单独开关/提交以便归因，最终不保留无用实验开关。
- [ ] 全链因子→截面/S3→选择→模拟→MODEL→固定执行输入目标手数严格一致；仅接受重复可见的全成本改善，内存/swap/磁盘代价明确，不靠取消功能提速。

### Task 10：联合验收及发布准备

**Files:** 两仓开发说明、既有部署验证入口与本次实施报告；不新建服务。
**Interfaces:** 产出逐项已实现/已测/未测矩阵、core/startup改动表、发布顺序和回退边界。

- [ ] 四场景：完整初始化、4050续算、修订回放、正式交易计算；另覆盖缺口/跨日/季度、传统45m和分钟网格。复用已有用例，不重新创建无关全仓测试。
- [ ] 补已有优化验收：新进程JIT、再择时复用、全→短→重建全量联动、超恢复范围交接；CSV旁路仍关闭。MODEL/Hybrid数值内核不重写。
- [ ] 对设计中的后半程遗留逐项给出去留：更广模拟行情复用、修订读取、MODEL状态转换、传统聚合/初始化MODEL/季度与统计争用。复用已有日志，缺什么补什么；记录值得实施的最小下一任务及依据，不默默从范围中删去。
- [ ] 汇总执行分段日志；dry_run不能证明真实成交，真实网络行为仅观察已授权自然运行。
- [ ] 专项检查每个新增输出、失败残留、reader句柄、硬链接持有、test可删除性；记录清理耗时及预算，禁止新建长期行情副本。
- [ ] 提交人工复核：先行情修正各独立批次，再固定正确输入下的因子优化；测试服务器/正式服务器的部署、重启、历史修正执行另获授权。

## 计划完成的定义

所有任务均有可复现结果才标完成；诊断不能冒充优化落地，代码通过不能冒充生产验收。设计批准后原生顺序执行，不在每项结束重复询问是否继续；需要改变策略/历史语义、权限或部署范围时才暂停确认。
