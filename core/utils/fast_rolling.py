"""Cached Numba rolling primitives for CPU-hotspot time-series factors.

All kernels are pure array/scalar functions with `@njit(cache=True)` so spawned
worker processes reuse disk-compiled machine code without recompilation.
Semantics match pandas `rolling(..., min_periods=1)` edge cases (NaN, +-inf,
tie breaking, ddof=1, first-NaN-wins argmax).
"""
import numpy as np
from numba import njit


@njit(cache=True)
def _rolling_rank_pct_nb(arr: np.ndarray, n: int) -> np.ndarray:
    length = arr.shape[0]
    out = np.empty(length, dtype=np.float64)
    if length == 0:
        return out
    if n <= 0:
        for i in range(length):
            out[i] = np.nan
        return out

    # Collect finite values for coordinate compression (pandas rolling.rank
    # treats both NaN and +-inf as missing).
    finite_count = 0
    for i in range(length):
        v = arr[i]
        if np.isfinite(v):
            finite_count += 1

    if finite_count == 0:
        for i in range(length):
            out[i] = np.nan
        return out

    finite_vals = np.empty(finite_count, dtype=np.float64)
    finite_pos = np.empty(finite_count, dtype=np.int32)
    k = 0
    for i in range(length):
        v = arr[i]
        if np.isfinite(v):
            finite_vals[k] = v
            finite_pos[k] = i
            k += 1

    order = np.argsort(finite_vals)
    codes = np.zeros(length, dtype=np.int32)
    rank = 1
    codes[finite_pos[order[0]]] = 1
    prev = finite_vals[order[0]]
    for j in range(1, finite_count):
        cur = finite_vals[order[j]]
        if cur != prev:
            rank += 1
            prev = cur
        codes[finite_pos[order[j]]] = rank

    # Fenwick tree (BIT) over compressed integer ranks 1..rank
    bit = np.zeros(rank + 1, dtype=np.int32)
    win_count = 0

    for i in range(length):
        c_in = codes[i]
        if c_in > 0:
            idx = c_in
            while idx <= rank:
                bit[idx] += 1
                idx += idx & -idx
            win_count += 1

        if i >= n:
            c_out = codes[i - n]
            if c_out > 0:
                idx = c_out
                while idx <= rank:
                    bit[idx] -= 1
                    idx += idx & -idx
                win_count -= 1

        if c_in > 0:
            # Query less = prefix_sum(c_in - 1) and le = prefix_sum(c_in)
            less = 0
            idx = c_in - 1
            while idx > 0:
                less += bit[idx]
                idx -= idx & -idx

            le = 0
            idx = c_in
            while idx > 0:
                le += bit[idx]
                idx -= idx & -idx

            equal = le - less
            out[i] = (less + 0.5 * (equal + 1.0)) / win_count
        else:
            out[i] = np.nan

    return out


@njit(cache=True)
def _rolling_argmax_first_nb(arr: np.ndarray, n: int) -> np.ndarray:
    """Exact match for pandas `rolling(n, min_periods=1).apply(lambda x: x.argmax(), raw=True)`.

    If any NaN exists in the window, NumPy `argmax()` returns the index of the
    FIRST NaN. Otherwise it returns the index of the FIRST maximum.
    """
    length = arr.shape[0]
    out = np.empty(length, dtype=np.float64)
    if length == 0:
        return out

    dq = np.empty(length, dtype=np.int32)
    head = 0
    tail = 0

    nan_dq = np.empty(length, dtype=np.int32)
    nan_head = 0
    nan_tail = 0

    for i in range(length):
        w0 = 0 if i < n else i - n + 1
        while head < tail and dq[head] < w0:
            head += 1
        while nan_head < nan_tail and nan_dq[nan_head] < w0:
            nan_head += 1

        v = arr[i]
        if np.isnan(v):
            nan_dq[nan_tail] = i
            nan_tail += 1
        else:
            # Strict `<` preserves the earliest index on ties.
            while head < tail and arr[dq[tail - 1]] < v:
                tail -= 1
            dq[tail] = i
            tail += 1

        if (nan_tail - nan_head) == (i - w0 + 1):
            # All elements in the window are NaN -> fails min_periods=1 -> NaN.
            out[i] = np.nan
        elif nan_head < nan_tail:
            out[i] = float(nan_dq[nan_head] - w0)
        else:
            out[i] = float(dq[head] - w0)

    return out


