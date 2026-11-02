# 预热高效与预计算覆盖方案

日期：2026-10-30（v3，12:58 重写）
范围：MODEL_ROBUST40（20m/40m/55m）
状态：P0 已完成；本文只描述待实现项

> v3 相对 v2 的删减：删掉"深度断言""publish 注释修正"（P1，已决定不做）、删掉因子归档层（L1 上移）的完整设计、删掉跨机迁移的 manifest 细节、删掉 9,780 的更正长文。只保留"要让预计算真正覆盖 ILLQ/CciV3，并在预热端兑现"所需的内容。

## 0. 已完成

| 项 | 状态 |
|---|---|
| ILLQ 参数 2856/2448 → 2320/2184（6n ≤ 14,064，满窗后与深度无关） | 三账户已完成 |
| `precompute_factor_configs` 三方对齐（alpha + 三账户） | 已完成，12/12 列声明一致 |
| 口径：实盘与回测同深度即达标，不追求因子数学收敛 | 已定 |

## 1. 现状链路与耗时基线

单轮实盘（40m_robust_4h，273s）与预热（独立进程，重跑同一条链路）共用这套阶段：

| 阶段 | 实盘耗时 | 数据来源 | 预热是否重付 |
|---|---:|---|---|
| 读取数据中心数据 | 38.6s | pkl 全量（robust 走 `fixed_history`，不读 arrow） | 是 |
| 时序因子计算 | 97.2s | 现算 | 是 |
| 截面因子计算 | 26.8s | 现算（含 ILLQ/CciV3） | 是 |
| 选币（4 进程） | 11.2s | `cache/all_candle_df_list.pkl` | 是 |
| prepare_live（仓管） | 54.2s | state + 原始 pkl + pivot | 是 |
| 计算目标仓位 | 14.5s | 含 numba 首次编译 9.9s | 是 |
| 其余（聚合/模拟/落库） | ~30s | — | 是 |

其中与本文直接相关的三块：**读 pkl 38.6s + ILLQ/CciV3 现算（串行 86s，4 进程后约 10~15s 墙钟）+ 预热重复付一遍**。

实测单币因子成本（14,064 行合成数据，含 pandas 开销）：

| 因子 | 参数个数 | 单币 | ×756 币（串行） |
|---|---:|---:|---:|
| `ILLQStdBbi45208` | 5 | 59.4ms | **45s** |
| `CciV3` | 3 | 53.7ms | **41s** |
| `动量210401_双项异常隔离V2` | 4 | 230.8ms | **175s** |

## 2. 预计算覆盖 ILLQ/CciV3 的四个阻碍

按"卡在哪一层"排序，四条都必须解开，缺一条就退回现算。

| # | 阻碍 | 代码位置 | 现状 |
|---|---|---|---|
| **R1** | **pkl 批次按设计不写因子列** | `alpha/core/preprocess.py:236-241` | 注释明确：`persist the RAW batch (OHLCV+meta only) ... writing factor columns to the batch pkl is dead I/O`。因子列只进 `market_dict` 返回值，再发布进 arrow |
| **R2** | **arrow 有因子列，但深度只有 5,000** | `alpha/config.py:84` `publish_kline_num = 5000` | robust 需要 14,064 行，arrow 只有 5,000 → 即使读也没有足够历史 |
| **R3** | **`required_history_start` 硬禁用 arrow** | `alphaonline/core/utils/datatools.py:306-308` | `arrow_dict = None if fixed_history else read_...`，三个 robust 账户全部 `fixed_history=True`，arrow 分支从未执行 |
| **R4** | **缺列回退只覆盖"全历史因子"** | 同上 `:201-204` `_full_history_factors` + `:310-315` | 只有 `requires_full_history=True`（仅动量）参与 `required_columns` 判定；ILLQ/CciV3 缺列时不触发回退，而是静默走现算 |
| R5 | 列名必须与配置逐字一致 | `select_coin.py:126-139` | `use_precompute` 三重条件任一不满足即静默 `calc_factor_vals`，**无日志**。数据重建后若列名对不上，不会有任何提示 |

R5 是运维风险：重建后 11 列若有一列命名不符，性能回退但结果正确，很难被发现。

## 3. 三条路线对比

| | A：pkl 带因子列 | **B：arrow 提深 + 统一走 arrow** | C：独立因子归档（parquet） |
|---|---|---|---|
| 改动面 | alpha 1 行（取消注释） | alpha 1 行 + alphaonline 8 行 | 新增读写层，估 300+ 行 |
| 读盘 | 1.11GB → 1.7GB/offset，读盘 30s → 45s | **arrow mmap 2.6s**（不变大反而快 10 倍） | 只读 11 列，约 1s |
| 因子 | 零计算 | 零计算 | 零计算 |
| 磁盘增量 | +0.6GB/offset | **+1.1GB/文件 × keep 2 × 2 类型 × 3 offset ≈ 20GB** | +0.2GB/offset/年 |
| 预热收益 | 省因子，多读盘，净收益≈0 | **省 30s 读盘 + 省因子** | 省最多，但工程量大 |
| 风险 | 低 | 中（要逐元素对拍，arrow 与 pkl 深度必须完全相同） | 高（新增一致性契约） |

