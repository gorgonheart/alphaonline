"""Compare initialized MODEL history with a prefix plus 4050-bar continuation.

Checks parent/delta/targets, account curves, and saved virtual/real simulator lots.
Does not recalculate child selection; that requires a 4050-compatible account.
"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def compare(bundle,market):
    from positions.ModelRobust40Strategy import calculate, advance, slice_context
    from positions.ModelRobust40Strategy import merge_context
    from tools.robust40.offline import hybrid_function,canonical
    context=bundle['context'];index=context['index']
    raw={}
    for p in Path(market).glob('swap_dict_batch*.pkl'):
        for symbol,frame in pd.read_pickle(p).items():
            frame=frame.copy()
            for col in ('candle_begin_time','first_candle_time'):
                frame[col]=pd.to_datetime(frame[col],utc=True).dt.tz_localize(None)
            raw[canonical(symbol)]=frame[frame.candle_begin_time<=index[-1]]
    calc=hybrid_function(Path(__file__).resolve().parents[3]/'positions/HybridRotationDriftStrategy.py')
    hybrid=calc(context['equities'],SimpleNamespace(params=bundle['params']));hybrid.attrs={}
    prefix=slice_context(context,index[:-48])
    previous=calculate(prefix,hybrid.loc[prefix['index']],raw)
    merged=prefix
    for step in (24,48):
        end=index[-49]+pd.Timedelta(hours=step)
        incoming=slice_context(context,index[(index<=end)&(index>=end-pd.Timedelta(hours=4049))])
        merged=merge_context(merged,incoming)
        short={s:d[d.candle_begin_time<=end].tail(4050) for s,d in raw.items()}
        previous=advance(merged,hybrid.loc[merged['index']],short,previous)
    full=bundle['result'];errors={}
    for name in ('parent','delta','account'):
        x,y=full[name].to_numpy(),previous[name].to_numpy()
        np.testing.assert_allclose(x,y,rtol=1e-10,atol=1e-9,equal_nan=True)
        errors[name]=float(np.nanmax(np.abs(x-y)))
    for kind in ('spot','swap'):
        np.testing.assert_allclose(full['targets'][kind],previous['targets'][kind],rtol=1e-10,atol=1e-12)
    for old,new in zip(full['virtual_accounts']+[full['account']],previous['virtual_accounts']+[previous['account']]):
        for a,b in zip(old.attrs['simulation_state'],new.attrs['simulation_state']):
            for x,y in zip(a,b):np.testing.assert_allclose(x,y,rtol=1e-10,atol=1e-9,equal_nan=True)
    return dict(passed=True,hours=len(index),minute=bundle['minute'],models=len(full['models']),
                checked_virtual_accounts=len(full['virtual_accounts']),max_abs=errors)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True)
    p.add_argument('--market',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();result=compare(pd.read_pickle(a.bundle),a.market)
    Path(a.output).write_text(json.dumps(result,indent=2),encoding='utf8');print(result)
