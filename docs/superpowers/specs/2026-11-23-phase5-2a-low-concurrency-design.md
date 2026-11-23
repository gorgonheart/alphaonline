# Phase 5 子项目 2A：交易端低并发与热路径优化设计文档

> **所属体系**：[统一开发入口](../../README.md) · [2026-11-23 Phase 5 与 v2.0.2 规划](../../2026-11-23-phase5-and-v202-migration-plan.md) · [开发规范与数据契约](../../kb/standards-and-contracts.md)
> **日期**：`2026-11-23`
> **基准提交**：`D:/alphaonline` `main=06c8b15`（Phase 4 已通过测试机 23 轮自然轮验收）
> **状态**：已完成独立可实施性核查并按确认裁决修订，待人工批准后进入实施计划（`writing-plans`）与 TDD 落地

---

## 1. 意图与背景（Why）

Phase 4（`06c8b15`）将测试机 4c8g 上 `25m-eart1106` 的 `account_calculation_s` 从 `115.7–121.2 s` 压降至 `94.78 s`、`total_s` 压降至 `128.09 s`。结合实测剖面与源码逐行复核，交易端存在 4 处经实证的高性价比优化点：

1. **仓位聚合 `agg_pos_ratio`（实测独占 `~5.4 s`）**：`core/real_trading.py:118-134` 打印的 `完成仓位限制 ~5.9–6.7 s` 因未重置 `s_time` 实际覆盖 `calc_ratios + agg_pos_ratio + validate_targets`（MODEL 账户禁止 `symbol_ratio_limit`，`apply_position_limits` 直接短路返回）；其中 `agg_pos_ratio` 14 次 `DataFrame.add(fill_value=0)` 独占 `~5.4 s`，列连续 NumPy 累加已本地实测压至 `~1.08 s`（`5.0x`，逐位一致）。
2. **`sections` 截面阶段（`~13.6 s`，仅占 `1.1–1.2` 核）**：`core/backtest.py:395` 将 `sections` 阶段的 `POLARS_MAX_THREADS` 限制为 `min(2, budget)`，且外层 4 个 `signal_block` 截面族完全串行；由于 Polars 1.36 共用单全局 Rayon 线程池，实盘（`gate is None`，`MemAvailable ≈ 5.45 GiB`）将 `_polars_threads` 放宽至 `budget`（4 核）并叠加 3 线程跨族并发，可让 S3（Python/NumPy）+ IPC I/O + 3 个 Polars 族真正并发吃满 3–4 核。
3. **`preflight` 修订输入读取（`input_read_s ~9–14 s`）**：`spot` Arrow、`swap` Arrow 与 `pivot` 完全串行；三路并行叠加文件级 `TimestampType.unit` 零拷贝整型二分，既降低读取墙钟耗时，又提前免费免疫子项目 2B（`v2.0.2` WS）产出的 `ms` 精度时间列，并保持 `MODEL_REPLAY` 分支耗时遥测完整。
4. **`replay` 共同状态恢复（`restore_s ~7.8 s`）**：`restore_snapshot` 串行恢复 14 个互不依赖的子策略目录（`S1..S14`），改用 4 线程并行恢复，仅 ~8 行改动、普通轮零开销。

本设计仅修改消费端 `D:/alphaonline`，不改动生产端 `D:/alpha` 架构与跨项目数据契约。

---

## 2. 目标、非目标与反目标

