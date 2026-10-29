"""ConditionalBeta1020 and the frozen robust_tail policy, without file globals."""
import numpy as np
import pandas as pd

LONG = [0, 1, 2, 3, 4, 13]
SHORT = list(range(5, 13))


def _clip_polygon(poly, a, bound):
    result = []
    for i, end in enumerate(poly):
        start = poly[i-1]
        ds, de = float(a@start-bound), float(a@end-bound)
        if (ds <= 1e-12) != (de <= 1e-12):
            result.append(start+ds/(ds-de)*(end-start))
        if de <= 1e-12:
            result.append(end)
    if not result:
        raise ValueError('Empty conditional-beta feasible polygon')
    return np.array(result)


def _polygon_optimum(poly, h, f):
    center = np.linalg.solve(h, f)
    candidates = list(poly)
    inside = True
    for i, start in enumerate(poly):
        edge = poly[(i+1)%len(poly)]-start
        if edge[0]*(center[1]-start[1])-edge[1]*(center[0]-start[0]) < -1e-10:
            inside = False
        denom = float(edge@h@edge)
        if denom > 1e-20:
            t = np.clip(float(edge@(f-h@start))/denom, 0, 1)
            candidates.append(start+t*edge)
    if inside:
        candidates.append(center)
    return candidates[int(np.argmin([.5*x@h@x-f@x for x in candidates]))]


def solve_sleeves(a, betas, cap=2.5):
    amount = np.maximum(np.abs(a).sum(axis=0), .01)
    h = np.diag(amount**2)
    poly = np.array([[0., 0.], [1., 0.], [1., 2.], [0., 2.]])
    rows = [a.sum(axis=0)]+[b@a for b in betas]
    for row in rows:
        poly = _clip_polygon(poly, row, .3)
        poly = _clip_polygon(poly, -row, .3)
    for _ in range(100):
        q = _polygon_optimum(poly, h, h@np.ones(2))
        x = a@q
        if np.abs(x).sum() <= cap+1e-9:
            break
        poly = _clip_polygon(poly, np.sign(x)@a, cap)
    else:
        raise ValueError('Conditional beta convex cuts did not converge')
    if max(abs(row@q) for row in rows) > .3+1e-8:
        raise ValueError('Conditional beta constraint failed')
    return q


def conditional_betas(close, minute, market='swap:BTCUSDT'):
    """Daily closes on each offset's own day; no current-day outcomes."""
    shifted = close.copy()
    shifted.index = shifted.index-pd.Timedelta(minutes=minute)
    daily = shifted.loc['2019':].resample('D').last().pct_change(fill_method=None)
    benchmark = daily[market]
    out = {}
    for mode in ['up', 'down']:
        y = daily.where(benchmark.ge(0) if mode == 'up' else benchmark.lt(0), axis=0)
        x = y.notna().mul(benchmark, axis=0).where(y.notna())
        count = y.rolling(120, min_periods=1).count()
        avg = lambda v: v.rolling(120, min_periods=15).mean()
        cov = avg(x*y)-avg(x)*avg(y)
        variance = (avg(x*x)-avg(x)**2).clip(lower=1e-12)
        out[mode] = (1+count/(count+60)*(cov/variance-1)).clip(0, 3).fillna(1).shift(1).fillna(1)
        out[mode].index += pd.Timedelta(minutes=minute)
    return out


def constrained_parent(hybrid, leverage, matrices, beta):
    """Keep research M60 rule, then project total long/short sleeve scales."""
    base = hybrid.copy();base.attrs = {};base[4] = .60
    effective = base.to_numpy()*leverage.to_numpy()
    long = sum(matrices[i].multiply(effective[:, i, None]) for i in LONG).tocsr()
    short = sum(matrices[i].multiply(effective[:, i, None]) for i in SHORT).tocsr()
    scales = np.ones((len(base), 2))
    for t in range(len(base)):
        a = np.column_stack([long.getrow(t).toarray().ravel(), short.getrow(t).toarray().ravel()])
        scales[t] = solve_sleeves(a, [beta['down'][t], beta['up'][t]])
    base.loc[:, LONG] *= scales[:, 0, None]
    base.loc[:, SHORT] *= scales[:, 1, None]
    if not np.isfinite(base.to_numpy()).all() or base.min().min() < -1e-10:
        raise ValueError('Invalid conditional parent')
    return base.clip(lower=0)