@njit(cache=True)
def _rolling_mean_kahan_segment_nb(arr: np.ndarray, start: int, end: int, n: int, out: np.ndarray) -> None:
    """Kahan-compensated sliding-window mean (min_periods=1) on arr[start:end]."""
    s = 0.0
    comp = 0.0
    cnt = 0
    for i in range(start, end):
        v = arr[i]
        if not np.isnan(v):
            y = v - comp
            t = s + y
            comp = 0.0 if (not np.isfinite(t) or not np.isfinite(y)) else ((t - s) - y)
            s = t
            cnt += 1
        if i - start >= n:
            old = arr[i - n]
            if not np.isnan(old):
                y = -old - comp
                t = s + y
                comp = (t - s) - y
                s = t
                cnt -= 1
        if cnt > 0:
            out[i] = s / cnt
        else:
            out[i] = np.nan


@njit(cache=True)
def _cci_causal_168_segments_nb(tp: np.ndarray, anomaly: np.ndarray, times_ns: np.ndarray,
                                has_times: bool, n: int) -> np.ndarray:
    length = tp.shape[0]
    factor = np.full(length, np.nan, dtype=np.float64)
    if length == 0:
        return factor

    ma = np.empty(length, dtype=np.float64)
    abs_dev = np.empty(length, dtype=np.float64)
    md = np.empty(length, dtype=np.float64)

    start = 0
    for pos in range(length + 1):
        if pos < length and not anomaly[pos]:
            continue
        end = pos
        if end > start:
            _rolling_mean_kahan_segment_nb(tp, start, end, n, ma)
            for i in range(start, end):
                v = tp[i]
                m = ma[i]
                if np.isnan(v) or np.isnan(m):
                    abs_dev[i] = np.nan
                else:
                    d = v - m
                    abs_dev[i] = -d if d < 0.0 else d
            _rolling_mean_kahan_segment_nb(abs_dev, start, end, n, md)
            for i in range(start, end):
                denom = md[i] * 0.015
                if denom == 0.0 or np.isnan(denom):
                    factor[i] = np.nan
                else:
                    factor[i] = (tp[i] - ma[i]) / denom
        start = end + 1

    # Apply 168-hour (or 169-bar when no timestamps) causal block mask
    if has_times:
        cooldown_ns = np.int64(168) * np.int64(3600) * np.int64(1_000_000_000)
        has_anom = False
        last_anom_ns = np.int64(0)
        for i in range(length):
            if anomaly[i]:
                has_anom = True
                last_anom_ns = times_ns[i]
            if has_anom and (times_ns[i] - last_anom_ns) <= cooldown_ns:
                factor[i] = np.nan
    else:
        last_anom_idx = -1000
        for i in range(length):
            if anomaly[i]:
                last_anom_idx = i
            if i - last_anom_idx < 169:
                factor[i] = np.nan

    return factor


@njit(cache=True)
def _rolling_sum_mean_kahan_nb(arr: np.ndarray, n: int, min_periods: int, is_mean: bool) -> np.ndarray:
    length = arr.shape[0]
    out = np.empty(length, dtype=np.float64)
    s = 0.0
    comp = 0.0
    cnt = 0
    for i in range(length):
        v = arr[i]
        if not np.isnan(v):
            y = v - comp
            t = s + y
            comp = 0.0 if (not np.isfinite(t) or not np.isfinite(y)) else ((t - s) - y)
            s = t
            cnt += 1
        need_recompute = False
        if i >= n:
            old = arr[i - n]
            if not np.isnan(old):
                if not np.isfinite(old) or not np.isfinite(s):
                    need_recompute = True
                else:
                    y = -old - comp
                    t = s + y
                    comp = (t - s) - y
                    s = t
                    if abs(s) <= 1e-11 * abs(old):
                        need_recompute = True
                cnt -= 1
        if need_recompute:
            w0 = 0 if i < n else i - n + 1
            s = 0.0
            comp = 0.0
            cnt = 0
            for j in range(w0, i + 1):
                vj = arr[j]
                if not np.isnan(vj):
                    y = vj - comp
                    t = s + y
                    comp = (t - s) - y
                    s = t
                    cnt += 1
        if cnt >= min_periods and cnt > 0:
            out[i] = (s / cnt) if is_mean else s
        else:
            out[i] = np.nan
    return out