### 目标（Goals）
- **G1（`P5-D1` · `D1-A`）**：向量化重写 `agg_pos_ratio`（列连续单块 `float64` 累加），本地 14×14050×400 实测 `5.40 s → ~1.08 s`（预计线上稳定省 **`~4–5 s`/轮**），并降低 pandas 中间宽表分配。
- **G2（`P5-C1` · `C1-A`）**：实盘（`gate is None`）将 `sections` 阶段的 `_polars_threads` 从 `min(2, budget)` 放宽至 `budget`，并在 `_calc_section_blocks` 中以最多 3 线程并行计算 `signal_block` 截面族、主线程确定性顺序提交与落盘，预计稳定省 **`~4–6 s`/轮**。
- **G3（`P5-A1`）**：将 `preflight` 修订读取改为 `spot` / `swap` / `pivot` 三路并行（主线程汇总 `MODEL_REPLAY` 分支遥测），并在 `read_arrow_tail` 中基于文件级 `TimestampType.unit` 做零拷贝整型二分（原生兼容 `s`/`ms`/`us`/`ns` 与有/无时区），预计稳定省 **`~4–7 s`/轮**。
- **G4（`P5-B2`）**：将 `restore_snapshot` 中 14 个子策略目录恢复改为最多 4 线程并行，真实修订 `replay` 轮预计省 **`~5–6 s`**，普通 `incremental` 轮零开销。

### 非目标（Non-Goals）
- **暂缓 `P5-B1`（硬链接 CoW 克隆）**：不改变 `shutil.copytree` 拷贝语义，避免引入“快照目录禁止原地写”的长期维护红线；待项目 1（`D:/alpha` `b5eb945` REST 延后 3s）上线观测一周真实资金费修订频率后再评估是否追加。
- **跳过 `apply_position_limits` / `redistribute_excess_weight` 重写（`D1-A`）**：`ModelRobust40Strategy.py:1483` 禁止 `symbol_ratio_limit`，在现行生产 6 个 MODEL 账户及测试机 `25m-eart1106` 上不可达（0 s 收益），不作无用改动。
- **不修改 `positions/ModelRobust40Strategy.py`（含 `validate_targets`）**：实测 `validate_targets` 仅 `~0.77 s`，而触碰该文件会在生产首轮诱发一次性 `~29 s` Numba 重编译，性价比极低。
- **不做 `P5-C2`（跨截面族块外层合并）**、**不做按文件名年份跳读 Pivot PKL**（[避坑清单](../../kb/rejected-and-failed-attempts.md) 已明确拒绝）。
- **不改动生产端 `D:/alpha`、任何因子/选币/模拟/下单/统计数学逻辑或 7 条跨项目契约**。

### 反目标（Anti-Goals）
- 任何情况下不得引入数值偏差（须满足各分项独立 Python Oracle 等价口径）。
- 后台全量初始化（`config.compute_gate is not None`，cgroup `MemoryHigh=4.5 GiB`）期间，**必须保持** `sections` 串行及 `_polars_threads=min(2, budget)`，不得破坏 `BURST ↔ YIELD` 节流或推高初始化内存峰值。
- 不得因 `ContextVar` 跨线程不继承而丢失 `MODEL_REPLAY` 的 `revision_arrow_spot_s` / `revision_arrow_swap_s` / `revision_pivot_s` 遥测字段。
- 不得破坏现有公共函数签名（`run_section_blocks`、`run_section_blocks_group`、`read_inputs`、`read_arrow_tail`、`restore_snapshot`、`agg_pos_ratio`）。

---

## 3. 备选方案与已选决策（Approaches）

经多轮评估与独立源码核查，所有关键架构取舍已由人工确认：

1. **整体范围（已选 1 档）**：落地 `P5-D1 + P5-C1 + P5-A1 + P5-B2`，暂缓 `P5-B1` 硬链接 CoW（磁盘压力由 Q4 清理历史归档包治本，而非用硬链接换空间）。
2. **`P5-C1` 线程与 Polars 全局 Rayon 池（已选 `C1-A`）**：
   - 核查发现 `core/backtest.py:395` 原将 `sections` 的 `_polars_threads` 限制在 `min(2, budget)`，而 Polars 1.36 所有 Python 线程共享单全局 Rayon 池（本地实测 `POLARS_MAX_THREADS=2` 下 3 线程仅 `1.08x`，放宽至 `4` 后相对 `2` 串行达 `1.52x`，再叠加 S3 与 IPC 重叠）。
   - 选定 **`C1-A`**：在 `core/backtest.py:395` 仅当实盘 `compute_gate is None` 时放宽至 `budget`（初始化 `compute_gate is not None` 维持 `min(2, budget)`），同步更新 `tests/test_shared_compute_handoff.py:41` 并补充双分支覆盖。弃用 `C1-B`（不动 backtest 导致纯 Polars 卡在 2 核）与 `C1-C`（多进程 `spawn` 开销和内存放大不划算）。
