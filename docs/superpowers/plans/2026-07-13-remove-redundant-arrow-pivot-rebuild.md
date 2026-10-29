# Remove Redundant Arrow Pivot Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the consumer's redundant on-the-fly Arrow pivot *rebuild* (`build_market_pivot_from_market_dict`) and revert `read_bmac_market_pivot` to the vendor `2.2.1core` 2-arg pkl reader, so the consumer reads the producer's already-materialized `market_pivot_{type}_{year}.pkl`.

**Architecture:** The producer (d:/alpha) ships `market_pivot_{type}_{year}.pkl` every cycle behind its own `save_ready('market_pivot_{type}')` gate — a stable cross-project contract. On branch `upgrade/core-2.2.1` the consumer added an Arrow-first rebuild that recomputes this pivot from the Arrow market-dict snapshot on every `calc_equity` call, falling back to the pkl only when the Arrow window misses. That rebuild is pure redundant work. We revert the pivot path to be byte-identical to `2.2.1core` (== `main`), reading the pkl directly. The Arrow fast path for the market **dict** (`read_arrow_market_dict_for_run_time`, used by `load_bmac_data`) is untouched.

**Tech Stack:** Python 3.11, pandas, pyarrow (Arrow IPC), pytest. Consumer project `d:/alphaonline`, branch `upgrade/core-2.2.1`. Producer artifacts under `d:/alpha/data/preprocess_1h_resample/{minute}m/`.

## Global Constraints

- **TDD** — red-first test drives every production change. NO production code without a failing test first.
- **Minimal code change** vs both `main` and `2.2.1core` — after the change the affected `read_bmac_market_pivot` body is byte-identical to `2.2.1core/utils/datatools.py:67-78`, and `core/equity.py` pivot load is line-identical to `main`.
- **No producer (d:/alpha) changes.** The producer already ships the pivot pkl correctly behind `save_ready('market_pivot_{type}')`.
- **Keep** the `from core.utils.arrow_reader import read_arrow_market_dict_for_run_time` import — still used by `load_bmac_data` at `core/utils/datatools.py:352`.
- **Accepted tradeoff:** dropping `funding_rate.fillna(0)` reintroduces the vendor `2.2.1core` cross-year-concat NaN behavior for `funding_rate`. Deliberate — vendor-identical, so NaN-tolerance is the vendor simulator's responsibility.
- Test env: `conda run --no-capture-output -n alpha311 python -m pytest`.

---

## Spec correction (read before Task 1)

The approved design spec (`docs/superpowers/specs/2026-07-13-remove-redundant-arrow-pivot-rebuild-design.md`, "Test" section) proposed a RED that drives `calc_equity` with a *disagreeing* on-disk fixture (Arrow snapshot missing a `candle_begin_time` row that the pkl has) and expects the current code to raise `KeyError` in `align_pivot_dimensions`.

**That RED is unachievable, for two independent reasons — verified against current code:**

1. `read_bmac_market_pivot` (current, `datatools.py:145`) guards the Arrow rebuild with `_pivot_covers_index(pivot, required_index)`. `equity.py:108` passes `required_index = df_spot_ratio.index.union(df_swap_ratio.index)`. A missing row means the Arrow pivot does **not** cover `required_index`, so the code **auto-falls-back to the pkl** — no `KeyError`, and the pkl (which has the row) is used. The disagreeing fixture therefore produces the *correct* result even on current code.
2. Even absent the coverage guard, the Arrow rebuild output is **byte-identical** to the pkl path by design — that is exactly what the now-deleted `tests/test_arrow_pivot_parity.py` proved (open/close/vwap1m equal on the common window; funding_rate equal after both fillna(0)). An output-based assertion cannot distinguish the two sources.

**Conclusion:** the removal is a *redundant-work* removal, not a *correctness-bug* fix. The only signal observable and **stable across RED and GREEN** is *how `equity.py` calls* `read_bmac_market_pivot`:
- **Current (RED):** 4-arg — `read_bmac_market_pivot(hour_offset, 'spot', run_time=run_time, required_index=required_index)`.
- **After revert (GREEN):** 2-arg — `read_bmac_market_pivot(hour_offset, 'spot')`.

So the TDD test is a **call-contract test at the `calc_equity` integration point**: spy on `core.equity.read_bmac_market_pivot`, assert it is invoked with exactly the 2-arg pkl contract (no `run_time` / `required_index` kwargs), then short-circuit before the simulator boots. This honors the spec's intent (integration at equity, TDD red-first, minimal diff) with a RED that actually fails on current code and passes after the revert. This deviation is within the approved intent; it is documented here rather than silently substituted.

---

## File Structure

