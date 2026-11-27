# PR-03：时序因子跳 `shift(0)` 拷贝与直建 Polars（消除 pandas 81 列往返）

- **拟提交标题**：`perf(factor): skip zero-shift copies and build Polars frames directly in factor workers`
- **优先级**：P0（强烈推荐首批合入）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**

---

## 1. 场景

`v2.4.1` 在 `calc_factors` 阶段使用多进程对每个币种调用 `process_candle_df_return_polars`，内部遍历该币种的所有时序因子（典型 30~80+ 个因子列）调用 `core/factor.py::calc_factor_vals`，随后将结果转为 `pl.DataFrame` 回传主进程合并。

## 2. 目标

1. 消除 `shift == 0` 时的数万次无意义 `pd.Series.shift(0)` 分配拷贝；
2. 消除逐币种「字典 → 构造 81 列 `pd.DataFrame` → `sort_values` → 布尔切片 → `pl.from_pandas`」的中间 pandas 宽表往返，直接从 NumPy 数组切片构造 `pl.DataFrame`。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/factor.py` 与 `core/select_coin.py`：
1. `core/factor.py:43,50` 在默认 `shift=0` 时仍无条件执行 `factor_series.shift(0).values`，单轮全市场（~1000 币 × ~55 列）产生约 **5.5 万次**无意义的 Series 拷贝。
2. `core/select_coin.py::calc_factors_by_candle` 先把所有因子列装进 `pd.DataFrame`，排序裁切后再在 `process_candle_df_return_polars` 中调用 `pl.from_pandas(factor_df)`；剖析显示仅 `pandas → Polars` 转换在 924 任务中就耗费约 **`15.0` 核秒**，且大幅抬高子进程瞬时内存。

## 4. 项目文档中的相关分析与设计

- 剖析证据：[2026-11-06 因子块因果审查](../2026-11-06-factor-block-causal-review.md)（§4 `pandas→Polars ~15.0 核秒`）、[重要性能分析](../kb/performance-analysis.md)（§2–§3）
- 实施与验收记录：[2026-11-19 性能优化规划](../2026-11-19-performance-optimization-plan.md)（Phase 1）、[DEVELOPMENT.md](../DEVELOPMENT.md)（Phase 4 WP1）
- 参考提交：`8b4280c`（`calc_factors_by_candle_polars`）、`06c8b15`（`core/factor.py` 跳过 `shift(0)`）

## 5. 依赖

- **内部代码依赖**：无（纯 `core/factor.py` 与 `core/select_coin.py` 局部重构）。
- **上游 `alpha` Arrow 依赖**：**无**。
- **兼容性**：保留原 `calc_factors_by_candle` 返回 `pd.DataFrame` 的公开接口；当任一自定义因子返回长度不等、非标准索引或 `CategoricalDtype` 的 `Series` 时，`calc_factors_by_candle_polars` 自动回退到 `_finalize_kline_factor_pandas + pl.from_pandas`，保证 100% 兼容任意第三方因子。

## 6. 收益与证据

| 测量项与口径 | 效果 |
| :--- | :--- |
| `core/factor.py` 跳过 `shift(0)` | 单轮省去约 **5.5 万次** `Series.shift(0)` 拷贝；`tests/test_p4_optimizations.py` 断言 0 次调用 `Series.shift(0)` 且输出 `atol=0.0` 精确一致 |
| `calc_factors_by_candle_polars`（叠加 Phase 1 热点优化） | Ubuntu 4c8g 测试机零干扰 1260 币 / 461 万行：`106.6 s → 60.9 s`（`-42.9%`）；正式机 369 万行 `63.4 s → 47.44 s`（Phase 1 首轮），924 任务 87 份 IPC 逐字节一致 |

## 7. 代码改动

### 目标文件
- `core/factor.py`
- `core/select_coin.py`

### 核心改动要点
1. **`core/factor.py::calc_factor_vals`**：
   - `shift == 0` 时直接取 `factor_series.to_numpy(copy=False)`（非 Series 取 `np.asarray`），仅 `shift != 0` 时调用 `.shift(shift).values`。
2. **`core/select_coin.py`**：
   - 拆分 `_compute_factor_columns_dict(candle_df, conf, factor_col_name_list)`（用 NumPy 切片算 `next_close`，对无缺失 object 字符串列免 `.astype(str)` 拷贝）；
   - 新增 `calc_factors_by_candle_polars`：校验所有列为等长一维数组且时间列为 `datetime64` 后，检测单调有序性（已排序直接 `np.searchsorted` 找起止下标 `k0`，未排序才 `argsort(kind='mergesort')`），直接 `pl.DataFrame(sliced).with_columns(pl.col(pl.Float64, pl.Float32).fill_nan(None))`（对齐 `pl.from_pandas` 对 `NaN→null` 的语义）；遇到异常/异构列无缝回退 pandas 路径；
   - `process_candle_df_return_polars` 直接调用 `calc_factors_by_candle_polars`。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 直接启用，无额外资源成本。 |
| **2c8g** | 🟢 无风险 | 直接启用，CPU 与瞬时内存双降。 |
| **2c4g** | 🟢 **反向降险（推荐必上）** | 消除每个币种中间 81 列 `pd.DataFrame` 与排序副本后，单 worker 瞬时内存峰值更低，非常适合 2c4g；只需保持原有 `job_num=1~2` 与合适的 `factor_col_limit=32~48`。 |
