# WS+grace14 与老纯 REST 的差别：差在"写盘语义"，不在 grace（2026-11-24）

> 对比对象：正式机 `alpha2`（纯 REST，`rest_fetch_delay_seconds=3.0`，`once_update_candles=99`）与测试机 `alpha`（WS + barrier + REST 补漏，`ws_wait_sec=10`）。
> 采样轮次：`20:55`（prod）与 `20:45`（test），均以 5m bar 闭合为 `+0 s`。只读采集，未改动任何配置。

---

## 1. 实测时间线

| 环节 | 纯 REST（prod `20:55`） | WS（test `20:45`） | 差 |
| --- | --- | --- | --- |
| 抓取 / 接收开始 | `+3.0 s`（硬编码延迟） | `+0.1 ~ 4.0 s`（WS 推送入队） | — |
| barrier 全量就绪 | 无此环节 | `+12.5 s`(spot) / `+14.5 s`(swap) | — |
| base 全量落盘 | `+23.5 s` / `+28.2 s`（含内联 resample） | 同上（= barrier 结束） | — |
| 1h resample | 内存内联，已含在上面 | **独立读盘 pass**：`29.8 s` / `32.5 s` | — |
| **Arrow 发布** | **`+28.6 s`** | **`+47.8 s`** | WS 慢 `19.2 s` |
| spot pivot 完成 | `+80.9 s` | `+81.2 s` | 持平 |
| swap pivot 完成 | **`+102.5 s`** | **`+145.3 s`** | WS 慢 `42.8 s` |
| 今日修订轮次 | **64/72 `replay`**，`changed_series` p50 `25`、max `146` | **0 轮**（字段不出现） | — |

结论先说：**WS 链路在"发布时延"上更慢，在"数据稳定性"上更好**。而 grace 改成 14 只影响第二行到第三行之间那 `1–2 s`，不改变这张表的量级。

---

## 2. 真正的分野：每轮重写 99 根 bar vs 只写 1 根

两条链路最终都走同一个 writer，但喂进去的数据量差 99 倍：

| | 纯 REST | WS |
| --- | ---: | ---: |
| 每 symbol 每轮提交的 bar 数 | **`99`**（`once_update_candles`，见 `core/handle.py:96`） | **`1`**（WS 闭合 K） |
| 每轮写盘行数（1,287 symbol） | **≈ 127,000 行** | **1,287 行** |
| 是否回看历史 bar | 是，每轮重写最近 ~8.25 h 窗口 | 否，写一次就不再碰 |
| 命中已存在 bar 时 | `_merge` 用新抓的值覆盖（`rest` prio 2，同 prio 覆盖自己） | 不会发生 |

`core/pipeline/data_update.py:291` 是这 99 的来源：

```python
params_list = [{'symbol': s, 'interval': base_interval, 'limit': once_update_candles} for s in symbols_trading]
```

`once_update_candles = 99` 是给"补历史缺口 / 自我修复"用的，但**没有任何"只写新行"的过滤**——抓回来 99 根，就全部交给 writer 重写。于是：

- **任何一根落在 8 h 窗口内的 bar，只要这次抓到的值和库里不同，就会变成 consumer 侧的一次 `changed_series`**；
- 最新那根 bar 在 `+3 s` 抓取时往往还没定型（Binance 侧还有零星成交/结算），下一轮再抓到"完整版" → 覆盖 → 修订；
- 72 轮里 64 轮 `replay`，`changed_series` 中位 25 个 series，正是"每轮都有几根 bar 微小漂移"的形态。

WS 链路相反：闭合 K 只推一次、只写一次，之后再也不回看，所以 `changed_series` 字段压根不出现（20/20 `incremental`）。

**这条差别与 grace 无关，与机器快慢无关，是写入语义决定的。**

---

## 3. 顺带解释了 REST 为什么慢

