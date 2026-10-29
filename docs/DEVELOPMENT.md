# alpha / alphaonline 统一开发说明

## 2026-10-29 MODEL_ROBUST40 v1 独立偏移版本

用户明确选择移植 MODEL_ROBUST40、各分钟独立计算和重建基准。新实现位于 `codex/live-offset-parity`，main 仍保留 e6fc3bd 旧基线。详见 [MODEL_ROBUST40 接口、结果与上线条件](MODEL_ROBUST40.md)。20m/40m/55m 已各自重建父权重、季度模型及 S5 delta，并以三目录正式回测入口复用原 14 子策略缓存验证。8,013 根组合曲线最大差 6.55e-11；最近 2,000 根通过实际实盘聚合接口的目标差为 0，末根目标手数完全一致；三偏移 240h 断点续算与全量一致。

策略隔离收敛：相对 main 仅 4 个 core 文件、+27/−4 行，保留显式开启的 prepare_live/transform_targets、执行器完整权益声明和配置实例级 required_history_start。core 无 MODEL 导入，共享 account_config/strategy_config/rebalance 恢复主干。MODEL 调仓执行器由新策略加载时附加注册；不替换旧模式。共享 TopKTo2K、VolumeMaxBias 恢复旧行为，研究边界使用独立 Robust40 因子名。账户 simulator_config/历史配置覆盖每次重载先恢复默认。详见 [策略隔离及五个账户兼容验证](2026-10-29-strategy-isolation.md)。

Tail-Only 未实现。S3 固定初始化日期使 5000 根滚动窗口不足，新账户示例明确最低历史；历史/配置/时钟不匹配会停止。状态在账户独立持久目录，保留待执行订单；示例位于 examples，未写入活动 accounts。初版全仓 305 passed / 4 skipped；重构验证结果以隔离报告为准。没有部署、生产写入或下单。尚需生产同源原始行情→14 子策略重算对账、20/40m 供数及真实执行条件验收；不能将同输入目标一致表述为实际成交一致。完整历史读取、约 344MB/偏移状态及日切模型刷新增加资源成本，不采用此前毫秒级收益估计。

## 2026-10-29 本地基线整理与偏移一致性审查

用户最终指定以本地 **2026-10-22 之前的版本**整理主干。保留 10 月 14 日 2.3.1 升级/因子修正、10 月 16 日 LOCAL 可选接口及 10 月 19 日统计图例；移除 10 月 29 日新增的 Robust4H 三个模块和 tools 包入口。完整草稿保存在 `codex/robust4h-draft-20261029`（5098bb7），不作为主干已实现功能。没有覆盖账户配置、模型或生产数据，也没有部署/重启。

详见 [基线与未提交内容审查](2026-10-29-live-baseline-audit.md) 和 [实盘/回测差异证据](2026-10-29-live-backtest-differences.md)。`alphatrade1` 无 Git，关键运行源码与本地 2.3.1 一致，使用原 Hybrid，没有 Robust4H 模块/模型。三偏移 MODEL_ROBUST40 不是这套线上策略的等价回测，其父权重实际上复用 0m 研究值。此前整合树离线 tests 为 280 passed / 4 skipped（缺真实 30m Arrow 快照）/ 95 warnings，38.12 秒；此结果不代表 Robust4H 或多偏移模型已通过。后续偏移修复在独立分支推进，排除 Tail-Only。

## 2026-10-19 独立 BNSOL / SOL 平仓工具

用户指定的另一项目 `D:/project-seed_0.3.0 (1)` 新增 `core/bnsol_unwind.py`，根目录 `种子计划.py` 改为只减仓入口，旧入口完整备份为 `种子计划_原版备份.py`。具体接口、运行和异常恢复见该项目 `BNSOL平仓说明.md`。不修改 alpha/alphaonline 的交易链路或账户配置。

统一账户卖 BNSOLUSDT 现货、买入平 SOLUSDT 永续空头：每批约 1 BNSOL、批次至少间隔 30 秒，<2 BNSOL 清可交易尾仓；结合 LOT_SIZE/MARKET_LOT_SIZE/最小金额避免拆出不可交易小额余量。按现有实盘双腿数量比例缩减，最后平完 SOL 空头，不将 BNSOL/SOL 当 1:1。默认只读预览，`--live` 才发送订单；不自动加仓、划转、补 BNB、借币或通知。

