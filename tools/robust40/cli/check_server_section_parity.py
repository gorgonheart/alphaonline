"""Offline S3 full-PKL versus 4050-window continuation on retained real candles."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def compare(folder,minute,origin=None):
    from tools.robust40.server import configured_s3, settings
    from tools.robust40.market_inputs import latest_ready
    stamp,ready_ns=latest_ready(folder,minute)
    files=sorted(folder.glob('swap_dict_batch*.pkl'))
    before={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in files}
    if any(v[1]>ready_ns for v in before.values()):raise ValueError('Raw publication is changing')
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    symbols={'BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','ADAUSDT','0GUSDT'}
    frames=[]
    for path in files:
        for symbol,source in pd.read_pickle(path).items():
            if symbol not in symbols:continue
            d=source.copy();d.candle_begin_time=pd.to_datetime(d.candle_begin_time,utc=True).dt.tz_localize(None)
            d=d.sort_values('candle_begin_time').reset_index(drop=True)
            d=d[d.candle_begin_time<pd.Timestamp(stamp,unit='s')]
            d['VolumeMaxBias_720']=d.volume/d.volume.rolling(720,min_periods=1).max()
            frames.append(d[['candle_begin_time','symbol','is_spot','symbol_spot','symbol_swap','是否交易','VolumeMaxBias_720','volume']])
    if len(frames)<3:raise ValueError('Insufficient real instruments')
    frame=pd.concat(frames,ignore_index=True)
    account=Path(__file__).resolve().parents[3]/f'accounts/{minute}m_robust_4h.py'
    start=configured_s3(settings(account)[0],minute)[0] if origin is None else pd.Timestamp(origin)
    params=(720,.1,.9,str(start))
    times=sorted(frame.candle_begin_time.unique());split=pd.Timestamp(times[-49])
    expected=section.signal(frame.copy(),params,'chosen')
    first,state=section.signal_with_state(frame[frame.candle_begin_time<=split].copy(),params,'chosen')
    np.testing.assert_array_equal(first.chosen,expected.loc[first.index,'chosen'])
    checked=0
    for step in (24,48):
        end=split+pd.Timedelta(hours=step)
        chunk=frame[frame.candle_begin_time.between(end-pd.Timedelta(hours=4049),end)].copy()
        chunk['VolumeMaxBias_720']=chunk.groupby('symbol',sort=False).volume.transform(lambda s:s/s.rolling(720,min_periods=1).max())
        actual,state=section.signal_with_state(chunk,params,'chosen',state)
        np.testing.assert_array_equal(actual.chosen,expected.loc[actual.index,'chosen'])
        checked+=len(actual)
    if before!={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in files}:
        raise ValueError('Raw inputs changed during S3 comparison')
    return dict(minute=minute,publication=stamp,symbols=len(frames),full_rows=len(frame),hours=len(times),
                initialized_until=str(split),origin=str(start),checked_cells=checked,passed=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    result=[compare(Path(args.market)/f'{m}m',m) for m in (20,40,55)]
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))
