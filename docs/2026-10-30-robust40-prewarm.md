# MODEL_ROBUST40 独立偏移预热与人工审核

## 后续接入修复：新增 core 人工审核项

审核记录：2026-10-30 用户明确回复“人工审核确认”，确认本次预热接入及下表 `core/select_coin.py` 新增 14 行修改。人工代码审核已通过；尚未由助手执行完整 startup 重启，首次实盘运行结果待确认。

本次 20m 报错不是已经加载的预热包内部定义不匹配，而是账户仍指向旧 research/model_robust40_v1 基准。活动 S3 起始日期为 2026-08-01，旧基准为 2025-11-28。现已为 20/40/55m 添加 warmup_path 并同步 bootstrap_path 到各自 research/warmup_packages/<分钟>m_20261030_review 实盘同源包，state_path 不变；三账户公开配置均与各自包一致。活动账户的其他内容保持原字节，原基准文件也未覆盖，自动补 BNB 保持开启。

40m 补生成已完成：1,945 根，共同截止 2026-10-30 00:40 UTC，全量/24h 续算对照通过；导出后再次校验包内 snapshot 与 MODEL 末根仓位及模拟状态通过。日志 research/prewarm_40m_20261030_retry.log。首次生成曾因 D 盘临时空间不足失败，清理的仅是本任务隔离目录中的可重算因子缓存，生产数据未删除。

真实续算验证进一步发现框架空仓时间轴缺口，**相对上一轮交付，新增 core 范围仅一个文件 +14 行**，请人工审核：

| 文件/位置 | 修改内容 | 影响边界 |
|---|---|---|
| `core/select_coin.py::_include_snapshot_gap` | 新增小函数：已有 snapshot 时，读取本账户 snapshot_time，把聚合起点向前扩到 snapshot 后第一个周期；正确处理 UTC 与时间粒度 | 只补漏掉的无信号时间，不平移信号、不修改权重；单账户和策略池使用各自 marker |
| `core/select_coin.py::agg_multi_strategy_ratio` | 在既有 fallback_start_date 后调用上述函数（一行） | 冷启动保持原行为；正常逐小时续算范围不变；跨多小时恢复不漏空仓小时 |
| `core/select_coin.py::agg_multi_strategy_ratio_v2` | 同步增加同一个调用（一行） | 备用差分聚合入口与原入口保持同一时间轴 |

审核命令：`git diff -- core/select_coin.py`。相对 main 的另外四个 core 文件仍是下表所列旧改动，不属于本次新增。28 个独立 oracle 用例先失败后通过，连同主链路共 39 passed；补充日频、单策略 snapshot 和已有回退窗口后，最终时间轴专项 32 passed，主链路 11 passed。覆盖 5/15/20/25/40/45/55 分钟及冷启动。20m 真实包恢复后，实际 live.prepare/update_state 已在独立目录成功跨 3 小时续算到 2026-10-30 00:20 UTC。

必须由 startup 完整启动执行成对恢复；旧进程的账户重试不会执行恢复逻辑。仅切 bootstrap 会把当前旧 snapshot 和新 MODEL 基准混用。本次不替用户重启或下单；以下“零 core 修改、未接入账户”描述的是初版预热交付，当前状态以本节及 DEVELOPMENT.md 最新记录为准。

## 范围与审核结论

初版新增功能：在首次交易前，用实盘同源行情预先运行子策略计算，并同时保存框架 snapshot 与 MODEL 状态。两者必须结束于同一根已闭合 K 线。启动恢复后，下一轮复用这些状态续算，再进入既有交易流程。

**初版预热的 `core/` 修改为 0 个文件、0 行；后续接入修复新增的一个文件 +14 行见上节。** 不实现 Tail-Only，不修改因子数学、调仓公式或 BNB 设置。新功能必须通过账户 `runtime.warmup_path` 显式启用；当前接入状态以上节为准，配置接入不等于已完成进程重启。

