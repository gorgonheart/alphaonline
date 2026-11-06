"""MODEL_ROBUST40 仓管策略：完整计算、专家/季度模型、续算与框架接口。

策略名、仓管参数、子策略池和offset由每个accounts文件独立声明；本文件不导入
任何固定账户配置。公共行情冻结、磁盘快照事务、调度和日志功能位于tools。

阅读顺序：框架入口 → 第2/3/7节策略计算 → 第8/10/11节状态与实盘接入。
第5/6节是保留原公式的模拟内核；本次归并不改变公式、容差或snapshot格式。
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import numba as nb
from numba.experimental import jitclass
from scipy import sparse

from tools.local_risk_model import lock, atomic_save
from tools.robust40.offline import canonical, rename_columns
from positions.HybridRotationDriftStrategy import calc_ratio as calc_hybrid_ratio
from tools.robust40.replay import before


# 框架只调用以下三个入口；内部函数也供离线对拍直接复用。
def effective_parent_params(params):
    """仅排除已确认未读取的旧研究字段；有效及未知参数仍参与严格比较。"""
    ignored = {'method', 'gross_cap', 'risk_definitions', 'momentum_60',
               'short_single_coin_max_ratio', 'research_inputs', 'child_returns',
               'conditional_risk_path'}
    return {key: value for key, value in params.items() if key not in ignored}


def prepare_live(me_conf, account, run_time):
    prepare(me_conf, account, run_time)


def calc_ratio(equity_dfs, stg_conf):
    return parent_ratios(equity_dfs, stg_conf)


def transform_targets(spot, swap, stg_conf):
    result = validate_targets(spot, swap, stg_conf)
    if stg_conf.params.get('runtime', {}).get('server_init'):
        from tools.robust40.server import finish_live
        finish_live(stg_conf)
    return result



# 1. 仓管专属特征与决策时钟（不是选币框架因子入口）

FEATURES = ['ret1', 'ret24', 'ret168', 'drawdown168', 'vol_ratio', 'downside168',
            'volume_ratio', 'log_volume24', 'amihud24', 'taker24', 'funding24', 'age_days']
MODEL_FEATURES = FEATURES + ['breadth_down', 'market_ret24', 'dispersion']


def utc_naive(value):
    t = pd.Timestamp(value)
    return t.tz_convert('UTC').tz_localize(None) if t.tzinfo is not None else t


def decision_days(index, minute):
    index = pd.DatetimeIndex(index)
    if not 0 <= minute < 60 or not (index.minute == minute).all() or (index.second != 0).any():
        raise ValueError('Decision minute does not match the account grid')
    return (index-pd.Timedelta(minutes=minute)).normalize()+pd.Timedelta(minutes=minute)


def select_quarter_model(models, timestamp):
    now = utc_naive(timestamp)
    candidates = [(utc_naive(k), k) for k in models if utc_naive(k) <= now]
    return models[max(candidates, key=lambda pair: pair[0])[1]] if candidates else None


def coin_features(df):
    """Use observed bar data only; callers apply the research one-bar lag."""
    c = df['close'].astype(float).where(lambda v: v > 0)
    r = c.pct_change(fill_method=None)
    volume = df['quote_volume'].astype(float).where(lambda v: v > 0)
    out = pd.DataFrame(index=df.index)
    out['ret1'] = r
    out['ret24'] = c.pct_change(24, fill_method=None)
    out['ret168'] = c.pct_change(168, fill_method=None)
    out['drawdown168'] = c/c.rolling(168, min_periods=168).max()-1
    sigma = r.rolling(168, min_periods=120).std()
    out['vol_ratio'] = r.rolling(24, min_periods=20).std()/sigma.replace(0, np.nan)
    out['downside168'] = r.clip(upper=0).pow(2).rolling(168, min_periods=120).mean().pow(.5)
    out['volume_ratio'] = volume.rolling(24, min_periods=20).mean()/volume.rolling(168, min_periods=120).mean()
    out['log_volume24'] = np.log1p(volume.rolling(24, min_periods=20).sum())
    out['amihud24'] = np.log1p((r.abs()/volume*1e6).rolling(24, min_periods=20).mean())
    if 'taker_buy_quote_asset_volume' in df:
        out['taker24'] = df['taker_buy_quote_asset_volume'].rolling(24, min_periods=20).sum()/volume.rolling(24, min_periods=20).sum()
    else:
        out['taker24'] = np.nan
    funding = df['funding_fee'] if 'funding_fee' in df else pd.Series(np.nan, index=df.index)
    out['funding24'] = funding.rolling(24, min_periods=20).sum()
    # Arrow inputs are already typed. Re-parsing samples repeated timestamps
    # for pandas' string cache, boxing thousands of Python dates per symbol.
    times = df['candle_begin_time']
    if not (isinstance(times.dtype, pd.DatetimeTZDtype) or
            isinstance(times.dtype, np.dtype) and times.dtype.kind == 'M'):
        times = pd.to_datetime(times)
    if 'first_candle_time' in df:
        first = df['first_candle_time']
        if not (isinstance(first.dtype, pd.DatetimeTZDtype) or
                isinstance(first.dtype, np.dtype) and first.dtype.kind == 'M'):
            first = pd.to_datetime(first)
    else:
        first = times.iloc[0]
    out['age_days'] = (times-first).dt.total_seconds()/86400
    return out.replace([np.inf, -np.inf], np.nan)


# 2. 父权重约束、专家组合与周度混合

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


# 当前MODEL调用方不传账户parent.gross_cap；这里的2.5与expert_views中的2.5共同定义V1约束。
# 指同币净额后绝对目标敞口之和；执行层2.45是另一层限制。
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


# 使用本offset输入行情计算条件beta，不读取parent.conditional_risk_path；up/down由算法固定。
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
    # V1固定S5基准0.60，不读取parent.momentum_60；后续约束/再择时/减仓仍可改变最终贡献。
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


# 3. 季度分类模型与S5减仓

def observations(raw, index, held, minute, after_day=None):
    """Match the research universe and lag; keep new intraday members unscored."""
    index = pd.DatetimeIndex(index)
    decision_days(index, minute)
    if not index.equals(held.index):
        raise ValueError('S5 holdings time axis differs from observation axis')
    days = index[index.hour == 0]
    if after_day is not None:
        days = days[days > after_day]
    count = np.zeros(len(index));down = count.copy();total = count.copy();square = count.copy()
    records = []
    for symbol in held.columns:
        if symbol not in raw:
            if held.loc[days, symbol].ne(0).any():
                raise ValueError(f'Missing held-symbol candles: {symbol}')
            continue
        df = raw[symbol].sort_values('candle_begin_time').drop_duplicates('candle_begin_time').reset_index(drop=True)
        df = df.loc[df.candle_begin_time <= index[-1]].copy()
        if len(df) < 168:
            continue
        f = coin_features(df)
        f.index = pd.DatetimeIndex(df.candle_begin_time)
        f = f.reindex(index).shift(1)
        valid = f.ret24.notna() & f.age_days.ge(7)
        returns = f.ret24.where(valid, 0).to_numpy()
        count += valid.to_numpy();down += (valid & f.ret24.lt(0)).to_numpy()
        total += returns;square += returns**2
        selected = held.loc[days, symbol].gt(0)
        if not selected.any():
            continue
        if df.candle_begin_time.max() < days[selected][-1]:
            raise ValueError(f'Stale held-symbol candles: {symbol}')
        price = df.set_index('candle_begin_time')
        if 'avg_price_1m' not in price:
            raise ValueError('Research forward label requires avg_price_1m')
        future = price.close.reindex(index).shift(-24)/price.avg_price_1m.reindex(index).shift(-1)-1
        obs = f.loc[days[selected], FEATURES].copy()
        obs['symbol'] = symbol;obs['time'] = obs.index;obs['forward24'] = future.reindex(obs.index)
        records.append(obs.reset_index(drop=True))
    if not records:
        return pd.DataFrame(columns=['time', 'symbol', 'forward24']+MODEL_FEATURES)
    average = np.divide(total, count, out=np.zeros(len(index)), where=count > 0)
    market = pd.DataFrame(dict(
        breadth_down=np.divide(down, count, out=np.zeros(len(index)), where=count > 0),
        market_ret24=average,
        dispersion=np.sqrt(np.maximum(np.divide(square, count, out=np.zeros(len(index)), where=count > 0)-average**2, 0))), index=index)
    out = pd.concat(records, ignore_index=True)
    for col in market:
        out[col] = market[col].reindex(out.time).to_numpy()
    return out.sort_values(['time', 'symbol']).reset_index(drop=True)


def quarterly_predictions(obs, end, previous=None):
    """Training parameters remain frozen; quarters without 180 days stay unscored."""
    from time import perf_counter
    from tools.robust40 import telemetry
    started=perf_counter();trained=0;reused=0;fit_seconds=0.;actions={}
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.linear_model import LogisticRegression
    out = obs.copy();out['MODEL'] = np.nan
    models = {};windows = []
    for begin in pd.date_range('2021-07-01', pd.Timestamp(end).normalize(), freq='QS'):
        train = obs[(obs.time >= begin-pd.Timedelta(days=365)) &
                    (obs.time < begin-pd.Timedelta(hours=48)) & obs.forward24.notna()]
        mask = (obs.time >= begin) & (obs.time < begin+pd.offsets.QuarterBegin(startingMonth=1))
        test = obs[mask]
        if train.time.nunique() < 180 or test.empty:
            continue
        y = train.forward24.lt(-.05).astype(int)
        if y.nunique() < 2:
            raise ValueError(f'Quarter {begin} has only one training class')
        model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(),
                              LogisticRegression(C=.1, max_iter=500, solver='lbfgs'))
        cached = previous['models'].get(begin) if previous is not None else None
        reusable = False
        if cached is not None:
            prior = previous['predictions']
            prior = prior[(prior.time >= begin-pd.Timedelta(days=365)) &
                          (prior.time < begin-pd.Timedelta(hours=48)) & prior.forward24.notna()]
            # Compare actual training inputs/labels, not raw prices or file dates.
            spec = lambda p: [(type(s), repr(s.get_params(deep=False))) for _, s in p.steps]
            reusable = (tuple(getattr(cached,'feature_names_in_',())) == tuple(MODEL_FEATURES) and
                spec(cached) == spec(model) and
                train[['time','symbol']].reset_index(drop=True).equals(prior[['time','symbol']].reset_index(drop=True)) and
                train[MODEL_FEATURES].clip(-1e6,1e6).reset_index(drop=True).equals(prior[MODEL_FEATURES].clip(-1e6,1e6).reset_index(drop=True)) and
                y.reset_index(drop=True).equals(prior.forward24.lt(-.05).astype(int).reset_index(drop=True)))
        if reusable:
            model = cached
            reused += 1
            actions[str(begin.date())]='reused'
        else:
            fit_start=perf_counter()
            model.fit(train[MODEL_FEATURES].clip(-1e6, 1e6), y)
            fit_seconds += perf_counter()-fit_start
            trained += 1
            actions[str(begin.date())]='uncached' if cached is None else 'training_changed'
        out.loc[mask, 'MODEL'] = model.predict_proba(test[MODEL_FEATURES].clip(-1e6, 1e6))[:, 1]
        models[begin] = model
        windows.append(dict(quarter=begin, train_days=train.time.nunique(), train_rows=len(train),
                            last_label_end=train.time.max()+pd.Timedelta(hours=24)))
    telemetry.add(scoring_calls=1,trained_quarters=trained,reused_quarters=reused,
                  fit_s=round(fit_seconds,6),scoring_s=round(perf_counter()-started,6))
    telemetry.record(quarter_actions=actions)
    telemetry.emit('quarterly_scoring')
    return out, models, pd.DataFrame(windows)


def s5_delta(predictions, held, parent, leverage, minute):
    scores = predictions.pivot(index='time', columns='symbol', values='MODEL')
    scores = scores.reindex(columns=held.columns).reindex(decision_days(held.index, minute)).to_numpy()
    rank = pd.DataFrame(np.where(held.to_numpy() > 0, scores, np.nan)).rank(axis=1, pct=True).to_numpy()
    contribution = held.mul(parent[4]*leverage[4], axis=0)
    delta = -.5*contribution*(rank > .8)
    if not np.isfinite(delta.to_numpy()).all() or delta.max().max() > 0:
        raise ValueError('Invalid S5 risk delta')
    return delta


# 4. S5减仓叠加

def apply_s5_delta(net, delta):
    if not net.index.equals(delta.index):
        raise ValueError('Delta time index must exactly match target time index')
    if not net.columns.is_unique or not delta.columns.is_unique:
        raise ValueError('Duplicate target symbols')
    if not np.isfinite(delta.to_numpy()).all() or (delta.to_numpy() > 0).any():
        raise ValueError('S5 delta must be finite and nonpositive')
    extra = delta.columns.difference(net.columns)
    if len(extra) and delta[extra].ne(0).any().any():
        raise ValueError('Nonzero S5 delta has no corresponding target symbol')
    aligned = delta.reindex(columns=net.columns, fill_value=0)
    # The reference applies negative deltas only to a positive net target.
    return net.where(net <= 0, (net+aligned).clip(lower=0))


# 5. 换仓死区与目标手数（保留原研究算法）

@nb.njit(cache=True)
def _filter_execution_lots(target, current, prices, sizes, min_order_usdt_ratio):
    difference, amount = calc_delta_lots_amount(target, current, prices, sizes)
    threshold = np.abs(current)*sizes*prices*min_order_usdt_ratio
    return current+filter_deltas(target, current, difference, amount, threshold)


@nb.njit(cache=True)
def execution_lots(equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios,
                   spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio, execution_cap, long_only_equity_ratio):
    # Shared mathematics for simulation and live calls. Explicit scalars avoid
    # cached globals retaining account parameters; no jitclass crosses this API.
    if not np.isfinite(equity) or equity <= 0:
        raise ValueError('Invalid execution equity')
    for j in range(len(spot_lots)):
        if not np.isfinite(spot_ratios[j]) or not np.isfinite(spot_lot_sizes[j]) or spot_lot_sizes[j] <= 0:
            raise ValueError('Invalid spot target or lot size')
    for j in range(len(swap_lots)):
        if not np.isfinite(swap_ratios[j]) or not np.isfinite(swap_lot_sizes[j]) or swap_lot_sizes[j] <= 0:
            raise ValueError('Invalid swap target or lot size')
    budget = equity*long_only_equity_ratio if np.sum(np.abs(swap_ratios)) < 1e-6 else equity
    s = calc_target_lots_by_ratio(budget, spot_prices, spot_ratios, spot_lot_sizes)
    u = calc_target_lots_by_ratio(budget, swap_prices, swap_ratios, swap_lot_sizes)
    s = _filter_execution_lots(s, spot_lots, spot_prices, spot_lot_sizes, min_order_usdt_ratio)
    u = _filter_execution_lots(u, swap_lots, swap_prices, swap_lot_sizes, min_order_usdt_ratio)
    gross = 0.
    for j in range(len(s)):
        if s[j] != 0:
            if not np.isfinite(spot_prices[j]) or spot_prices[j] <= 0:
                raise ValueError('Invalid held spot price')
            gross += abs(s[j]*spot_lot_sizes[j]*spot_prices[j]/equity)
    for j in range(len(u)):
        if u[j] != 0:
            if not np.isfinite(swap_prices[j]) or swap_prices[j] <= 0:
                raise ValueError('Invalid held swap price')
            gross += abs(u[j]*swap_lot_sizes[j]*swap_prices[j]/equity)
    if gross > execution_cap:
        s = (s*(execution_cap/gross)).astype(np.int64)
        u = (u*(execution_cap/gross)).astype(np.int64)
    return s, u


@jitclass
class RebRobust40:
    spot_lot_sizes: nb.float64[:]
    swap_lot_sizes: nb.float64[:]
    min_order_usdt_ratio: float
    execution_cap: float

    def __init__(self, spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio=.4, execution_cap=2.45):
        self.spot_lot_sizes = spot_lot_sizes.copy()
        self.swap_lot_sizes = swap_lot_sizes.copy()
        self.min_order_usdt_ratio = min_order_usdt_ratio
        self.execution_cap = execution_cap

    def _filter(self, target, current, prices, sizes):
        difference, amount = calc_delta_lots_amount(target, current, prices, sizes)
        threshold = np.abs(current)*sizes*prices*self.min_order_usdt_ratio
        return current+filter_deltas(target, current, difference, amount, threshold)

    def calc_lots(self, equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios):
        return execution_lots(equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios,
                              self.spot_lot_sizes, self.swap_lot_sizes, self.min_order_usdt_ratio,
                              self.execution_cap, LONG_ONLY_EQUITY_RATIO)

LONG_ONLY_EQUITY_RATIO = .97

@nb.njit(cache=True)
def calc_target_lots_by_ratio(equity, prices, ratios, lot_sizes):
    """
    根据目标持仓比例，计算目标持仓手数
    """
    target_lots = np.zeros(len(lot_sizes), dtype=np.int64)
    target_equity = equity * ratios
    mask = np.logical_and(np.abs(target_equity) > 0.01, np.logical_and(prices != 0, lot_sizes != 0))
    target_lots[mask] = (target_equity[mask] / prices[mask] / lot_sizes[mask]).astype(np.int64)
    int64_max = np.iinfo(np.int64).max
    int64_min = np.iinfo(np.int64).min
    extreme_value_mask = (target_lots == int64_max) | (target_lots == int64_min)
    if np.any(extreme_value_mask):
        extreme_indices = np.where(extreme_value_mask)[0]
        print(f'警告：发现 {len(extreme_indices)} 个int64极值，已强制设置为0')
        target_lots[extreme_value_mask] = 0
    return target_lots

@nb.njit(cache=True)
def calc_delta_lots_amount(target_lots, current_lots, prices, lot_sizes):
    """
    计算调仓手数和金额
    """
    delta_lots = target_lots - current_lots
    delta_amount = np.zeros(len(lot_sizes), dtype=np.float64)
    mask = delta_lots != 0
    delta_amount[mask] = np.abs(delta_lots[mask]) * lot_sizes[mask] * prices[mask]
    return (delta_lots, delta_amount)

@nb.njit(cache=True)
def filter_deltas(target_lots, current_lots, delta_lots, delta_amount, min_order_limit):
    mask_builds = np.logical_and(current_lots == 0, target_lots != 0)
    mask_liquidations = np.logical_and(current_lots != 0, target_lots == 0)
    mask_bld_liq = np.logical_or(mask_builds, mask_liquidations)
    mask_invalid = np.logical_and(delta_amount < min_order_limit, np.logical_not(mask_bld_liq))
    delta_lots[mask_invalid] = 0
    return delta_lots


# 6. 仓管虚拟账户模拟（原框架版权：邢不行）

@jitclass
class Simulator:
    equity: float
    fee_rate: float
    min_order_limit: float
    lot_sizes: nb.float64[:]
    lots: nb.int64[:]
    target_lots: nb.int64[:]
    last_prices: nb.float64[:]
    has_last_prices: bool

    def __init__(self, init_capital, lot_sizes, fee_rate, init_lots, min_order_limit):
        """
        初始化
        :param init_capital: 初始资金
        :param lot_sizes: 每个币种的最小下单量
        :param fee_rate: 手续费率
        :param init_lots: 初始持仓
        :param min_order_limit: 最小下单金额
        """
        self.equity = init_capital
        self.fee_rate = fee_rate
        self.min_order_limit = min_order_limit
        n = len(lot_sizes)
        self.lot_sizes = np.zeros(n, dtype=np.float64)
        self.lot_sizes[:] = lot_sizes
        self.last_prices = np.zeros(n, dtype=np.float64)
        self.has_last_prices = False
        self.lots = np.zeros(n, dtype=np.int64)
        self.lots[:] = init_lots
        self.target_lots = np.zeros(n, dtype=np.int64)
        self.target_lots[:] = init_lots

    def set_target_lots(self, target_lots):
        self.target_lots[:] = target_lots

    def adjust_capital(self, delta):
        """真金白银出入金：直接增减账户现金权益（USDT）。"""
        self.equity += delta

    def fill_last_prices(self, prices):
        mask = np.logical_not(np.isnan(prices))
        self.last_prices[mask] = prices[mask]
        self.has_last_prices = True

    def settle_equity(self, prices):
        """
        结算当前账户权益
        :param prices: 当前价格
        :return:
        """
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(prices)))
        equity_delta = np.sum((prices[mask] - self.last_prices[mask]) * self.lot_sizes[mask] * self.lots[mask])
        self.equity += equity_delta

    def on_open(self, open_prices, funding_rates, mark_prices):
        """
        模拟: K 线开盘 -> K 线收盘时刻
        :param open_prices: 开盘价
        :param funding_rates: 资金费
        :param mark_prices: 计算资金费的标记价格（目前就用开盘价来）
        :return:
        """
        if not self.has_last_prices:
            self.fill_last_prices(open_prices)
        self.settle_equity(open_prices)
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(mark_prices)))
        pos_val = notional_value = self.lot_sizes[mask] * self.lots[mask] * mark_prices[mask]
        funding_fee = np.sum(notional_value * funding_rates[mask])
        self.equity -= funding_fee
        self.fill_last_prices(open_prices)
        return (self.equity, funding_fee, pos_val)

    def on_execution(self, exec_prices):
        """
        模拟: K 线开盘时刻 -> 调仓时刻
        :param exec_prices:  执行价格
        :return:            调仓后的账户权益、调仓后的仓位名义价值
        """
        if not self.has_last_prices:
            self.fill_last_prices(exec_prices)
        self.settle_equity(exec_prices)
        delta = self.target_lots - self.lots
        mask = np.logical_and(delta != 0, np.logical_not(np.isnan(exec_prices)))
        turnover = np.zeros(len(self.lot_sizes), dtype=np.float64)
        turnover[mask] = np.abs(delta[mask]) * self.lot_sizes[mask] * exec_prices[mask]
        mask = np.logical_and(mask, turnover >= self.min_order_limit)
        turnover_total = turnover[mask].sum()
        if np.isnan(turnover_total):
            raise RuntimeError('Turnover is nan')
        fee = turnover_total * self.fee_rate
        self.equity -= fee
        self.lots[mask] = self.target_lots[mask]
        self.fill_last_prices(exec_prices)
        return (self.equity, turnover_total, fee)

    def on_close(self, close_prices):
        """
        模拟: K 线收盘 -> K 线收盘时刻
        :param close_prices: 收盘价
        :return:           收盘后的账户权益
        """
        if not self.has_last_prices:
            self.fill_last_prices(close_prices)
        self.settle_equity(close_prices)
        self.fill_last_prices(close_prices)
        mask = np.logical_and(self.lots != 0, np.logical_not(np.isnan(close_prices)))
        pos_val = self.lot_sizes[mask] * self.lots[mask] * close_prices[mask]
        return (self.equity, pos_val)

@nb.njit(cache=True)
def compute_capital_flow(equity, flow_val, mode):
    """
    根据本期收盘权益与规则载荷，算出实际出入金 C（钱加权口径，不维护份额/NAV）。
    :param equity: 本期收盘账户权益 E_i（注资前）
    :param flow_val: 规则载荷（mode==0 绝对 USDT / mode==1 占权益比例(小数) / mode==2 自动识别）
    :param mode: -1 关闭 / 0 绝对 USDT / 1 占权益比例(小数,如0.1=10%) / 2 自动识别
        （mode==2：|flow_val|<=100 按百分比(flow_val%,如20=20%)，|flow_val|>100 按绝对 USDT。
         因最小下单金额限制，<100 USDT 的绝对出入金意义不大，故把 0-100 区间复用为百分比比例）
    :return: C 实际出入金
    """
    C = 0.0
    if mode < 0 or equity < 0.0:
        return C
    if mode == 0:
        C = flow_val
    elif mode == 1:
        if equity > 0.0:
            C = flow_val * equity
    elif abs(flow_val) <= 100.0:
        if equity > 0.0:
            C = flow_val / 100.0 * equity
    else:
        C = flow_val
    if C < 0.0 and equity + C < 0.0:
        C = -equity
    return C

@nb.jit(nopython=True, boundscheck=True, cache=True)
def start_simulation(init_capital, leverages, spot_lot_sizes, swap_lot_sizes, spot_c_rate, swap_c_rate, spot_min_order_limit, swap_min_order_limit, min_margin_rate, spot_ratio, swap_ratio, spot_open_p, spot_close_p, spot_vwap1m_p, swap_open_p, swap_close_p, swap_vwap1m_p, funding_rates, min_order_usdt_ratio, execution_cap, require_rebalance, capital_flow_arr, capital_flow_mode, resume_state=None):
    """
    模拟交易
    :param init_capital: 初始资金
    :param leverages: 杠杆
    :param spot_lot_sizes: spot 现货的最小下单量
    :param swap_lot_sizes: swap 合约的最小下单量
    :param spot_c_rate: spot 现货的手续费率
    :param swap_c_rate: swap 合约的手续费率
    :param spot_min_order_limit: spot 现货最小下单金额
    :param swap_min_order_limit: swap 合约最小下单金额
    :param min_margin_rate: 维持保证金率
    :param spot_ratio: spot 的仓位透视表 (numpy 矩阵)
    :param swap_ratio: swap 的仓位透视表 (numpy 矩阵)
    :param spot_open_p: spot 的开仓价格透视表 (numpy 矩阵)
    :param spot_close_p: spot 的平仓价格透视表 (numpy 矩阵)
    :param spot_vwap1m_p: spot 的 vwap1m 价格透视表 (numpy 矩阵)
    :param swap_open_p: swap 的开仓价格透视表 (numpy 矩阵)
    :param swap_close_p: swap 的平仓价格透视表 (numpy 矩阵)
    :param swap_vwap1m_p: swap 的 vwap1m 价格透视表 (numpy 矩阵)
    :param funding_rates: swap 的 funding rate 透视表 (numpy 矩阵)
    :param min_order_usdt_ratio: 换仓死区比例（原pos_calc参数）
    :param execution_cap: 原pos_calc总敞口约束
    :param require_rebalance: 是否需要调仓
    :return:
    """
    # Cache boundary contains only stable array/scalar/tuple types. Construct the
    # original jitclass inside native code, never box it or put its type in the
    # cross-process cache signature. All kernel dependencies stay in this file.
    pos_calc = RebRobust40(spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio, execution_cap)
    n_bars = spot_ratio.shape[0]
    n_syms_spot = spot_ratio.shape[1]
    n_syms_swap = swap_ratio.shape[1]
    start_lots_spot = np.zeros(n_syms_spot, dtype=np.int64)
    start_lots_swap = np.zeros(n_syms_swap, dtype=np.int64)
    funding_rates_spot = np.zeros(n_syms_spot, dtype=np.float64)
    turnovers = np.zeros(n_bars, dtype=np.float64)
    fees = np.zeros(n_bars, dtype=np.float64)
    equities = np.zeros(n_bars, dtype=np.float64)
    funding_fees = np.zeros(n_bars, dtype=np.float64)
    margin_rates = np.zeros(n_bars, dtype=np.float64)
    long_pos_values = np.zeros(n_bars, dtype=np.float64)
    short_pos_values = np.zeros(n_bars, dtype=np.float64)
    capital_flows = np.zeros(n_bars, dtype=np.float64)
    sim_spot = Simulator(init_capital, spot_lot_sizes, spot_c_rate, start_lots_spot, spot_min_order_limit)
    sim_swap = Simulator(0, swap_lot_sizes, swap_c_rate, start_lots_swap, swap_min_order_limit)
    if resume_state is not None:
        ss, su = resume_state
        sim_spot.equity = ss[0]
        sim_spot.lots[:] = ss[1]
        sim_spot.target_lots[:] = ss[2]
        sim_spot.last_prices[:] = ss[3]
        sim_spot.has_last_prices = ss[4]
        sim_swap.equity = su[0]
        sim_swap.lots[:] = su[1]
        sim_swap.target_lots[:] = su[2]
        sim_swap.last_prices[:] = su[3]
        sim_swap.has_last_prices = su[4]
    for i in range(n_bars):
        '1. 模拟开盘on_open'
        equity_spot, _, pos_value_spot = sim_spot.on_open(spot_open_p[i], funding_rates_spot, spot_open_p[i])
        equity_swap, funding_fee, pos_value_swap = sim_swap.on_open(swap_open_p[i], funding_rates[i], swap_open_p[i])
        position_val = np.sum(np.abs(pos_value_spot)) + np.sum(np.abs(pos_value_swap))
        if position_val < 1e-08:
            margin_rate = 10000.0
        else:
            margin_rate = (equity_spot + equity_swap) / float(position_val)
        if margin_rate < min_margin_rate:
            margin_rates[i] = margin_rate
            break
        '2. 模拟开仓on_execution'
        equity_spot, turnover_spot, fee_spot = sim_spot.on_execution(spot_vwap1m_p[i])
        equity_swap, turnover_swap, fee_swap = sim_swap.on_execution(swap_vwap1m_p[i])
        '3. 模拟K线结束on_close'
        equity_spot_close, pos_value_spot_close = sim_spot.on_close(spot_close_p[i])
        equity_swap_close, pos_value_swap_close = sim_swap.on_close(swap_close_p[i])
        long_pos_value = np.sum(pos_value_spot_close[pos_value_spot_close > 0]) + np.sum(pos_value_swap_close[pos_value_swap_close > 0])
        short_pos_value = -(np.sum(pos_value_spot_close[pos_value_spot_close < 0]) + np.sum(pos_value_swap_close[pos_value_swap_close < 0]))
        funding_fees[i] = funding_fee
        equities[i] = equity_spot_close + equity_swap_close
        turnovers[i] = turnover_spot + turnover_swap
        fees[i] = fee_spot + fee_swap
        margin_rates[i] = margin_rate
        long_pos_values[i] = long_pos_value
        short_pos_values[i] = short_pos_value
        E_i = equity_spot_close + equity_swap_close
        C = compute_capital_flow(E_i, capital_flow_arr[i], capital_flow_mode)
        if C != 0.0:
            sim_spot.adjust_capital(C)
        capital_flows[i] = C
        equity_leveraged = (equity_spot_close + equity_swap_close + C) * leverages[i]
        '4. 计算目标持仓'
        if require_rebalance[i] == 1:
            target_lots_spot, target_lots_swap = pos_calc.calc_lots(equity_leveraged, spot_close_p[i], sim_spot.lots, spot_ratio[i], swap_close_p[i], sim_swap.lots, swap_ratio[i])
            sim_spot.set_target_lots(target_lots_spot)
            sim_swap.set_target_lots(target_lots_swap)
    state = ((sim_spot.equity, sim_spot.lots.copy(), sim_spot.target_lots.copy(), sim_spot.last_prices.copy(), sim_spot.has_last_prices),
             (sim_swap.equity, sim_swap.lots.copy(), sim_swap.target_lots.copy(), sim_swap.last_prices.copy(), sim_swap.has_last_prices))
    return (equities, turnovers, fees, funding_fees, margin_rates, long_pos_values, short_pos_values, capital_flows, state)


# 7. 完整计算与增量计算

def portfolio_targets(weights, context):
    effective = weights.reindex(context['index']).fillna(0).to_numpy()*context['leverage'].to_numpy()
    combined = sum(m.multiply(effective[:, i, None]) for i, m in enumerate(context['matrices'])).tocsr()
    cols = context['columns'];out = {}
    for kind in ['spot', 'swap']:
        loc = [i for i, c in enumerate(cols) if c.startswith(kind+':')]
        out[kind] = pd.DataFrame(combined[:, loc].toarray(), index=context['index'],
                                columns=[cols[i].split(':', 1)[1] for i in loc])
    return out


def simulate(targets, context, buffer, resume_state=None, checkpoint_hours=0):
    if checkpoint_hours:
        return simulate_checkpoints(targets, context, buffer, resume_state, checkpoint_hours)
    args = context['simulation']
    result = start_simulation(**args, min_order_usdt_ratio=buffer, execution_cap=2.45, resume_state=resume_state,
        spot_ratio=np.ascontiguousarray(targets['spot'].to_numpy()),
        swap_ratio=np.ascontiguousarray(targets['swap'].to_numpy()))
    out = pd.DataFrame(dict(zip(['equity', 'turnover', 'fee', 'funding_fee', 'marginRatio',
                                'long_pos_value', 'short_pos_value', 'cash_flow'], result)), index=context['index'])
    if not np.isfinite(out.equity).all() or out.equity.le(0).any():
        raise ValueError('Virtual account depleted or invalid')
    out.attrs['simulation_state'] = result[-1]
    return out


def calculate(context, hybrid_weights, raw_swap, progress=lambda message: None, checkpoint_hours=0):
    """All time axes are UTC-naive internally, with the offset preserved."""
    index = context['index'];minute = context['minute']
    day_keys = decision_days(index, minute)
    if index.tz is not None:
        raise ValueError('Engine context must use UTC-naive timestamps')
    if len(context['matrices']) != 14 or not hybrid_weights.index.isin(index).all():
        raise ValueError('Invalid 14-child context')
    progress('conditional beta')
    risk = conditional_betas(context['close'], minute)
    times = hybrid_weights.index
    risk_days = decision_days(times, minute)
    beta = {k: v.reindex(index=risk_days, columns=context['columns']).to_numpy() for k, v in risk.items()}
    if any(not np.isfinite(v).all() for v in beta.values()):
        raise ValueError('Conditional beta history missing')
    loc = index.get_indexer(times)
    matrices = [m[loc] for m in context['matrices']]
    lev = context['leverage'].loc[times]
    base = constrained_parent(hybrid_weights, lev, matrices, beta)
    progress('eight constrained expert views')
    views = expert_views(base, lev, matrices, beta)
    daily = {}
    accounts = []
    for i, weights in enumerate([base]+views):
        account = simulate(portfolio_targets(weights, context), context, .2, checkpoint_hours=checkpoint_hours)
        accounts.append(account)
        net = account.equity.pct_change().fillna(0)
        complete = net.groupby(day_keys).size().eq(24)
        daily[i] = net.groupby(day_keys).sum().loc[complete[complete].index]
        progress(f'virtual account {i+1}/9')
    net = pd.DataFrame(daily)
    momentum = context['equities'][4].set_index('candle_begin_time')['close']
    mom_day = decision_days(momentum.index, minute)
    complete = momentum.groupby(mom_day).size().eq(24)
    mom = momentum.groupby(mom_day).last().pct_change().loc[complete[complete].index]
    decisions = pd.date_range(net.index[0], day_keys[-1], freq='D')
    mix = robust_weekly_mix(net.reindex(decisions), mom)
    q = mix.reindex(decision_days(base.index, minute), method='ffill');q.index = base.index
    parent = base*q[0].to_numpy()[:, None]
    for i, view in enumerate(views, 1):
        parent += view*q[i].to_numpy()[:, None]
    parent = parent.where(parent.notna(), base)
    np.testing.assert_allclose(parent[4], base[4], rtol=0, atol=1e-12)
    parent = parent.reindex(index).fillna(0)
    progress('daily risk observations')
    swap_loc = [i for i, c in enumerate(context['columns']) if c.startswith('swap:')]
    held = pd.DataFrame(context['matrices'][4][:, swap_loc].toarray(), index=index,
                        columns=[context['columns'][i].split(':', 1)[1] for i in swap_loc])
    obs = observations(raw_swap, index, held, minute)
    pred, models, windows = quarterly_predictions(obs, index[-1])
    delta = s5_delta(pred, held, parent, context['leverage'], minute)
    targets = portfolio_targets(parent, context)
    targets['swap'] = apply_s5_delta(targets['swap'], delta)
    progress('final MODEL_ROBUST40 account')
    final = simulate(targets, context, .4, checkpoint_hours=checkpoint_hours)
    return dict(parent=parent, base=base, views=views, virtual_accounts=accounts,
                weekly_mix=mix, predictions=pred, models=models, train_windows=windows,
                delta=delta, targets=targets, account=final, observation_day=day_keys[-1])


def slice_context(context, index):
    """Select bars while retaining explicit instruments and simulator units."""
    loc = context['index'].get_indexer(index)
    if (loc < 0).any():raise ValueError('Unknown context time')
    out = dict(context, index=index, leverage=context['leverage'].loc[index],
               matrices=[m[loc] for m in context['matrices']])
    rows = {'leverages', 'funding_rates', 'require_rebalance', 'capital_flow_arr'}
    out['simulation'] = {k: (v[loc] if k in rows or k.endswith('_p') else v)
                         for k, v in context['simulation'].items()}
    out['equities'] = [d[d.candle_begin_time.isin(index)].copy() for d in context['equities']]
    out['close'] = context['close'].loc[:index[-1]].copy()
    return out


def _append_account(old, new, checkpoint_hours=0):
    state = new.attrs['simulation_state']
    a = old.copy();b = new.copy();a.attrs = {};b.attrs = {}
    out = pd.concat([a, b]);out.attrs['simulation_state'] = state
    if checkpoint_hours:
        if old.attrs.get('checkpoints') and old.attrs.get('checkpoint_layout') != new.attrs.get('checkpoint_layout'):
            raise ValueError('Checkpoint instrument layout/units differ; remapping is required')
        points = dict(old.attrs.get('checkpoints', {}));points.update(new.attrs.get('checkpoints', {}))
        out.attrs['checkpoints'] = {t: points[t] for t in sorted(points)[-checkpoint_hours:]}
        out.attrs['checkpoint_layout'] = new.attrs['checkpoint_layout']
    return out


def advance(context, hybrid_weights, raw_swap, previous, progress=lambda message: None, checkpoint_hours=0):
    """Resume virtual accounts; refit/score only when an offset day is added."""
    index = context['index'];minute = context['minute']
    new_index = index[index > previous['parent'].index[-1]]
    if len(new_index) == 0:return previous
    if new_index[0] != previous['parent'].index[-1]+pd.Timedelta(hours=1):
        raise ValueError('Gap before new robust40 observations')
    fresh = slice_context(context, new_index)
    risk = conditional_betas(context['close'], minute)
    keys = decision_days(new_index, minute)
    beta = {k: v.reindex(index=keys, columns=context['columns']).to_numpy() for k, v in risk.items()}
    if any(not np.isfinite(v).all() for v in beta.values()):
        raise ValueError('Conditional beta history missing')
    base_new = constrained_parent(hybrid_weights.loc[new_index], fresh['leverage'], fresh['matrices'], beta)
    views_new = expert_views(base_new, fresh['leverage'], fresh['matrices'], beta)
    accounts = []
    for i, weights in enumerate([base_new]+views_new):
        old = previous['virtual_accounts'][i]
        addition = simulate(portfolio_targets(weights, fresh), fresh, .2, old.attrs['simulation_state'], checkpoint_hours)
        accounts.append(_append_account(old, addition, checkpoint_hours))
    days = decision_days(index, minute)
    complete = pd.Series(1, index=index).groupby(days).size().eq(24)
    net = pd.DataFrame({i: a.equity.pct_change().fillna(0).groupby(days).sum().loc[complete[complete].index]
                        for i, a in enumerate(accounts)})
    momentum = context['equities'][4].set_index('candle_begin_time')['close']
    mom_days = decision_days(momentum.index, minute)
    mom_complete = momentum.groupby(mom_days).size().eq(24)
    mom = momentum.groupby(mom_days).last().pct_change().loc[mom_complete[mom_complete].index]
    mix = robust_weekly_mix(net.reindex(pd.date_range(net.index[0], days[-1], freq='D')), mom)
    q = mix.reindex(keys, method='ffill');q.index = new_index
    parent_new = base_new*q[0].to_numpy()[:, None]
    for i, view in enumerate(views_new, 1):parent_new += view*q[i].to_numpy()[:, None]
    parent_new = parent_new.where(parent_new.notna(), base_new)
    parent = pd.concat([previous['parent'], parent_new])
    swap_loc = [i for i, c in enumerate(context['columns']) if c.startswith('swap:')]
    symbols = [context['columns'][i].split(':', 1)[1] for i in swap_loc]
    pred = previous['predictions'];models = previous['models'];windows = previous['train_windows']
    if days[-1] > previous['observation_day']:
        progress('new offset day: refresh daily observations and quarterly scores')
        # Ordinary hours reuse prior observations; load raw candles only when
        # new offset days (including catch-up/replay days) actually need them.
        if callable(raw_swap):raw_swap = raw_swap()
        held = pd.DataFrame(context['matrices'][4][:, swap_loc].toarray(), index=index, columns=symbols)
        obs = observations(raw_swap, index, held, minute, after_day=previous['observation_day'])
        obs = pd.concat([pred.drop(columns='MODEL'), obs], ignore_index=True).sort_values(['time', 'symbol']).reset_index(drop=True)
        # Mature labels only from prices now available; old feature rows stay fixed.
        tloc = index.get_indexer(obs.time);sloc = pd.Index(symbols).get_indexer(obs.symbol)
        if (tloc < 0).any() or (sloc < 0).any():raise ValueError('Training sample mapping missing')
        mature = tloc+24 < len(index)
        obs['forward24'] = np.nan
        obs.loc[mature, 'forward24'] = (context['simulation']['swap_close_p'][tloc[mature]+24, sloc[mature]]/
            context['simulation']['swap_vwap1m_p'][tloc[mature]+1, sloc[mature]]-1)
        pred, models, windows = quarterly_predictions(obs, index[-1], previous=previous if checkpoint_hours else None)
    held_new = pd.DataFrame(fresh['matrices'][4][:, swap_loc].toarray(), index=new_index, columns=symbols)
    delta_new = s5_delta(pred, held_new, parent_new, fresh['leverage'], minute)
    targets_new = portfolio_targets(parent_new, fresh)
    targets_new['swap'] = apply_s5_delta(targets_new['swap'], delta_new)
    account_new = simulate(targets_new, fresh, .4, previous['account'].attrs['simulation_state'], checkpoint_hours)
    return dict(parent=parent, base=pd.concat([previous['base'], base_new]),
        views=[pd.concat([a, b]) for a, b in zip(previous['views'], views_new)],
        virtual_accounts=accounts, weekly_mix=mix, predictions=pred, models=models, train_windows=windows,
        delta=pd.concat([previous['delta'], delta_new]),
        targets={k: pd.concat([previous['targets'][k], targets_new[k]]) for k in targets_new},
        account=_append_account(previous['account'], account_new, checkpoint_hours), observation_day=days[-1])


# 8. 账户输入合并、状态保存与初始化

def utc_index(index):
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_convert('UTC').tz_localize(None)
    if not index.is_unique or not index.is_monotonic_increasing or index.hasnans:
        raise ValueError('Invalid or duplicate time axis')
    return index


def expected_bar(run_time, minute, utc_offset=0):
    now = pd.Timestamp(run_time)
    now = now.tz_convert('UTC').tz_localize(None) if now.tz is not None else now-pd.Timedelta(hours=utc_offset)
    decision_days(pd.DatetimeIndex([now]), minute)
    return now-pd.Timedelta(hours=1)


def validate_context(c):
    index = utc_index(c['index'])
    decision_days(index, c['minute'])
    if len(index) == 0 or not index.equals(pd.date_range(index[0], index[-1], freq='h')):
        raise ValueError('Hourly history has a gap')
    if len(c['matrices']) != 14 or len(c['equities']) != 14:
        raise ValueError('MODEL_ROBUST40 requires 14 children')
    if len(set(c['columns'])) != len(c['columns']):
        raise ValueError('Duplicate instruments')
    if not c['leverage'].index.equals(index) or not np.isfinite(c['leverage']).all().all():
        raise ValueError('Missing retiming history')
    for m in c['matrices']:
        if m.shape != (len(index), len(c['columns'])) or not np.isfinite(m.data).all():
            raise ValueError('Invalid child position history')


def _expand_matrix(matrix, old, columns):
    coo = matrix.tocoo()
    dest = pd.Index(columns).get_indexer(old)
    return sparse.csr_matrix((coo.data, (coo.row, dest[coo.col])), shape=(matrix.shape[0], len(columns)))


def _same(a, b, label, atol=1e-10, rtol=1e-8):
    if not np.allclose(a, b, atol=atol, rtol=rtol, equal_nan=True):
        detail = ''
        if isinstance(a, (pd.DataFrame, pd.Series)):
            # Locate only on failure; keep the normal overlap check unchanged.
            first = tuple(np.argwhere(~np.isclose(a, b, atol=atol, rtol=rtol, equal_nan=True))[0])
            column = f', column={a.columns[first[1]]}' if isinstance(a, pd.DataFrame) else ''
            detail = (f': index={a.index[first[0]]}{column}, '
                      f'saved={np.asarray(a)[first]}, incoming={np.asarray(b)[first]}')
        raise ValueError(f'Historical revision in {label}{detail}; rebuild this offset baseline before resuming')


def merge_context(old, new, minimum_overlap=168, warmup=1000):
    """Append closed bars, preserve prior observations and frozen virtual lot sizes.

    Moving-window child equity is accepted only if it differs by one constant
    scale, after its initialization region. No silent overwrite of old history.
    """
    validate_context(old);validate_context(new)
    if old['minute'] != new['minute']:
        raise ValueError('Offset differs from saved state')
    last = old['index'][-1]
    if new['index'][-1] < last:
        raise ValueError('Incoming history is stale')
    overlap = old['index'].intersection(new['index'][warmup:])[-minimum_overlap:]
    if len(overlap) < minimum_overlap:
        raise ValueError('Insufficient initialized overlap to continue')
    addition = new['index'][new['index'] > last]
    if len(addition) and addition[0] != last+pd.Timedelta(hours=1):
        raise ValueError('Gap before incoming bars')
    columns = sorted(set(old['columns']) | set(new['columns']))
    oi = old['index'].get_indexer(overlap);ni = new['index'].get_indexer(overlap)
    ai = new['index'].get_indexer(addition)
    matrices = []
    for i, (a, b) in enumerate(zip(old['matrices'], new['matrices'])):
        a = _expand_matrix(a, old['columns'], columns);b = _expand_matrix(b, new['columns'], columns)
        _same(a[oi].toarray(), b[ni].toarray(), f'S{i+1} positions')
        matrices.append(sparse.vstack([a, b[ai]], format='csr'))
    _same(old['leverage'].loc[overlap], new['leverage'].loc[overlap], 'retiming')
    equities = []
    for i, (a, b) in enumerate(zip(old['equities'], new['equities'])):
        a = a.set_index('candle_begin_time');b = b.set_index('candle_begin_time').copy()
        if not overlap.isin(a.index).all() or not overlap.isin(b.index).all():
            raise ValueError('Child equity overlap missing')
        scale = a.loc[overlap[-1], 'close']/b.loc[overlap[-1], 'close']
        if not np.isfinite(scale) or scale <= 0:raise ValueError('Invalid child equity scale')
        b.loc[:, ['open', 'high', 'low', 'close']] *= scale
        if set(a.columns) != set(b.columns):raise ValueError('Child factor columns changed')
        _same(a.loc[overlap], b.loc[overlap, a.columns], f'S{i+1} equity/factors', atol=1e-8, rtol=1e-6)
        equities.append(pd.concat([a, b.loc[addition, a.columns]]).rename_axis('candle_begin_time').reset_index())
    simulation = dict(old['simulation'])
    row_fields = {'leverages', 'require_rebalance', 'capital_flow_arr'}
    for key in row_fields:
        simulation[key] = np.concatenate([simulation[key], new['simulation'][key][ai]])
    for kind in ('spot', 'swap'):
        cols = [c for c in columns if c.startswith(kind+':')]
        oc = [c for c in old['columns'] if c.startswith(kind+':')]
        nc = [c for c in new['columns'] if c.startswith(kind+':')]
        old_sizes = pd.Series(old['simulation'][kind+'_lot_sizes'], index=oc)
        sizes = pd.Series(new['simulation'][kind+'_lot_sizes'], index=nc)
        common = old_sizes.index.intersection(sizes.index)
        _same(old_sizes.loc[common], sizes.loc[common], kind+' instrument units', atol=0, rtol=1e-12)
        simulation[kind+'_lot_sizes'] = old_sizes.reindex(cols).fillna(sizes).to_numpy()
        if not np.isfinite(simulation[kind+'_lot_sizes']).all():raise ValueError('Missing lot sizes')
        keys = [kind+'_'+k+'_p' for k in ('open', 'close', 'vwap1m')]
        if kind == 'swap':keys.append('funding_rates')
        for key in keys:
            a = pd.DataFrame(old['simulation'][key], index=old['index'], columns=oc).reindex(columns=cols)
            b = pd.DataFrame(new['simulation'][key], index=new['index'], columns=nc).reindex(columns=cols)
            common = sorted(set(oc) & set(nc))
            _same(a.loc[overlap, common], b.loc[overlap, common], key)
            if key == 'funding_rates':a = a.fillna(0);b = b.fillna(0)
            simulation[key] = np.ascontiguousarray(pd.concat([a, b.loc[addition]]).to_numpy())
    for key in simulation.keys()-row_fields:
        if not isinstance(simulation[key], np.ndarray) and simulation[key] != new['simulation'][key]:
            raise ValueError(f'Simulator parameter changed: {key}')
    # Newly listed instruments may bring older prices, but never revise old symbols.
    price_columns = list(dict.fromkeys(columns+list(old['close'].columns)+list(new['close'].columns)))
    references = [c for c in price_columns if c not in columns]
    for column in references:
        if column not in old['close'] or column not in new['close']:
            raise ValueError('Reference price history changed')
        _same(old['close'].loc[overlap, column], new['close'].loc[overlap, column], 'reference prices')
    close = old['close'].reindex(columns=price_columns).combine_first(new['close'].reindex(columns=price_columns))
    close = close.loc[:new['index'][-1]]
    result = dict(old, index=old['index'].append(addition), columns=columns, matrices=matrices,
        leverage=pd.concat([old['leverage'], new['leverage'].loc[addition]]),
        equities=equities, simulation=simulation, close=close)
    validate_context(result)
    return result


def expand_result(result, old_columns, columns):
    """Map saved lots by instrument, retaining delisted positions and pending orders."""
    out = dict(result)
    def account(frame):
        states = []
        for kind, state in zip(('spot', 'swap'), frame.attrs['simulation_state']):
            old = [c for c in old_columns if c.startswith(kind+':')]
            new = [c for c in columns if c.startswith(kind+':')]
            equity, lots, targets, prices, initialized = state
            align = lambda values: pd.Series(values, index=old).reindex(new, fill_value=0).to_numpy()
            states.append((equity, align(lots), align(targets), align(prices), initialized))
        frame = frame.copy();frame.attrs['simulation_state'] = tuple(states)
        if old_columns != columns:
            frame.attrs.pop('checkpoints', None)
            frame.attrs.pop('checkpoint_layout', None)
        return frame
    out['virtual_accounts'] = [account(a) for a in result['virtual_accounts']]
    out['account'] = account(result['account'])
    symbols = lambda kind: [c.split(':', 1)[1] for c in columns if c.startswith(kind+':')]
    out['targets'] = {k: v.reindex(columns=symbols(k), fill_value=0) for k, v in result['targets'].items()}
    out['delta'] = result['delta'].reindex(columns=symbols('swap'), fill_value=0)
    return out


def update_state(state_path, bootstrap_path, identity, minute, incoming, raw_swap, expected, validate=None, progress=None, checkpoint_hours=0):
    """A failed update leaves the previous state unchanged; duplicate runs verify input."""
    state_path = Path(state_path);bootstrap_path = Path(bootstrap_path)
    report = progress or (lambda message: None)
    if state_path.resolve() == bootstrap_path.resolve():raise ValueError('State must not overwrite bootstrap')
    with lock(state_path):
        bundle = pd.read_pickle(state_path if state_path.exists() else bootstrap_path)
        return _update_state(bundle, state_path, identity, minute, incoming, raw_swap,
                             expected, validate, report, checkpoint_hours)


def _update_state(bundle, state_path, identity, minute, incoming, raw_swap, expected,
                  validate=None, report=lambda message: None, checkpoint_hours=0):
    """Caller holds the state lock from load through atomic save; never reload here."""
    if bundle['version'] != 1 or bundle['minute'] != minute:
        raise ValueError('Wrong bootstrap version or minute')
    if bundle.get('account_identity', identity) != identity:
        raise ValueError('State belongs to another account')
    if incoming['index'][-1] != expected:
        raise ValueError('Incoming data does not end at the expected closed bar')
    if validate is not None:validate(bundle)
    if not bundle['result']['parent'].index.equals(bundle['context']['index']):
        raise ValueError('Saved calculation and history time axes differ')
    context = merge_context(bundle['context'], incoming)
    previous = expand_result(bundle['result'], bundle['context']['columns'], context['columns'])
    report('状态读取、历史重叠核对及增量合并完成')
    # Normal import retains Hybrid's disk cache across fresh hourly workers.
    # The AST loader in tools.offline intentionally disables reference caches.
    hybrid = calc_hybrid_ratio(context['equities'], SimpleNamespace(params=bundle['params']));hybrid.attrs = {}
    report('Hybrid基础权重计算完成，开始专家账户及MODEL续算')
    result = advance(context, hybrid, raw_swap, previous, checkpoint_hours=checkpoint_hours)
    report('专家账户及MODEL续算完成，开始保存状态')
    updated = dict(bundle, context=context, result=result, account_identity=identity)
    atomic_save(state_path, updated)
    return updated


def initialize_state(path, identity, minute, context, raw, expected, params, pool, simulator, checkpoint_hours=0):
    """First calculation from frozen inputs; never requires a research bootstrap."""
    path=Path(path)
    with lock(path):
        if path.exists():raise FileExistsError(path)
        validate_context(context)
        if context['index'][-1]!=expected or context['minute']!=minute:
            raise ValueError('Initialization cutoff/offset differs')
        hybrid=calc_hybrid_ratio(context['equities'],SimpleNamespace(params=params));hybrid.attrs={}
        result=calculate(context,hybrid,raw,checkpoint_hours=checkpoint_hours)
        bundle=dict(version=1,identity=f'LIVE-MODEL_ROBUST40-{identity}',account_identity=identity,
                    minute=minute,params=effective_parent_params(params),strategy_pool=pool,simulator_config=simulator,
                    context=context,result=result)
        atomic_save(path,bundle)
        return bundle


# 9. MODEL执行模式注册

class LiveRebRobust40:
    use_full_equity = True

    def __init__(self, spot_lot_sizes, swap_lot_sizes, min_order_usdt_ratio=.4, execution_cap=2.45):
        # Match the original owned lot-size arrays without constructing a JIT class.
        self.spot_lot_sizes = np.asarray(spot_lot_sizes, dtype=float).copy()
        self.swap_lot_sizes = np.asarray(swap_lot_sizes, dtype=float).copy()
        self.min_order_usdt_ratio = float(min_order_usdt_ratio)
        self.execution_cap = float(execution_cap)

    def calc_lots(self, equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios):
        for prices, held, target in ((spot_prices, spot_lots, spot_ratios),
                                     (swap_prices, swap_lots, swap_ratios)):
            active = (held != 0) | (target != 0)
            if np.any(active & (~np.isfinite(prices) | (prices <= 0))):
                raise ValueError('Missing active instrument price for MODEL_ROBUST40')
        return execution_lots(equity, spot_prices, spot_lots, spot_ratios, swap_prices, swap_lots, swap_ratios,
                              self.spot_lot_sizes, self.swap_lot_sizes, self.min_order_usdt_ratio,
                              self.execution_cap, LONG_ONLY_EQUITY_RATIO)


def register_execution():
    """Add one named extension; never replace built-in modes or another plugin."""
    from core import rebalance
    existing = getattr(rebalance, 'RebRobust40', None)
    if existing is not None and existing is not LiveRebRobust40:
        raise ValueError('RebRobust40 execution name is already registered')
    rebalance.RebRobust40 = LiveRebRobust40


# 10. 框架输入、父权重及目标一致性校验

def _frame(frame):
    # Only labels are normalized here. Numeric consumers select/reindex before
    # writing, so a private header avoids two copies of every market matrix.
    out = frame.copy(deep=False)
    out.columns = [canonical(c) for c in out.columns]
    if not out.columns.is_unique:raise ValueError('Canonical instrument names collide')
    out.index = utc_index(out.index)
    return out


def virtual_lot_sizes(saved, ratios, read_exginfo):
    """Use core.equity's metadata; changed units require a fresh calculation."""
    sizes = {}
    for position, kind in enumerate(('spot', 'swap')):
        cols = [c.split(':', 1)[1] for c in saved['context']['columns'] if c.startswith(kind+':')] if saved else []
        frozen = pd.Series(saved['context']['simulation'][kind+'_lot_sizes'], index=cols) if saved else pd.Series(dtype=float)
        names = set().union(*(pair[position].columns for pair in ratios))
        required = sorted(set(cols) | {canonical(c) for c in names})
        if not required:
            sizes[kind] = pd.Series(dtype=float)
            continue
        metadata = read_exginfo(kind)
        units = pd.Series(pd.to_numeric(metadata['lot_size'], errors='raise').to_numpy(dtype=float),
                          index=metadata['symbol'].map(canonical))
        if not units.index.is_unique:
            raise ValueError(f'Canonical instrument units collide: {kind}')
        units = units.reindex(required)
        invalid = ~np.isfinite(units) | (units <= 0)
        if invalid.any():
            raise ValueError('Missing instrument unit for virtual account: '+
                             ', '.join(kind+':'+s for s in units.index[invalid]))
        changed = ~np.isclose(frozen.to_numpy(), units.reindex(cols).to_numpy(), rtol=1e-12, atol=0)
        if changed.any():
            raise ValueError('Simulation unit changed; rebuild this offset: '+
                             ', '.join(kind+':'+s for s in frozen.index[changed]))
        sizes[kind] = units
    return sizes


