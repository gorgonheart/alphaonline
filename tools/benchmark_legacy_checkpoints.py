"""Offline simulator benchmark on saved 5m/15m targets; never imports account_exec.

Selection/retiming are held fixed. This is not a full strategy backtest acceptance.
Writes only its explicitly supplied research JSON report.
"""
import argparse
import copy
import json
import pickle
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def benchmark(account, root):
    from core.equity import start_simulation
    from core.simulator import Simulator
    from core.rebalance import RebAlways
    minute = int(account.split('m')[0])
    market = Path('D:/alpha/data/preprocess_1h_resample')/f'{minute}m'
    pivots = {k: pd.read_pickle(market/f'market_pivot_{k}_2026.pkl') for k in ('spot','swap')}
    children = sorted((p for p in (root/'data/snapshot'/account).iterdir() if p.is_dir() and p.name.startswith('S')),
                      key=lambda p:int(p.name.split('-')[0][1:]))
    records = []
    for folder in children:
        saved = pd.read_pickle(folder/'lots_state0.pkl')
        ratios = {k:pd.read_pickle(folder/f'ratio_{k}.pkl').reindex(columns=saved[k]['columns'],fill_value=0).tail(240)
                  for k in ('spot','swap')}
        index = ratios['swap'].index
        assert index.equals(ratios['spot'].index) and len(index) == 240
        args = dict(leverages=np.ones(len(index)),min_margin_rate=.05,require_rebalance=np.ones(len(index),dtype=np.int8))
        for k in ('spot','swap'):
            args[k+'_ratio'] = ratios[k].to_numpy()
            for field in ('open','close','vwap1m'):
                args[k+'_'+field+'_p'] = pivots[k][field].reindex(index=index,columns=ratios[k].columns).to_numpy()
        args['funding_rates'] = pivots['swap']['funding_rate'].reindex(index=index,columns=ratios['swap'].columns).fillna(0).to_numpy()
        def run(left,right,points=0,initial=None,inputs=args):
            sims = []
            for k in ('spot','swap'):
                base = saved[k]['sim']
                sim = Simulator(10000. if k=='spot' else 0., base.lot_sizes.copy(),base.fee_rate,
                                np.zeros(len(base.lot_sizes),dtype=np.int64),base.min_order_limit)
                if initial is not None:sim.restore(initial[k])
                sims.append(sim)
            calc = RebAlways(sims[0].lot_sizes,sims[1].lot_sizes)
            sliced = {k:v[left:right] if isinstance(v,np.ndarray) else v for k,v in inputs.items()}
            began=time.perf_counter()
            out=start_simulation(**sliced,pos_calc=calc,sim_spot=sims[0],sim_swap=sims[1],lookback_hours=points)
            return out,time.perf_counter()-began
        # Warm imports/vectorized paths, then compare identical real saved targets.
        run(0,4)
        base,t0=run(0,239)
        captured,t24=run(0,239,24)
        for x,y in zip(base[:7],captured[:7]):np.testing.assert_array_equal(x,y)
        changed=dict(args)
        chosen=None
        for k in ('swap','spot'):
            positions=np.flatnonzero((args[k+'_ratio'][-4] != 0)&np.isfinite(args[k+'_close_p'][-4]))
            if len(positions):
                j=int(positions[0]);key=k+'_close_p';changed[key]=args[key].copy();changed[key][-4,j]*=1.01
                chosen=k+':'+str(ratios[k].columns[j]);break
        reference,_=run(0,240,inputs=changed)
        cp=captured[-1][0];left=cp['bar_index']+1
        replay,treplay=run(left,240,initial=cp,inputs=changed)
        for i in range(7):
            np.testing.assert_allclose(np.r_[captured[i][:left],replay[i]],reference[i],rtol=1e-12,atol=1e-9)
        for expected,actual in zip(reference[7:9],replay[7:9]):
            for k in ('equity','lots','target_lots','last_prices'):
                np.testing.assert_allclose(getattr(expected,k),getattr(actual,k),rtol=1e-12,atol=1e-9,equal_nan=True)
        initial={k:s.snapshot() for k,s in zip(('spot','swap'),base[7:9])}
        appended,tappend=run(239,240,initial=initial,inputs=changed)
        no_replay=np.r_[base[0],appended[0]]
        records.append(dict(child=folder.name,revision_symbol=chosen,lookback0_s=t0,lookback24_s=t24,
            capture_overhead_s=t24-t0,append_one_s=tappend,replay_hours=240-left,replay_s=treplay,
            checkpoint_bytes=len(pickle.dumps(captured[-1],protocol=pickle.HIGHEST_PROTOCOL)),
            no_replay_max_equity_error=float(np.max(abs(no_replay-reference[0]))),replay_matches=True))
        print(account,folder.name,'passed',flush=True)
    return dict(account=account,children=records,checkpoint_bytes=sum(x['checkpoint_bytes'] for x in records),
                capture_overhead_s=sum(x['capture_overhead_s'] for x in records),
                replay_s=sum(x['replay_s'] for x in records),append_s=sum(x['append_one_s'] for x in records))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    # No external operation is permitted even if a dependency unexpectedly tries.
    import socket
    def blocked(*a,**k):raise RuntimeError('Network disabled for offline checkpoint benchmark')
    socket.socket.connect=blocked;socket.create_connection=blocked
    root=Path(__file__).resolve().parents[1]
    result=[benchmark(name,root) for name in ('5m-onheart1020_','15m-nheart1020_')]
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',a.output,flush=True)
