"""Combine the existing two-strategy rotation with Canglangting-style drift.

The module is deliberately independent of ``core``.  It first builds the
same baseline as :mod:`RotationStrategy_long` (the first two strategies are
rotated and every other strategy keeps its configured ``cap_ratios``), then
replaces the *relative* weights of selected, same-direction groups with
natural-PnL drift and optional side rebalancing.  The group's baseline total
weight is preserved, so static groups and the rotated group retain their
account-level allocation.

``params``
---------
``factor_list`` and ``cap_ratios`` have the same meaning as
``RotationStrategy_long``.  ``rotation_indices`` defaults to ``[0, 1]``.
``dynamic_groups`` accepts either ``[2, 3, 4]`` or a mapping such as::

    {"indices": [2, 3, 4], "direction": "long", "mode": "both",
     "period_hours": 2160, "trigger_threshold": .6,
     "rebalance_duration_hours": 24}

Group mappings inherit defaults from ``side_rebalance.long`` or
``side_rebalance.short``.  ``rebalance_anchor_time`` and ``init_time`` are
optional.  Short-side freeze options are compatible with the sample strategy.
The implementation intentionally does not apply account-level leverage or
exposure clamps after composition: applying those to a subset would change
the fixed groups' account allocation.  A caller can set the group's target
weights and compare the resulting portfolio at the outer backtest level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

import numpy as np
import pandas as pd

try:  # numba is bundled with the alpha311 environment; keep a safe fallback.
    from numba import njit
except ImportError:  # pragma: no cover - exercised only in minimal deployments
    njit = None

# Annotations are postponed; numerical workers must not load account/framework
# configuration just to import this strategy (also permits normal Numba caching).
if TYPE_CHECKING:
    from core.model.strategy_config import PosStrategyConfig


EPS = 1e-12


def _drift_core_python(
    equity: np.ndarray,
    base_relative: np.ndarray,
    target_relative: np.ndarray,
    freeze: np.ndarray,
    periodic_due: np.ndarray,
    conditional_enabled: bool,
    trigger_threshold: float,
    duration: int,
    short_direction: bool,
    start_index: int,
) -> np.ndarray:
    """Reference implementation used when numba is unavailable."""
    n_rows, m = equity.shape
    out = np.zeros((n_rows, m), dtype=np.float64)
    factors = np.ones_like(equity, dtype=np.float64)
    for i in range(1, n_rows):
        for j in range(m):
            prev = equity[i - 1, j]
            curr = equity[i, j]
            factors[i, j] = curr / prev if np.isfinite(prev) and prev > 0 and np.isfinite(curr) else 1.0
    w = base_relative.copy()
    # The reference strategy is uninitialized before ``init_time``: those
    # rows must remain zero so they cannot contribute to the account curve.
    out[start_index] = w
    active_step = 0
    active_reason = 0
    periodic_pending = False
    for i in range(start_index + 1, n_rows):
        factor = factors[i].copy()
        if short_direction and freeze[i]:
            factor = np.maximum(0.0, 2.0 - factor)
        previous_w = w.copy()
        w = w * np.maximum(factor, 0.0)
        total = float(w.sum())
        w = w / total if total > EPS else previous_w
        if periodic_due[i]:
            periodic_pending = True
        if freeze[i]:
            out[i] = w
            continue
        periodic = periodic_pending
        conditional = False
        if conditional_enabled:
            for j in range(m):
                reference = target_relative[j]
                deviation = abs(w[j] / reference - 1.0) if reference > EPS else (1.0 if w[j] > EPS else 0.0)
                if deviation > trigger_threshold:
                    conditional = True
                    break
        if active_step > 0 and periodic and active_reason == 2:
            active_step = 0
        if active_step > 0:
            remaining = duration - active_step
            if remaining <= 1:
                w = target_relative.copy()
            else:
                w = w + (target_relative - w) / remaining
            active_step += 1
            if active_step >= duration:
                active_step = 0
        elif periodic or conditional:
            active_reason = 1 if periodic else 2
            active_step = 1
            if duration <= 1:
                w = target_relative.copy()
                active_step = 0
            else:
                w = w + (target_relative - w) / duration
            if periodic:
                periodic_pending = False
        total = float(w.sum())
        out[i] = w / total if total > EPS else base_relative
    return out


if njit is not None:
    @njit(cache=True)
    def _drift_core_numba(
        equity: np.ndarray,
        base_relative: np.ndarray,
        target_relative: np.ndarray,
        freeze: np.ndarray,
        periodic_due: np.ndarray,
        conditional_enabled: bool,
        trigger_threshold: float,
        duration: int,
        short_direction: bool,
        start_index: int,
    ) -> np.ndarray:
        n_rows, m = equity.shape
        out = np.zeros((n_rows, m), dtype=np.float64)
        factors = np.ones_like(equity, dtype=np.float64)
        for i in range(1, n_rows):
            for j in range(m):
                prev = equity[i - 1, j]
                curr = equity[i, j]
                if np.isfinite(prev) and prev > 0 and np.isfinite(curr):
                    factors[i, j] = curr / prev
        w = base_relative.copy()
        # Keep pre-init rows at zero, matching the reference strategy.
        out[start_index] = w
        active_step = 0
        active_reason = 0
        periodic_pending = False
        for i in range(start_index + 1, n_rows):
            factor = factors[i].copy()
            if short_direction and freeze[i]:
                for j in range(m):
                    factor[j] = max(0.0, 2.0 - factor[j])
            previous_w = w.copy()
            for j in range(m):
                w[j] *= max(factor[j], 0.0)
            total = w.sum()
            if total > EPS:
                w /= total
            else:
                w = previous_w
            if periodic_due[i]:
                periodic_pending = True
            if freeze[i]:
                out[i] = w
                continue
            periodic = periodic_pending
            conditional = False
            if conditional_enabled:
                for j in range(m):
                    reference = target_relative[j]
                    if reference > EPS:
                        deviation = abs(w[j] / reference - 1.0)
                    else:
                        deviation = 1.0 if w[j] > EPS else 0.0
                    if deviation > trigger_threshold:
                        conditional = True
                        break
            if active_step > 0 and periodic and active_reason == 2:
                active_step = 0
            if active_step > 0:
                remaining = duration - active_step
                if remaining <= 1:
                    w = target_relative.copy()
                else:
                    w = w + (target_relative - w) / remaining
                active_step += 1
                if active_step >= duration:
                    active_step = 0
            elif periodic or conditional:
                active_reason = 1 if periodic else 2
                active_step = 1
                if duration <= 1:
                    w = target_relative.copy()
                    active_step = 0
                else:
                    w = w + (target_relative - w) / duration
                if periodic:
                    periodic_pending = False
            total = w.sum()
            if total > EPS:
                out[i] = w / total
            else:
                out[i] = base_relative
        return out
else:
    _drift_core_numba = None


@dataclass(frozen=True)
class _GroupConfig:
    indices: tuple[int, ...]
    direction: str
    mode: str
    period_hours: int | None
    trigger_threshold: float | None
    rebalance_duration_hours: int
    freeze_enabled: bool
    freeze_window_hours: int
    freeze_threshold: float
    freeze_duration_hours: int
    target_relative: tuple[float, ...] | None


def _as_positive_int(value: Any, name: str, default: int) -> int:
    if value is None:
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if not np.isfinite(number) or number <= 0 or not number.is_integer():
        raise ValueError(f"{name} must be a positive integer")
    return int(number)


def _as_float(value: Any, name: str, default: float | None = None) -> float | None:
    if value is None:
        value = default
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not np.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _direction(value: Any, default: str = "long") -> str:
    value = str(value or default).lower()
    if value not in {"long", "short"}:
        raise ValueError("dynamic group direction must be `long` or `short`")
    return value


def _mode(value: Any, default: str = "both") -> str:
    value = str(value or default).lower()
    if value not in {"none", "periodic", "conditional", "both"}:
        raise ValueError("dynamic group mode must be none, periodic, conditional, or both")
    return value


def _factor_rotation(
    equity_dfs: list[pd.DataFrame],
    cap_ratios: np.ndarray,
    factor_list: Iterable[tuple],
    rotation_indices: tuple[int, ...],
) -> pd.DataFrame:
    """Build the baseline used by ``positions.RotationStrategy_long``."""
    n = len(equity_dfs)
    times = pd.to_datetime(equity_dfs[0]["candle_begin_time"])
    result = np.tile(cap_ratios, (len(times), 1)).astype(float)
    if not factor_list or not rotation_indices:
        return pd.DataFrame(result, index=times, columns=range(n))

    factors: list[pd.Series] = []
    for factor in factor_list:
        if len(factor) < 3:
            raise ValueError("factor_list entries require (name, ascending, parameter, weight)")
        column = f"{factor[0]}_{factor[2]}"
        missing = [idx for idx in rotation_indices if column not in equity_dfs[idx]]
        if missing:
            raise KeyError(f"rotation factor column {column!r} missing for strategy indices {missing}")
        matrix = pd.DataFrame(
            {idx: equity_dfs[idx][column].to_numpy() for idx in rotation_indices},
            index=times,
        )
        ranks = matrix.rank(axis=1, ascending=bool(factor[1]), method="min")
        winner = ranks.eq(ranks.min(axis=1), axis=0).astype(float)
        winner_sum = winner.sum(axis=1)
        winner = winner.div(winner_sum.replace(0, np.nan), axis=0).fillna(1.0 / len(rotation_indices))
        factors.append(winner)

    signal = sum(factors) / len(factors)
    for pos, idx in enumerate(rotation_indices):
        result[:, idx] = cap_ratios[idx] * signal[idx].to_numpy()
    return pd.DataFrame(result, index=times, columns=range(n))


def _group_defaults(params: dict, direction: str) -> dict:
    side = params.get("side_rebalance") or {}
    raw = side.get(direction, {}) if isinstance(side, dict) else {}
    if not isinstance(raw, dict):
        raise ValueError(f"side_rebalance.{direction} must be a dict")
    mode = _mode(raw.get("mode", "both"))
    period = None
    if mode in {"periodic", "both"}:
        period = _as_positive_int(raw.get("period_hours"), f"{direction}.period_hours", 2160)
    trigger = None
    if mode in {"conditional", "both"}:
        trigger = _as_float(raw.get("trigger_threshold"), f"{direction}.trigger_threshold", 0.6)
        if trigger is None or trigger <= 0:
            raise ValueError(f"{direction}.trigger_threshold must be positive")
    duration = _as_positive_int(raw.get("rebalance_duration_hours"), f"{direction}.rebalance_duration_hours", 24)
    if period is not None and duration > period:
        raise ValueError(f"{direction}.rebalance_duration_hours cannot exceed period_hours")
    return {
        "direction": direction,
        "mode": mode,
        "period_hours": period,
        "trigger_threshold": trigger,
        "rebalance_duration_hours": duration,
        "freeze_enabled": _bool(params.get("short_freeze_enabled"), True) if direction == "short" else False,
        "freeze_window_hours": _as_positive_int(params.get("short_freeze_window_hours"), "short_freeze_window_hours", 1),
        "freeze_threshold": _as_float(params.get("short_freeze_threshold"), "short_freeze_threshold", 0.10),
        "freeze_duration_hours": _as_positive_int(params.get("short_freeze_duration_hours"), "short_freeze_duration_hours", 4),
    }


def _parse_groups(params: dict, n: int, directions: list[str]) -> list[_GroupConfig]:
    raw_groups = params.get("dynamic_groups", params.get("drift_groups", []))
    if raw_groups is None:
        return []
    if isinstance(raw_groups, dict):
        # A single group mapping has ``indices`` at the top level.  A named
        # mapping (``{"long_core": {...}, ...}``) is expanded by values.
        if "indices" in raw_groups or "strategy_indices" in raw_groups:
            raw_groups = [raw_groups]
        else:
            raw_groups = list(raw_groups.values())
    if not isinstance(raw_groups, (list, tuple)):
        raise ValueError("dynamic_groups must be a list or mapping")

    groups: list[_GroupConfig] = []
    used: set[int] = set()
    global_side = params.get("side_rebalance") or {}
    for raw in raw_groups:
        if isinstance(raw, dict):
            indices_raw = raw.get("indices", raw.get("strategy_indices"))
            if indices_raw is None:
                raise ValueError("a dynamic group requires `indices`")
            options = dict(raw)
        else:
            indices_raw = raw
            options = {}
        if isinstance(indices_raw, (int, np.integer)):
            indices_raw = [indices_raw]
        try:
            indices = tuple(dict.fromkeys(int(x) for x in indices_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("dynamic group indices must be integers") from exc
        if not indices:
            raise ValueError("dynamic group cannot be empty")
        if any(idx < 0 or idx >= n for idx in indices):
            raise ValueError(f"dynamic group indices must be in [0, {n - 1}]")
        overlap = used.intersection(indices)
        if overlap:
            raise ValueError(f"dynamic groups overlap at indices {sorted(overlap)}")
        used.update(indices)

        inferred = {directions[idx] for idx in indices}
        if len(inferred) > 1:
            raise ValueError("a dynamic group must contain strategies of one direction only")
        direction = _direction(options.get("direction"), next(iter(inferred)))
        if direction != next(iter(inferred)):
            raise ValueError("dynamic group direction does not match direction_list")
        defaults = _group_defaults(params, direction)
        # A group may override side defaults.  A top-level side_rebalance can
        # also be supplied as a direct group mapping for convenience.
        side_default = global_side.get(direction, {}) if isinstance(global_side, dict) else {}
        if not isinstance(side_default, dict):
            side_default = {}
        merged = {**defaults, **side_default, **options, "direction": direction}
        mode = _mode(merged.get("mode"), defaults["mode"])
        period = None if mode not in {"periodic", "both"} else _as_positive_int(
            merged.get("period_hours"), "dynamic_group.period_hours", 2160
        )
        trigger = None if mode not in {"conditional", "both"} else _as_float(
            merged.get("trigger_threshold"), "dynamic_group.trigger_threshold", 0.6
        )
        if trigger is not None and trigger <= 0:
            raise ValueError("dynamic_group.trigger_threshold must be positive")
        duration = _as_positive_int(merged.get("rebalance_duration_hours"), "dynamic_group.rebalance_duration_hours", 24)
        if period is not None and duration > period:
            raise ValueError("dynamic_group.rebalance_duration_hours cannot exceed period_hours")
        target_relative = None
        target_raw = options.get("target_ratios")
        if target_raw is None and isinstance(global_side, dict):
            target_raw = global_side.get("target_ratios")
        if target_raw is not None:
            target_arr = np.asarray(target_raw, dtype=float)
            if target_arr.ndim != 1:
                raise ValueError("target_ratios must be a one-dimensional vector")
            if len(target_arr) == n:
                target_arr = target_arr[list(indices)]
            elif len(target_arr) != len(indices):
                raise ValueError("target_ratios must match the strategy count or dynamic group size")
            if (not np.isfinite(target_arr).all()) or (target_arr < 0).any() or target_arr.sum() <= EPS:
                raise ValueError("target_ratios must be finite, non-negative, and non-zero")
            target_relative = tuple((target_arr / target_arr.sum()).tolist())
        groups.append(
            _GroupConfig(
                indices=indices,
                direction=direction,
                mode=mode,
                period_hours=period,
                trigger_threshold=trigger,
                rebalance_duration_hours=duration,
                freeze_enabled=_bool(merged.get("freeze_enabled"), bool(merged.get("short_freeze_enabled", False)))
                if direction == "short" else False,
                freeze_window_hours=_as_positive_int(merged.get("freeze_window_hours", merged.get("short_freeze_window_hours")), "freeze_window_hours", 1),
                freeze_threshold=_as_float(merged.get("freeze_threshold", merged.get("short_freeze_threshold")), "freeze_threshold", 0.10) or 0.10,
                freeze_duration_hours=_as_positive_int(merged.get("freeze_duration_hours", merged.get("short_freeze_duration_hours")), "freeze_duration_hours", 4),
                target_relative=target_relative,
            )
        )
    return groups


def _freeze_flags(
    equity: np.ndarray,
    target: np.ndarray,
    times: pd.DatetimeIndex,
    group: _GroupConfig,
) -> np.ndarray:
    if not (group.direction == "short" and group.freeze_enabled):
        return np.zeros(len(times), dtype=bool)
    composite = (equity * target[np.newaxis, :]).sum(axis=1)
    flags = np.zeros(len(times), dtype=bool)
    ns = times.to_numpy(dtype="datetime64[ns]").astype("int64")
    window = int(pd.Timedelta(hours=group.freeze_window_hours).value)
    for i in range(1, len(times)):
        j = int(np.searchsorted(ns, ns[i] - window, side="left"))
        if j < i and composite[j] > EPS and np.isfinite(composite[i]):
            if abs(composite[i] / composite[j] - 1.0) > group.freeze_threshold:
                flags[i : min(len(times), i + group.freeze_duration_hours)] = True
    return flags


def _drift_relative(
    equity_dfs: list[pd.DataFrame],
    group: _GroupConfig,
    base_relative: np.ndarray,
    times: pd.DatetimeIndex,
    anchor_time: pd.Timestamp,
    init_time: pd.Timestamp | None,
) -> np.ndarray:
    """Return rows of relative weights whose sum is exactly one."""
    m = len(group.indices)
    n_rows = len(times)
    target_relative = np.asarray(group.target_relative, dtype=float) if group.target_relative is not None else base_relative
    # ``mode=none`` disables rebalancing but still keeps natural PnL drift.
    if m == 1:
        return np.tile(base_relative, (n_rows, 1))

    equity_columns = []
    for idx in group.indices:
        df = equity_dfs[idx]
        series = pd.Series(
            df["close"].to_numpy(dtype=float),
            index=pd.to_datetime(df["candle_begin_time"]),
        ).reindex(times).ffill()
        # A missing value at the beginning cannot be reconstructed causally.
        # Fail loudly instead of bfill-ing from a future equity observation.
        if series.isna().any():
            raise ValueError(
                f"dynamic strategy index {idx} has no causal close value at the "
                "start of the common time axis"
            )
        equity_columns.append(series.to_numpy(dtype=float))
    equity = np.column_stack(equity_columns)
    equity = np.where(np.isfinite(equity) & (equity > 0), equity, np.nan)
    # Freeze detection follows the configured starting (cap) proportions in
    # the reference implementation, even when Re has a separate target.
    freeze = _freeze_flags(equity, base_relative, times, group)
    periodic_due = np.zeros(n_rows, dtype=np.bool_)
    # The first periodic Re is strictly after the initialization bar, as in
    # DriftExposureLeverageStrategyFillSideReShortPnL.
    start_index = 0 if init_time is None else int(times.searchsorted(init_time, side="left"))
    start_index = min(max(start_index, 0), n_rows - 1)
    if group.period_hours is not None and group.mode in {"periodic", "both"}:
        after_time = times[start_index]
        steps = int((after_time - anchor_time) // pd.Timedelta(hours=group.period_hours)) + 1
        candidate = anchor_time + pd.Timedelta(hours=steps * group.period_hours)
        next_periodic = int(times.searchsorted(candidate, side="left"))
        if next_periodic <= start_index:
            next_periodic = start_index + 1
        while next_periodic < n_rows:
            periodic_due[next_periodic] = True
            candidate = anchor_time + pd.Timedelta(
                hours=(int((times[next_periodic] - anchor_time).total_seconds() // 3600 // group.period_hours) + 1)
                * group.period_hours
            )
            next_periodic = int(times.searchsorted(candidate, side="left"))

    conditional_enabled = group.mode in {"conditional", "both"} and group.trigger_threshold is not None
    core = _drift_core_numba if _drift_core_numba is not None else _drift_core_python
    return core(
        equity,
        base_relative,
        target_relative,
        freeze,
        periodic_due,
        conditional_enabled,
        float(group.trigger_threshold or 0.0),
        int(group.rebalance_duration_hours),
        group.direction == "short",
        start_index,
    )


def calc_ratio(equity_dfs: list[pd.DataFrame], stg_conf: PosStrategyConfig) -> pd.DataFrame:
    """Calculate hybrid rotation/drift allocation ratios."""
    if not equity_dfs:
        raise ValueError("equity_dfs cannot be empty")
    params = stg_conf.params or {}
    n = len(equity_dfs)
    cap = np.asarray(params.get("cap_ratios", []), dtype=float)
    if cap.ndim != 1 or len(cap) != n or not np.isfinite(cap).all() or (cap < 0).any() or cap.sum() <= EPS:
        raise ValueError("cap_ratios must be a finite non-negative vector matching strategy count")
    times = pd.DatetimeIndex(pd.to_datetime(equity_dfs[0]["candle_begin_time"]))
    if times.has_duplicates or len(times) == 0:
        raise ValueError("the first equity curve must contain unique candle_begin_time values")

    raw_directions = params.get("direction_list")
    if raw_directions is None:
        # Most configs already provide direction_list.  For convenience, infer
        # it from the loaded BacktestConfig when possible so short groups do not
        # silently use long-side PnL factors.
        directions = []
        loaded = getattr(stg_conf, "strategy_cfg_list", None) or []
        for conf in loaded[:n]:
            long_weight = 0.0
            short_weight = 0.0
            for strategy in getattr(conf, "strategy_list", []) or []:
                long_weight += float(getattr(strategy, "long_cap_weight", 0.0) or 0.0)
                short_weight += float(getattr(strategy, "short_cap_weight", 0.0) or 0.0)
            if long_weight > EPS and short_weight <= EPS:
                directions.append("long")
            elif short_weight > EPS and long_weight <= EPS:
                directions.append("short")
            else:
                directions.append("long")
        if len(directions) != n:
            directions = ["long"] * n
    else:
        if len(raw_directions) != n:
            raise ValueError("direction_list length must match strategy count")
        directions = [_direction(x) for x in raw_directions]

    rotation_raw = params.get("rotation_indices", [0, 1])
    rotation_indices = tuple(dict.fromkeys(int(x) for x in rotation_raw))
    if any(x < 0 or x >= n for x in rotation_indices):
        raise ValueError("rotation_indices out of range")
    baseline = _factor_rotation(equity_dfs, cap, params.get("factor_list", []), rotation_indices)

    init_raw = params.get("init_time")
    init_time = pd.Timestamp(init_raw) if init_raw is not None else None
    if init_time is not None:
        if times.tz is not None and init_time.tzinfo is None:
            init_time = init_time.tz_localize(times.tz)
        elif times.tz is None and init_time.tzinfo is not None:
            init_time = init_time.tz_convert("UTC").tz_localize(None)
    init_index = 0 if init_time is None else int(times.searchsorted(init_time, side="left"))
    if init_index >= len(times):
        raise ValueError("init_time is later than all equity data")
    anchor_raw = params.get("rebalance_anchor_time")
    anchor = pd.Timestamp(anchor_raw) if anchor_raw is not None else times[init_index]
    if times.tz is not None and anchor.tzinfo is None:
        anchor = anchor.tz_localize(times.tz)
    elif times.tz is None and anchor.tzinfo is not None:
        anchor = anchor.tz_convert("UTC").tz_localize(None)

    groups = _parse_groups(params, n, directions)
    result = baseline.to_numpy(dtype=float)
    rotation_set = set(rotation_indices)
    for group in groups:
        idx = list(group.indices)
        if rotation_set.intersection(idx) and not _bool(params.get("allow_rotation_overlap"), False):
            raise ValueError(
                "dynamic_groups cannot include rotation_indices unless "
                "allow_rotation_overlap=True"
            )
        total = cap[idx].sum()
        if total <= EPS:
            continue
        rel = cap[idx] / total
        dynamic_rel = _drift_relative(equity_dfs, group, rel, times, anchor, init_time)
        group_total = baseline.iloc[:, idx].sum(axis=1).to_numpy(dtype=float)
        result[:, idx] = dynamic_rel * group_total[:, None]

    result = np.maximum(result, 0.0)
    return_df = pd.DataFrame(result, index=times, columns=range(n))
    return_df.attrs["hybrid_dynamic_groups"] = [group.indices for group in groups]
    return_df.attrs["hybrid_baseline"] = baseline
    return return_df