def build_context(equities, ratios, leverage, pivots, sizes, minute, simulator):
    """Normalize only representation; do not shift signals or fill missing retiming."""
    equities = [d.copy() for d in equities]
    for d in equities:d['candle_begin_time'] = utc_index(d.candle_begin_time)
    index = pd.DatetimeIndex(equities[0].candle_begin_time)
    if any(not index.equals(pd.DatetimeIndex(d.candle_begin_time)) for d in equities):
        raise ValueError('Child equity time axes differ')
    # Collect labels first, then convert one child at a time. Keeping fourteen
    # aligned dense copies until CSR conversion doubled the initialization peak.
    columns = sorted({kind+':'+canonical(s) for pair in ratios
                      for kind,frame in zip(('spot','swap'),pair) for s in frame.columns})
    mapping = {c: i for i, c in enumerate(columns)}
    matrices = []
    for pair in ratios:
        matrix = sparse.csr_matrix((len(index), len(columns)))
        for kind, frame in zip(('spot', 'swap'), pair):
            frame = _frame(frame)
            if not index.isin(frame.index).all():raise ValueError('Child ratios missing equity bars')
            frame = frame.loc[index]
            if frame.isna().any().any():raise ValueError('Invalid child ratios')
            coo = sparse.coo_matrix(frame.to_numpy())
            loc = np.array([mapping[kind+':'+s] for s in frame.columns], dtype=int)
            matrix += sparse.csr_matrix((coo.data, (coo.row, loc[coo.col])), shape=matrix.shape)
        matrices.append(matrix)
    leverage = leverage.copy();leverage.index = utc_index(leverage.index)
    n = len(index)
    sim = dict(init_capital=float(simulator['initial_usdt']), leverages=np.ones(n),
        spot_c_rate=simulator['spot_c_rate'], swap_c_rate=simulator['swap_c_rate'],
        spot_min_order_limit=float(simulator['spot_min_order_limit']), swap_min_order_limit=float(simulator['swap_min_order_limit']),
        min_margin_rate=simulator['margin_rate'], require_rebalance=np.ones(n, dtype=np.int8),
        capital_flow_arr=np.zeros(n), capital_flow_mode=-1)
    closes = []
    benchmark = None
    for kind in ('spot', 'swap'):
        symbols = [c.split(':', 1)[1] for c in columns if c.startswith(kind+':')]
        p = {k: _frame(v) for k, v in pivots[kind].items()}
        if kind == 'swap':
            if 'BTCUSDT' not in p['close']:
                raise ValueError('BTC benchmark candles are required even when trading is excluded')
            benchmark = p['close']['BTCUSDT'].loc[:index[-1]]
        for key in ('open', 'close', 'vwap1m'):
            if not index.isin(p[key].index).all():raise ValueError('Market pivot is stale/incomplete')
            sim[kind+'_'+key+'_p'] = np.ascontiguousarray(p[key].reindex(index=index, columns=symbols).to_numpy())
        if kind == 'swap':
            sim['funding_rates'] = np.ascontiguousarray(p['funding_rate'].reindex(index=index, columns=symbols).fillna(0).to_numpy())
        sim[kind+'_lot_sizes'] = sizes[kind].reindex(symbols).to_numpy(dtype=float)
        invalid = ~np.isfinite(sim[kind+'_lot_sizes']) | (sim[kind+'_lot_sizes'] <= 0)
        if invalid.any():
            names = ', '.join(kind+':'+s for s in np.asarray(symbols)[invalid])
            raise ValueError(f'Missing instrument unit for virtual account: {names}')
        close = p['close'].reindex(columns=symbols).loc[:index[-1]]
        close.columns = [kind+':'+s for s in symbols];closes.append(close)
    close = pd.concat(closes, axis=1).reindex(columns=columns)
    close['swap:BTCUSDT'] = benchmark
    return dict(index=index, minute=minute, columns=columns, matrices=matrices,
        leverage=leverage.reindex(index), equities=equities, simulation=sim,
        close=close)