- `tests/test_equity_pivot_source.py` — **new.** One integration test asserting `calc_equity` loads the market pivot via the 2-arg pkl contract (not the Arrow-rebuild kwargs). Spies on `core.equity.read_bmac_market_pivot`, raises a sentinel to stop before `Simulator`/numba.
- `core/equity.py` — **modify** lines 107–110: delete the `required_index` union + comment, revert both pivot loads to 2-arg. Line-identical to `main`.
- `core/utils/datatools.py` — **modify** lines 68–152: delete `_PIVOT_SRC_COLUMNS`, `build_market_pivot_from_market_dict`, `_pivot_covers_index`, `_read_bmac_market_pivot_pkl`; replace `read_bmac_market_pivot` with the byte-identical `2.2.1core` 2-arg body (no fillna). Keep the `read_arrow_market_dict_for_run_time` import.
- `tests/test_arrow_pivot_parity.py` — **delete.** Imports and tests only the removed rebuild path.

## Task ordering rationale

- **Task 1 (RED)** writes the failing call-contract test — fails because current `equity.py` passes 4 args.
- **Task 2 (GREEN, behavioral)** reverts `equity.py` to the 2-arg call. This alone turns the new test green and is independently reviewable — it is the behavioral fix. `datatools.read_bmac_market_pivot` still accepts the extra kwargs (defaulted), so nothing breaks.
- **Task 3 (dead-code removal)** reverts `datatools.py` to the vendor 2-arg body and deletes `tests/test_arrow_pivot_parity.py` **in the same commit** — the test imports the helpers being deleted, so they must go together or the suite breaks on import. This is a separable review unit (pure dead-code removal + stale-test deletion) a reviewer could approve independently of Task 2.

> **Coupling note:** Task 2 must land before or with Task 3, never Task 3 alone. Reverting `datatools` to 2-arg while `equity.py` still passes `run_time=`/`required_index=` would raise `TypeError` on every `calc_equity` call. Task 2 removes those kwargs first.

---

### Task 1: RED — call-contract test at `calc_equity`

**Files:**
- Create: `tests/test_equity_pivot_source.py`
- Test target: `core/equity.py::calc_equity` (unmodified in this task)

**Interfaces:**
- Consumes: `core.equity.calc_equity(conf, run_time, leverage=None, state_name=None, account_name='')`; `core.equity.read_bmac_market_pivot` (module-level name, patchable). `calc_equity` reads `df_spot_ratio.pkl` / `df_swap_ratio.pkl` from `conf.get_result_folder()` when `account_name=''`, tz-localizes their indices to UTC, computes `hour_offset = f'{run_time.minute}m'`, then calls `read_bmac_market_pivot(...)` for `'spot'` then `'swap'` (equity.py:106-110).
- Produces: nothing consumed by later tasks; this is a leaf test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_equity_pivot_source.py`:

```python
"""equity 必须通过 2 参数 pkl 契约读取 market pivot（read_bmac_market_pivot(hour_offset, symbol_type)），
不得走 Arrow 现场重建（run_time=/required_index= kwargs）。

背景：生产者 d:/alpha 每轮发布 market_pivot_{type}_{year}.pkl（save_ready('market_pivot_{type}') 门控），
是稳定的跨项目契约。upgrade/core-2.2.1 上一度加了从 Arrow market-dict 现场重建 pivot 的冗余路径；
其输出与 pkl 逐格相同（见已删除的 test_arrow_pivot_parity.py），故只能从"调用契约"观测：
2 参数 pkl == 期望，4 参数（含 run_time/required_index）== 冗余重建。

在 calc_equity 集成点断言调用形态，命中后抛哨兵异常在 Simulator/numba 启动前短路。"""
from datetime import datetime

import pandas as pd
import pytest

import core.equity as equity


class _StopBeforeSim(Exception):
    """哨兵：观测到 pivot 调用后立即中止 calc_equity，不启动 Simulator。"""


def _write_ratio_pkls(folder):
    # tz-naive index —— calc_equity 在 account_name='' 分支会 tz_localize('UTC')。
    idx = pd.to_datetime(['2026-01-01 00:00:00', '2026-01-01 01:00:00', '2026-01-01 02:00:00'])
    df_spot = pd.DataFrame({'BTCUSDT': [0.1, 0.2, 0.3], 'ETHUSDT': [0.0, 0.1, 0.2]}, index=idx)
    df_swap = pd.DataFrame({'BTCUSDT': [0.1, 0.2, 0.3], 'ETHUSDT': [0.0, 0.1, 0.2]}, index=idx)
    df_spot.to_pickle(folder / 'df_spot_ratio.pkl')
    df_swap.to_pickle(folder / 'df_swap_ratio.pkl')


class _Conf:
    def __init__(self, folder):
        self._folder = folder

    def get_result_folder(self):
        return self._folder