def expert_views(base, leverage, matrices, beta):
    """Eight half-removal/redistribution views, preserving S5 and constraints."""
    lev = leverage.to_numpy();weights = base.to_numpy()
    combined = sum(m.multiply((weights*lev)[:, i, None]) for i, m in enumerate(matrices)).tocsr()
    gross_by_child = np.column_stack([np.asarray(abs(m).sum(axis=1)).ravel() for m in matrices])
    exposures = np.zeros((len(base), 14, 3))
    for i, m in enumerate(matrices):
        exposures[:, i, 0] = np.asarray(m.sum(axis=1)).ravel()
        for k, name in enumerate(['up', 'down'], 1):
            exposures[:, i, k] = np.asarray(m.multiply(beta[name]).sum(axis=1)).ravel()
    reference = np.einsum('ij,ijk->ik', weights*lev, exposures)
    views = []
    for removed in SHORT:
        proposed = weights.copy()
        amount = weights*lev*gross_by_child
        released = amount[:, removed]*.5
        proposed[:, removed] *= .5
        ids = [i for i in SHORT if i != removed]
        budget = amount[:, ids].sum(axis=1)
        multiplier = np.minimum(2., 1+np.divide(released, budget, out=np.zeros(len(base)), where=budget > 1e-12))
        proposed[:, ids] *= multiplier[:, None]
        candidate = np.einsum('ij,ijk->ik', proposed*lev, exposures)
        change = candidate-reference
        ceilings = np.ones_like(change)
        np.divide(.3-reference, change, out=ceilings, where=change > 1e-12)
        negative = np.ones_like(change)
        np.divide(-.3-reference, change, out=negative, where=change < -1e-12)
        ceilings[change < -1e-12] = negative[change < -1e-12]
        fraction = np.clip(ceilings.min(axis=1), 0, 1)
        target = sum(m.multiply((proposed*lev)[:, i, None]) for i, m in enumerate(matrices)).tocsr()
        change_target = target-combined
        gross = np.asarray(abs(combined+change_target.multiply(fraction[:, None])).sum(axis=1)).ravel()
        if (gross > 2.5+1e-9).any():
            lo, hi = np.zeros(len(base)), fraction.copy()
            for _ in range(34):
                mid = (lo+hi)/2
                value = np.asarray(abs(combined+change_target.multiply(mid[:, None])).sum(axis=1)).ravel()
                lo = np.where(value <= 2.5, mid, lo);hi = np.where(value > 2.5, mid, hi)
            fraction = np.where(gross > 2.5+1e-9, lo, fraction)
        out = weights+(proposed-weights)*fraction[:, None]
        final_risk = reference+change*fraction[:, None]
        if np.abs(final_risk).max() > .3+1e-8 or not np.isfinite(out).all():
            raise ValueError('Expert view risk constraint failed')
        np.testing.assert_allclose(out[:, 4], weights[:, 4], rtol=0, atol=0)
        views.append(pd.DataFrame(out, index=base.index, columns=base.columns))
    return views


def robust_weekly_mix(net, momentum):
    """Frozen robust_tail policy: 365 prior days, 180-day minimum, Monday updates."""
    if net.shape[1] != 9:
        raise ValueError('ROBUST40 needs base plus eight virtual accounts')
    days = net.index
    mom = momentum.reindex(days)
    out = pd.DataFrame(0., index=days, columns=net.columns)
    current = np.r_[1., np.zeros(8)]
    for i, day in enumerate(days):
        if day.weekday() == 0:
            history = days[max(0, i-365):i]
            history = history[mom.reindex(history).notna()]
            if len(history) >= 180:
                tail = mom.loc[history].le(mom.loc[history].quantile(.2)).to_numpy()
                if tail.sum() >= 30:
                    delta = net.loc[history].iloc[:, 1:].to_numpy()-net.loc[history].iloc[:, [0]].to_numpy()
                    if not np.isfinite(delta).all():
                        raise ValueError('Incomplete virtual-account daily returns')
                    score = delta.mean(0)+delta[tail].mean(0)
                    blocks = pd.DataFrame(delta*(1+tail[:, None]/tail.mean())).groupby(np.arange(len(delta))//7).mean()
                    score -= 1.28*blocks.std(ddof=1).to_numpy()/np.sqrt(len(blocks))
                    current = np.r_[1., np.zeros(8)]
                    chosen = np.argsort(-score, kind='stable')[:3]
                    chosen = chosen[score[chosen] > 0]
                    if len(chosen):
                        current[0] = .5
                        current[1+chosen] = .5*score[chosen]/score[chosen].sum()
        out.iloc[i] = current
    return out
