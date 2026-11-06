"""Exchange snapshot normalization only: no orders, trade history or cost rebuilding."""
import numpy as np
import pandas as pd


def portfolio_overview(balance, positions, ticker, base_coins, account_info):
    try:
        equity = float(account_info['actualEquity'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('BN actualEquity missing or invalid') from None
    if not np.isfinite(equity):
        raise ValueError('BN actualEquity is not finite')
    if balance.empty and (equity != 0 or not positions.empty):
        raise ValueError('BN balance missing for a nonempty account')
    prices = pd.Series({row['symbol']: float(row['price']) for row in ticker}, dtype=float)
    assets = balance.copy()
    cash = 0.
    spot = pd.DataFrame(index=pd.Index([], name='symbol'))
    if not assets.empty:
        amounts = pd.to_numeric(assets['totalWalletBalance'], errors='raise')
        if not np.isfinite(amounts).all():
            raise ValueError('BN wallet quantities are not finite')
        is_cash = assets['symbol'].isin(base_coins)
        cash = float(amounts.loc[is_cash].sum())  # totalWalletBalance already includes locked funds
        spot = assets.loc[~is_cash & (amounts != 0)].copy()
        spot['symbol'] += 'USDT'
        spot = spot.set_index('symbol')
        spot['当前持仓量'] = pd.to_numeric(spot['totalWalletBalance'], errors='raise')
        spot['当前价格'] = prices.reindex(spot.index).where(lambda p: np.isfinite(p) & (p > 0))
        spot['仓位价值'] = spot['当前持仓量'] * spot['当前价格']
    spot_value = spot['仓位价值'].sum(skipna=False) if not spot.empty else 0.
    try:
        unimmr = float(account_info['uniMMR'])
    except (KeyError, TypeError, ValueError):
        unimmr = np.nan
    return dict(account_equity=equity, seed_coin_spot_equity=0.,
        statistics=dict(uniMMR=unimmr, equity_source='binance_pm_actualEquity', equity_currency='USD',
                        raw_balances=assets, account_info=dict(account_info)),
        spot_assets=dict(spot_position_df=spot, equity=cash+spot_value, usdt=cash, prices=prices),
        swap_assets=dict(swap_position_df=positions))


def observation_baseline_mask(times, observed_at, hours, *, run_times=None, run_time=None, tolerance_minutes=10):
    """1h uses the previous scheduled round; observations may drift either way.

    A baseline must still precede the current observation. Legacy callers without
    a scheduled round and 24h reports retain the old one-sided time window.
    """
    times = pd.to_datetime(times, format='mixed', errors='coerce')
    now = pd.Timestamp(observed_at)
    target = now - pd.Timedelta(hours=hours)
    tolerance = pd.Timedelta(minutes=tolerance_minutes)
    if hours == 1 and run_time is not None:
        if run_times is None:
            return pd.Series(False, index=times.index)
        slots = pd.to_datetime(run_times, format='mixed', errors='coerce')
        return ((slots == pd.Timestamp(run_time)-pd.Timedelta(hours=1)) &
                ((times-target).abs() <= tolerance) & (times < now))
    return (times <= target) & (times >= target-tolerance)


def position_history(records, *, source, offset_minute, observed_at=None, run_time=None):
    """Reuse already loaded post-order records; old/pre-order scopes stay separate."""
    candidates = []
    for frame in records.values():
        attrs = frame.attrs
        if (attrs.get('phase') != 'post_trade' or attrs.get('snapshot_source') != source
                or attrs.get('offset_minute') != offset_minute):
            continue
        record_time = attrs.get('observed_at')
        if record_time is None:
            continue
        candidates.append(frame)
    if observed_at is not None and candidates:
        times = pd.Series([pd.Timestamp(f.attrs['observed_at']) for f in candidates])
        slots = pd.Series([f.attrs.get('run_time') for f in candidates])
        chosen = []
        for hours in (1,24):
            mask = observation_baseline_mask(times, observed_at, hours, run_times=slots, run_time=run_time)
            if mask.any():
                chosen.append(candidates[times.loc[mask].idxmax()])
        candidates = chosen
    frames = []
    for frame in candidates:
        attrs = frame.attrs
        current = (pd.DataFrame(frame, copy=False).reset_index() if not frame.empty
                   else pd.DataFrame({'symbol':['EMPTY'], 'market_value':[0.]}))
        current.attrs = {}
        current['timestamp'] = attrs['run_time']
        current['observed_at'] = attrs['observed_at']
        frames.append(current)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