def test_calc_equity_loads_pivot_via_2arg_pkl_contract(tmp_path, monkeypatch):
    _write_ratio_pkls(tmp_path)
    conf = _Conf(tmp_path)
    run_time = datetime(2026, 6, 27, 20, 30)

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise _StopBeforeSim

    monkeypatch.setattr(equity, 'read_bmac_market_pivot', _spy)

    with pytest.raises(_StopBeforeSim):
        equity.calc_equity(conf, run_time)

    assert calls, 'read_bmac_market_pivot 未被调用'
    args, kwargs = calls[0]
    # 2 参数 pkl 契约：(hour_offset, symbol_type)，无 Arrow 重建 kwargs。
    assert kwargs == {}, f'不应传 Arrow 重建 kwargs，实际 kwargs={kwargs}'
    assert len(args) == 2, f'应为 2 位置参数 (hour_offset, symbol_type)，实际 args={args}'
    assert args[0] == '30m', f'hour_offset 应为 30m，实际 {args[0]}'
    assert args[1] == 'spot', f'首个 symbol_type 应为 spot，实际 {args[1]}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run --no-capture-output -n alpha311 python -m pytest tests/test_equity_pivot_source.py -v`
Expected: FAIL. Current `equity.py:109` calls `read_bmac_market_pivot(hour_offset, 'spot', run_time=run_time, required_index=required_index)`, so the spy records `kwargs={'run_time': ..., 'required_index': ...}` and `len(args) == 2` but the `kwargs == {}` assertion fails with `不应传 Arrow 重建 kwargs`.

> If instead the test errors *before* reaching the spy (e.g. `calc_equity` raises on ratio-pkl load or tz-localize), read the traceback and fix the fixture — the spy must be the thing that stops execution. Do not proceed to Task 2 until the failure is the `kwargs == {}` assertion.

- [ ] **Step 3: Commit the red test**

```bash
cd d:/alphaonline
git add tests/test_equity_pivot_source.py
git commit -m "test: red — equity must load market pivot via 2-arg pkl contract"
```

---

### Task 2: GREEN — revert `equity.py` pivot load to 2-arg

**Files:**
- Modify: `core/equity.py:107-110`

**Interfaces:**
- Consumes: `read_bmac_market_pivot(hour_offset, symbol_type)` (2-arg; `datatools` still accepts the old kwargs defaulted, so this is forward-safe until Task 3).
- Produces: `pivot_dict_spot`, `pivot_dict_swap` — same dicts, now always from the pkl path.

- [ ] **Step 1: Apply the revert**

In `core/equity.py`, replace lines 107–110:

```python
    # Arrow 优先重建 pivot；required_index 用两侧 ratio 的时间轴并集，Arrow 窗口不覆盖时自动降级 pkl 全量。
    required_index = df_spot_ratio.index.union(df_swap_ratio.index)
    pivot_dict_spot = read_bmac_market_pivot(hour_offset, 'spot', run_time=run_time, required_index=required_index)
    pivot_dict_swap = read_bmac_market_pivot(hour_offset, 'swap', run_time=run_time, required_index=required_index)
```

with (line-identical to `main`):

```python
    pivot_dict_spot = read_bmac_market_pivot(hour_offset, 'spot')
    pivot_dict_swap = read_bmac_market_pivot(hour_offset, 'swap')
```

- [ ] **Step 2: Run the new test to verify it passes**

Run: `conda run --no-capture-output -n alpha311 python -m pytest tests/test_equity_pivot_source.py -v`
Expected: PASS. The spy now records `args=('30m', 'spot')`, `kwargs={}`.

- [ ] **Step 3: Run the fast consumer suite to confirm no regression**

Run: `conda run --no-capture-output -n alpha311 python -m pytest tests/ -q --ignore=tests/test_arrow_pivot_parity.py`
Expected: all pass (the parity test is ignored here because Task 3 deletes it; it still passes on real data if present). If any non-parity test fails, stop and diagnose before committing.

- [ ] **Step 4: Commit**

```bash
cd d:/alphaonline
git add core/equity.py
git commit -m "fix: equity reads market pivot via 2-arg pkl contract (drop Arrow rebuild)"
```

---

### Task 3: Remove dead rebuild helpers + delete stale parity test

**Files:**
- Modify: `core/utils/datatools.py:68-152`
- Delete: `tests/test_arrow_pivot_parity.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `read_bmac_market_pivot(hour_offset, symbol_type)` — byte-identical to `2.2.1core/utils/datatools.py:67-78`.

- [ ] **Step 1: Delete the four dead units and revert `read_bmac_market_pivot`**

In `core/utils/datatools.py`, delete everything from the `_PIVOT_SRC_COLUMNS` comment block (currently line 68, the comment starting `# pivot 源列, ...`) through the end of the current `read_bmac_market_pivot` body (line 152) — i.e. remove `_PIVOT_SRC_COLUMNS`, `build_market_pivot_from_market_dict`, `_pivot_covers_index`, `_read_bmac_market_pivot_pkl`, and the current 4-arg `read_bmac_market_pivot`.