def normalized(value):
    if isinstance(value, dict):return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, range)):return [normalized(v) for v in value]
    return value


# Only these fields feed coin_features/observations. Do not convert unrelated
# precomputed factors into pandas just to build the daily MODEL observations.
MODEL_RAW_COLUMNS = ('close', 'quote_volume', 'taker_buy_quote_asset_volume',
                     'funding_fee', 'first_candle_time', 'avg_price_1m')


def load_model_raw(root, offset, run_time, expected, live):
    """Same publication, row count and feature inputs as the original full read."""
    if live:
        from tools.robust40.handoff import read_arrow_tail
        publication = read_arrow_tail(root, 'swap', run_time, expected,
                                      columns=MODEL_RAW_COLUMNS, tail=4050)
        if not publication:raise ValueError('MODEL requires same-round raw Arrow')
        sources = [publication]
    elif run_time is not None and (Path(root)/offset/f'swap_dict_{int(run_time.timestamp())}.arrow').exists():
        from tools.initialization_data import read_market
        path=Path(root)/offset/f'swap_dict_{int(run_time.timestamp())}.arrow'
        # Keep all symbols/full history. funding_fee absence keeps its original
        # missing-value semantics; it is never substituted with funding_rate.
        sources=({s:f} for s,f in read_market(path,columns=['candle_begin_time',*MODEL_RAW_COLUMNS],
                                             end=expected,require_full=True))
    else:
        sources = (pd.read_pickle(p) for p in sorted((Path(root)/offset).rglob('swap_dict_batch*.pkl')))
    raw = {}
    for source in sources:
        for symbol, frame in source.items():
            symbol = canonical(symbol)
            if symbol in raw:raise ValueError('Duplicate raw swap symbol')
            frame = frame.copy();frame['candle_begin_time'] = utc_index(frame.candle_begin_time)
            if 'first_candle_time' not in frame:
                raise ValueError('Raw swap history must retain first_candle_time')
            frame['first_candle_time'] = pd.to_datetime(frame.first_candle_time, utc=True).dt.tz_localize(None)
            raw[symbol] = frame.loc[frame.candle_begin_time <= expected]
            if live:raw[symbol] = raw[symbol].tail(4050)
    if not raw:raise ValueError('No complete raw swap history')
    return raw


