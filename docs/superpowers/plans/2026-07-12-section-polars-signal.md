# Section signal_polars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `signal_polars` to `sections/动态回撤阈值.py` and `sections/固定回撤阈值触发币数占比开仓门槛.py` so accounts using them take the fast `calc_cross_sections_polars` branch instead of falling back to the pandas path.

**Architecture:** Both factors are pure cross-sectional (grouped only by `candle_begin_time`, no cross-time state), so each maps directly onto Polars window expressions (`.over('candle_begin_time')`) and stays fully lazy — the TopKTo2K/RankBias pattern, not the VolumeMaxBias官方 collect→pandas adapter (that pattern is only needed for numba state machines). A new parity test asserts each `signal_polars` is byte-equivalent to its pandas `signal` oracle.

**Tech Stack:** Python, Polars (LazyFrame), pandas, pytest, conda env `alpha311`.

## Global Constraints

- Run everything under conda env `alpha311`: `conda run -n alpha311 --no-capture-output python -m pytest ...` (the `alpha` env fails on `numpy._core`).
- All comments/docstrings in Chinese, matching existing section files.
- `signal_polars` signature is fixed by `calc_cross_sections_polars`: `signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame`. It receives the full-history LazyFrame (base `KLINE_COLS` + dependency factor columns already attached) and must return the same frame with one column named exactly `factor_name` added, row order preserved.
- Strict parity with the pandas `signal` (the oracle), including how NaN/null and non-finite values are handled.
- `pl.Expr.std()` defaults to `ddof=1`, matching pandas `groupby(...).transform('std')` — do not pass `ddof=0`.
- Dispatch in `calc_cross_sections` (select_coin.py:456) is a pure `hasattr(factor, 'signal_polars')` check across all configured sections; adding the method is sufficient to flip the branch. Do NOT modify the dispatch logic.
- Keep the 邢不行 license header on files that already have it. `动态回撤阈值.py` has it; `固定回撤阈值触发币数占比开仓门槛.py` does not — do not add one.

---

### Task 1: Parity test harness + first factor (动态回撤阈值)