- 每轮 `99 × 1,287 ≈ 127k` 根 K 线的 HTTP 载荷（按每根 ~100 B 估，约 `12 MB/轮`），prod 每 10 分钟一轮；
- 单 symbol 落地成本两者一致：prod `25.1 s / 767 = 33 ms`，test 补漏 `5.2 s / 170 = 31 ms`——同机器上一个量级，说明代价主要由"99 根"决定，不是网络抖动；
- WS 补漏也是 99 根（走同一个 `fetch_update_resample`），所以**补 170 只就要往 writer 队列塞 ≈ 16,800 行**——这是 grace 偏短时的隐性成本，之前只算了 API 时间。

---

## 4. 回答两个问题

### 4.1 grace 改到 14 后，与老纯 REST 有多大区别

| 维度 | 结论 |
| --- | --- |
| 数据内容 | 同一根 bar 的 OHLCV 一致（已用 `select_coin` / `position_results` 逐组核验过，258/258、271/271 组零差） |
| 发布时延 | WS 慢 `19 s`（Arrow `+47.8` vs `+28.6`），swap 就绪慢 `30 s`；**不进 `total_s` 的原因不是"数据陈旧"，而是 `total_s` 从 worker 启动才起算——这 19–43 s 进的是下单时点（见 §4.3）** |
| 修订 / replay | REST `64/72` 轮 replay；WS `0/20`。这是两者唯一的实质差距 |
| 上游资源 | REST 每轮 12 MB + 127k 行写盘；WS 常态 0 API、1,287 行写盘 |
| 故障面 | REST 单源，挂了就缺；WS 多了代理（`7897`）、订阅重建、掉线轮（`18:15` 全缺） |

一句话：**grace 14 不会让你"更像 REST"，它只是让 WS 链路的 REST 兜底降到 0；两者真正的差距是 99 根重写 vs 1 根 write-once。**

### 4.2 "REST + 为 REST 优化的 grace" 能不能等效

**只调延迟，不等效。** `rest_fetch_delay_seconds` 3 → 13 只解决"最新那根 bar 有没有定型"，但 99 根窗口照旧每轮重写：窗口内任意一根 bar 与库里不同就会记一次修订，`changed_series` 不会归零。收益是"把最新 bar 的漂移压掉"，属于从 25 降到个位数量级，不是从 25 降到 0。

**改成 write-once 才等效，而且比 WS 更好。** 具体是把 `once_update_candles` 从"写入窗口"改成"抓取窗口"：

- 抓取仍可取 99 根（保留自愈能力），但**只把库里没有的 bar 交给 writer**（`candle_begin_time > 已存最后一根`）；
- 效果：`changed_series` 结构与 WS 一致（新 bar 只写一次）；写盘量从 127k 行降到 ~1.3k 行；HTTP 可同步降到 `limit=2~3`，fetch 从 `20–25 s` 降到个位数秒；
- 代价：失去"8 h 窗口每轮自愈"，需要单独的修补路径（例如每小时一次全窗口校验，或保留现有 `gap` 标记 + 启动补齐）。

这条路线的发布时延预计 `+15 s` 左右，**同时优于现在两条链路**，且不需要 WS 底座、不需要 barrier、不需要代理、没有双写。

### 4.3 消费端读的是哪根 K 线：`cutoff` 是 begin_time，不是"陈旧 1 小时"

结论：**读的是 `run_time` 这一刻刚刚闭合的那根 1h K 线**，不是上一小时那根。

- `stamp = int(run_time.timestamp())`，`run_time` 对齐 5 分钟网格（如 `20:25:00`）；
- 期望状态时间轴 `cutoff = stamp − 1h`（`tools/robust40/server.py` 的 `inspect(..., stamp-1h)`），记录里 `cutoff` 是 UTC 的 `candle_begin_time`；
- 25m 账户的 1h 轴以 `:25` 为界，故 `cutoff = 19:25` 的 bar 区间是 `[19:25, 20:25)`，**正好在 `run_time=20:25` 闭合**；
- 发布侧同名戳印证：`spot_dict_/swap_dict_/market_pivot_*_{stamp}.ready` 的 `stamp` 就是闭合时刻（`1795523100 = 20:25:00`），内容到 `begin_time = stamp − 1h`。