def prepare(me_conf, account, run_time, *, inputs=None):
    """One state read under the same lock used for merge, calculation and save."""
    from time import perf_counter
    from core.utils.log_kit import logger
    started = perf_counter()
    logger.info('MODEL仓管：开始准备输入及续算状态')
    options = me_conf.strategy.params['runtime']
    if options.get('initializing') is True:
        return _prepare(me_conf, account, run_time, saved=None, started=started, inputs=inputs)
    state = Path(options['state_path']);bootstrap = Path(options['bootstrap_path'])
    if state.resolve() == bootstrap.resolve():raise ValueError('State must not overwrite bootstrap')
    with lock(state):
        saved = pd.read_pickle(state if state.exists() else bootstrap)
        return _prepare(me_conf, account, run_time, saved=saved, started=started)


def _prepare(me_conf, account, run_time, *, saved, started, inputs=None):
    from time import perf_counter
    from core.utils.log_kit import logger
    last = started
    def progress(message):
        nonlocal last
        now = perf_counter()
        logger.info(f'MODEL仓管：{message}；阶段{now-last:.3f}秒，累计{now-started:.3f}秒')
        last = now
    from config import bmac_data_path, utc_offset, simulator_config
    from core.utils.datatools import read_bmac_market_pivot, read_bmac_exginfo
    strategy = me_conf.strategy
    if strategy.hold_period != '1H' or strategy.rebalance_cap_step != 1 or strategy.symbol_ratio_limit:
        raise ValueError('ROBUST40 requires 1H, full parent change and its own exposure constraints')
    minute = account.hour_offset_minute
    expected = expected_bar(run_time, minute, utc_offset)
    if not strategy.params.get('enable_live_hooks', False) or account.rebalance_mode.mode != 'RebRobust40':
        raise ValueError('ROBUST40 execution configuration is inconsistent')
    if account.rebalance_mode.params != dict(min_order_usdt_ratio=.4, execution_cap=2.45) or account.leverage != 1:
        raise ValueError('ROBUST40 v1 requires frozen execution parameters and leverage=1')
    options = strategy.params['runtime']
    # 唯一配置来源是account.hour_offset；minute已由框架解析为hour_offset_minute。
    state = Path(options['state_path']);bootstrap = Path(options['bootstrap_path'])
    # State cannot live in folders that run_by_account clears every hour.
    from config import runtime_folder
    for cleared in [Path(runtime_folder), Path('data/cache'), Path('data/runtime')]:
        if state.resolve().is_relative_to(cleared.resolve()):raise ValueError('State path is in a cleared cache directory')
    initializing = options.get('initializing') is True
    def check(bundle):
        # 已保存的旧研究说明字段不影响计算；只忽略明确列出的无效项，不放宽其他参数。
        if normalized(effective_parent_params(bundle['params'])) != normalized(effective_parent_params(strategy.params['parent'])):
            raise ValueError('Parent configuration changed')
        if normalized(bundle['simulator_config']) != normalized(simulator_config):
            raise ValueError('Child simulation configuration differs from baseline')
        if len(me_conf.factory.config_list) != 14:raise ValueError('Expected 14 children')
        for conf, reference in zip(me_conf.factory.config_list, bundle['strategy_pool']):
            if normalized(conf.strategy_list_raw) != normalized(reference['strategy_list']):
                raise ValueError('Child strategy definition differs from MODEL_ROBUST40 baseline')
            timing = dict(name=conf.timing.name, params=conf.timing.params) if conf.timing else None
            if normalized(timing) != normalized(reference.get('re_timing')):
                raise ValueError('Child retiming definition differs from baseline')
    if saved is not None:check(saved)
    progress('历史状态读取及配置核对完成')
    index = utc_index(me_conf.equity_dfs[0].candle_begin_time)
    leverage = pd.DataFrame(1., index=index, columns=range(14))
    for i, conf in enumerate(me_conf.factory.config_list):
        if conf.timing:
            if inputs is not None:
                value = inputs['timing'][conf.name].copy(deep=False)
            else:
                path = conf.get_result_folder()/'再择时动态杠杆.csv'
                value = pd.read_csv(path, index_col='candle_begin_time', parse_dates=True)['动态杠杆']
            value.index = utc_index(value.index);leverage[i] = value.reindex(index)
    pivots = ({k: read_bmac_market_pivot(account.hour_offset, k) for k in ('spot', 'swap')}
              if inputs is None else inputs['pivots'])
    sizes = virtual_lot_sizes(saved, me_conf.ratio_dfs, read_bmac_exginfo)
    equities = me_conf.equity_dfs
    if options.get('server_init') and not initializing:
        start = expected-pd.Timedelta(hours=4049)
        equities = [d.loc[utc_index(d.candle_begin_time)>=start].copy() for d in equities]
    context = build_context(equities, me_conf.ratio_dfs, leverage, pivots, sizes, minute, simulator_config)
    if initializing:
        dense=sum(int(f.memory_usage(index=True,deep=False).sum()) for pair in me_conf.ratio_dfs for f in pair)
        csr=sum(m.data.nbytes+m.indices.nbytes+m.indptr.nbytes for m in context['matrices'])
        arrays=sum(v.nbytes for v in context['simulation'].values() if isinstance(v,np.ndarray))
        logger.info(f'INIT_DATA MODEL输入字节：child_dense={dense} csr={csr} simulation_arrays={arrays}；共享缓冲不可相加当RSS')
    progress('行情、子策略及再择时输入整理完成')
    def raw_loader():
        raw_started = perf_counter()
        raw = load_model_raw(bmac_data_path, account.hour_offset, run_time, expected,
                             live=bool(options.get('server_init') and not initializing))
        # Separate sub-timer: advance may already have simulated virtual
        # accounts, so the outer phase timer is not the raw I/O duration.
        logger.info(f'MODEL仓管：按需原始行情读取完成，耗时{perf_counter()-raw_started:.3f}秒')
        return raw
    progress('输入准备完成，开始仓管计算（原始行情仅初始化/跨日按需读取）')
    if initializing:
        from tools.robust40.server import configured_s3, validate_initial_history
        from config import strategy_pool, min_kline_num
        origin,n=configured_s3({'strategy_pool':strategy_pool},minute)
        raw = raw_loader()
        logger.info(f'INIT_DATA MODEL原始行情：symbols={len(raw)} '
                    f'frame_bytes={sum(int(f.memory_usage(index=True,deep=False).sum()) for f in raw.values())}；不含Python对象额外开销')
        validate_initial_history(raw,origin,n,min_kline_num)
        bundle = initialize_state(state,account.name,minute,context,raw,expected,strategy.params['parent'],strategy_pool,simulator_config,
                                  checkpoint_hours=options.get('checkpoint_hours', 0))
    else:
        bundle = _update_state(saved, state, account.name, minute, context, raw_loader, expected, check, report=progress,
                               checkpoint_hours=options.get('checkpoint_hours', 0))
    strategy._robust40_result = bundle['result']
    if initializing and options.get('checkpoint_hours'):
        from tools.robust40.handoff import read_inputs
        # MODEL raw is projected; revision checks need their own broader column
        # set, but only the bounded 168-hour tail of the same Arrow publication.
        inputs=read_inputs(run_time,minute,expected,initializing=True,pivots=pivots)
        pd.to_pickle(inputs,state.parent/'revision_inputs.pkl')
    strategy._robust40_index = pd.DatetimeIndex(me_conf.equity_dfs[0].candle_begin_time)
    progress('仓管计算及状态保存完成')


