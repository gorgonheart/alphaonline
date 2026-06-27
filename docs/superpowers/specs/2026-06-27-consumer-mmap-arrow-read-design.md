# 实盘框架（d:/alphaonline）零拷贝 Arrow 读取 设计文档

- 日期: 2026-06-27
- 项目: `d:/alphaonline`（仓位管理实盘框架 / 消费者）
- 配套文档: `d:/alpha/docs/superpowers/specs/2026-06-27-producer-io-and-arrow-publish-design.md`（生产者）
- 状态: 待评审

## 1. 背景与目标

消费者每 5 分钟从数据中心读取 `{symbol: DataFrame}` 批次，用于因子计算和实盘下单。
当前读取路径：`load_bmac_data` glob `{type}_dict_batch*.pkl`，逐文件
`pd.read_pickle`，再 `load_realtime_data` 把合并结果**再序列化**成
`data/cache/all_candle_df_list.pkl` 供多进程 worker 读取——一次多余的序列化往返。

生产者侧将新增"每 symbol 一 record batch + manifest"的 **Arrow IPC 发布文件**
（见配套生产者文档）。本设计让消费者优先**内存映射零拷贝**读取该文件，省掉
pickle 反序列化与 `all_candle_df_list.pkl` 往返，把消费者读取从数十秒降到 ~1–2s，
并保留对旧 pkl/feather 路径的**自动 fallback**。

**目标延迟**：消费者读取阶段 ≤ 数秒；端到端（含生产者优化）达成用户 10–20s 目标。

## 2. 现状读取路径（精确契约）

- **Gate**：`check_bmac_update_flag(run_time)` 轮询
  `{minute}m/{spot,swap}_dict_{ts}.ready`（[datatools.py:45-53](../../../core/utils/datatools.py)）。
- **数据**：`load_bmac_data` glob `{type}_dict_batch*.pkl`，每个是 `{symbol: DataFrame}`
  dict（[datatools.py:191-220](../../../core/utils/datatools.py)）。
- **逐 symbol 处理** `read_and_merge_bmac_data`（[datatools.py:144-188](../../../core/utils/datatools.py)）：
  1. 稳定币 / black_list / white_list 过滤；
  2. `drop_duplicates(candle_begin_time)` + `sort_values`；
  3. 三列 `tz_localize(None)`：`first_candle_time / last_candle_time / candle_begin_time`；
  4. **先**按 `candle_begin_time + utc_offset < run_time` 过滤；
  5. `< min_kline_num` 丢弃；`align_spot_swap_mapping`；
  6. **后** `df[-get_kline_num():]`（4050）→ 这是裁切，也是发布深度需 ≥4050+余量的原因；
  7. `reset_index(drop=True)`。
- 必需列：`symbol, candle_begin_time, first_candle_time, last_candle_time,
  symbol_spot, symbol_swap`, OHLCV, `funding_fee, avg_price_1m, avg_price_5m`, 因子列。
- **另有 pivot 路径**：`read_bmac_market_pivot` 读 `market_pivot_{type}_*.pkl` +
  `check_bmac_pivot_flag`（[datatools.py:56-78](../../../core/utils/datatools.py)）——本设计**不改**。

## 3. 设计

### 3.1 `load_bmac_data` 增加 Arrow 快路径

改造为"先 Arrow、否则 fallback"：

```
hour_offset = f'{run_time.minute}m'
arrow_path  = bmac_data_path / hour_offset / f'{symbol_type}_dict_{ts}.arrow'

if arrow_path.exists():          # 快路径
    data_dict = read_arrow_market_dict(arrow_path)   # mmap 零拷贝重建
else:                             # fallback：兼容旧/新文件格式
    data_dict = read_legacy_batches(symbol_type, hour_offset)  # *.pkl 或 *.fea
```

- `read_arrow_market_dict`：`pa.memory_map(path)` → `ipc.open_file/stream` → 按
  manifest 把 record batch *i* 映射到 symbol → `to_pandas()`（数值列零拷贝）。
  **不做** concat、不做 groupby。
- 重建出的 `{symbol: DataFrame}` 喂给**现有的** `read_and_merge_bmac_data`，
  §2 的逐 symbol 处理逻辑**完全复用，不改**。即 Arrow 只替换"怎么拿到 dict"，
  不改"拿到 dict 之后做什么"，把行为差异降到最小。
- `read_legacy_batches` 同时支持 `.pkl` 与 `.fea`（配合生产者 feather 切换，
  消费者必须**先**支持双格式，再让生产者切 feather）。

### 3.2 消除 `all_candle_df_list.pkl` 往返

当前 `load_realtime_data` 把合并结果写 `data/cache/all_candle_df_list.pkl` 供
worker 读。Arrow 快路径下：
- worker 直接接收 `*.arrow` 文件**路径**，各自 `pa.memory_map` 同一文件
  （页缓存共享，多进程零拷贝，互不复制）；
- 取消中间 `all_candle_df_list.pkl` 的写 + 读。
- fallback 路径保留原 `all_candle_df_list.pkl` 行为，确保旧路不变。

> 需核对 `load_realtime_data` 与下游 worker 的具体传参形态（实现计划阶段细化），
> 确保由"传内存对象/传 pkl 路径"切换为"传 arrow 路径"不破坏 worker 入参。