历史窗口是 `cutoff − 168 h`（`REVISION_HISTORY_HOURS=168`，本轮 `input_start = 11-17 11:25` → `cutoff = 11-24 11:25`），即"最近 7 天 + 刚闭合那根"。

**因此"不陈旧"，但轮次会等发布**。`startup.py` 先 `check_bmac_update_flag(run_time)` 轮询 `spot_dict_/swap_dict_{stamp}.ready`（1 s 一次，最多等到 `run_time+4 min`），再 `Coordinator.command()` 轮询四个 `.ready`（含两个 pivot，上限 `DATA_READY_WAIT_SECONDS=240`）。实测：

| 观测（`stamp = 20:25:00` 同一切点） | 正式机 | 测试机 |
| --- | ---: | ---: |
| `spot_dict_` / `market_pivot_spot_` 就绪 | `+84 s` | `+81 s` |
| `swap_dict_` / `market_pivot_swap_` 就绪 | **`+113 s`** | **`+143 s`** |
| worker 启动滞后（今日 12 轮中位） | **`112.7 s`** | **`148.0 s`** |
| `total_s`（incremental 同口径） | `111.5`（n=2） | `103.1`（n=12） |
| **下单完成 = 启动滞后 + `total_s`** | **`+226.9`** | **`+251.8`** |

口径修正：`total_s` 由 `telemetry.round_log` 在 worker 进程内起算，**卡在发布之前的等待完全不计入**——这才是"不进 `total_s`"的真实原因。但它是真实墙钟代价：同 mode 下测试机比正式机晚 **25 s** 完成（+251.8 vs +226.9），差距 ≈ swap 就绪晚 `30 s` 减去计算快 `8 s`。差在 swap 一侧，spot 几乎相同（`+81` vs `+84`）。

顺带一个风险：`check_flags` 的硬上限是 `run_time+4 min`，测试机常态已用掉 `148/240 s`，余量约 `90 s`；正式机约 `127 s`。

---

## 5. 建议

**先做 1，再决定 2/3。**

1. **验证实验（成本最低，能直接定论）**：在测试机上把 REST 路径跑一轮 A/B——`use_websocket=false`、`rest_fetch_delay_seconds=13`、`once_update_candles=2`，跑 12 轮看 `changed_series`。
   - 若 `changed_series` 字段消失（= 0 修订）→ 说明 WS 的唯一优势可以用 3 行改动换到，**正式机不需要上 WS**；
   - 若仍有修订 → 说明差异来自 Binance 侧的历史 bar 修订，WS 的 write-once 才是唯一解。
   - 该实验需要重启测试机生产者，属于需要授权的动作，未执行。
2. **若要做 write-once**：改 `fetch_update_resample` 的写入口过滤（只提交新 bar），`once_update_candles` 降到 2–3，保留一个低频全窗口校验任务做自愈。收益：replay 归零 + 时延反超 + 写盘量降 50 倍。
3. **若仍决定推广 WS**：`ws_wait_sec=14`（见 [grace 参数取值分析](2026-11-24-grace-tuning-analysis.md)），并接受 swap 就绪从 `+113 s` 推到 `+143 s`（**下单时点晚约 25 s**，见 §4.3；不是"对消费端无影响"）。此时**必须**同步把 REST 兜底改成 write-once，否则补漏的 170 只会往队列塞 1.7 万行。

---

## 6. 数据来源

| 项目 | 来源 |
| --- | --- |
| 时间线 | `alpha2/logs/realtime_data.log`（`20:55` 轮）、`alpha/logs/realtime_data.log` + `realtime_data-worker-2026-11-24.log`（`20:45` 轮） |
| 修订分布 | `alphatrade/logs/server_robust40.log`（prod 今日 72 轮）、`alphatrade/reports/startup.log`（test） |
| 写入窗口 | `core/handle.py:96`（`once_update_candles=99`）、`core/pipeline/data_update.py:291-302` |
| 覆盖优先级 | `core/storage/writer.py:30`（`_SOURCE_PRIO`）、`_merge()` 的 `keep="last"` |
