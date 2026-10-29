"""Versioned causal repair of Cci按异常0401修正; leave the legacy factor intact."""
import numpy as np
import pandas as pd


def signal_multi_params(df, param_list) -> dict:
    high = df['high'].astype(float).reset_index(drop=True).copy()
    low = df['low'].astype(float).reset_index(drop=True).copy()
    close = df['close'].astype(float).reset_index(drop=True).copy()
    tp = (high + low + close) / 3.0
    if len(df):
        # Ignore the invalid listing candle without future backfill or mutation.
        high.iloc[0] = low.iloc[0] = tp.iloc[0] = np.nan
    rolling_low = low.rolling(2).min()
    pctchange = (high.rolling(2).max() - rolling_low) / rolling_low
    times = None
    if 'candle_begin_time' in df:
        times = pd.to_datetime(df['candle_begin_time']).reset_index(drop=True)
        if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
            raise ValueError('CCI requires unique, increasing candle_begin_time values')
    ret = {}
    for param in param_list:
        n = int(param)
        if n <= 0:
            raise ValueError('CCI window must be positive')
        pctma = pctchange.rolling(n, min_periods=1).mean()
        pctstd = pctchange.rolling(n, min_periods=1).std()
        anomaly = ((pctchange - pctma).div(pctstd).abs() > 25) & (pctchange.abs() > 0.2)
        factor = pd.Series(np.nan, index=pd.RangeIndex(len(df)), dtype=float)
        start = 0
        for end in np.append(np.flatnonzero(anomaly.to_numpy()), len(df)):
            segment = tp.iloc[start:end]
            if len(segment):
                ma = segment.rolling(n, min_periods=1).mean()
                md = (segment - ma).abs().rolling(n, min_periods=1).mean() * 0.015
                factor.iloc[start:end] = ((segment - ma) / md.replace(0, np.nan)).to_numpy()
            start = end + 1
        if times is not None:
            last_anomaly = times.where(anomaly).ffill()
            blocked = (times - last_anomaly) <= pd.Timedelta(hours=168)
        else:
            blocked = anomaly.rolling(169, min_periods=1).max().astype(bool)
        factor.loc[blocked] = np.nan
        ret[str(param)] = factor
    return ret