Replace that entire span with the byte-identical `2.2.1core` body:

```python
def read_bmac_market_pivot(hour_offset, symbol_type):
    market_piovt_dict = {}
    for file_path in (bmac_data_path / hour_offset).rglob(f'market_pivot_{symbol_type}_*.pkl'):
        for key, value in pd.read_pickle(file_path).items():
            if key not in market_piovt_dict:
                market_piovt_dict[key] = []
            market_piovt_dict[key].append(value)

    for key, df_list in market_piovt_dict.items():
        market_piovt_dict[key] = pd.concat(market_piovt_dict[key], copy=False).sort_index()

    return market_piovt_dict
```

Leave the `from core.utils.arrow_reader import read_arrow_market_dict_for_run_time` import (line 25) in place — `load_bmac_data` still uses it (datatools.py:352). Leave `check_bmac_pivot_flag` (lines 57-65) and everything after `read_bmac_market_pivot` unchanged.

- [ ] **Step 2: Delete the stale parity test**

```bash
cd d:/alphaonline
git rm tests/test_arrow_pivot_parity.py
```

(It imports `build_market_pivot_from_market_dict` and `_read_bmac_market_pivot_pkl`, both now removed — leaving it would break collection with `ImportError`.)

- [ ] **Step 3: Verify the revert is byte-identical to the vendor**

Run:
```bash
cd d:/alphaonline
python -c "import re,pathlib; f=lambda p: re.search(r'def read_bmac_market_pivot.*?return market_piovt_dict', pathlib.Path(p).read_text(encoding='utf-8'), re.S).group(0); a=f('core/utils/datatools.py'); b=f('2.2.1core/utils/datatools.py'); print('IDENTICAL' if a==b else 'DIFF'); print(a) if a!=b else None"
```
Expected: `IDENTICAL`.

- [ ] **Step 4: Confirm no lingering references to the removed units**

Run:
```bash
cd d:/alphaonline
git grep -n "build_market_pivot_from_market_dict\|_read_bmac_market_pivot_pkl\|_pivot_covers_index\|_PIVOT_SRC_COLUMNS" -- 'core/**' 'tests/**' || echo "NO_REFERENCES"
```
Expected: `NO_REFERENCES` (docs/specs may still mention them by name — that's fine; only `core/` and `tests/` matter).

- [ ] **Step 5: Run the full consumer suite**

Run: `conda run --no-capture-output -n alpha311 python -m pytest tests/ -q`
Expected: all pass. `test_equity_pivot_source.py` green; `test_arrow_pivot_parity.py` gone; merge/backtest/section/arrow-reader/fallback tests unaffected.

- [ ] **Step 6: Commit**

```bash
cd d:/alphaonline
git add core/utils/datatools.py
git rm --cached tests/test_arrow_pivot_parity.py 2>/dev/null; true
git commit -m "refactor: remove redundant Arrow pivot rebuild; revert read_bmac_market_pivot to 2.2.1core"
```

---

## Verification (whole plan)

- [ ] `tests/test_equity_pivot_source.py` fails on the pre-Task-2 tree, passes after.
- [ ] `read_bmac_market_pivot` body in `core/utils/datatools.py` is byte-identical to `2.2.1core/utils/datatools.py` (Task 3 Step 3 prints `IDENTICAL`).
- [ ] `core/equity.py` pivot load is the two 2-arg lines, matching `main`.
- [ ] Full consumer suite green: `conda run --no-capture-output -n alpha311 python -m pytest tests/ -q`.
- [ ] No `core/` or `tests/` references to `build_market_pivot_from_market_dict`, `_read_bmac_market_pivot_pkl`, `_pivot_covers_index`, `_PIVOT_SRC_COLUMNS`.
- [ ] `read_arrow_market_dict_for_run_time` import retained; `load_bmac_data` (dict Arrow fast path) untouched.

## Self-review notes

- **Spec coverage:** all three spec production changes are covered — datatools revert (Task 3), equity revert (Task 2), parity-test deletion (Task 3). The spec's TDD test is covered by Task 1, with the documented RED-mechanism correction (call-contract spy instead of output/`KeyError`, because the rebuild output is identical and the coverage guard masks the missing row).
- **Type consistency:** the spy signature `_spy(*args, **kwargs)` matches both the current 4-arg and reverted 2-arg call shapes; `_Conf.get_result_folder()` matches the attribute `calc_equity` uses on the `account_name=''` branch.
- **Ordering safety:** Task 2 before Task 3 avoids the `TypeError` window (2-arg datatools vs 4-arg equity caller).
