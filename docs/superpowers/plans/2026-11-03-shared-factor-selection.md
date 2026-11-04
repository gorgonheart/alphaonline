# Shared Factor and Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if execution is delegated. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留策略与交易行为的前提下，统一初始化、4050续算、修订回放及正式交易使用的因子/截面/选币计算，降低重复转换与选币内存，提高完整流程性能。

**Architecture:** 通用块所有权与读写放在core/utils/factor_data.py；计算与兼容入口集中在core/select_coin.py，由core/backtest.py及初始化编排共同调用。factors/sections保留数学，tools负责资源与恢复，positions保留MODEL，accounts保留参数。正式交易消费三种起点产生的同一目标契约，不另建算法。

**Tech Stack:** Python3.11 alpha311、pandas、Polars、PyArrow IPC、NumPy、既有Numba；Windows/Linux均保留spawn。

**Spec:** ../specs/2026-11-03-shared-factor-selection-design.md（用户已确认）。

## Global Constraints

- 用户已授权执行并明确选择在D:/alphaonline当前目录实施；保留全部已有未提交修改。当前main，不创建worktree、不打包提交其他会话修改；每任务保存本轮增量与测试记录，最终提交另按用户指令处理。
- 只用`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -X utf8 -B`。下文`$py`表示该解释器绝对路径；pytest均加`-p no:cacheprovider`，生产模块不得为测试改环境。
- 保留140xx初始化/4050运行、MODEL incremental_lookback_hours=0、传统策略自己的lookback、168小时修订检测与25检查点。
- 不改账户/S3日期/自动BNB/下单算法；不tail-only、不启用实盘、不部署、不重启、不删除生产数据，不修改alpha/alpha2。
- MemoryHigh4.5GiB/Max5GiB/SwapMax12GiB不自动提高；不恢复逐策略/逐块进程回收。
- 数值/缺值/物理行序严格按原契约，既有数学容差不放宽，不生成新golden掩盖差异。
- 源码按原文件行尾写回；用`git -c core.whitespace=cr-at-eol diff --check`。
- 所有暂存目录在调用方拥有的隔离测试/运行目录；snapshot安装、回放事务和计算提交边界不变。
- 全部任务状态写入本计划独立执行记录；未通过验证的任务不能标完成。每任务最终检查只运行对应测试组，最终共享流程做一次贯通回归。

## Review Focus

1. raw在t修订，但next_close/shift可能影响t以前输出；任务1/7验证恢复覆盖，不能只验证最新行。
2. 一个未知pandas截面或custom钩子改变共享输入，逐因子分派可能改变后续结果；任务4/6保留适配及调用顺序。
3. 参数按factor_col_limit分片可能影响有顺序依赖的因子；任务3不得无条件合并所有参数或改变分片语义。
4. 部分成功后的错误：块已写但元数据未安装、S3已更新但MODEL失败、计算已提交但交易失败；任务2/5/7分别验证所有权与恢复。
5. 同名现货合约、offset边界、断市/下架后持仓及类别编码变化；任务2/5/6/7验证原排名、状态和映射。

## File structure and shared interfaces

主要业务新增一个文件：`core/utils/factor_data.py`。从tools/initialization_data.py迁入通用FactorBlocks和read_market，旧模块仅重导出以保持既有调用；新接口统一由core持有。

