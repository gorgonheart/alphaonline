# PR-02：`data_bridge` 外部数据源进程内缓存与 NumPy 向量化

- **拟提交标题**：`perf(data_bridge): cache coin_cap/btc files per (path, mtime, size) and vectorize alignment`
- **优先级**：P0（强烈推荐首批合入）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**

---

## 1. 场景

策略使用外部辅助数据因子（如 `CirculatingMcap` 依赖 `coin-cap`，`CorrBTC` 依赖 `coin-btc`）时，因子计算阶段会对全市场 800~1200+ 个现货/合约币种逐一调用 `core/data_bridge.py` 中的 `load_coin_cap()` 与 `load_coin_btc()`。

## 2. 目标

消除全市场循环中对同一 `BTCUSDT.pkl` 与双市场共有币种 `coin_cap/*.pkl` 的重复读盘/去重/排序，并用零拷贝时间戳视图与 NumPy 向量化替代慢速 pandas `merge` / `merge_asof` / `Series.map(lambda ...)`。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/data_bridge.py`：
1. `load_coin_btc` 对每个币种重复执行 `pd.read_pickle('BTCUSDT.pkl')`、`drop_duplicates`、`sort_values`、`tz_localize(None)` 与 `pd.merge`，一轮下来同一个 BTC 文件被读盘和排序上千次。
2. `load_coin_cap` 对现货与合约同名币种重复读盘，每次执行 `pd.merge_asof`、重复 `pd.to_datetime`（pandas 2.2 中单次校验约 `~5.95 ms`），并用 Python 级 `times.map(lambda x: 10 ** np.log10(x).round(0))` 逐行求市值倍率。
3. 线上剖析显示，市值与 BTC 外部数据处理合计曾占时序因子阶段超过 `30+` 核秒。

## 4. 项目文档中的相关分析与设计

- 热点剖析：[2026-11-06 因子块因果审查](../2026-11-06-factor-block-causal-review.md)（§4）、[重要性能分析](../kb/performance-analysis.md)（§3）
- Phase 1 & Phase 4 实施记录：[DEVELOPMENT.md](../DEVELOPMENT.md)（2026-11-20 Phase 4 WP1）、[2026-11-19 性能优化规划](../2026-11-19-performance-optimization-plan.md)
- 参考提交：`8b4280c`（Phase 1：`_BTC_CACHE` + `searchsorted` + `_mode_log10_scale` + `_ffill_1d`）、`06c8b15`（Phase 4：`_to_naive_ns` 零拷贝视图 + `_CAP_CACHE`）

## 5. 依赖

- **内部代码依赖**：无（纯 `core/data_bridge.py` 自包含改造）。
- **上游 `alpha` Arrow 依赖**：**无**（仍读取既有的 `coin_cap/*.pkl` 与 `{exchange}_spot_1h_resample/{offset}/BTCUSDT.pkl` / `BTC-USDT.csv`）。
- **注意事项**：同步保留 `v2.4.1` 中 `pd.to_numeric(extra_df['usd_price'], errors='coerce')` 的脏文本容错，避免非数值 `usd_price` 触发 `astype(float)` 报错。

## 6. 收益与证据

| 测量项与口径 | 优化前 | 优化后 | 收益 |
| :--- | ---: | ---: | :--- |
| `_to_naive_ns`（Windows `alpha311` 微基准，已类型化 `datetime64`） | `5.953 ms` | `0.0117 ms` | **`511×`** |
| `load_coin_btc` 磁盘读盘次数（全市场 1000 币，4 worker） | `~1000 次` | `4 次`（每 worker 1 次） | **减少 `99.6%` 读盘** |
| `load_coin_cap` 同轮 spot/swap 双侧读盘 | 2 次/币 | 1 次/币 | **减少 `50%` 读盘** |
| 叠加 Phase 1+4 对 `FACTOR_BLOCKS` 贡献（4c8g 实盘） | `~63.4 s` | `39–42 s` | **端到端缩短 `~21–24 s`（含 PR-03/PR-10）** |

- **等价验证**：`tests/test_p4_optimizations.py`（`test_wp1_to_naive_ns_avoids_pd_to_datetime_on_typed_series`、`test_wp1_coin_cap_cache_reuses_and_invalidates_on_mtime_or_size`）及 `tests/test_p1_hotspot_oracles.py` 全部通过，缓存按 `(path, mtime_ns, size)` 自动失效。

## 7. 代码改动

### 目标文件
- `core/data_bridge.py`

### 核心改动要点
1. **零拷贝时间视图 `_to_naive_ns(series)`**：对 `DatetimeTZDtype` / `datetime64` 直接取 `.to_numpy(dtype='datetime64[ns]', copy=False).view(np.int64)`，仅非时间类型回退 `pd.to_datetime`。
2. **有界签名缓存**：
   - `_BTC_CACHE`（上限 `_BTC_CACHE_MAX = 8`）：按 `(resolved_path, mtime_ns, size)` 缓存排序后的 `(ts_ns, close_arr)`。
   - `_CAP_CACHE`（上限 `_CAP_CACHE_MAX = 2048`）：按 `(path, mtime_ns, size)` 缓存去重排序后的 `extra_df`。
3. **NumPy 向量化对齐与倍率计算**：
   - `load_coin_cap`：用 `np.searchsorted(extra_ts, candle_ts, side='right') - 1` 替代 `pd.merge_asof`；用 `_mode_log10_scale`（`np.power(10.0, np.round(np.log10(ratio)))` + `np.unique(..., return_counts=True)`）替代 `Series.map(lambda ...)`；用 `_ffill_1d` 替代 `Series.fillna(method='ffill', inplace=True)`（同时消除 pandas FutureWarning）。
   - `load_coin_btc`：用 `np.searchsorted(..., side='left')` 精确匹配时间戳 + `_ffill_1d` 替代 `pd.merge(..., how='left')`。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 默认 `_CAP_CACHE_MAX = 2048`、`_BTC_CACHE_MAX = 8` 即可。 |
| **2c8g** | 🟢 低风险 | 建议 `_CAP_CACHE_MAX = 1024`。因子 worker 进程在 `calc_factors` 结束后立即销毁，不会跨轮常驻泄漏。 |
| **2c4g** | 🟡 **中风险（需调小缓存上限）** | `_CAP_CACHE` 若在多 worker 中各缓存上千张 `coin_cap` DataFrame，可能给每个 worker 增加 `~80–150 MiB` 瞬时驻留内存。建议将 `_CAP_CACHE_MAX` 设为可配置项，在 2c4g 上设为 **`256–512`**（或仅保留 `_BTC_CACHE` 与向量化算子，关闭 `_CAP_CACHE`），同时 `job_num` 控制在 `1–2`。 |
