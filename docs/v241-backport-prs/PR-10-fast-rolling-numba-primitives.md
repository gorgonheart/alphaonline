# PR-10：通用 Numba / Cython 滚动统计原语库（`core/utils/fast_rolling.py`）

- **拟提交标题**：`perf(utils): add cached Numba/Cython rolling primitives (rank_pct, argmax, exact corr) for factor acceleration`
- **优先级**：P1（供所有策略因子按需选用的通用加速底座）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**（`v2.4.1` 环境已包含 `numba`、`numpy`、`pandas`）

---

## 1. 场景

在实盘和回测的时序因子计算中，大量因子频繁调用 pandas 慢速滚动算子：
- `Series.rolling(n, min_periods=1).rank(ascending=True, pct=True)`（`O(N * n)` 逐窗排序）
- `Series.rolling(n, min_periods=1).apply(lambda x: x.argmax(), raw=True)`（逐窗 Python 回调）
- `s1.rolling(n, min_periods=1).corr(s2)`（多参数循环反复做双序列 NaN 掩码与 Series 包装）

## 2. 目标

在 `core/utils/fast_rolling.py` 中提供一组**与 pandas 语义逐位等价、跨进程可缓存（`@njit(cache=True)`）**的通用滚动原语，只抽取与具体私密因子无关的数学原语供所有策略因子按需调用。

## 3. 问题

`position-mgmt-trading_v2.4.1` 未提供底层滚动加速原语，用户在 `factors/` 中默认写 pandas `rolling().rank()` / `rolling().apply()` / 多参数 `rolling().corr()`。在线上 924 任务剖析中，仅调用 `rolling.rank` 和 `rolling.apply(argmax)` 的几类因子就占去 **`50+` 核秒** CPU。

## 4. 项目文档中的相关分析与设计

- 热点定位：[2026-11-06 因子块因果审查](../2026-11-06-factor-block-causal-review.md)（§4）
- Phase 1 & Phase 4 设计与验证：[2026-11-19 性能优化规划](../2026-11-19-performance-optimization-plan.md)（Phase 1）、[DEVELOPMENT.md](../DEVELOPMENT.md)（2026-11-20 Phase 4 WP1）
- 参考提交：`8b4280c`（新增 `core/utils/fast_rolling.py` 与 `tests/test_p1_hotspot_oracles.py`）、`06c8b15`（新增 `rolling_corr_pandas_exact` 与 `tests/test_p4_optimizations.py`）

## 5. 依赖

- **内部代码依赖**：无（纯增量新增工具模块；不修改任何现有框架主流程，用户因子显式 `import` 即生效）。
- **上游 `alpha` Arrow 依赖**：**无**。
- **回馈裁剪边界**：`alphaonline/core/utils/fast_rolling.py` 中包含的 `_cci_causal_168_*`、`_factor_111_param_nb` 属于特定私有因子核，**回馈 `v2.4.1` 时应剔除**，仅保留以下 4 个完全通用的数学原语：
  1. `rolling_rank_pct(values, n)`（坐标压缩 + Fenwick 树状数组 `O(N log N)`，精确复刻 pandas NaN/`±inf`/平局均分语义）
  2. `rolling_argmax_first(values, n)`（双单调队列 `O(N)`，精确复刻 NumPy `argmax` 首 NaN 优先与首最大值平局语义）
  3. `rolling_corr_pandas_exact(series_x, series_y, param_list)`（预计算一次联合 NaN 掩码与乘积数组，直接驱动 `pandas._libs.window.aggregations.{roll_mean, roll_sum, roll_var}`，与 `Series.rolling().corr()` 保持 `0.0` 逐位与 NaN 掩码一致）
  4. `rolling_corr(x, y, n)`（Numba Welford 滑窗相关性，含相消保护）

## 6. 收益与证据

| 原语 / 典型因子 | 优化前 | 优化后 | 收益与精度 |
| :--- | :--- | :--- | :--- |
| `rolling_rank_pct`（替代 `rolling(n).rank(pct=True)`） | `O(N * n)` 逐窗排序 | `O(N log N)` 树状数组 | 窗口越大加速越显著（`n=240~816` 下约 `5–15×`），全边界精确一致 |
| `rolling_argmax_first`（替代 `rolling(n).apply(argmax)`） | Python 回调 `O(N * n)` | Numba 双单调队列 `O(N)` | 消除 Python 回调，`10×+` 提速，首 NaN/平局精确一致 |
| `rolling_corr_pandas_exact`（Windows `alpha311` 微基准） | `2.892 ms` | `1.810 ms` | **`1.60×`**，常数非零窗口与原版 **`0.0` 逐位零差** |
| 整阶段贡献（与 PR-02/03 合计，Ubuntu 4c8g） | `106.6 s`（零干扰） | `60.9 s` | **`-42.9%`** |

## 7. 代码改动

### 目标文件
- `core/utils/fast_rolling.py`（新增，仅含通用滚动原语）
- `tests/test_fast_rolling_oracles.py`（新增，独立 pandas Oracle 精确对拍）

### 核心改动要点
1. 所有 Numba 核均声明 `@njit(cache=True)`，保证 `spawn` 子进程直接命中磁盘 `.nbc/.nbi` 机器码缓存，不重复编译。
2. 对 `rolling_corr_pandas_exact` 加 `try/except ImportError` 兜底（若未来 pandas 主版本移动内部 `_libs.window.aggregations` 符号，平滑回退原生 `series_x.rolling(n, min_periods=1).corr(series_y)`）。
3. 可顺带提供一行命令行预热入口（如 `python -m core.utils.fast_rolling --warm`），部署后离线预编译缓存。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 风险成因与适配建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 低风险 | 首次运行触发几秒冷编译，次轮起命中磁盘缓存零编译开销。 |
| **2c8g** | 🟡 **中风险（冷启动毛刺）** | 首次调用未预热的 `@njit(cache=True)` 会瞬时占 1–2 核与 `~200–300 MiB` 内存；若 2 个 worker 同时冷编译可能重叠触发缓存竞争。**建议在部署/更新后离线执行一次预热命令**。 |
| **2c4g** | 🟡 **中风险（冷编译 + 缓存目录权限）** | 1. 必须在非交易窗口离线预热，避免整点冷编译拖慢首轮并叠加内存峰值；<br>2. 在容器/只读部署目录运行前需显式设置可写的 `NUMBA_CACHE_DIR`；<br>3. 若用户仅用简单均值/涨跌幅因子且想省去 Numba JIT 内存开销，因本模块为按需 import，不引用即 0 成本。 |