@njit(cache=True)
def _rolling_mean_std_welford_nb(arr: np.ndarray, n: int):
    """Exact sliding-window mean & ddof=1 std (min_periods=1) matching pandas Welford."""
    length = arr.shape[0]
    mean_out = np.empty(length, dtype=np.float64)
    std_out = np.empty(length, dtype=np.float64)
    mean = 0.0
    ssq = 0.0
    cnt = 0
    for i in range(length):
        v = arr[i]
        has_in = not np.isnan(v)
        has_out = i >= n and (not np.isnan(arr[i - n]))
        old = arr[i - n] if i >= n else np.nan

        if has_in and not has_out:
            cnt += 1
            delta = v - mean
            mean += delta / cnt
            ssq += delta * (v - mean)
        elif has_out and not has_in:
            if cnt <= 1:
                cnt = 0
                mean = 0.0
                ssq = 0.0
            else:
                cnt -= 1
                old_mean = mean
                mean = (mean * (cnt + 1) - old) / cnt
                ssq -= (old - old_mean) * (old - mean)
                if ssq <= 0.0:
                    # Two-pass exact recompute on cancellation
                    w0 = i - n + 1
                    m2 = 0.0
                    c2 = 0
                    for j in range(w0, i + 1):
                        val = arr[j]
                        if not np.isnan(val):
                            m2 += val
                            c2 += 1
                    mean = m2 / c2 if c2 > 0 else 0.0
                    s2 = 0.0
                    for j in range(w0, i + 1):
                        val = arr[j]
                        if not np.isnan(val):
                            d2 = val - mean
                            s2 += d2 * d2
                    ssq = s2
                    cnt = c2
        elif has_in and has_out:
            if cnt == 1:
                mean = v
                ssq = 0.0
            else:
                old_mean = mean
                mean += (v - old) / cnt
                ssq += (v - old) * ((v - mean) + (old - old_mean))
                # Guard against cancellation after a large outlier exits the window
                if ssq <= 0.0 or (abs(old - old_mean) > 1e3 * max(1.0, abs(v - mean))):
                    w0 = i - n + 1
                    m2 = 0.0
                    c2 = 0
                    for j in range(w0, i + 1):
                        val = arr[j]
                        if not np.isnan(val):
                            m2 += val
                            c2 += 1
                    mean = m2 / c2 if c2 > 0 else 0.0
                    s2 = 0.0
                    for j in range(w0, i + 1):
                        val = arr[j]
                        if not np.isnan(val):
                            d2 = val - mean
                            s2 += d2 * d2
                    ssq = s2
                    cnt = c2

        if cnt >= 1:
            mean_out[i] = mean
        else:
            mean_out[i] = np.nan
        if cnt >= 2:
            std_out[i] = np.sqrt(ssq / (cnt - 1)) if ssq > 0.0 else 0.0
        else:
            std_out[i] = np.nan
    return mean_out, std_out


@njit(cache=True)
def _cci_causal_168_full_nb(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                            times_ns: np.ndarray, has_times: bool, n: int) -> np.ndarray:
    length = high.shape[0]
    if length == 0:
        return np.empty(0, dtype=np.float64)

    tp = np.empty(length, dtype=np.float64)
    tp[0] = np.nan
    for i in range(1, length):
        tp[i] = (high[i] + low[i] + close[i]) / 3.0

    pctchange = np.empty(length, dtype=np.float64)
    pctchange[0] = np.nan
    if length >= 2:
        pctchange[1] = np.nan
    for i in range(2, length):
        h0 = high[i - 1]
        h1 = high[i]
        l0 = low[i - 1]
        l1 = low[i]
        if np.isnan(h0) or np.isnan(h1) or np.isnan(l0) or np.isnan(l1):
            pctchange[i] = np.nan
        else:
            rmax = h0 if h0 >= h1 else h1
            rmin = l0 if l0 <= l1 else l1
            if rmin == 0.0:
                pctchange[i] = np.inf if (rmax - rmin) > 0.0 else (np.nan if rmax == rmin else -np.inf)
            else:
                pctchange[i] = (rmax - rmin) / rmin

    pctma, pctstd = _rolling_mean_std_welford_nb(pctchange, n)
    anomaly = np.zeros(length, dtype=np.bool_)
    for i in range(length):
        pc = pctchange[i]
        pm = pctma[i]
        ps = pctstd[i]
        if not np.isnan(pc) and not np.isnan(pm) and not np.isnan(ps):
            z = np.inf if ps == 0.0 and pc != pm else ((pc - pm) / ps if ps != 0.0 else np.nan)
            if (not np.isnan(z)) and abs(z) > 25.0 and abs(pc) > 0.2:
                anomaly[i] = True

    return _cci_causal_168_segments_nb(tp, anomaly, times_ns, has_times, n)