下单前持久化未完成批次状态，POST 超时不盲重试；部分成交以实际现货成交量平对应 SOL，异常停止待核对，未决批次阻止再次下单。精度以下碎币/既有不可交易余额明确报错，不虚报全清。alpha311 模拟交易所专项 21 项通过，目标入口 `--help` 离线通过。未连接真实账户、未下单或启动守护；BNSOLUSDT 统一账户准入、当前市场规则与权限仍需运行时只读预览确认。

同日预览故障修复：用户 alpha311fordata/ccxt 4.4.14 报 ExchangeNotAvailable；本机 alpha311/ccxt 4.5.53 只读复现为已停用的 `papi/v1/um/conditional/openOrders` 返回 404（币安文档标明 2026-04-28 停用）。删除旧查询，保留 `um/algo/openAlgoOrders` 检查，新增脱敏接口/HTTP/数字错误码提示，并区分未发单预览与存在未完成批次。专项现为 23 项通过。修复后真实账户只读预览 9 次 GET 全部完成、生成首批计划；诊断显式禁止非 GET，不创建 pending、不下单，未改依赖环境。不能再将当前准入/查询权限记为未验证，实盘订单权限与成交仍未验证。

维护日期：2026-10-15。此文件是两项目当前实现与跨项目约定的统一入口。历史设计文档保留背景，不能据其标题认定功能已落地。新对话先读两端 AGENTS.md、本文件、相关专题，再检查两个工作区的 git status/diff；未提交修改也是当前状态，不能用 HEAD 覆盖。

### 2026-10-15 线上/本地末根因子诊断

**后续升级后对账（21:15 轮）**：按用户改为 `15m-nheart0701`，双方 snapshot/因子缓存均固定 `2026-10-15 12:15 UTC`。完整结果见 [升级后 15m 对账](../research/updated_15m_20261015/REPORT.md)。14 个父策略中 11 个末根选币与内部聚合比例一致，S3/S12/S13 不同。此前未闭环的 21 个现货准入差异已定位到跨进程市场规则缓存：Linux fork 继承统一账户杠杆现货规则，Windows spawn 的 dummy client 重取普通现货规则；当轮日志、21 币公开杠杆资格及隔离复现吻合。显式传同一规则的隔离实验两端一致，但业务修复尚未实施。线上当前数据中心已改为 `/home/ubuntu/alpha/data`。双方新动量/ILLQ/CciV3 共 12 个预计算列在各自 Arrow→因子缓存均吻合；合约行情和费率的跨机差异已存在于生产者发布数据。S3、S12、S13 的独立末根选币重放通过。仅诊断，未修改/部署/重启业务。

只读比较线上旧版与本地新版的5m snapshot（19:05轮）及15m完整因子末截面（19:15轮），详见[诊断报告](../research/live_compare_20261015/REPORT.md)。实证：旧111改写首行quote_volume会污染新币当前回撤，再影响全币池z-score；本地输入隔离修正及bStocks过滤可解释大部分动态回撤差异。CciV3两端源码一致，5币×3参数独立Python复算吻合各自C产物；实际增量历史长度不同可改变最后一根值并导致LTC/XPL与DOGE选币切换。共享data/cache会被下一个账户覆盖，snapshot选币文件不含完整逐因子值。未闭环项：21个现货在消费端准入差异的当轮具体剔除原因、VolumeMaxBias全部历史状态事件、市值数据差异。没有修改业务或线上部署，不把本次抽样结论外推为全链无回归。

## 项目与环境

| 项目 | 职责 | 主要入口 |
|---|---|---|
| D:/alpha | Binance 行情采集、增量重采样、预处理、C 因子预计算、Arrow 发布 | realtime_data.py、core/preprocess.py |
| D:/alphaonline | 数据读取、因子与选币、回测净值、仓位管理、交易、统计 | startup.py、core/real_trading.py |

