"""One calculation path for independent-offset bootstrap, replay and live updates."""
import numpy as np
import pandas as pd

from .features import decision_days
from .parent import conditional_betas, constrained_parent, expert_views, robust_weekly_mix
from .model import observations, quarterly_predictions, s5_delta
from .execution import apply_s5_delta
from .rebalance import RebRobust40
from .simulation import start_simulation


def portfolio_targets(weights, context):
    effective = weights.reindex(context['index']).fillna(0).to_numpy()*context['leverage'].to_numpy()
    combined = sum(m.multiply(effective[:, i, None]) for i, m in enumerate(context['matrices'])).tocsr()
    cols = context['columns'];out = {}
    for kind in ['spot', 'swap']:
        loc = [i for i, c in enumerate(cols) if c.startswith(kind+':')]
        out[kind] = pd.DataFrame(combined[:, loc].toarray(), index=context['index'],
                                columns=[cols[i].split(':', 1)[1] for i in loc])
    return out


def simulate(targets, context, buffer, resume_state=None):
    args = context['simulation']
    reb = RebRobust40(args['spot_lot_sizes'], args['swap_lot_sizes'], buffer, 2.45)
    result = start_simulation(**args, pos_calc=reb, resume_state=resume_state,
        spot_ratio=np.ascontiguousarray(targets['spot'].to_numpy()),
        swap_ratio=np.ascontiguousarray(targets['swap'].to_numpy()))
    out = pd.DataFrame(dict(zip(['equity', 'turnover', 'fee', 'funding_fee', 'marginRatio',
                                'long_pos_value', 'short_pos_value', 'cash_flow'], result)), index=context['index'])
    if not np.isfinite(out.equity).all() or out.equity.le(0).any():
        raise ValueError('Virtual account depleted or invalid')
    out.attrs['simulation_state'] = result[-1]
    return out


def calculate(context, hybrid_weights, raw_swap, progress=lambda message: None):
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
        account = simulate(portfolio_targets(weights, context), context, .2)
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
    final = simulate(targets, context, .4)
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


def _append_account(old, new):
    state = new.attrs['simulation_state']
    a = old.copy();b = new.copy();a.attrs = {};b.attrs = {}
    out = pd.concat([a, b]);out.attrs['simulation_state'] = state
    return out


def advance(context, hybrid_weights, raw_swap, previous, progress=lambda message: None):
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
        addition = simulate(portfolio_targets(weights, fresh), fresh, .2, old.attrs['simulation_state'])
        accounts.append(_append_account(old, addition))
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
        pred, models, windows = quarterly_predictions(obs, index[-1])
    held_new = pd.DataFrame(fresh['matrices'][4][:, swap_loc].toarray(), index=new_index, columns=symbols)
    delta_new = s5_delta(pred, held_new, parent_new, fresh['leverage'], minute)
    targets_new = portfolio_targets(parent_new, fresh)
    targets_new['swap'] = apply_s5_delta(targets_new['swap'], delta_new)
    account_new = simulate(targets_new, fresh, .4, previous['account'].attrs['simulation_state'])
    return dict(parent=parent, base=pd.concat([previous['base'], base_new]),
        views=[pd.concat([a, b]) for a, b in zip(previous['views'], views_new)],
        virtual_accounts=accounts, weekly_mix=mix, predictions=pred, models=models, train_windows=windows,
        delta=pd.concat([previous['delta'], delta_new]),
        targets={k: pd.concat([previous['targets'][k], targets_new[k]]) for k in targets_new},
        account=_append_account(previous['account'], account_new), observation_day=days[-1])