def parent_ratios(equities, strategy):
    if not hasattr(strategy, '_robust40_result'):
        raise ValueError('MODEL_ROBUST40 requires prepare_live before calc_ratio')
    original = pd.DatetimeIndex(equities[0].candle_begin_time)
    values = strategy._robust40_result['parent'].reindex(utc_index(original))
    if values.isna().any().any():raise ValueError('Missing ROBUST40 parent weights')
    values.index = original
    return values.copy()


def validate_targets(spot, swap, strategy):
    result = strategy._robust40_result
    # Prove that core aggregation applied the child timing exactly once.
    for kind, frame in [('spot', spot), ('swap', swap)]:
        normalized_frame = _frame(frame)
        reference = result['targets'][kind].reindex(index=normalized_frame.index, columns=normalized_frame.columns)
        if kind == 'swap':
            delta = result['delta'].reindex(index=normalized_frame.index, columns=normalized_frame.columns, fill_value=0)
            normalized_frame = apply_s5_delta(normalized_frame, delta)
        if reference.isna().any().any() or not np.allclose(normalized_frame, reference, rtol=1e-10, atol=1e-12):
            raise ValueError('Framework target differs from shared ROBUST40 calculation')
        if kind == 'swap':
            swap = normalized_frame.set_axis(frame.index).set_axis(frame.columns, axis=1)
    return spot, swap