- `FactorBlocks(directory, *, hours, minute, backend='ipc')`、`append(frame, shard)`、`read_history(columns)`、`close()`沿用语义。
- `FactorBlocks.block_keys() -> tuple[int, ...]`：只返回时间块键。
- `FactorBlocks.read_block(key, columns, *, order=None) -> pl.DataFrame`：只返回本块请求列，调用方拥有返回值，store不缓存读取表；order为本块物理行的NumPy整数索引。
- `FactorBlocks.row_order(key, keys=('candle_begin_time','symbol')) -> np.ndarray`：只读取排序键，产生与原sort一致的排列。
- `FactorBlocks.add_section(column, parts: dict[int, pl.DataFrame]) -> None`：每part包含_row及结果列；只安装成功完成的列，保持基础行身份，不能混入其它列或更改行数。
- `core.select_coin.calc_factors(conf, *, run_time=None, store=None)`：未传store保留公共接口；传store时由共同worker生产块，不再写临时行情PKL或因子Parquet。
- `core.select_coin.calc_cross_sections(conf, *, store=None)`：有store时按依赖/状态协议计算，无store适配原文件消费者。
- `core.select_coin.select_factor_blocks(configs, store, directory, *, workers, start=None) -> dict[int, dict[int, list[Path]]]`：输出与现有select_blocks一致；start裁剪选币输出，不裁剪截面预热输入。
- `core.backtest.prepare_factor_selection(factory, run_time, *, directory) -> tuple[FactorBlocks, dict[int, dict[int, list[Path]]]]`：统一输入/因子/截面/选币；返回store供最后的映射消费者读取，调用方负责close。
- `core.backtest.persist_block_selections(configs, paths) -> None`：只把必需的小选币结果写成原每策略PKL，供原聚合/报告；不重新写全因子面板。

worker直接写出是否优于有界返回是任务3性能试验，不能反过来强迫下游接口适配；最终生产接口不同时保留两套算法。功能配置复用现有资源项；config.py功能区新增`shared_factor_pipeline = False`，不放accounts，四场景共用此开关，不新增账户策略参数。未经完整验收保持默认旧入口，验收后再列人工启用项。配置加载/worker覆盖必须保持该功能值一致，不允许初始化工具静默覆盖成另一值。

## Task 1: 建立四场景契约及新基线

**Files:** Create `tests/test_shared_compute_contracts.py`, `research/shared_compute_benchmark.py`; reference existing research/initialization_refactor_bench.py、initialization_continuation_check.py；记录至research/shared_compute_20261103/。

**Interfaces:** 消费现有测试fixture与原实际函数；产出`run_case(case: str, output: Path, *, code_root: Path, fixture_root: Path, account: Path, stamp: int) -> dict`，case取initialize/continue/gap/replay/trade_mock，报告完整wall、阶段、进程树内存与输出比较。测试原始输入固定只读，候选和基线分离snapshot。CLI参数固定为`--case --output --code-root --fixture-root --account --stamp`；continue/gap/replay的起始snapshot从fixture_root内读取后复制到output工作区，只读源不改变。缺少源或目标已存在时明确退出，不复用旧结果。

基准调用：`$py -X utf8 -B research/shared_compute_benchmark.py --case initialize --code-root <baseline或candidate源码目录> --fixture-root <只读同轮输入目录> --account <离线公开账户配置> --stamp <同轮整数时间戳> --output <本轮独占输出目录>`。Expected：退出0、报告exit_code=0及完整wall/阶段/内存；源缺失时不能标完成。实际参数由现有冻结样本核实后逐项记录，不在计划中臆造生产路径。

- [x] 记录当前相关文件基线（包含未提交运行代码，不是只用HEAD）；与新候选输出分开；不复制生产data，不新增哈希。
- [x] 建立原行为保护测试：`test_replay_effective_start_covers_next_close`、`test_trade_commit_survives_exchange_failure`、`test_seven_hour_gap_and_rebuild_boundary`；断言回放与充分历史全量oracle一致、历史订单0、提交后的状态不回退。