| 本轮文件 | 修改项 | 人工审核重点 |
|---|---|---|
| `startup.py` | 原 snapshot 清理前调用 `prepare_startup`，清理后、自检前调用 `restore_startup`；新增局部 import | 清理前验证失败即停止；debug 跳过；未配置 warmup_path 的账户不执行配置加载/状态恢复 |
| `tools/prewarm_robust40.py` | 独立命令：冻结已完成的一轮行情，在全新目录复用现有子策略回测及净值处理，再建立 MODEL | 输出全部隔离；Windows spawn 子进程同样隔离；只查询市场规则，不调用 startup、real_trading、账户余额操作、撤单、下单、补 BNB |
| `tools/robust40/warmup.py` | 成对导出、清理前暂存、清理后恢复 | 账户/分钟/公开策略配置一致；snapshot marker、比例、模拟器 state0/state1 和 MODEL 同截止；末根持仓一致；重启不回退已有 MODEL state |
| `tools/rebuild_robust40_live_baseline.py` | 新增 `--workspace` 与 `--with-snapshot`；优先读取隔离目录冻结行情 | 沿用既有全量/24h 续算比较；配对导出只接受隔离工作目录；配置变化不发布可恢复包 |
| `tests/test_robust40_warmup.py` | 首次/重启/异常及 Windows spawn 离线验证 | 20/40/55 分钟、不完整包、配置变化、同时间不同仓位、旧策略不执行新路径 |

`startup.py` 的变更虽不在 core 内，仍属于共享启动路径，应人工审核。没有改动原清理条件、调度周期、原账户自检、下单入口或统计入口。

## 既有 core 改动清单（不是本轮新增）

为了避免“本轮未改 core”掩盖分支既有差异，下表按 `main=e6fc3bd` 比较。现有四文件合计 +27/−4 行，完整补丁可执行 `git diff main -- core` 审核。

| 文件 | 已有最小改动 | 默认兼容边界 |
|---|---|---|
| `core/model/backtest_config.py` | 实例保存、校验并传入 `required_history_start` | 未设置为 None；不改变普通策略配置 |
| `core/utils/datatools.py` | 显式固定历史账户走完整 PKL、检查起点覆盖，空输入报错 | 普通策略继续原 Arrow/PKL 与 get_kline_num 截窗路径 |
| `core/real_trading.py` | 净值整理后调用可选 prepare_live；仓位限制后调用可选 transform_targets | 必须 enable_live_hooks=True 且策略提供对应函数才触发 |
| `core/position.py` | 调仓执行器可声明 use_full_equity | 默认 False，保留原手续费预留及相应资金折扣；MODEL 自己选择口径 |

以上 core 不导入 MODEL 模块。共享 section 的统一修正属于此前用户单独批准的变更，不是此次预热附带修改。旧 5/15/20/25/45 系列未配置 warmup_path，继续原启动和计算路径；本轮没有重跑这些旧账户真实行情，不把默认分支检查表述为全策略实盘验收。

## 预热与下单重合的计算

预热复用 `MultiEquityBacktestConfig.backtest_strategies`、`process_equities`：读取历史、因子、选币、子策略比例聚合、模拟交易、再择时和父策略所需净值。随后用同源输入计算 MODEL 父权重、S5 delta 和虚拟账户状态，并执行全量与 24h 续算比较。

同时保存整个账户 snapshot 目录，包括选币、ratio、account_state0/lots_state0、再择时 state1 以及框架自身维护的其他续算文件。所谓“最近一小时 snapshot”是**状态推进到最近已完成的一小时**，不是只保存一行或截掉状态所需历史。

第一轮交易仍需读取新行情、更新市场规则、补算新 K 线、校验历史重叠，再按实时资产与价格计算订单。不能预先固定真实目标手数，也不承诺首次运行只需毫秒。自动补 BNB 保持原账户设置，预热过程中不执行补币。

应先完成预热与恢复，再开始正常调度；若预热期间已错过某个交易时刻，首次下单等待下一个正常分钟偏移，不补发历史时刻的订单。

## 各分钟独立生成

每个命令只处理一个账户。工作目录和输出包都必须是新目录；20m/40m/55m 不共享快照，不平移复用父权重。某一偏移没有数据或生成失败，不删除、不覆盖其他偏移产物。可分时分别生成，也可在内存和 CPU 足够时运行独立进程；每份都包含完整历史，建议先串行评估资源。

