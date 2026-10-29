"""Research formulas from MomentumRiskState1023, with explicit decision clocks."""
import numpy as np
import pandas as pd

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
    times = pd.to_datetime(df['candle_begin_time'])
    first = pd.to_datetime(df['first_candle_time']) if 'first_candle_time' in df else times.iloc[0]
    out['age_days'] = (times-first).dt.total_seconds()/86400
    return out.replace([np.inf, -np.inf], np.nan)