# 11. 仓管自身恢复点与重放（磁盘事务由tools负责）

def simulate_checkpoints(targets, context, buffer, resume_state, hours):
    """Run the old kernel, capturing only a bounded tail; no kernel change."""
    if not isinstance(hours, int) or hours < 1:
        raise ValueError('checkpoint hours must be a positive integer')
    index = context['index']; count = len(index)
    if not count:raise ValueError('Cannot checkpoint empty simulation')
    tail = max(0, count-hours)
    spans = ([(0, tail)] if tail else [])+[(i,i+1) for i in range(tail,count)]
    chunks = [];points = {};state = resume_state
    for left,right in spans:
        rows = {'leverages','funding_rates','require_rebalance','capital_flow_arr'}
        args = {k:v[left:right] if k in rows or k.endswith('_p') else v
                for k,v in context['simulation'].items()}
        part = simulate({k:v.iloc[left:right] for k,v in targets.items()},
                        dict(index=index[left:right], simulation=args), buffer, state)
        state = part.attrs['simulation_state']
        if left >= tail:points[index[right-1]] = state
        part.attrs = {};chunks.append(part)
    result = pd.concat(chunks)
    result.attrs.update(simulation_state=state, checkpoints=points)
    result.attrs['checkpoint_layout'] = tuple((k,tuple(targets[k].columns),tuple(context['simulation'][k+'_lot_sizes']))
                                               for k in ('spot','swap'))
    return result