3. **`P5-D1` 仓位段范围（已选 `D1-A`）**：
   - 核查发现 `apply_position_limits` / `redistribute_excess_weight` 在 MODEL 账户上不可达，真实热点是 `agg_pos_ratio`（本地 14×14050×400 实测 `5.40 s → 1.08 s`）。
   - 选定 **`D1-A`**：聚焦 `agg_pos_ratio`，跳过 `apply_position_limits` / `redistribute_excess_weight` 重写，也不碰 `ModelRobust40Strategy.py`。
4. **`P5-A1` 非 `ns` Arrow 时间戳返回精度（工程师裁决，供审阅核对）**：
   - 核查发现 `pandas 2.2.3 + pyarrow 17.0.0` 下既有 `utc_times()` 与 `input_frames()` 均**保留 Arrow 原生时间单位**（`datetime64[ms|us|s]`），而非强制转 `ns`。
   - **裁决**：`read_arrow_tail` 在整型二分阶段按文件级 `unit` 精确换算边界键（0 额外开销），构造 `frame.index` 时**保留 Arrow 原生 `datetime64[{unit}]`**（不强转 `ns`），从而与 `utc_times`/`input_frames` 保持 `check_exact=True` 完全一致，且不改动 `utc_times` / `first_candle_time` / `indexed=False` 既有契约。（2B 切 WS 仅在首次 `ns→ms` 过渡轮使 `first_revision` 走一次 `get_indexer` 比对，仍正确返回 `None`、无伪修订，次轮起全 `ms` 恢复快路。）

---

## 4. 环境与跨项目硬约束

1. **Python 与测试环境**：唯一使用 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（Python `3.11.11`、`pandas 2.2.3`、`numpy 2.0.2`、`polars 1.36.1`、`pyarrow 17.0.0`）；Windows pytest 统一传 `-p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check`。
2. **2B 联动免疫**：`v2.0.2` WS 路径产出 `ms` 精度时间戳，`P5-A1` 必须零额外开销支持 `s`/`ms`/`us`/`ns` 与有/无时区，并保留完整 `MODEL_REPLAY` 分支遥测供 2B 24h 门禁核验。
3. **TDD + 独立 Python Oracle + 原子提交**：4 项优化均先写失败/Oracle 测试再改生产代码，分 4 个独立 commit（`P5-D1` → `P5-B2` → `P5-A1` → `P5-C1`），可精准单点回退。
4. **默认离线验证**：不启动实盘/生产者、不下单、不重启服务；远端测试机部署需另行授权。

---

## 5. 分项详细设计

### 5.1 `P5-D1`：`agg_pos_ratio` 列连续单块 NumPy 向量化

