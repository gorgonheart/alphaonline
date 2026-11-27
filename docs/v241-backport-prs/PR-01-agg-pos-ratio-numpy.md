# PR-01：`agg_pos_ratio` 仓位聚合列连续 NumPy 向量化

- **拟提交标题**：`perf(backtest_config): vectorize agg_pos_ratio with column-contiguous NumPy accumulation`
- **优先级**：P0（强烈推荐首批合入）
- **上游 `alpha` Arrow 依赖**：**无**（100% 独立于数据源格式）
- **其他 PR 依赖**：**无**

---

## 1. 场景

多子策略组合（仓位管理）在 `calc_ratios` 计算出各子策略资金权重 `pos_ratio` 后，调用 `MultiEquityBacktestConfig.agg_pos_ratio(pos_ratio)` 将所有子策略的现货/合约选币权重表按时间轴与币种并集加权汇总。子策略越多、历史时间轴越长（如 14 子策略 × 4050~14050 小时 × 300~500 币种），该环节开销越显著。

## 2. 目标

在保证输出 DataFrame（列并集排序、索引顺序、`columns.name`、`NaN` 语义、缺失索引抛 `KeyError`）与 `v2.4.1` 原版 **100% 逐位一致**的前提下，消除重复宽表对齐与拷贝开销，同时降低峰值内存。

## 3. 问题

`position-mgmt-trading_v2.4.1/core/model/backtest_config.py` 原实现在循环内对每个子策略重复执行：
```python
spot_ratio = df_spot_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)
df_spot_ratio_sum = df_spot_ratio_sum.add(spot_ratio, fill_value=0)
```
这会导致：
1. 每次 `.loc[pos_ratio.index, :]` 即使索引完全一致也会做一次完整宽表拷贝；
2. 每次 `.mul(...)` 分配一张临时宽表；
3. 连续 14 次 `DataFrame.add(..., fill_value=0)` 反复重建联合列索引并做整表拷贝，产生大量内存碎片与 GC 压力。

## 4. 项目文档中的相关分析与设计

- 实时剖面定位：[2026-11-23 Phase 5 规划](../2026-11-23-phase5-and-v202-migration-plan.md)（§0.1、§子项目 2A `P5-D1`）
- 详细设计与边界证明：[2026-11-23 Phase 5 (2A) 设计文档](../superpowers/specs/2026-11-23-phase5-2a-low-concurrency-design.md)（§5.1 `P5-D1`）
- TDD 实施计划：[2026-11-23 Phase 5 (2A) 实施计划](../superpowers/plans/2026-11-23-phase5-2a-low-concurrency.md)（Task 1）
- 参考提交：`52e5c86`（主实现）、`0e9b8bd`（0 列侧缺失索引保留 `KeyError`）、`2001075`（保留 `columns.name`）

## 5. 依赖

- **内部代码依赖**：无（仅需标准 `numpy` / `pandas`）。
- **上游 `alpha` Arrow 依赖**：**无**。
- **兼容性**：保持 `agg_pos_ratio(self, pos_ratio)` 公共签名与异常语义完全不变。

## 6. 收益与证据

| 测量环境与口径 | 优化前 | 优化后 | 收益 |
| :--- | ---: | ---: | :--- |
| Windows `alpha311` 合成基准（14 × 14050 × 400） | `5.35 s` | `1.17 s` | **`-78.1%`（`4.57×`）** |
| Ubuntu 4c8g 测试机真数离线对拍（14 子策略） | `1.91 s` | `0.79 s` | **`-58.6%`（`2.42×`）** |
| Ubuntu 4c8g 测试机自然轮（`完成仓位限制` 整段） | `6.11 s` | `3.16 s` | **净省 `2.95 s`（`-48.3%`）** |

- **精度验证**：`tests/test_phase5_optimizations.py` 使用原版 `DataFrame.add(fill_value=0)` 作独立 Oracle，覆盖 14 策略稀疏重叠列、非单调子集索引、单策略保序、0 列单市场、复杂 `NaN` 与缺失列交互、缺失时间索引抛 `KeyError` 与 `columns.name='symbol'`，全部 `check_exact=True`（随机浮点累加 `atol=1e-15`）。

## 7. 代码改动

### 目标文件
- `core/model/backtest_config.py`（对应 `v2.4.1` 的 `MultiEquityBacktestConfig.agg_pos_ratio`）

### 核心改动要点
1. 新增 `@staticmethod _agg_side_ratios(side_frames, pos_ratio, target_index)`：
   - 迭代 `all_cols = all_cols.union(df.columns)` 并保留 `columns.name`，复刻连续 `DataFrame.add` 的列排序并集规则；
   - 预分配转置连续数组 `acc = np.zeros((n_cols, n_rows), dtype=np.float64)`，使每列累加 `acc[c] += arr[:, j] * w` 为连续内存访问，避免花式索引散写拷贝；
   - 索引相等时零拷贝 `to_numpy(copy=False)`，唯一索引走 `get_indexer`，缺失索引回退 `df.loc[out_index, :]` 保留原始 `KeyError`；
   - 无 `NaN` 走快路；遇 `NaN` 懒分配 `seen_finite` / `seen_nan` 掩码精确复刻 `NaN.add(missing, fill_value=0) == NaN` 与 `NaN.add(finite, fill_value=0) == finite` 语义；
   - 最终返回 `pd.DataFrame(acc.T, index=out_index, columns=all_cols)`。
2. `agg_pos_ratio` 分别对 spot、swap 侧调用 `_agg_side_ratios`。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 默认直接启用，单线程纯 NumPy 运算，零并发开销。 |
| **2c8g** | 🟢 无风险 | 默认直接启用，消除 14 次 pandas 中间宽表拷贝后峰值内存显著下降。 |
| **2c4g** | 🟢 **反向降险（推荐必上）** | 在 2c4g 上比原版更省内存：仅需预分配 1 张 `(n_cols, n_rows)` `float64` 矩阵（例如 400 币 × 4050 行约 `12.4 MiB`），避免原版多次 `DataFrame.add` 产生的瞬时双倍/三倍内存抖动。无需任何降配参数。 |