@njit(cache=True)
def _factor_111_param_nb(tp: np.ndarray, qvolume: np.ndarray, takervolume: np.ndarray, n: int) -> np.ndarray:
    length = tp.shape[0]
    if length == 0:
        return np.empty(0, dtype=np.float64)

    ma = _rolling_sum_mean_kahan_nb(tp, n, n, True)
    qv_ma = _rolling_sum_mean_kahan_nb(qvolume, n, n, True)

    qv_rel = np.empty(length, dtype=np.float64)
    qv_rel_abs = np.empty(length, dtype=np.float64)
    for i in range(length):
        q = qvolume[i]
        qm = qv_ma[i]
        if np.isnan(q) or np.isnan(qm):
            qv_rel[i] = np.nan
            qv_rel_abs[i] = np.nan
        elif qm == 0.0:
            r = np.nan if q == 0.0 else (np.inf if q > 0.0 else -np.inf)
            qv_rel[i] = r
            qv_rel_abs[i] = abs(r - 1.0)
        else:
            r = q / qm
            qv_rel[i] = r
            qv_rel_abs[i] = abs(r - 1.0)

    high = _rolling_sum_mean_kahan_nb(qv_rel, n, n, True)
    low = _rolling_sum_mean_kahan_nb(qv_rel_abs, n, n, True)
    vol1 = _rolling_sum_mean_kahan_nb(qvolume, n, 1, False)
    buy_vol = _rolling_sum_mean_kahan_nb(takervolume, n, 1, False)

    mtm = np.empty(length, dtype=np.float64)
    for i in range(length):
        h = high[i]
        lo = low[i]
        mx = h if h > lo else lo
        cs = mx / (1.0 + abs(mx))

        v1 = vol1[i]
        bv = buy_vol[i]
        if np.isnan(v1) or np.isnan(bv):
            bvr = np.nan
        elif v1 == 0.0:
            bvr = np.nan if bv == 0.0 else (np.inf if bv > 0.0 else -np.inf)
        else:
            bvr = bv / v1
        t1 = abs(bvr)
        t2 = abs(bvr - 1.0)
        tmax = t1 if t1 > t2 else t2

        m = ma[i]
        t = tp[i]
        if np.isnan(t) or np.isnan(m):
            tp_rel = np.nan
        elif m == 0.0:
            tp_rel = np.nan if t == 0.0 else (np.inf if t > 0.0 else -np.inf)
        else:
            tp_rel = t / m - 1.0

        mtm[i] = tp_rel * cs * tmax * abs(tp_rel)

    return _rolling_sum_mean_kahan_nb(mtm, n, 1, True)