**选 B。** 理由：只有 B 同时解决"读盘"和"预计算"两件事，且 arrow 本来就是为 mmap 设计的；C 是 B 的下一步，等 B 跑稳再考虑。

关键前提：arrow 的 `publish_kline_num` 必须 ≥ pkl 实际深度 14,064。取 **14,100**（`tail(14100)` 对 14,064 行的 df 无裁剪，得到 14,064 行，与 pkl 逐行相同）。

## 4. 详细修改范围

### H1｜arrow 深度提到 14,100（alpha，1 行）

| 文件 | 行 | 现值 | 改为 |
|---|---:|---|---|
| `D:/alpha/config.py` | 84 | `publish_kline_num = 5000` | `publish_kline_num = 14100` |

连带影响：

- 单文件 589MB → **1.66GB**（32 列 × 14,064 行 × 756 symbol）
- 保留策略 `cleanup_old_snapshots(..., keep=2)`（`preprocess.py:555`）不变 → 峰值 `1.66 × 2 × 2 类型 × 3 offset = 20GB`
- 磁盘总占用：pkl 15GB + pivot 1.6GB + arrow 20GB ≈ 37GB（D 盘余量 64GB，可行）
- 发布耗时：写盘量 ×2.8，实测需在改后跑一轮确认（当前发布未单列计时）

### H2｜让 robust 账户在读 arrow 前先做深度校验（alphaonline，约 8 行）

`core/utils/datatools.py:306-315`，把"有 `required_history_start` 就禁用 arrow"改为"arrow 深度够就用，不够再回退"：

```python
# 现状
arrow_dict = None if fixed_history else read_arrow_market_dict_for_run_time(...)
# 改为
arrow_dict = read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type, run_time)
if arrow_dict is not None and fixed_history:
    earliest = pd.Timestamp(required_start)
    if any(df['candle_begin_time'].min() > earliest
           for df in arrow_dict.values() if df is not None and not df.empty):
        logger.warning('Arrow 深度未覆盖 required_history_start，回退 PKL')
        arrow_dict = None
```

要点：

- 不能只判断"文件存在"，必须判断**首行时间**——arrow 深度不足时文件照样存在，R2 就是这么被掩盖的
- 回退必须整体回退（`arrow_dict=None`），不能逐 symbol 混用，否则同一轮里不同币的因子口径不同
- `read_and_merge_bmac_data:270-278` 的 `required_history_start` 断言保持不动，作为第二道校验

### H3｜把"列校验集"与"补算集"拆开（alphaonline，约 6 行）

`core/utils/datatools.py:201-204`。现在一个函数身兼两职，导致 R4：

| 用途 | 应该包含 | 现状 |
|---|---|---|
| `required_columns`（arrow 缺列 → 回退 pkl） | **全部** `precompute_factor_configs` | 只有动量 |
| 缺列补算（pkl 分支现算补齐） | 只有 `requires_full_history=True` | 一致 |

```python
def _required_precompute_columns(conf):      # 新增：全部声明列
    return {f'{n}_{p}' for n, ps in getattr(conf, 'precompute_factor_configs', {}).items() for p in ps}

def _full_history_factors(conf):             # 保持：只补算全历史因子
    ...
```

调用处 `:310` 改用 `_required_precompute_columns(conf)`。收益：arrow 只要缺 ILLQ/CciV3 任何一列就整体回退 pkl，而不是静默现算——把 R5 的静默变成显式。

### H4｜预热进程的 numba 编译前置（`tools/robust40`，约 10 行）

预热是独立进程，`simulate` 首次 25.1s + `calc_lots` 首次 9.9s 全部重付。在预热入口（`tools/prewarm_robust40.py::run_worker`，第 86 行起、`mef.backtest_strategies` 之前）加一次最小形状的占位调用，把编译时间从"卡在预热中段"挪到"进程启动即完成"，同时给出计时日志。

不改变任何数学，只改变编译时机。若接受 35s 固定成本，可跳过。

### H5｜同根幂等短路（`tools/robust40/live.py`，约 8 行）

`prepare_live` 现在无条件重跑：pivots 读取 1.1s + raw 分片 2.6s + build_context/merge/validate 约 19s + state 写 1.5s。若 `state.index[-1] >= expected_bar`（重试、补跑、预热与实盘撞同一根），直接跳过数据构建，只走仓位计算。