**涉及文件**：[core/model/backtest_config.py](../../core/model/backtest_config.py#L993-L1006)

- **现状**：14 次循环执行 `df_spot_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)` 后调用 `df_spot_ratio_sum.add(..., fill_value=0)`，每轮重新对齐并拷贝整张 14050×400 宽表。
- **新实现设计**：
  1. 若 `not self.ratio_dfs`，返回 `(pd.DataFrame(), pd.DataFrame())`（保留原行为）。
  2. 目标行索引 `target_index = pos_ratio.index`，行数 `T = len(target_index)`。
  3. 抽离私有辅助 `_agg_side_ratios(side_frames, pos_ratio, target_index)`，分别聚合 `spot`（索引 0）与 `swap`（索引 1）：
     - **列并集顺序**：从空 `pd.Index([])` 按 `side_frames` 顺序迭代 `all_cols = all_cols.union(df.columns)`，与连续调用 `pd.DataFrame().add(..., fill_value=0)` 的列顺序 100% 一致（单策略保留原列序，多策略并集排序，兼容 0 列单市场场景）。
     - 若 `len(all_cols) == 0`，返回 `pd.DataFrame(index=target_index, columns=all_cols)`。
     - **行对齐无多余拷贝**：对每个 `df`，若 `df.index.equals(target_index)` 直接取底层数值（零 `.loc` 拷贝）；否则检查 `row_pos = df.index.get_indexer(target_index)`，仅当 `row_pos` 全部 `>= 0` 且 `df.index.is_unique` 时做行切片，若含缺失或重复标签则回退 `df.loc[target_index]`（确保保留缺标签抛 `KeyError` 等既有语义）。
     - **列连续累加（零 fancy-index scatter）**：分配转置连续数组 `acc = np.zeros((len(all_cols), T), dtype=np.float64)`（每列 `acc[c]` 为长度为 `T` 的连续内存行）。对每个子策略 `idx`：
       - `w = pos_ratio[idx].to_numpy(dtype=np.float64, copy=False)`
       - `col_pos = all_cols.get_indexer(df.columns)`
       - **无 NaN 快路**（`not np.isnan(arr).any() and not np.isnan(w).any()`）：逐列 `acc[c] += arr[:, j] * w`（本地 14×14050×400 实测 `5.40 s → 1.08 s`，逐位 `np.array_equal=True`；且若之前存在 `nan_only` 掩码，将对应列置为已见有限值）。
       - **含 NaN 精确回退路径**（复刻 `DataFrame.add(..., fill_value=0)` 逐格语义）：懒分配 `seen_finite = np.zeros((len(all_cols), T), dtype=bool)` 与 `seen_nan = np.zeros((len(all_cols), T), dtype=bool)`：
         - 在 `pd.DataFrame().add(s0, fill_value=0).add(s1, fill_value=0)...` 中，对某格 `(r, c)`：若 `s_k` 在该格有列且值为 `NaN`，且此前未累积过有限值（含首次出现时左侧无列按 `fill_value=0` 填充 `0 + NaN = NaN`），则该格暂标为 `seen_nan`；若某策略有该列且值为有限值（包括 `0.0`），则 `acc[c, r] += val` 且 `seen_finite[c, r] = True`（覆盖此前的 `NaN`，因 `NaN.add(x, fill_value=0) == x`）；如果某策略**没有**该列，对已在左侧为 `NaN` 的格而言 `NaN.add(missing, fill_value=0) == NaN`（仍保持 `NaN`，直到遇到某个策略贡献有限值！）。
         - 最终对 `seen_nan & ~seen_finite` 位置填 `np.nan`。
     - 返回 `pd.DataFrame(acc.T, index=target_index, columns=all_cols)`（按原 `idx` 顺序逐策略累加，浮点舍入顺序与原版完全一致）。

---

### 5.2 `P5-C1`：`sections` 实盘 4 核 Polars 预算 + 跨 `signal_block` 族 3 线程并行

**涉及文件**：
- [core/backtest.py](../../core/backtest.py#L392-L396)
- [core/utils/section_state.py](../../core/utils/section_state.py#L43-L133)
- [core/select_coin.py](../../core/select_coin.py#L652-L698)
- [tests/test_shared_compute_handoff.py](../../tests/test_shared_compute_handoff.py#L8-L45)

#### A. 实盘放宽 `sections` 的 Polars 线程池预算（`core/backtest.py`）
- 在 `prepare_factor_selection` L393–395：
  ```python
  gate = getattr(config, 'compute_gate', None)
  section_threads = budget if gate is None else min(2, budget)
  with phase('initialization.sections'):
      store.sections, store.section_columns = _run_block_stage(
          calc_cross_sections, conf, store=store, _polars_threads=section_threads
      )
  ```
- 后台初始化（`config.compute_gate` 非空）维持 `min(2, budget)`，严守 `MemoryHigh=4.5 GiB` 与节流契约；实盘（`compute_gate is None`）放宽至 `budget`（`MODEL_JOB_NUM=4` 时为 `4`）。
- 同步更新 `tests/test_shared_compute_handoff.py:41` 断言 `budgets == [1, budget, budget]`（因为该用例显式设置 `compute_gate=None`），并新增一条带非空 `compute_gate` 的用例验证初始化模式仍断言 `[1, min(2, budget), budget]`。

#### B. 纯计算与主线程提交分离（`core/utils/section_state.py`）
- 新增 3 个函数（保持 `run_section_blocks` / `run_section_blocks_group` 原签名与行为 100% 不变）：
  1. `compute_section_blocks(conf, factor, store, columns, params, column)` → 仅调用只读 `store.block_keys()` / `store.read_block(...)` 及 `factor.signal_block(...)`，**不调用 `store.add_section`，不写磁盘**，返回 `(column, parts, persist_path, state)`（非持久化时 `persist_path=None`）。
  2. `compute_section_blocks_group(conf, factor, store, param_specs)` → 统一处理单参/持久化与多参无状态（沿用单块合并读列优化），返回 `list[(column, parts, persist_path, state)]`。
  3. `publish_section_outputs(store, outputs)` → 仅在主线程调用，按序对每条 `(column, parts, persist_path, state)` 先 `store.add_section(column, parts)`，若 `persist_path is not None` 再原子落盘（`temporary = persist_path.with_suffix('.tmp'); pd.to_pickle(state, temporary); temporary.replace(persist_path)`）。
- `run_section_blocks` / `run_section_blocks_group` 内部直接委托给 `compute_*` + `publish_section_outputs`。

#### C. `_calc_section_blocks(conf, store)` 3 线程跨族调度（`core/select_coin.py`）
- 按 `conf.section_params_dict.items()` 原始顺序过滤出有效截面族 `entries`，每项标记 `kind = 'block'`（`hasattr(factor, 'signal_block')`）或 `'legacy'`。
- 筛选 `block_entries = [e for e in entries if e['kind'] == 'block']`。
- **并发分支（`len(block_entries) > 1 and gate is None`）**：
  - 用 `with ThreadPoolExecutor(max_workers=min(3, len(block_entries))) as pool:` 提交全部 `block_entries` 执行 `compute_section_blocks_group(conf, e['factor'], store, e['param_specs'])`。
  - 在 `with` 块内按原序调用 `future.result()` 收集 `block_results`（若任一族抛异常，`with` 退出会等待所有工作线程自然收敛再传播异常，确保调用方执行 `store.close()` 时零悬空读线程，且**无任何**半写入截面列或部分落盘 S3 checkpoint）。
  - 全部计算成功后，主线程按 `entries` 原始声明顺序提交：`block` 族调用 `publish_section_outputs(store, block_results.pop(e['name']))`，`legacy` 族走既有串行逻辑。
- **串行分支（`len(block_entries) <= 1 or gate is not None`）**：按原顺序逐族串行执行，与旧逻辑完全一致。

---

### 5.3 `P5-A1`：`preflight` 三路并行读取 + 零拷贝多精度整型二分

**涉及文件**：[tools/robust40/handoff.py](../../tools/robust40/handoff.py#L209-L281)

#### A. `read_inputs(...)` 三路并行与线程安全遥测（L209–227）
- 常规 Arrow 快路分支改为 `ThreadPoolExecutor(max_workers=3)` 并发读取 `('spot', 'swap', 'pivot')` 三路：
  - 每个工作函数仅记录局部 `time.perf_counter()` 耗时并返回 `(data, elapsed_s)`，**绝不在子线程访问 `telemetry` `ContextVar`**。
  - 主线程在 `with ThreadPoolExecutor(...)` 内按固定顺序 `('spot', 'swap', 'pivot')` 汇总：
    - 若某路抛异常：当 `telemetry.active()` 时按原 `telemetry.phase` 语义写入 `failed_phase`（`telemetry._current.get().setdefault('failed_phase', phase_name)` 或通过 `telemetry` 小辅助函数），待线程池退出后再重新抛出。
    - 成功后在主线程调用 `telemetry.add(**{phase_name: round(elapsed_s, 6)})`（仅 `telemetry.active()` 时）。
    - 若 `spot` / `swap` 为空，按原逻辑抛出 `ValueError(f'Missing same-round {kind} Arrow for revision checks')`（若需记 `failed_phase`，注意原代码空校验在 `with telemetry.phase(...)` 之后，因此空校验抛错不设置 `revision_arrow_*_s` 为 `failed_phase`，继续保持原行为）。
    - 按 `spot` → `swap` → `pivot` 顺序合并进 `out` 返回。

#### B. `read_arrow_tail(...)` 文件级 `TimestampType.unit` 零拷贝整型二分（L244–281）
- 在 `reader = pa.ipc.open_file(source)` 后、币种循环外**读取 1 次** `ts_type = reader.schema.field('candle_begin_time').type`。
- `_TS_UNIT_TO_NS = {'s': 10**9, 'ms': 10**6, 'us': 10**3, 'ns': 1}`。
- 若 `pa.types.is_timestamp(ts_type)` 且 `ts_type.unit in _TS_UNIT_TO_NS`：
  - `unit = ts_type.unit`，`scale = _TS_UNIT_TO_NS[unit]`。
  - 循环外一次性换算边界整数键（严格等价于 `start <= t <= end`）：
    - `end_key = end.value // scale`
    - `start_key = (start.value + scale - 1) // scale`
  - 循环内对每个 `batch`：
    - `ts_col = batch.column('candle_begin_time')`
    - 若 `ts_col.null_count == 0`：
      - `raw = ts_col.cast(pa.int64()).to_numpy(zero_copy_only=True)`（Arrow 中任何时区或无时区的 `timestamp[unit, tz]` 底层 `int64` 均为对应 `unit` 的 UTC/naive epoch 刻度，O(1) 零拷贝）。
      - `right = int(np.searchsorted(raw, end_key, side='right'))`
      - `left = max(0, right - tail) if tail is not None else int(np.searchsorted(raw, start_key, side='left'))`
      - 若 `right <= left: continue`
      - `frame = batch.select(data_columns).slice(left, right - left).to_pandas(use_threads=False)`
      - 若 `indexed`：
        - **保留 Arrow 原生时间单位**（与 `utc_times`/`input_frames` 精确一致）：`frame.index = pd.DatetimeIndex(raw[left:right].copy(), dtype=f'datetime64[{unit}]', name='candle_begin_time')`（`.copy()` 断开对 mmap 页的长期引用，与原 `copy(deep=True)` 一致）。
    - 若 `ts_col.null_count > 0` 或非 `TimestampType`：回退既有 `utc_times(ts_col.to_numpy())` 路径。
- `missing_revision_fee` 补 `np.nan`、`is_unique` 校验、`first_candle_time` 归一化与 `is_monotonic_increasing` 排序逻辑完全保持不变。

---

### 5.4 `P5-B2`：`restore_snapshot` 子策略并行恢复

**涉及文件**：[tools/robust40/handoff.py](../../tools/robust40/handoff.py#L348-L350)

- 将 `for child in {p.parent for p in lots}: restore_child(child, time)` 改为：
  - `children = sorted({p.parent for p in lots})`
  - 若 `len(children) <= 1`：直接串行调用 `restore_child(children[0], time)`。
  - 若 `len(children) > 1`：使用 `with ThreadPoolExecutor(max_workers=min(4, len(children))) as pool:` 提交各 `child` 并行执行 `restore_child(child, time)`，在 `with` 块内按序调用 `future.result()` 校验并传播异常。
- 各 `child` 目录文件完全不交，零共享写状态；普通 `incremental` 轮零开销；`replay` 轮发生在低内存阶段（`MemAvailable > 5 GiB`），4 线程瞬时增耗 `< 100 MiB`。

---

## 6. 测试与验证计划（TDD + 独立 Python Oracle）

新增独立测试文件 `tests/test_phase5_optimizations.py`（全离线、不联网）：

1. **`P5-D1`（`agg_pos_ratio`）Oracle 测试**：
   - 独立保留旧版 `DataFrame.add(fill_value=0)` 实现作 Oracle；覆盖 14 子策略随机稀疏/重叠列、`pos_ratio` 子集/乱序索引、单策略保序、空 `ratio_dfs`、纯单市场（0 列 spot）、缺索引抛 `KeyError`、含 `NaN` 单元格（单策略 `NaN` / 多策略缺失列保持 `NaN` / 后续策略有限值覆盖 `NaN`）等全部边界，断言 `pd.testing.assert_frame_equal(..., check_exact=True)`（或 `rtol=0, atol=1e-15`）。
2. **`P5-C1`（`sections` 并行与线程预算）测试**：
   - 构造多块 `FactorBlocks` + 生产全部 4 个 `signal_block` 截面族（含 S3 持久化 checkpoint 续算与 3 个多参无状态族），对比单线程串行与 3 线程并行：`check_exact=True`、`store.section_columns` 顺序一致、S3 checkpoint 产物一致。
   - 异常原子性测试：某 `signal_block` 族中途抛错，断言 `store.section_columns == []` 且磁盘无残留 `.pkl`/`.tmp`。
   - 初始化节流保护测试：`config.compute_gate` 非空时保持串行不拉线程池，且 `prepare_factor_selection` 仍传 `_polars_threads=min(2, budget)`；`compute_gate=None` 时传 `_polars_threads=budget`。
   - 既有回归：`test_shared_compute_handoff.py`、`test_shared_section_dispatch.py`、`test_section_block_states.py`、`test_p4_optimizations.py`。
3. **`P5-A1`（`preflight` 三路并行 + 多精度整型二分）测试**：
   - 参数化测试 Arrow `candle_begin_time` 为 `timestamp('ns', 'UTC')`、`timestamp('ms', 'UTC')`、`timestamp('us', None)`、`timestamp('s', 'Asia/Hong_Kong')` 以及含亚单位纳秒边界 / 含 null 回退场景，对比 `input_frames` / `utc_times` 参考实现断言 `pd.testing.assert_frame_equal(..., check_exact=True)`（同时覆盖 `indexed=True` 与 `indexed=False, tail=N`）。
   - 验证 `read_inputs` 在 `telemetry.round_log` 下三路并发执行时正确记录 `revision_arrow_spot_s`、`revision_arrow_swap_s`、`revision_pivot_s`；单路失败时正确记录 `failed_phase` 并抛原异常。
   - 既有回归：`test_revision_input_reads.py`、`test_replay_logging.py`、`test_removed_series_governance.py`、`test_universe_expansion_revision.py`。
4. **`P5-B2`（并行 `restore_child`）测试**：
   - 构造含多子策略（含 `re_timing` 双 `lots_state0/1`）的 snapshot，对比串行与并行 `restore_snapshot` 产物逐文件等价；注入单子目录损坏，验证异常准确传播且无残留线程。
   - 既有回归：`test_checkpoint_live.py`、`test_robust40_checkpoint_trial.py`、`test_server_snapshot_lifecycle.py`。
5. **收尾文档同步**：实施完成后更新 [docs/DEVELOPMENT.md](../../DEVELOPMENT.md)、[docs/kb/roadmap-and-backlog.md](../../kb/roadmap-and-backlog.md) 与 [docs/2026-11-23-phase5-and-v202-migration-plan.md](../../2026-11-23-phase5-and-v202-migration-plan.md)（写明 `C1-A`、`D1-A`、`P5-B1` 暂缓裁决及本地回归/微基准结果）。

---

## 7. 留给实施者的自由裁量项（Builder Discretion）

- 内部私有辅助函数命名与拆分粒度（如 `_agg_side_ratios`、`_arrow_ts_bounds`、`compute_section_blocks_group` 等）。
- `telemetry` 主线程写 `failed_phase` 的封装细节（可增加极简内部 helper 或直接操作当前上下文 dict，只要保持 `test_replay_logging.py` 与既有 JSON 字段完全兼容）。
- 4 个原子提交按风险从低到高排序：`P5-D1` → `P5-B2` → `P5-A1` → `P5-C1`。