@njit(cache=True)
def _rolling_corr_nb(x: np.ndarray, y: np.ndarray, n: int) -> np.ndarray:
    """Fast pairwise-valid sliding Pearson correlation (ddof=1) matching pandas rolling().corr()."""
    length = x.shape[0]
    out = np.full(length, np.nan, dtype=np.float64)
    if length == 0 or n < 2:
        return out

    sx = 0.0
    sy = 0.0
    sxx = 0.0
    syy = 0.0
    sxy = 0.0
    cnt = 0

    for i in range(length):
        xi = x[i]
        yi = y[i]
        if (not np.isnan(xi)) and (not np.isnan(yi)):
            sx += xi
            sy += yi
            sxx += xi * xi
            syy += yi * yi
            sxy += xi * yi
            cnt += 1
        if i >= n:
            xo = x[i - n]
            yo = y[i - n]
            if (not np.isnan(xo)) and (not np.isnan(yo)):
                sx -= xo
                sy -= yo
                sxx -= xo * xo
                syy -= yo * yo
                sxy -= xo * yo
                cnt -= 1
        if cnt >= 2:
            vx = sxx - (sx * sx) / cnt
            vy = syy - (sy * sy) / cnt
            if vx <= 1e-10 * max(1e-30, sxx) or vy <= 1e-10 * max(1e-30, syy):
                # Two-pass exact recompute on potential cancellation
                w0 = 0 if i < n else i - n + 1
                mx = 0.0
                my = 0.0
                c2 = 0
                for j in range(w0, i + 1):
                    xj = x[j]
                    yj = y[j]
                    if (not np.isnan(xj)) and (not np.isnan(yj)):
                        mx += xj
                        my += yj
                        c2 += 1
                if c2 >= 2:
                    mx /= c2
                    my /= c2
                    vx2 = 0.0
                    vy2 = 0.0
                    cxy2 = 0.0
                    for j in range(w0, i + 1):
                        xj = x[j]
                        yj = y[j]
                        if (not np.isnan(xj)) and (not np.isnan(yj)):
                            dx = xj - mx
                            dy = yj - my
                            vx2 += dx * dx
                            vy2 += dy * dy
                            cxy2 += dx * dy
                    if vx2 > 0.0 and vy2 > 0.0:
                        r = cxy2 / np.sqrt(vx2 * vy2)
                        if r > 1.0:
                            r = 1.0
                        elif r < -1.0:
                            r = -1.0
                        out[i] = r
            else:
                cxy = sxy - (sx * sy) / cnt
                r = cxy / np.sqrt(vx * vy)
                if r > 1.0:
                    r = 1.0
                elif r < -1.0:
                    r = -1.0
                out[i] = r
    return out


def rolling_rank_pct(values, n: int) -> np.ndarray:
    """Fast O(N log N) exact rolling percentile rank (ascending=True, pct=True, min_periods=1)."""
    arr = np.ascontiguousarray(values, dtype=np.float64)
    return _rolling_rank_pct_nb(arr, int(n))


def rolling_argmax_first(values, n: int) -> np.ndarray:
    """Fast O(N) exact rolling argmax (first max / first NaN, min_periods=1)."""
    arr = np.ascontiguousarray(values, dtype=np.float64)
    return _rolling_argmax_first_nb(arr, int(n))


def cci_causal_168_segments(tp: np.ndarray, anomaly: np.ndarray, times_ns: np.ndarray,
                            has_times: bool, n: int) -> np.ndarray:
    """Fast segmented CCI calculation + 168h causal block mask."""
    return _cci_causal_168_segments_nb(
        np.ascontiguousarray(tp, dtype=np.float64),
        np.ascontiguousarray(anomaly, dtype=np.bool_),
        np.ascontiguousarray(times_ns, dtype=np.int64),
        bool(has_times),
        int(n),
    )


def cci_causal_168_full(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                        times_ns: np.ndarray, has_times: bool, n: int) -> np.ndarray:
    """Full Numba implementation of Cci异常隔离168_因果版 for a single parameter."""
    return _cci_causal_168_full_nb(
        np.ascontiguousarray(high, dtype=np.float64),
        np.ascontiguousarray(low, dtype=np.float64),
        np.ascontiguousarray(close, dtype=np.float64),
        np.ascontiguousarray(times_ns, dtype=np.int64),
        bool(has_times),
        int(n),
    )


def factor_111_param(tp: np.ndarray, qvolume: np.ndarray, takervolume: np.ndarray, n: int) -> np.ndarray:
    """Full Numba implementation of factors.111 for a single parameter."""
    return _factor_111_param_nb(
        np.ascontiguousarray(tp, dtype=np.float64),
        np.ascontiguousarray(qvolume, dtype=np.float64),
        np.ascontiguousarray(takervolume, dtype=np.float64),
        int(n),
    )


def rolling_corr(x, y, n: int) -> np.ndarray:
    """Fast pairwise-valid sliding Pearson correlation matching pandas rolling(n, min_periods=1).corr()."""
    return _rolling_corr_nb(
        np.ascontiguousarray(x, dtype=np.float64),
        np.ascontiguousarray(y, dtype=np.float64),
        int(n),
    )