def model_replay(context, hybrid, raw, previous, revised_from, hours=24, checkpoint_time=None):
    """Recompute the corrected suffix. Unaffected prefix and model fits are reused."""
    restored, time = restore_model(context, previous, revised_from, checkpoint_time)
    return advance(context, hybrid, raw, restored, checkpoint_hours=hours), time

def restore_model(context, previous, revised_from, checkpoint_time=None):
    accounts = previous['virtual_accounts']+[previous['account']]
    points = set(accounts[0].attrs.get('checkpoints', {}))
    for account in accounts[1:]:points &= set(account.attrs.get('checkpoints', {}))
    time = before(points, revised_from) if checkpoint_time is None else pd.Timestamp(checkpoint_time)
    if time not in points or time >= pd.Timestamp(revised_from):raise ValueError('Invalid common checkpoint')
    if time not in context['index']:raise ValueError('Checkpoint outside supplied context')
    day = decision_days(pd.DatetimeIndex([time]), context['minute'])[0]
    def trim(frame):
        out = frame.loc[:time].copy();out.attrs = {}
        return out
    def account(frame):
        out = trim(frame);state = frame.attrs['checkpoints'][time]
        layout = tuple((k,tuple(s.split(':',1)[1] for s in context['columns'] if s.startswith(k+':')),
                        tuple(context['simulation'][k+'_lot_sizes'])) for k in ('spot','swap'))
        if frame.attrs.get('checkpoint_layout') != layout:
            raise ValueError('Checkpoint instrument layout/units differ; remapping is required')
        for kind, s in zip(('spot','swap'), state):
            if len(s[1]) != len(context['simulation'][kind+'_lot_sizes']):
                raise ValueError('Checkpoint instrument universe differs; remapping is required')
        out.attrs['simulation_state'] = state
        out.attrs['checkpoints'] = {t:s for t,s in frame.attrs['checkpoints'].items() if t <= time}
        out.attrs['checkpoint_layout'] = layout
        return out
    restored = dict(previous)
    for key in ('parent','base','delta'):restored[key] = trim(previous[key])
    restored['views'] = [trim(v) for v in previous['views']]
    restored['targets'] = {k:trim(v) for k,v in previous['targets'].items()}
    restored['virtual_accounts'] = [account(v) for v in previous['virtual_accounts']]
    restored['account'] = account(previous['account'])
    restored['weekly_mix'] = previous['weekly_mix'].loc[:day].copy()
    restored['predictions'] = previous['predictions'].loc[previous['predictions'].time <= day].copy()
    # A checkpoint cannot know labels whose outcome was still in the future.
    if not restored['predictions'].empty:
        immature = restored['predictions'].time+pd.Timedelta(hours=24) > time
        restored['predictions'].loc[immature, 'forward24'] = np.nan
    restored['models'] = {q:m for q,m in previous['models'].items() if q <= time}
    windows = previous['train_windows']
    restored['train_windows'] = windows[windows.quarter <= time].copy() if 'quarter' in windows else windows.copy()
    restored['observation_day'] = day
    return restored, time


register_execution()