收益：重试场景从 54s → **<2s**。预热与实盘衔接时这个场景每天都会出现一次。

### H6｜state 写盘后置（可选，约 5 行）

121MB 整包 pickle + fsync 约 1.5s，位于下单链路上。移到 `calc_target_position` 之后、或改异步线程。低风险低收益，排在最后。

### 汇总

| 项 | 文件 | 行数 | 收益（单轮） | 风险 |
|---|---|---:|---|---|
| H1 | `alpha/config.py:84` | 1 | 使 H2 成立 | 磁盘 +20GB |
| H2 | `core/utils/datatools.py:306-315` | ~8 | 读盘 38.6s → **2.6s** | 需逐元素对拍 |
| H3 | `core/utils/datatools.py:201-210, 310` | ~6 | 消除静默现算 | 低 |
| H4 | `tools/prewarm_robust40.py:86-115` | ~10 | 预热 −35s（编译前移） | 无 |
| H5 | `tools/robust40/live.py` | ~8 | 重试 54s → 2s | 低 |
| H6 | `tools/robust40/runtime.py` | ~5 | −1.5s | 低 |

H1+H2 兑现"预计算覆盖 ILLQ/CciV3"：`use_precompute` 三重条件（声明命中 / 参数命中 / 列存在）在 arrow 路径下全部满足，`calc_factors_by_candle` 直接取列，不再进 `calc_factor_vals`（`select_coin.py:133-135`）。

## 5. 预热端到端的最佳实践

目标形态：预热不再是"另起一套流程"，而是"在冻结输入上跑一次实盘链路"。

| 阶段 | 现状 | 目标 | 靠哪项 |
|---|---|---|---|
| S0 输入冻结 | `shutil.copy2` 复制 3GB pkl+pivot，前后 mtime/size 比对 | 保留（Windows 无 reflink，硬链接会破坏"防变"校验） | H1 后可增加 arrow 的 copy，体积 +3.3GB |
| S1 行情 | 读 frozen pkl 30s | arrow mmap 2.6s | H1+H2 |
| S2 因子 | 现算 ILLQ/CciV3（串行 86s） | 直接读列，零计算 | H1+H2+H3 |
| S3 选币 | 4 进程各 `read_pickle('all_candle_df_list.pkl')` | 保持不变（page cache 命中后单次 1~2s，收益小、改动大） | — |
| S4 净值/聚合 | 现算 | 不变 | — |
| S5 parent/模型 | 现算 + numba 编译 35s | 编译前移 | H4 |
| S6 衔接实盘 | 全量重跑 54s | 同根短路 | H5 |

落地顺序：**H3 → H1 → H2 → H4 → H5 → H6**。H3 先做是因为它无副作用且能把 R5 的静默问题暴露出来；H1 改完必须先确认 arrow 深度真的等于 14,064（`tail(14100)` 对短于 14,100 的 df 不裁剪），再动 H2。

## 6. 验收

| 项 | 判据 |
|---|---|
| arrow 与 pkl 同源 | 同一 `run_time`，逐 symbol 比对 `(行数, 首行时间, 末行时间)` 全等；`candle_begin_time` 序列全等 |
| 预计算真命中 | 在 `select_coin.py:133` 加临时计数，14 条腿的 ILLQ/CciV3 调用次数为 **0** |
| 结果不变 | 关掉 H1/H2（强制 pkl）与开启（走 arrow）两版，`选币结果.pkl` 逐元素相等、`target_lots` 整数数组全等 |
| 预热可复现 | 同一 stamp 跑两次预热，`parent` / `context` / `选币结果` 逐元素相等 |
| 磁盘 | 单 offset 目录 `du -sh` ≤ 8GB（pkl 5GB + arrow 3.3GB），三 offset ≤ 24GB |
| 回退 | 删除 arrow 目录后，实盘必须自动回退 pkl 且日志有 WARNING，费用回到 38.6s 但不报错 |

## 7. 明确不做

1. **不让 pkl 带因子列**（路线 A）：读盘 +50%，抵消因子收益，且与 `preprocess.py:236` 的既有设计冲突。
2. **不建独立因子归档层**（路线 C）：等 B 跑稳、且 L5 面板需拉长到 400 天以上时再评估。
3. **不加深度断言 / 不改 publish 注释**：已决定不做，风险由 H3 的回退与 R5 的临时计数覆盖。
4. **不改 `S3` 的 `'2026-08-01'` 字面量**：与本文的深度链路无关，会改变 state 体积与 `weekly_mix` 行为，单独评估。
5. **不给选币阶段加共享内存**：改动面覆盖多进程契约，收益在 page cache 下不足 10s。
