"""Offset-specific daily observations and causal quarterly MODEL scoring."""
import numpy as np
import pandas as pd

from .features import FEATURES, MODEL_FEATURES, coin_features, decision_days


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


def quarterly_predictions(obs, end):
    """Training parameters remain frozen; quarters without 180 days stay unscored."""
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
        model.fit(train[MODEL_FEATURES].clip(-1e6, 1e6), y)
        out.loc[mask, 'MODEL'] = model.predict_proba(test[MODEL_FEATURES].clip(-1e6, 1e6))[:, 1]
        models[begin] = model
        windows.append(dict(quarter=begin, train_days=train.time.nunique(), train_rows=len(train),
                            last_label_end=train.time.max()+pd.Timedelta(hours=24)))
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