核心断言（独立fixture提供reference/replayed/committed_before/error_after，不能由候选计算expected）：
```python
pd.testing.assert_frame_equal(replayed.targets, reference.targets)
assert replayed.section_cutoff == replayed.child_cutoff == replayed.model_cutoff
assert historical_orders == []
assert error_after.committed_cutoff == committed_before.committed_cutoff
```
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_compute_contracts.py tests/test_checkpoint_live.py -q -p no:cacheprovider`。Expected：保护测试通过；若发现原缺陷，以独立失败用例记录，先定位是否必须修正才能完成共同入口，不能更改期望掩盖。
- [x] 记录17因子接口改造后的初始化/4050基线；没有真实数据的场景明确缺失，以合成全路径契约补充但不冒称性能验收。
- [x] 保存本任务结果与当前差异清单；此任务不修改数值实现。

## Task 2: 通用块存储与所有权

**Files:** Create `core/utils/factor_data.py`, `tests/test_factor_data_ownership.py`; modify tools/initialization_data.py、tools/streaming_selection.py；reuse tests/test_initialization_data.py。

**Interfaces:** 实现上述FactorBlocks新增方法；旧iter_blocks保留适配，不能在yield栈内持有不同于返回对象的宽表。

- [x] 写失败测试：`test_reader_releases_unsorted_owner_before_selection`使用weakref捕获读取表，排序结果仍存活时旧表引用已释放；`test_read_block_projects_only_requested_columns`确认实际读取列；`test_section_install_failure_keeps_previous_columns`确认失败列未进入store。

所有权核心断言：`assert raw_frame_ref() is None`，同时`assert sorted_frame.equals(expected_sorted)`；列读取断言`assert seen_columns == requested_columns`，失败安装断言`assert store.section_columns == previous_columns`。memory测试后端本身持有源数据是明确例外，所有权用IPC后端验证，不能让例外掩盖生产泄漏。
- [x] 写全12offset、同symbol不同市场、重复排名键、空块/类别列的独立行序用例，断言新旧`_row`与字段逐值一致，排序不偷偷增加改变并列顺序的键。
- [x] Run `$py -X utf8 -B -m pytest tests/test_factor_data_ownership.py -q -p no:cacheprovider`。Expected：缺新接口/旧引用未释放的明确RED。
- [x] 迁入通用存储，实施按键读取与窄排列；返回后仅保留路径/元数据，close只删除自身独占目录；不改变公共输入和snapshot。
- [x] Run `$py -X utf8 -B -m pytest tests/test_factor_data_ownership.py tests/test_initialization_data.py tests/test_initialization_pipeline.py -q -p no:cacheprovider`。Expected：全部对应契约通过，Windows映射可关闭；保留与新接口冲突的旧测试时先明确其假设为何不再是公开契约。
- [x] 保存本轮增量和RED/GREEN证据。

## Task 3: 时序输入与有界调度统一

**Files:** Modify core/select_coin.py、core/utils/datatools.py、core/utils/functions.py、core/utils/arrow_reader.py（仅必要投影接口）、tools/streaming_factors.py；create tests/test_shared_factor_stage.py。

**Interfaces:** `iter_preprocessed_candles(conf, run_time)`放core/utils/functions.py，按原swap/spot、市场过滤顺序提供处理后单币帧；`load_realtime_data`复用其数学并保持原文件接口。Arrow描述任务放core/select_coin.py的模块级worker，复用read_and_merge_bmac_data和process_candle_df_return_polars。`calc_factors(...,store=...)`消费两类输入且输出完全相同行序。

- [x] RED：`test_store_path_does_not_write_temporary_candle_pickle`、`test_pending_factor_results_are_bounded`、`test_out_of_order_completion_keeps_original_rows`、`test_factor_sharding_keeps_parameter_order`。

核心断言：`assert not (cache / 'all_candle_df_list.pkl').exists()`；`assert peak_pending <= 2 * workers`；`assert actual.equals(expected_original_order)`；参数/分片调度序列与原实现的记录逐项相同，不只比较最终正常数值。
- [x] 对auto/full_pkl/arrow_strict/arrow_full_strict分别验证缺列、损坏Arrow、固定历史、市场过滤、最小上市历史以及长因子先补全后裁切，不能以严格模式替换传统auto回退。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_factor_stage.py -q -p no:cacheprovider`。Expected：新路径缺失/产生临时PKL/任务积压等明确RED。
- [x] 实现有界计算与保存，保留factor_col_limit分片及既有特殊参数语义；输入使用描述或单币帧，不跨进程发送全市场列表。结束/异常时释放结果和executor。
- [x] 用同一批全历史及4050数据比较有界返回重叠与直接分片写出，记录创建、传输、写入与父/worker内存；选完整阶段较优且对拍一致的方案，另一个不加入生产开关。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_factor_stage.py tests/test_model_legacy_multi_params.py tests/test_full_arrow_initialization.py tests/test_arrow_reader.py -q -p no:cacheprovider`。Expected：通过；上游C/Python既有差异单列不放宽。
- [x] 更新接口说明和增量记录。

## Task 4: 截面依赖复用与逐因子适配

**Files:** Modify core/select_coin.py、tools/streaming_factors.py；create tests/test_shared_section_dispatch.py。

**Interfaces:** `calc_cross_sections(conf, *, store=None)`统一分派；`run_section`原签名继续负责数学及状态。相同依赖组只读一次，跨组及时释放。未知pandas/custom截面维持原输入顺序和可观察行为。

- [x] RED：`test_one_pandas_section_does_not_force_native_sections_to_pandas`、`test_shared_dependencies_read_once_per_group`、`test_unknown_section_mutation_keeps_legacy_semantics`、`test_output_rows_match_dependency_rows`。

核心断言：混合接口fixture的调用序列`assert calls == ['native:polars', 'legacy:pandas']`，共享读取`assert dependency_reads == 1`，结果`assert actual.equals(original)`。如果未知函数副作用迫使适配组保持旧调用链，该组必须仍与原调用链一致，不能以独立分派改变它。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_section_dispatch.py -q -p no:cacheprovider`。Expected：全局all_polars分支或重复读取导致RED。
- [x] 实现依赖分组与按能力适配；不能猜测未知函数所需列，已声明依赖/状态输入的原生函数可窄投影；输出以稳定行身份安装。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_section_dispatch.py tests/test_section_polars_parity.py tests/test_initialization_pipeline.py -q -p no:cacheprovider`。Expected：原截面结果/异常/顺序不变。
- [x] 记录读取量、物化/计算/写出时间及峰值，保存增量。

## Task 5: TopK与S3的有状态分块

**Files:** Modify sections/TopKTo2K.py、sections/VolumeMaxBias_进出场阈值分离官方.py、core/utils/section_state.py、core/select_coin.py；create tests/test_section_block_states.py。

**Interfaces:** 已知截面可新增`signal_block(frame: pl.DataFrame, params, column: str, state: dict | None) -> tuple[pl.DataFrame, dict]`，state仅运行中使用；S3最终保存仍为原signal_with_checkpoints协议。TopK临时状态按品种有效行推进，运行结束丢弃，正常计算仍读足原依赖范围。未知截面保留整段适配。

- [x] 独立oracle覆盖TopK的rank-min、top10%、bottom21%、n根实际行窗口；S3沿用独立oracle，测试有/无checkpoint、首个可用截面、空截面、缺币/重上市、同分与跨块队列。
- [x] RED：`test_topk_blocks_match_full_with_missing_hours`、`test_s3_blocks_preserve_25_common_points`、`test_failed_block_does_not_install_section_state`、`test_restored_s3_matches_corrected_full_history`。

核心断言：`np.testing.assert_array_equal(actual[column], oracle[column])`；`assert len(state['checkpoints']) == 25`（fixture提供至少25小时）；`assert state['cutoff'] == final_time`；失败后的磁盘状态与失败前逐字段相同，不以文件摘要验证。
- [x] Run `$py -X utf8 -B -m pytest tests/test_section_block_states.py -q -p no:cacheprovider`。Expected：新块接口缺失或独立切块重置历史的RED。
- [x] 实现原数学的分块状态推进；最终写出前保持待提交状态，不逐块覆盖有效snapshot；不增加新持久化版本或更改S3配置日期。
- [x] Run `$py -X utf8 -B -m pytest tests/test_section_block_states.py tests/test_section_resume_state.py tests/test_checkpoint_live.py tests/test_section_polars_parity.py -q -p no:cacheprovider`。Expected：全量/续算/共同恢复结果一致；失败行为符合原事务。
- [x] 比较截面完整成本，若分块比原实现明显更慢，保留已验证依赖复用并记录该状态内核候选不启用，而非为了内存接受明显降速。
- [x] 保存审查项及RED/GREEN记录。

## Task 6: 共享选币工作集与并发

**Files:** Modify core/select_coin.py、tools/streaming_selection.py、core/utils/factor_data.py；create tests/test_shared_selection_stage.py。

**Interfaces:** 实现select_factor_blocks；同一块窄排序索引只算一次，策略按相近依赖列分组，有界任务完成后只保留小结果路径。旧select_blocks转发该函数。特殊策略通过明确完整输入适配，不误走无状态分块。

- [x] RED：`test_wide_block_is_not_sorted_for_each_dependency_group`、`test_completed_group_does_not_retain_factor_buffers`、`test_no_all_blocks_selection_result_list`。

核心断言：`assert order_builds == number_of_blocks`；`assert consumed_group_ref() is None`；`assert actual.equals(expected)`。输入包含同时间同symbol不同市场、并列值和空输出，expected来自原真实selector；不能先对结果重排来掩盖差异。
- [x] 覆盖rank并列、比例/固定数、空结果、long/short联动、NaN/null、mix优先级、日频、所有offset、custom与entry_exit；断言最终SELECT_RES_COLS、物理行序及权重一致。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_selection_stage.py -q -p no:cacheprovider`。Expected：新接口/所有权与未分组宽表行为的RED。
- [x] 实现核心函数与旧入口适配；不修改core/model/strategy_config.py中的排名和过滤数学；不先用top_k或set_sorted替代真实排序。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_selection_stage.py tests/test_streaming_selection.py tests/test_initialization_pipeline.py -q -p no:cacheprovider`。Expected：全部覆盖路径一致。
- [x] 同数据比较2/3/4任务、依赖分组粗细，计入读写和排列创建，选币峰值下降且完整成本有利才采用；所有数值库线程仍计入总预算。
- [x] 保存增量、峰值与时间取舍；小文件stream合并不在本任务强制执行。

## Task 7: 四场景接线与事务保护

**Files:** Modify core/backtest.py、tools/initialization_pipeline.py、tools/robust40/cli/server_robust40.py、config.py；按必要修改core/real_trading.py、tools/robust40/handoff.py、tools/robust40/server.py；extend tests/test_shared_compute_contracts.py；create tests/test_shared_compute_handoff.py。

**Interfaces:** 实现prepare_factor_selection和persist_block_selections；run_backtest_multi和初始化run调用同一前半程。输出裁剪由原snapshot_time/max_hold_period/lookback决定；store供映射最后消费者使用并明确关闭。启用条件是功能配置及可证明的策略能力，不能按ModelRobust40Strategy名称硬编码通用计算。

- [x] RED：`test_initialize_continue_replay_use_same_factor_selection_entry`、`test_traditional_lookback_is_not_model_replay`、`test_selected_history_retains_original_snapshot_merge`、`test_reports_and_spot_swap_mapping_survive_factor_cleanup`。

核心断言：三个计算case均调用`prepare_factor_selection`；`assert legacy_conf.incremental_lookback_hours == configured_lookback`；原报告文件集合完整；共同截止相同；交易mock的`assert candidate_order_params == baseline_order_params`，模拟收益一致不能替代订单参数检查。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_compute_handoff.py -q -p no:cacheprovider`。Expected：入口分离或丢失文件消费者的RED。
- [x] 接线并保留原下游聚合/模拟/再择时/仓管数学。保留必要报告输出，不产生重复因子Parquet；不改变临时目录并发清理语义，不新增跨账户异步。
- [x] 原恢复点不足以覆盖next_close/shift影响时，用任务1失败oracle独立修正恢复边界并在core人工清单标注正确性变更；不能扩大修订检测范围或开启MODEL lookback。
- [x] Run `$py -X utf8 -B -m pytest tests/test_shared_compute_contracts.py tests/test_shared_compute_handoff.py tests/test_initialization_handoff.py tests/test_checkpoint_live.py tests/test_legacy_checkpoint_trial.py tests/test_replay_logging.py -q -p no:cacheprovider`。Expected：正常/补7小时/回放/交易mock通过；缺真实历史的skip单列。
- [x] 测试计算前、S3后、MODEL后、提交后交易故障；对旧snapshot可恢复性、共同截止和无历史下单逐项断言。
- [x] 保存core逐文件/功能变化清单，明确无必要改动的startup/positions/equity保持原样。