**Files:**
- Create: `tests/test_section_polars_parity.py`
- Modify: `sections/动态回撤阈值.py` (add `signal_polars`, add `import polars as pl`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `sections/动态回撤阈值.signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame` — `params` is the int window `n`; adds column `factor_name` = cross-sectional z-score of `当前回撤_{n}`.
  - Test helper `tests/test_section_polars_parity.py::_run_parity(module, params, factor_name, df) -> None` used by Task 2. It writes `df` to a temp parquet, runs the pandas `signal` on a fresh copy (oracle) and `signal_polars` on `pl.scan_parquet(tmp)` (fast path), collects, and asserts the two `factor_name` columns are equal.

- [ ] **Step 1: Write the failing test**

Create `tests/test_section_polars_parity.py`:

```python
"""截面 section 的 signal_polars 与 pandas signal（oracle）严格等价对账。

纯合成、确定性输入：不依赖数据中心快照。合成帧先落地为 parquet 再用
pl.scan_parquet 读入，复现生产路径（calc_cross_sections_polars）里 NaN→null 的
表示，把「依赖列到底是 nan 还是 null」这一 parity 风险钉死。oracle 口径：以
pandas signal 为准。"""
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

# tests 自插 repo root（仓库无 conftest.py / pytest.ini）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_section(mod_name):
    return importlib.import_module(f'sections.{mod_name}')


def _run_parity(module, params, factor_name, df, tmp_path):
    """df: 含所有依赖列的 pandas 帧。落地 parquet→两路计算→对账 factor 列。"""
    pq = tmp_path / 'base.parquet'
    df.to_parquet(pq)

    # oracle：pandas signal 在独立副本上计算（df_pd 从同一 parquet 读回，保证输入一致）
    df_pd = pd.read_parquet(pq)
    out_pd = module.signal(df_pd.copy(), params, factor_name)
    expected = out_pd[factor_name].reset_index(drop=True)

    # fast path：signal_polars 在 scan_parquet lazy 上计算
    lf = pl.scan_parquet(pq)
    out_pl = module.signal_polars(lf, params, factor_name).collect()
    got = out_pl[factor_name].to_pandas()

    # 数值列对齐 dtype 后逐格比较；NaN 位置须一致，inf 一致
    exp = expected.astype('float64').to_numpy()
    gv = got.astype('float64').to_numpy()
    en, gn = np.isnan(exp), np.isnan(gv)
    assert np.array_equal(en, gn), (
        f'{factor_name} NaN 位置不一致 mismatch={int((en ^ gn).sum())}')
    ev = np.where(en, 0.0, exp)
    gg = np.where(gn, 0.0, gv)
    assert np.allclose(ev, gg, equal_nan=False, rtol=0, atol=1e-12), (
        f'{factor_name} 值不一致 max|diff|={np.abs(ev - gg).max():.3e}')


def _zscore_frame():
    """两个截面：t0 正常离散、t1 全相等（std=0→z 分母 0，pandas 得 NaN→fillna 0）。
    含一个 LUNA 式 NaN 依赖值，触发 nan→null 归一路径。"""
    rows = []
    # t0：4 个 symbol，当前回撤_24 离散
    for sym, dd in [('A', -0.10), ('B', -0.20), ('C', -0.05), ('D', np.nan)]:
        rows.append(('2024-01-01 00:00:00', sym, dd))
    # t1：3 个 symbol，当前回撤_24 全相等（std=0）
    for sym in ['A', 'B', 'C']:
        rows.append(('2024-01-01 00:30:00', sym, -0.15))
    df = pd.DataFrame(rows, columns=['candle_begin_time', 'symbol', '当前回撤_24'])
    df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'])
    df['is_spot'] = 0
    return df


def test_动态回撤阈值_parity(tmp_path):
    mod = _load_section('动态回撤阈值')
    df = _zscore_frame()
    _run_parity(mod, 24, '动态回撤阈值_24', df, tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n alpha311 --no-capture-output python -m pytest tests/test_section_polars_parity.py::test_动态回撤阈值_parity -v`
Expected: FAIL — `AttributeError: module 'sections.动态回撤阈值' has no attribute 'signal_polars'`.

- [ ] **Step 3: Add `signal_polars` to `sections/动态回撤阈值.py`**

Add `import polars as pl` after the module docstring (top of file, before `def signal`). Then append:

```python
def signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame:
    """Polars 版本：当前回撤 的截面 z-score。

    与 pandas signal 严格等价：按 candle_begin_time 求 mean/std（ddof=1），
    z=(x-mean)/std，再 fillna(0)。入口用 fill_nan(None) 把浮点 nan 归一为 null，
    使 mean/std 的 skipna 语义与 pandas transform('mean'|'std') 一致。
    """
    n = params
    col = f'当前回撤_{n}'
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    lf = lf.with_columns([
        pl.col('_dd').mean().over('candle_begin_time').alias('_mean'),
        pl.col('_dd').std().over('candle_begin_time').alias('_std'),
    ])
    lf = lf.with_columns(
        ((pl.col('_dd') - pl.col('_mean')) / pl.col('_std')).alias('_z')
    )
    # fillna(0)：nan/null→0（inf 若出现则保留，与 pandas 一致）
    lf = lf.with_columns(pl.col('_z').fill_nan(0).fill_null(0).alias(factor_name))
    lf = lf.drop(['_dd', '_mean', '_std', '_z'])
    return lf
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 --no-capture-output python -m pytest tests/test_section_polars_parity.py::test_动态回撤阈值_parity -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_section_polars_parity.py sections/动态回撤阈值.py
git commit -m "feat: 动态回撤阈值 增加 signal_polars（截面 z-score，pandas 对账通过）"
```

---

### Task 2: Second factor (固定回撤阈值触发币数占比开仓门槛) + dead-code removal

**Files:**
- Modify: `sections/固定回撤阈值触发币数占比开仓门槛.py` (add `signal_polars`, add `import polars as pl`, delete dead `filter_dependencies_as_conditions = False` line)
- Modify: `tests/test_section_polars_parity.py` (add test case)

**Interfaces:**
- Consumes: `_run_parity` and `_load_section` from Task 1.
- Produces: `sections/固定回撤阈值触发币数占比开仓门槛.signal_polars(lf, params, factor_name) -> pl.LazyFrame` — `params` is `(window, threshold, max_pct)`; adds int column `factor_name` ∈ {0,1}, breadth-gated per cross-section.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_section_polars_parity.py`:

```python
def _breadth_frame():
    """两个截面，含现货域/交易域过滤 + 触发数占比门槛边界。
    max_pct=0.5：t0 domain 池=4，max_count=ceil(4*0.5)=2；触发 2 只 <=2 → 命中。
    t1 domain 池=4，触发 3 只 >2 → 全 0。含 is_spot=1 与空 symbol 行验证被剔除。"""
    rows = []
    # (time, symbol, dd, 是否交易, is_spot, symbol_spot, symbol_swap)
    t0 = '2024-01-01 00:00:00'
    rows += [
        (t0, 'A', -0.30, 1, 0, 'A-SPOT', 'A-SWAP'),   # 触发 (<-0.2)
        (t0, 'B', -0.25, 1, 0, 'B-SPOT', 'B-SWAP'),   # 触发
        (t0, 'C', -0.10, 1, 0, 'C-SPOT', 'C-SWAP'),   # domain 内，不触发
        (t0, 'D', -0.05, 1, 0, 'D-SPOT', 'D-SWAP'),   # domain 内，不触发
        (t0, 'E', -0.99, 1, 1, 'E-SPOT', 'E-SWAP'),   # is_spot=1，被剔除
        (t0, 'F', -0.99, 1, 0, '', ''),               # 空 symbol，被剔除
    ]
    t1 = '2024-01-01 00:30:00'
    rows += [
        (t1, 'A', -0.30, 1, 0, 'A-SPOT', 'A-SWAP'),   # 触发
        (t1, 'B', -0.25, 1, 0, 'B-SPOT', 'B-SWAP'),   # 触发
        (t1, 'C', -0.28, 1, 0, 'C-SPOT', 'C-SWAP'),   # 触发 → 共3只>2 → 全0
        (t1, 'D', -0.05, 1, 0, 'D-SPOT', 'D-SWAP'),   # domain 内，不触发
    ]
    df = pd.DataFrame(rows, columns=[
        'candle_begin_time', 'symbol', '当前回撤_24',
        '是否交易', 'is_spot', 'symbol_spot', 'symbol_swap'])
    df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'])
    return df


def test_固定回撤阈值触发币数占比开仓门槛_parity(tmp_path):
    mod = _load_section('固定回撤阈值触发币数占比开仓门槛')
    df = _breadth_frame()
    _run_parity(mod, (24, -0.2, 0.5), '固定回撤阈值触发币数占比开仓门槛_(24, -0.2, 0.5)',
                df, tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n alpha311 --no-capture-output python -m pytest "tests/test_section_polars_parity.py::test_固定回撤阈值触发币数占比开仓门槛_parity" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'signal_polars'`.

- [ ] **Step 3: Edit `sections/固定回撤阈值触发币数占比开仓门槛.py`**

Delete the dead line `filter_dependencies_as_conditions = False` (line 4 — grep confirms no code reads this attribute anywhere). Add `import polars as pl` next to `import numpy as np`. Append:

```python
def signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame:
    """Polars 版本：固定回撤阈值触发币数占比开仓门槛。

    与 pandas signal 严格等价：domain 域内按 candle_begin_time 统计触发币数占比，
    仅当本截面触发数 <= ceil(池数 * max_pct) 时，触发币记 1，其余记 0（无 null）。
    入口 fill_nan(None) 使 is_not_null() 与 pandas notna() 语义一致。
    """
    window, threshold, max_pct = params
    col = f'当前回撤_{window}'
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    domain = (
        pl.col('_dd').is_not_null()
        & (pl.col('是否交易') == 1)
        & (pl.col('is_spot') == 0)
        & (pl.col('symbol_spot') != '')
        & (pl.col('symbol_swap') != '')
    )
    trigger = domain & (pl.col('_dd') < threshold)
    lf = lf.with_columns([domain.alias('_domain'), trigger.alias('_trigger')])
    lf = lf.with_columns([
        pl.col('_domain').cast(pl.Int64).sum().over('candle_begin_time').alias('_pool'),
        pl.col('_trigger').cast(pl.Int64).sum().over('candle_begin_time').alias('_trig_cnt'),
    ])
    lf = lf.with_columns((pl.col('_pool') * max_pct).ceil().alias('_maxc'))
    lf = lf.with_columns(
        pl.when(pl.col('_trigger') & (pl.col('_trig_cnt') <= pl.col('_maxc')))
        .then(1).otherwise(0).cast(pl.Int64).alias(factor_name)
    )
    lf = lf.drop(['_dd', '_domain', '_trigger', '_pool', '_trig_cnt', '_maxc'])
    return lf
```

Note on parity: the pandas `signal` initializes `df[factor_name] = 0` (int) then sets 1 on the gated mask, so the oracle column is int 0/1. `_run_parity` casts both sides to float64 before comparison, so int-vs-int64 dtype differences are absorbed.

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 --no-capture-output python -m pytest "tests/test_section_polars_parity.py::test_固定回撤阈值触发币数占比开仓门槛_parity" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_section_polars_parity.py "sections/固定回撤阈值触发币数占比开仓门槛.py"
git commit -m "feat: 固定回撤阈值触发币数占比开仓门槛 增加 signal_polars + 删死配置"
```

---

### Task 3: Dispatch verification

**Files:**
- Modify: `tests/test_section_polars_parity.py` (add dispatch test)

**Interfaces:**
- Consumes: `FactorHub.get_by_name` from `core.utils.factor_hub`.
- Produces: nothing (final task).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_section_polars_parity.py`:

```python
def test_both_sections_detected_by_dispatch():
    """calc_cross_sections 用 hasattr(factor,'signal_polars') 决定走 Polars 快路径。
    两个 section 现在都应被识别，避免因缺实现整账户退回 pandas 慢路径。"""
    from core.utils.factor_hub import FactorHub
    for name in ['动态回撤阈值', '固定回撤阈值触发币数占比开仓门槛',
                 'TopKTo2K', 'RankBias']:
        factor = FactorHub.get_by_name(name)
        assert hasattr(factor, 'signal_polars'), f'{name} 缺少 signal_polars'
```

- [ ] **Step 2: Run test to verify it passes** (this one should pass immediately once Tasks 1-2 are done — it verifies the integration)

Run: `conda run -n alpha311 --no-capture-output python -m pytest tests/test_section_polars_parity.py::test_both_sections_detected_by_dispatch -v`
Expected: PASS.

- [ ] **Step 3: Run the whole new test file**

Run: `conda run -n alpha311 --no-capture-output python -m pytest tests/test_section_polars_parity.py -v`
Expected: 3 PASS (two parity + one dispatch).

- [ ] **Step 4: Run the full suite to confirm no regressions**

Run: `conda run -n alpha311 --no-capture-output python -m pytest tests/ -q`
Expected: all pass except the known data-driven `test_arrow_pkl_merge_parity[swap]/[spot]` (pre-existing, unrelated — documented in CLAUDE.md).

- [ ] **Step 5: Commit**

```bash
git add tests/test_section_polars_parity.py
git commit -m "test: 校验两个回撤 section 被 signal_polars 分派识别"
```

---

## Self-Review

**Spec coverage:**
- 动态回撤阈值 signal_polars → Task 1. ✓
- 固定回撤阈值触发币数占比开仓门槛 signal_polars → Task 2. ✓
- Delete dead `filter_dependencies_as_conditions` → Task 2 Step 3. ✓
- Parity test (oracle-equivalence) → Tasks 1-2. ✓
- std=0 edge case → Task 1 `_zscore_frame` t1 (all-equal cross-section). ✓
- nan→null representation risk → covered by parquet round-trip in `_run_parity` + LUNA-style NaN row + domain filter rows. ✓
- Dispatch enters Polars branch → Task 3. ✓

**Deviations from spec (flagged for reviewer):**
1. Spec proposed skipif-gated real-data test with dynamic snapshot discovery. This plan uses a **deterministic synthetic parity test** (always runs, no data-center dependency) with a **parquet round-trip** to reproduce the exact nan→null representation the production loader sees. Rationale: this is a pure two-function equivalence check with a legacy pandas oracle; synthetic + round-trip is more robust and CI-friendly than a rotating snapshot, and directly targets the representation risk the spec cares about.
2. Spec described the std=0 case as producing `inf`. Mathematically std=0 ⟹ all values equal the mean ⟹ numerator 0 ⟹ `0/0 = NaN`, not inf. Both paths produce NaN → `fill_nan(0)` → 0. The `fill_nan(0).fill_null(0)` handling still preserves any genuine inf (parity-safe); the test locks the std=0 → 0 behavior.

**Placeholder scan:** none — all steps have concrete code and commands.

**Type consistency:** `signal_polars(lf, params, factor_name)` signature identical across both factors and matches the `calc_cross_sections_polars` call site (`factor.signal_polars(lf_chunk, param, factor_col_name)`). `_run_parity` signature consistent between Task 1 (defines) and Task 2 (consumes). `factor_name` string format matches `f'{factor_name}_{param}'` convention (e.g. `'动态回撤阈值_24'`, `'固定回撤阈值触发币数占比开仓门槛_(24, -0.2, 0.5)'`).