在 D:/alphaonline 下使用 alpha311：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' tools/prewarm_robust40.py --account accounts/20m_robust_4h.py --workspace research/prewarm_20m_run1 --output research/warmup_packages/20m_run1
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' tools/prewarm_robust40.py --account accounts/40m_robust_4h.py --workspace research/prewarm_40m_run1 --output research/warmup_packages/40m_run1
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' tools/prewarm_robust40.py --account accounts/55m_robust_4h.py --workspace research/prewarm_55m_run1 --output research/warmup_packages/55m_run1
```

失败后换新的目录重试，原目录保留用于诊断。命令不会启动/重启数据生产者，不修改活动账户，也不会自动安装生成包。市场规则查询复用该账户客户端及框架子进程的现有行为，包括原有平台差异；历史重叠检查仍严格执行，不放宽 Historical revision。

输入要求：该分钟 spot/swap batch PKL、pivot、四类同轮 ready 已完成，生产者保留的历史覆盖 required_history_start；原冻结模型 bootstrap 仍用于保留既有模拟手数单位。冻结行情时检查源文件是否正在被生产者更新，变化则停止；不把半轮产物当作完成快照。额外因子数据源沿用配置的只读路径，运行期间应保持代码、公开配置及这些输入稳定。

## 人工审核后接入

审核对应包成功生成了 `runtime_bootstrap.pkl`、`account.csv`、`snapshot/` 和最后写入的 `warmup.pkl`。`warmup.pkl` 只包含账户标识和公开计算配置，不包含 API 密钥或通知地址。

在该账户现有 `strategy_config['params']['runtime']` 内增加一项，例如：

```python
'warmup_path': 'research/warmup_packages/20m_run1',
```

其 `minute` 和账户 hour_offset 必须一致，state_path 保持账户独立且位于 cache/runtime/snapshot 之外。使用现有 clean_start=True 启动时，先验证并将完整状态暂存到 `data/warmup_handoff/`，原清理完成后恢复到正常 snapshot/state 路径。首次从包安装状态；后续重启优先保留已推进的账户状态与当前 snapshot，绝不回退到包中的旧时间点。

该流程必须在没有同账户交易进程同时写状态时执行。如果已存在 MODEL state、但 snapshot 不存在或时间不匹配，启动在清理前报错，保留原文件；需要显式重建成对状态，不能仅删除其中一个。恢复失败保留临时目录供人工排查，不自动拼接半套状态。

配置、黑白名单或策略语义变更后需要重新生成包，不能通过修改包内配置标识跳过检查。路径型 runtime 配置变化不影响数学，比较时只保留 minute；其他公开策略、子策略、模拟器和执行相关配置参与比较。凭据轮换与 BNB 设置不因导入包被覆盖。

生成任务彼此独立；但同一个 startup 管理多个账户时，某个已启用包校验失败仍会中断该次整体启动，这是清理前保护行为。不要把“独立生成”误解为启动时忽略某个账户状态错误。

## 验证记录与尚未完成项

本轮离线已验证首次恢复、已推进状态重启、20/40/55 分钟、黑名单变更、末根持仓不匹配、缺再择时状态、旧账户隔离、ready 完整性及清理调用顺序；Windows 实际 spawn 子进程验证框架输出路径均指向独立工作目录。其间发现 Windows 重定向日志的 GBK 编码失败，已在预热 worker 内设置 UTF-8，不修改共享日志 core。

离线专项 40 passed / 4 warnings；入口补充同名字段隔离和 dict 关键字写法后，预热专项最终 16 passed。备份目录内五个指定旧账户及三个活动 MODEL 账户的实际配置都未触发预热恢复。55m 实盘同源包已生成在 `research/warmup_packages/55m_20261030_review`，约 719.7 MiB，截止 2026-10-29 20:55 UTC；全量/24h 续算对照通过，且真实包在独立目录验证首次恢复和清理后重启恢复成功。

20m 独立包也已生成在 `research/warmup_packages/20m_20261030_review`，约 719.4 MiB，截止 2026-10-29 21:20 UTC；同样通过全量/24h 续算、真实包首次恢复及清理后重启恢复。40m 在本次检查时没有完整 ready 发布，未生成正式包。三种分钟的恢复逻辑均有离线测试；40m 尚未完成真实数据预热验收。

其余分钟仍需各自完整行情及可查询的市场规则；未生成成功的包不可接入。各分钟最新结果见 DEVELOPMENT.md。本轮不部署、不重启、不下单，也不自动为活动账户开启 warmup_path。包、临时工作目录和恢复副本均有较大磁盘开销，本次保留供审查，未自动清理。