Python 统一使用 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`。当前交易框架为 2.3.1（20260911），带本地定制。xbx-py11=0.1.23、ccxt=4.5.53、Polars=1.36.1、pandas=2.2.3、NumPy=2.0.2、PyArrow=17.0.0；依赖环境共享，更新后检查两端受影响测试。

配置可能在 import 时加载账户或访问本地路径；不要为了读配置运行实盘入口。账户文件可能含密钥，不在日志、文档或测试输出中展开。文件修改和离线测试不代表已部署、已重启或已实盘验证。

## 主数据链路与不变约定

1. alpha 下载/增量更新原始 K 线，经分钟偏移重采样、enrich 后得到 `{symbol: DataFrame}`。
2. 持久化 batch PKL 保留原始行情和完整的生产者保留窗口，作为增量缓存及消费者回退源。`apply_precomputed_factors` 对完整输入计算，因子只加到返回的内存字典，不把因子写回这些 batch PKL。
3. Arrow IPC FILE 按 symbol 分 batch，schema metadata 中保存 symbol 列表。因子计算后才 tail 至 `publish_kline_num`（当前 5000）。原子替换快照，随后由管线发布 `{type}_dict_{epoch}.ready`。不能先发布 ready 再补因子。
4. alphaonline 通过 run_time 找到 Arrow，mmap 读取并对齐现货/合约映射；缺失或损坏则读 batch PKL。行情列、时间顺序、过滤结果必须等价；Arrow 可以多出明确声明的预计算列。
5. 预计算复用必须同时满足账户声明、参数匹配、列存在。模块名称就是语义版本的一部分，不能把旧动量列改名当新版。标记 `requires_full_history` 的新版动量缺列时，整份 Arrow 回退 PKL，在过滤未闭合 K 线后、裁切前补算；不得在短 Arrow 窗口初始化长历史 EMA/异常状态。
6. 时序因子 → 截面因子（优先 Polars，保留 pandas oracle）→ 选币（普通或 entry_exit）→ 多 offset/策略权重 → 子策略回测净值 → 仓位管理策略 → 目标持仓 → 订单 → 统计。因子修改必须核对这条链上的使用点，不能只检查文件可以 import。
7. 行情 pivot 是另一个独立产物：生产者按年生成 `market_pivot_{spot,swap}_*.pkl`，有自己的 ready。消费者 equity 使用两参数 `read_bmac_market_pivot(hour_offset, symbol_type)`，**不现场从 Arrow 重建 pivot**。不能停止生成 PKL pivot。

时间以 UTC K 线为准，消费端按 `utc_offset` 过滤未闭合数据。新版异常因子的输入时间必须唯一、递增、无 NaT。所谓“完整历史”是生产者当前保留窗口，不等于交易所无限历史；修改保留深度会改变 EMA 初值，必须独立验证。

## 已有本地功能登记

| 功能 | 主要位置 | 维护要求 |
|---|---|---|
| 增量模块 1–5：重采样、candle 更新、batch enrich、pivot、调度接入 | alpha/core/resample_candle.py、candle_manager.py、preprocess.py | 首轮与全量一致，后续与合并历史全算一致，异常按既有逻辑回退；不每轮删缓存 |
| spot/swap 历史深度拆分、偏移调度、handle 传配置 | alpha/config.py、core/handle.py | 不恢复一个共享历史长度；历史设计中的数值不是当前配置依据 |
| C 预计算与多 symbol/因子并行 | alpha/factors/c_ext、core/preprocess.py | C 核心无共享可变状态；Python oracle 独立；异常传播，不能吞错发布 |
| 一次 enrich、内存因子发布、减少 PKL 重写、IO 计时 | alpha/core/preprocess.py、core/arrow_publish.py | 保留 ready 顺序、原子发布、完整输入先计算后裁切 |
| Arrow mmap、PKL 回退、向量化映射对齐 | alphaonline/core/utils/arrow_reader.py、datatools.py | 保留原版 align 作为对账参照；共享 Arrow schema 可能使整型行情列变浮点，值仍须一致 |
| exginfo 本地快照及回退 | alphaonline/core/utils/datatools.py | 避免生产者重写造成 EOF；不改变生产数据 |
| 预计算配置贯通、缺列本地计算 | alphaonline/config.py、model/backtest_config.py、select_coin.py | 仅匹配声明的列可复用；新版动量另有全历史回退 |
| 时序/截面因子并行写盘、分块控制内存 | alphaonline/core/select_coin.py | 消费 executor.map 结果，让写入异常抛出；保留稳定列顺序 |
| 两个回撤截面因子的 signal_polars | alphaonline/sections/动态回撤阈值.py、固定回撤阈值触发币数占比开仓门槛.py | pandas/Polars 含 NaN、inf、阈值边界一致 |
| 回测子进程释放内存、ProcessPoolExecutor/LPT 调度 | alphaonline/core/backtest.py | 不因替换第三方 core 丢失 |
| entry_exit 持仓状态续算 | strategy_config.py、utils/ee_continuation.py | 最新重叠截面覆盖旧缓存；长持仓单位正确；回退窗口一致；offset Int16 |
| 干跑模式 | account_config.py、utils/functions.py、real_trading.py | dry_run 不撤单、不下单、不修改 BNB/账户配置、不归集资产、不调基础保证金、不记录虚假成交 |
| 本地运行归档禁用、页面杠杆语义与文件保留数量 | 原运行链相应函数 | 升级不要机械恢复上游默认行为 |
| 2.3.0/2.3.1 交易所与交易新功能 | exchange_hub、binance、okx、trade、account_config、statis_func | OKX 路由、bStocks、maker_ratio、BNB、拒单容错、成交额与策略归属均需保留 |

具体第三方合并决策见 [2.3.1 升级记录](superpowers/specs/2026-10-14-upgrade-core-2.3.1.md)。升级前实际代码是本地 2.2.1；2.3.0 的 Git 提交只有分析，不能省略其功能合并。默认 maker_ratio=0；建仓/清仓走 IOC。成交记录已接入统计；当前根目录没有启用 delist.py/monitor.py 独立服务。

## 2026-10-14 因子修正

| 旧名 | 新名 | 实际修改范围 |
|---|---|---|
| 111 | 111_输入隔离 | 交易端深拷贝隔离输入，数学保持旧版；不进入 C 预计算 |
| Cci按异常0401修正 | Cci异常隔离168_因果版 | 交易端因果计算、参数间隔离、按时间 168 小时屏蔽；不替换 CciV3 |
| 动量因子210401修正 | 动量210401_双项异常隔离V2 | 交易端 Python oracle；生产者独立 C 扩展、配置与 Arrow 列名同步 |

实际引用在 `alphaonline/accounts/40m_dad0604_2.2.1.py`；文件名保留，三类字符串引用已替换，策略参数/权重/凭据未改。账户与 `alpha/config.py` 的动量预计算键均更新。历史因子及账户备份不删除。ILLQStdBbi45208 和 CciV3 原 C 实现、参数保持不变。

后续参数调整（2026-10-14）：数据中心动量预计算参数改为 `[13608, 5421, 3509, 12600]`，ILLQ/CciV3 参数不变。账户策略及预计算声明由用户稍后手动同步，本次不修改。同步前账户请求未发布的旧参数会触发既有全历史 PKL 回退，可能变慢；C 核心接收运行时参数，无需为此重新编译。

新版动量默认 `(n, 4368, 167, 168)`，亦支持四元组。第一根输入不回填未来；检测窗口按行计数，仅接纳正常观察，拒绝值不进入统计；异常或非法价格同时重置趋势和价格位置两项；恢复同时满足预热根数和冷却时长。新 C 为 CFFI 编译的 **C**，不是 C++，无旧版 52560 截断及段长度依赖未来输出。diagnostics 提供各状态供测试，不改变交易输入。

111 修正版仅隔离输入，仍保留旧版首行替换数学，不应宣称其已完全因果化。用户交付的三个修正版是数值依据；C 优化不能擅自“改善”公式。

新 C 构建：在 alpha 根目录运行 `python factors/c_ext/momentum_v2_build.py`（alpha311）。Windows 需要 MSVC x64 与 Windows SDK；本机可先调用 vcvars64.bat 并使 SDK 的 rc.exe 在 PATH 中，设置 DISTUTILS_USE_SDK=1。沙箱链接器无法启动 rc.exe 时使用获准的本机构建。Linux 必须在目标环境重新编译 `.so`，Windows `.pyd` 不能复制到 Linux。旧的三个 build 脚本仍对应旧动量、ILLQ、CciV3，互不覆盖。

更新部署顺序：先在目标环境构建生产者新扩展，验证新列发布，再启动消费端新配置。过渡期旧快照缺列已有 PKL 全历史回退，代价是计算变慢。因子语义变更后不要继续使用旧策略快照；当前 clean_start=True，正式启动按原流程重建。此次没有删除生产快照、运行实盘或重启服务。

## TDD 与验证规范

先用独立 Python 依据写失败测试，确认旧行为或缺失接线确实失败，再修改代码；不以 C 输出生成新 golden 来“修复”测试。布尔事件、NaN 掩码、冷却边界、索引、输入不变和参数顺序应精确相同；C/Python 浮点允许明确的小容差，本次逐项使用 rtol=atol=2e-11。同一实现的 Arrow 往返、增量/全量和参数顺序对账要求 exact。

本次红阶段：旧 C 与新版在 2400 根异常样本上有 509 处 NaN 掩码差异、1956 处整体不一致；新增 C 模块未实现时失败；账户旧名及旧/缺失 Arrow 的全历史回退测试先失败后修复。没有改写历史 golden。

| 测试 | 覆盖 |
|---|---|
| alpha/tests/test_corrected_momentum.py | 独立 Python 对照各分量/状态，三组真实参数及四元组，短/空/常量、异常/非法价格、时间间隙、168h 边界、前缀因果、参数顺序、随机长序列、真实 BTC 和线程安全 |
| alpha/tests/test_module9_embedded_factors.py | 新旧动量均覆盖首轮、第二轮增量、禁用预计算；磁盘 PKL 仍无因子 |
| alpha 原 module6、CCI、ILLQ、module7、module8 测试 | 历史 C/golden 未回归，并行分配、Arrow 发布 |
| alphaonline/tests/test_corrected_factors.py | 三因子输入隔离、111 数学不变、CCI 因果/冷却、账户/生产者名称、旧/新/缺失快照回退 |
| alphaonline/tests/test_precompute_contract.py | 真 C → 因子嵌入 → Arrow → 交易端复用，另与独立新动量 Python 对照；缺列回退 |
| alphaonline/tests/test_main_chain_parity.py | pandas/Polars 因子 → 选币 → 仓位 → 订单 → 策略归属、过滤/成交额 |
| alphaonline 其他 tests | 真实 Arrow/PKL 行情、映射、entry_exit、交易升级回归 |

测试命令（终端切到对应项目；临时路径使用新的目录）：

```powershell
# alpha
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_corrected_momentum.py tests/test_module6_momentum_factor.py tests/test_module_cci.py tests/test_module_illq.py tests/test_module7_gen_factor_parallel.py tests/test_module8_arrow_publish.py tests/test_module9_embedded_factors.py -q -p no:cacheprovider --basetemp=D:/alpha/.tmp-factor-check
# alphaonline
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-factor-check
```

基线：2.3.1 升级最终为交易端 269 passed、生产者相关 24 passed。本次因子修正最终结果见下方维护记录；不要把阶段性数字当作最新验证。真实数据测试需要本地数据与 C 编译产物，省略/跳过必须说明。永远不以 tests 通过等同于真实交易所成交验证。

## 历史证据索引

- alpha/修改内容.md：增量模块 1–5 原始说明。
- alpha/docs/superpowers/specs/2026-06-09-split-kline-count-spot-swap-design.md：深度拆分。
- alpha/docs/superpowers/specs/2026-06-10-momentum-factor-c-preprocess-design.md：旧动量 C。
- alpha/docs/superpowers/specs/2026-06-13-illq-cci-c-extension-design.md：ILLQ/CciV3。
- alpha/docs/superpowers/specs/2026-06-13-gen-factor-data-parallel-design.md：并行。
- alpha/docs/superpowers/specs/2026-06-27-producer-io-and-arrow-publish-design.md：发布与 IO；结合后续 d5aefe4（batch 不落因子）、34a399c（内存发布）、2175e16（计时）阅读。
- alphaonline/docs/superpowers/specs/2026-06-27-consumer-mmap-arrow-read-design.md：消费端 Arrow；0943575 实现回退，63a4842 实现向量对齐。
- alphaonline/docs/superpowers/specs/2026-07-12-section-polars-signal-design.md：两个截面因子；7d7de9a、67a4f4c 为实现。
- alphaonline/docs/superpowers/specs/2026-07-13-remove-redundant-arrow-pivot-rebuild-design.md：删除冗余 pivot 构建。
- alphaonline/docs/superpowers/specs/2026-07-12-upgrade-core-2.2.1-design.md 及对应 plans：历史升级。
- alphaonline/docs/superpowers/specs/2026-08-12-upgrade-core-2.3.0-analysis.md、conflicts.md：历史分析，非落地证据。
- alphaonline/docs/superpowers/specs/2026-10-14-upgrade-core-2.3.1.md：本轮已落地升级。

## 后续修改如何维护

新功能完成时更新本文件的功能登记、接口影响、测试结果和未部署事项；专题内容可另存 docs/superpowers，但此入口必须有链接。遇到旧文档与代码矛盾，以当前代码+可复现测试为依据并修正文档。跨两项目的变更必须写清生产/消费双方变动及回退行为；账户、数据、缓存、第三方框架和本地性能优化不能互相覆盖。无需为每次变更增加摘要文件或额外校验门禁。

## 2026-10-15 动量 V2 性能优化

用户提供的 Ubuntu 日志中，swap Market Dict 从 48.5s 升到 62.6s；另外存在约 7.6s 的预处理启动延迟、1s 发布差异、9.9s pivot 重建差异，不能全部归因于因子。实际 embedded 路径每个市场逐币种计算，spot/swap 两路并行；不要误认为另一函数 gen_factor_data 的线程池在此处生效。

本次仅优化生产者动量包装与 C 核心，交易端 Python 公式继续作为独立 oracle，账户和其他因子配置不变：

- Python 对已为 datetime 的列直接读取纳秒数组，以一次向量比较验证 NaT/严格递增，保留字符串、时区和其他时间精度的兼容。
- 生产 signal 路径不再构造七列诊断 DataFrame、不分配诊断输出；diagnostics 仍能返回完整状态。
- C 使用可选诊断输出，合并工作区分配，环形缓冲区按实际需要分配，以递增位置替代逐行取模。
- 四根价格窗口一次计算供检测与位置项复用；仅当涨跌幅条件成立时才计算 z-score 的 sqrt/division，原阈值运算不变。
- 对 NumPy float64 使用 IEEE-754 位检查有限值，避免 MSVC 热循环频繁调用 CRT；memcpy 避免别名未定义行为。没有启用 fast-math，没有减少输入历史或改变公式。

同机 Windows / alpha311 合成固定数据、四参数 `[13608, 5421, 3509, 12600]`，交替测量 31 次中位数：

| 根数 | 优化前完整调用 ms | 优化后完整调用 ms | 优化前 C 核心 ms | 优化后 C 核心 ms |
|---|---:|---:|---:|---:|
| 3000 | 9.1011 | 0.9264 | 3.7906 | 0.5891 |
| 7200 | 18.6224 | 1.7078 | 5.5502 | 1.4071 |
| 14060 | 30.3163 | 2.8792 | 13.3424 | 2.6471 |

这是离线因子微基准，不是服务器全流程提速承诺。Linux 编译器、CPU、IO 与数据分布不同，应在目标机器复测。可独立上传 `alpha/tests/benchmark_momentum_v2.py`（git archive 默认排除 tests）并执行：

```bash
cd ~/alpha
python tests/benchmark_momentum_v2.py --repeats 31
```

脚本只用合成数据、无网络、无生产文件写入；默认不依赖旧动量，`--legacy` 仅在旧扩展可用时用于对比。

部署需要一起上传 `factors/动量210401_双项异常隔离V2.py`、`factors/c_ext/momentum_v2_wrapper.py`、`momentum_v2_core.c`、`momentum_v2_build.py`，然后在运行环境执行 `python factors/c_ext/momentum_v2_build.py`。新 C 导出 `compute_momentum_v2_fast`，旧二进制会被包装层明确拒绝并提示重编译；不能只上传 Python 文件继续使用旧 .so。ILLQ/CciV3 不需要为此次优化重编译。自动生成的 `_momentum_v2_core.c` 不应从 Windows 上传；如果已上传且编码错误，先备份移走该生成文件再构建，保留无下划线的手写源码。

验证：两个新增测试先确认旧实现会重复解析时间/构造诊断帧，再优化。最终动量数值/快速路径相关 43 项通过，另新增隔离增量测试 1 项通过（合计 44 项）。包含真实 BTC、长随机输入、异常和冷却状态、时区/时间精度/字符串、只读输入、生产与诊断 exact 对账。原 test_module9 集合因本地 `data/binance_spot_1h_resample/40m/BTCUSDT.pkl` 缺失未能收集，不能记为通过；新增测试使用临时目录和真实 CandleFileManager 验证首轮/增量及磁盘 PKL 无因子列，无生产数据写入。

最终编译产物下，交易端 `tests/test_precompute_contract.py` **4 passed**（5.63 秒），覆盖 C → Arrow → 消费者复用及独立 Python oracle。未在 Ubuntu 实测或部署，未修改用户近期的账户文件调整。

### 本次最终维护记录

最新诊断结论（2026-10-15 14:02–14:03）：用户更换代理后，保持相同脚本、币种、历史起点，重测 limit=500/750/900/999/1000/200，BTCUSDT、HDUSDT、NEOUSDT 共 18 次请求全部 HTTP 200。1000 对应耗时 0.281/0.234/0.563 秒，BTC/NEO 返回 1000 条，HD 返回其可用 476 条。此前“建议 200”仅适用于旧代理路径，现已被新测试取代：当前路径可保留生产 limit=1000，无需为旧代理限制降低分页大小。结果强烈支持代理路径/出口状态相关原因，不能确定具体 WAF 规则或证明 745 币种并发长期稳定性；本轮无 TLS 连接重置。未修改生产参数、未重启进程。

#### 2026-10-15 本机资金费率 HTTP 对比诊断

13:49–13:51 使用 alpha311、当前 config.py 的同一代理，仅请求公开行情端点；未修改生产代码、未重启或写入生产数据。诊断脚本为 `D:/alpha/tests/diagnose_funding_http.py`，默认基础对比，`--matrix` 参数对比，`--verify-pagination` 数据分页核对；脚本会联网，不能纳入默认离线测试。

- 原参数 limit=1000：单次、三并发、间隔 2 秒串行均 HTTP 403；BTC 增加 endTime、改近期 startTime、去除 startTime 仍 403。不能再将本次错误直接归因为并发超限。
- 同代理服务器时间接口 200；BTC 的 limit=1、100、200 成功。无代理直连在 15 秒超时；不能据此确定服务器出口或代理内部具体拦截规则。
- 小页方案验证：BTCUSDT、HDUSDT、NEOUSDT 各连续下载两页 100 条，下一页从原始 fundingTime + 1 毫秒开始；与一次 200 条原始 JSON 列表逐项完全相等，均无重复时间，9 次请求全部 200。这是有限样本验证，不是 745 币种完整历史验收。
- 结论：当前本机路径存在与 limit=1000 相关的拒绝现象，小页分页在实测样本有效。现有生产资金费率仍固定 1000，尚未应用该方案；应用时必须同步分页完成条件并控制请求频率，小页会增加请求量。现有 75 秒指数退避、整批 gather 后保存、HTTP 异常跨进程序列化等问题仍未修复。运行中的旧进程不受诊断脚本影响。
- 本次 alpha 的 git diff 无法读取（Git 返回 Not a git repository）；alphaonline 的已有未提交变更保留。

后续分页上限实测（13:54–13:56，同代理串行、每次间隔 2 秒）：从 500 开始，500/750/900/999/1000/400/300/250/201 各测 BTC、HD、NEO，27 次全部 403。200 两轮共 6 次全部 200，耗时 0.188–0.235 秒。最终 `--verify-limit 200` 再测三个币种：一次 200 条分别与两页 100 条原始 JSON 完全一致，9 次请求全部成功，候选请求耗时 1.063/0.219/0.171 秒（含新会话首次连接开销）。当前路径推荐 200，相比 100 满页请求数约减半；不能将这一观察写成币安全局官方上限，也未证明 745 币种长期稳定性。用户所贴 ClientConnectorError/ConnectionResetError 发生在代理 TLS 握手，独立于 HTTP 403；本轮未复现该错误，调整页大小不保证解决连接重置。测试脚本新增 `--limits 500,750,...`、`--verify-limit 200`，生产参数仍未修改，进程未重启。

- 2026-10-14：交易端完整 tests **278 passed，0 failed，0 skipped**（66.15 秒）；数据端上述相关集合 **66 passed，0 failed，0 skipped**（6.52 秒）。均在 alpha311 运行。
- 新动量扩展已在本机 MSVC x64 构建，真实 BTC、18000 根随机序列及异常事件与独立 Python 逐项对账通过。历史三个 C 模块/golden 保留并通过回归。
- 两端工作区尚未提交；前一轮 2.3.1 升级也属于当前未提交修改。未部署、未重启、未实盘下单，生产历史快照未改写。
- 历史 pandas FutureWarning 仍存在。本次不扩大范围清理与因子修正无关的告警。
- 后续动量参数调整：新增 `test_configured_momentum_parameters_match_python_history`，先确认旧配置失败，修改后 **1 passed**（3.46 秒）；从实际配置加载四个新参数，验证真实 BTC 历史的 C 嵌入列与独立 Python 一致、输入不变。本次仅运行此相关检查，未重跑全仓；账户参数待用户同步。

#### 2026-10-15 线上多程序历史 equity 汇总

新增独立工具与结果目录 [`equitymerger`](../equitymerger/README.md)。按用户要求只读获取服务器五个程序 `alphatrade / alphatrade1 / alphatrade1backup / alphatradebackup / alphatradetest` 的 data 下全部 60 份账户 `equity.csv`，按账号去掉有效 MMDD 后缀归并；显式兼容 `5m-onheart421` 及 `20m-heart / 20m_heart` 历史命名。

结果位于 `equitymerger/账号名/equity.csv`，11 个账号合计 100,402 条原始记录，时间严格升序，本次无重复时间或数值冲突。保留原始 10 列及数值精度；来源文件保存在 `sources`，来源和账号总览见两个 summary CSV。原始时间为实际统计时间，同小时不同秒的记录不能当作重复；历史断档不补造数据。汇总使用 UTF-8 BOM，生产 equity 文件仍为 GBK，未覆盖生产文件。

验证：alpha311 下 4 项针对性合并测试通过，全部输出逐字段读回与源记录一致，时间唯一且递增。仅新增离线汇总工具、分析文件和文档，未修改交易、数据生产、账户配置或服务器状态。采集开始时间为 2026-10-15 20:27:21（UTC+8）；结果是该次采集的历史快照，不自动追随后续更新。

## 2026-10-16 S12 LOCAL 可选仓管接口

新增 [LOCAL部署与验证说明](LOCAL_RISK.md)：positions/HybridLocalRiskStrategy.py、tools/local_risk_model.py、tests/test_local_risk_model.py。原Hybrid输出后仅缩S12，默认shadow，不转仓。周期模型计算隔离到单线程子进程，168h预测、672h训练；账户独立原子状态、UTC固定锚、重叠收益检查与异常拒绝。普通小时不加载LightGBM。

本次不修改两端core、原Hybrid、startup、生产者或现有账户配置，未部署/重启/下单。alpha311专项6项通过；0m研究259预测/50184小时乘数与原正式研究误差分别4.44e-16/2.22e-16。没有新投资表现。当前15m共同历史仅3061小时，不足初始化；必须先准备同偏移、同定义历史，不能直接部署0m研究状态。Linux流程、启用配置、故障处理及未决项见专题。此前core等未提交改动不是本轮修改。

## 2026-10-19 微信资金曲线布局

`core/utils/statistics.py` 与 `statistics_debug.py` 保留原有单图与统计口径，将子策略完整名称、编号及当前权重集中到图底双列图例，按策略数量额外预留图例高度；仓位分配曲线共用该图例与颜色，不再把长图例塞进矮子图。去掉子策略的密集极值/末值标注，保留账户曲线标注，保存后关闭 figure。绘图函数参数和微信发送接口不变。

验证：alpha311 下提取实际绘图函数进行 8 组隔离渲染（两套统计各覆盖 0/13/25 策略以及无仓位比例文件），替代数据读取与发送，不导入账户或调用外部接口；图例完整、位于画布内且不与绘图区重叠，普通子图最小高度约 1.14–1.15 英寸，13 策略预览已检查。未部署、重启或实际发送微信；合成样本未覆盖任意超长策略名称。

## 2026-10-24 50m 持仓 CSV 数据修复

按用户授权修复服务器 `alphatradetest/data/50m_rgonheart1020/持仓信息/SOLUSDT.csv`：中文表头已损坏且编码为UTF-8，末行三个小数点误写成中点，造成统计脚本缺列。先按原始字节备份，再恢复标准表头、规范三个小数点并保存为GBK；全部25行及其他数值保留。目录7份CSV的字段/数值读取验证通过，本地实际持仓计算函数隔离验证通过。未修改代码或交易配置，未重启、下单或发送通知，未等待下一次自动统计。备份位置和证据见[修复记录](../research/spot_csv_repair_20261024/REPORT.md)。