## Task 8: 完整性能与最终审核

**Files:** research/shared_compute_benchmark.py、research/shared_compute_20261103/*、docs/2026-11-03-shared-compute-review.md、docs/DEVELOPMENT.md、docs/model-robust40-deployment.md；仅更新已实现内容。

**Interfaces:** 沿用任务1完整计时case；新旧同源输入、独立输出，对比因子/选币/全部snapshot/目标/订单参数；不新增实盘或通知入口。

- [x] 运行各任务对应最终测试一次并汇总，输出fail/skip及确切环境；不把已有批次简单相加。
- [x] 全市场当前43策略严格对拍；传统5/15/20/25/45实际配置或公开fixture验证；日频/custom/entry_exit保留专项小样。
- [x] 初始化后连续两轮4050、7小时补缺口、有效持仓修订回放、跨日/季度、交易mock贯通；完整修订oracle只在历史覆盖足够时声称通过。
- [x] 至少3组成对完整初始化、正常续算和回放测量，冷/暖编译分开；计入读取、转换、文件创建、状态保存、进程退出及清理。当前无真实输入则明确性能阻塞，不用合成数据宣称生产提升。
- [x] 报告选币峰值、因子有效CPU、整轮wall、临时空间和内存生命周期；Windows数据不冒充Linux；Linux运行待单独授权。
- [x] 复核计划接口/设计要求均有实现和测试；无明显端到端收益的候选撤回该候选本轮修改，保留独立有收益项与原有用户修改。
- [x] 最终独立代码审查必须读取本轮增量而非全部未提交diff；Critical/Important修复先RED后GREEN，未决项清楚列出；不自动部署/启用生产开关。
- [x] 更新文档、人工启用/回退项与完整交付说明；任何未通过四场景验收的入口不得标已完成。

## Plan self-review

- Spec四场景映射到任务1/7/8；生命周期任务2；因子任务3；截面任务4/5；选币任务6；资源与完整成本任务8。
- 共享接口以上表为唯一命名；任务之间不另命名同义函数。
- 数据输入与输出裁剪分开；恢复点与因子预热分开；不能因改入口全局禁用传统lookback或custom功能。
- 条件项聚合v2/IPC stream/全面MODEL数组化不阻塞本计划，也不算本计划承诺已实现。
- 原文档提到的“计算失败不下单”指不提交新的策略订单，不承诺撤销当前框架在计算前执行的撤单/补BNB等已有动作；mock验证原顺序保留。


## 执行状态补充

2026-11-03：任务1–8在本地离线范围完成。最终31文件回归719通过/71缺数据跳过；10组成对初始化/续算/7小时缺口/有效持仓修订回放通过，连续两轮4050含实际订单格式化参数对拍。完整修订oracle、跨日/季度行为采用专项测试；性能样本不宣称季度切换或真实交易延迟已测。Windows容量不外推Linux，缺失实际账户不计通过，完整证据和人工清单见docs/2026-11-03-shared-compute-review.md。

终审修复开关绕过、next_close验收链和普通策略原生线程预算。shared=False且旧data_pipeline=True恢复原初始化工具实现；shared=True才统一core。混合市场重复键保留全历史语义，窄键排列/逐列释放已解决原7.95GiB失败候选，最终完整初始化4.217–4.372GiB。worker直写因子提交峰值增长明显，未采用；其全量比较脚本失败不能计为已通过。

最终10组成对计算均通过，汇总脚本因早期pilot缺case字段曾退出，明确legacy-pilot类别后单独重跑成功；未重跑/覆盖已通过计算。实际源文件15个/core5个，保持默认关闭。未提交、部署、启动生产、下单或修改alpha。沿用用户就地实施决定保留源码基线、现有未提交修改及执行证据；不因skill清理模板删除唯一审查基线。