> **实现期调整（2026-06-27）**：经核对，`all_candle_df_list.pkl` 是
> `mem_save_exe` 的 `max_workers=1` 子进程边界（`load_realtime_data` 在子进程写、
> `core/select_coin.py::calc_factors` 在父进程读），并非纯 in-process 冗余序列化。
> 消除它需改因子计算 worker 边界，风险高于收益，本期**保持不变**，降级为可选后续。
> 本期收益来自 Arrow 替换 pkl 反序列化本身（`load_bmac_data` 快路径）。


### 3.3 一致性与 run_time 校验

- ready 标志语义不变，仍是 gate；生产者已保证 ready 在因子写盘后才打（修复竞态）。
- 消费者读到 `*.arrow` 后，校验 manifest 中记录的 `run_time_ts == 期望 ts`，
  防止读到上一轮残留文件。不一致 → 走 fallback 或按数据未就绪处理。

## 4. 不在本设计范围内

- pivot 读取（`read_bmac_market_pivot` / `check_bmac_pivot_flag`）：保持 pkl。
- exginfo 读取：保持 `snapshot_bmac_exginfo` + `read_bmac_exginfo` 现有机制。
- 因子计算、选币、下单等下游逻辑：完全不动。

## 5. 跨项目契约（消费者依赖 / 生产者承诺）

| 项 | 消费者依赖 |
| --- | --- |
| ready 标志 | `{spot,swap}_dict_{ts}.ready`，且语义为"含因子已就绪" |
| 快路径文件 | `{type}_dict_{ts}.arrow`，每 symbol 一 record batch + manifest（batch idx→symbol, run_time_ts） |
| fallback 文件 | `{type}_dict_batch*.{pkl,fea}` |
| 发布深度 | `tail(publish_kline_num≈5000) ≥ get_kline_num(4050) + run_time 过滤余量` |
| 必需列 | 见 §2，**不可缺列**（尤其 symbol_spot/swap、first/last_candle_time、factor 列） |
| 原子性 | 生产者 tmp→fsync→rename→ready，消费者据此保证读到完整快照 |

**上线顺序（强约束）**：
1. 先上消费者：`load_bmac_data` 支持 `.pkl` + `.fea` + `.arrow` 三路（Arrow 不存在
   时自动 fallback），此时生产者仍输出 pkl，行为不变。
2. 再上生产者：切 feather + Arrow 发布 + 竞态修复。
3. 验证后清理：消费者可下线 pkl 分支（可选）。

> **fallback 的生命周期（重要）**：消费者 fallback 的 `{type}_dict_batch*` glob 依赖
> 生产者的内部批次布局。生产者 Lever 1（base/recent 分层，见生产者文档 §3.1）一旦
> 上线，`batch*` 布局即失效，**此后 Arrow 快路径成为唯一可靠接口，fallback 仅作降级
> 兜底且不再保证命中**。因此里程碑顺序必须是：消费者 Arrow 快路径稳定运行 → 生产者
> 才上 Lever 1。在生产者只做 Lever 2+3（仍单文件批次）期间，fallback 持续有效。

## 6. 风险与回滚

- **格式不同步**：若生产者先切 feather 而消费者只认 pkl → glob 落空。靠上线顺序
  （消费者先双格式）规避。
- **Arrow 列类型**：object/混合类型列 `to_pandas` 可能拷贝或报错。实现阶段对全列
  dtype 做一次校验（与生产者 §3.3 校验同源）。
- **per-symbol dtype 不可复现（已确认，无害）**：单文件 Arrow IPC 要求所有 record
  batch 共用一个 schema，生产者以**首个 symbol** 的 schema 为基准。pkl 路径每 symbol
  独立存储，故能保留 per-symbol dtype。30m 实测：spot 429 symbol 中 2 个
  （REDUSDT/USUALUSDT）的 `trade_num` 在 pkl 为 float64、Arrow 为 int64。根因是
  生产者 `resample_candle.py::fill_gaps` 对**曾有缺口**的 symbol reindex 注入 NaN →
  整数列被提升为 float64，`fillna(0)` 后 dtype 不回落——这是 **legacy pkl 产物**，
  整数列本应是 int64。值**逐行完全相同**（零 NaN、零小数），下游因子运算 int→float
  自动提升，结果不变。结论：literal per-symbol dtype parity 在单文件 Arrow 下**架构
  上不可能**，对账测试据此放宽为"值对账 + dtype 分歧仅限整数值列"。

- **多进程 mmap**：Windows 上同一文件多进程映射只读安全；需确认 worker 生命周期内
  文件不被生产者下一轮覆盖（生产者按 ts 命名，新轮是新文件，旧文件按保留策略清理，
  不会就地覆盖正在读的文件）。
- 全程**加法 + fallback**：Arrow 缺失/损坏自动回退 pkl，最坏退回今天的性能，不影响
  正确性与下单。

## 7. 验收

- `*.arrow` 存在时，`load_bmac_data` 走 mmap 零拷贝，读取阶段墙钟 ≤ 数秒。
- Arrow 重建的 `{symbol: DataFrame}` 经 `read_and_merge_bmac_data` 后，与旧 pkl 路径
  产出**逐 symbol、逐列、逐行一致**（构造对账测试）。
- `all_candle_df_list.pkl` 往返在快路径下消失；worker 直接 mmap。
- 删除/损坏 `*.arrow` 时自动 fallback，结果与当前线上一致。
- run_time_ts 校验拦截上一轮残留文件。
