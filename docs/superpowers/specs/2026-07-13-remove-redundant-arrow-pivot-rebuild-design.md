# Remove redundant Arrow pivot rebuild (consumer 2.2.1)

**Date:** 2026-07-13
**Project:** d:/alphaonline (consumer), branch `upgrade/core-2.2.1`
**Status:** design approved, pending spec review

## Problem

On branch `upgrade/core-2.2.1`, `core/utils/datatools.py` added an Arrow-first pivot
*rebuild*: `build_market_pivot_from_market_dict()` reconstructs the `{open, close,
(funding_rate,) vwap1m}` pivot on-the-fly from the Arrow market-dict snapshot on every
`calc_equity` call, falling back to the year-split `market_pivot_*.pkl` only when the Arrow
window fails to cover the required index.

The premise (stated verbatim in `tests/test_arrow_pivot_parity.py`) was that *"生产者只发布
market_pivot_*.pkl … 不发 market_pivot_*.arrow"* and therefore the consumer must rebuild the
pivot from the Arrow dict to make the 2.2.1 simulator/equity features work under the Arrow
route "无需上游改动".

That premise is wrong. The producer (d:/alpha) **does** ship `market_pivot_{type}_{year}.pkl`
every cycle, gated by its own `save_ready('market_pivot_{type}')` flag — a stable
cross-project contract that predates the Arrow work and is unchanged by it. Both the vendor
`2.2.1core` and the consumer's own `main` branch read that pkl directly via a 2-arg
`read_bmac_market_pivot(hour_offset, symbol_type)`.

So the entire Arrow rebuild path is **redundant work**: it recomputes, on every equity call,
a pivot table that is already materialized on disk by the producer.

## Goal

Remove the redundant rebuild. Revert consumer pivot loading to the vendor `2.2.1core` shape
(== `main`), reading the producer's `market_pivot_*.pkl`. Requirements:

1. **TDD** — a red-first test drives the change.
2. **Minimal code change** vs both `main` and `2.2.1core` — after the change, the affected
   code is identical to `2.2.1core`.

## Non-goals

- The Arrow fast path for the market **dict** (`load_bmac_data` → `read_arrow_market_dict_for_run_time`)
  is correct and stays untouched. Only the *pivot* rebuild is removed.
- No producer (d:/alpha) changes. The producer already ships the pivot pkl correctly.

## Design

### Production changes (3 files)

**1. `core/utils/datatools.py`** — delete the redundant unit and revert:
- Remove `_PIVOT_SRC_COLUMNS`, `build_market_pivot_from_market_dict`, `_pivot_covers_index`,
  `_read_bmac_market_pivot_pkl`.
- Replace `read_bmac_market_pivot(hour_offset, symbol_type, run_time=None, required_index=None)`
  with the byte-identical `2.2.1core` 2-arg body:
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
  (No `funding_rate.fillna(0)` — matches vendor exactly. See accepted tradeoff below.)
- Keep the `read_arrow_market_dict_for_run_time` import — `load_bmac_data` still uses it for
  the market dict.

**2. `core/equity.py`** (lines 107–110) — delete the `required_index` union and its comment;
revert to line-identical-to-`main`:
```python
pivot_dict_spot = read_bmac_market_pivot(hour_offset, 'spot')
pivot_dict_swap = read_bmac_market_pivot(hour_offset, 'swap')
```

**3. `tests/test_arrow_pivot_parity.py`** — delete. It imports and tests only the removed
rebuild path (`build_market_pivot_from_market_dict`, `_read_bmac_market_pivot_pkl`).

### Test (TDD, integration at equity — red-first)

New test `tests/test_equity_pivot_source.py` drives `calc_equity` with a **disagreeing
on-disk fixture** so the pivot source is observable in the output:

- Temp bmac data dir for one minute-offset containing:
  - an **Arrow snapshot** `{type}_dict_{ts}.arrow` whose window is *missing a
    `candle_begin_time` row* that
  - the **year-split** `market_pivot_{type}_{year}.pkl` contains;
  - `df_spot_ratio.pkl` / `df_swap_ratio.pkl` referencing the full time range (incl. the row
    Arrow lacks).
- **RED (current code):** equity passes `run_time` → Arrow rebuild → pivot lacks the row →
  `align_pivot_dimensions`' `.loc[candle_begin_times, symbols]` raises `KeyError` (row absent).
  Test fails.
- **GREEN (after revert):** equity calls 2-arg → pkl → pivot covers the full index →
  `calc_equity` returns an `account_df` spanning all rows. Assert the returned frame's
  `candle_begin_time` set equals the pkl's full index — proving the pkl is the source.
- Minimal `BacktestConfig` + tiny 2–3 symbol / few-row ratio data so the Simulator/numba path
  runs cheaply.

**Fixture-fragility fallback (flagged, not silent):** `calc_equity` has many config/state
touchpoints (Simulator, rebalance_mode, snapshot folders). If a full `calc_equity` harness
proves disproportionately fragile, fall back to asserting at the `align_pivot_dimensions`
boundary — same disagreeing fixture, same RED (`KeyError` on the missing row) → GREEN — without
booting the simulator. Try full `calc_equity` first per the approved choice; downgrade only if
the harness balloons, and note it in the implementation.

## Accepted tradeoffs

- **Dropping `funding_rate.fillna(0)`** reintroduces the vendor `2.2.1core` cross-year-concat
  NaN behavior for `funding_rate` (symbols absent in some years concat to NaN; the removed
  helper filled these with 0, matching the producer's `make_market_pivot` intent). Deliberate:
  the user chose vendor-identical to keep future core upgrades clean. `2.2.1core` ships this
  way, so any NaN-tolerance is the vendor simulator's responsibility.

## Verification

- New red-first test passes after the revert.
- Existing consumer test suite (merge/backtest/section) stays green.
- Post-change `git diff 2.2.1core/utils/datatools.py core/utils/datatools.py` shows the
  `read_bmac_market_pivot` body identical; `core/equity.py` pivot load identical to `main`.
