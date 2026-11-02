"""Offline checkpoint overhead on the saved 40m model and compact S3 state.

The revision oracle holds child targets fixed; it does not rerun factor selection.
"""
import argparse
import importlib
import json
import pickle
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def run(root):
    from positions.ModelRobust40Strategy import simulate, portfolio_targets
    from tools.robust40.offline import hybrid_function
    from positions.ModelRobust40Strategy import model_replay
    from tools.robust40.replay import section_run
    snapshot=root/'data/snapshot/40m_robust_4h_MODEL_ROBUST40_V1_40m'
    source=snapshot/'model/state.pkl';version=source.stat().st_mtime_ns
    b=pd.read_pickle(source);c=b['context'];previous=b['result'];captured=dict(previous)
    report={'model':{},'section':{}}
    accounts=[];baseline_seconds=0.;capture_seconds=0.;checkpoints=[];hourly_overhead=[]
    for i,(weights,reference) in enumerate(zip([previous['base']]+previous['views']+[None],
                                               previous['virtual_accounts']+[previous['account']])):
        targets=previous['targets'] if weights is None else portfolio_targets(weights,c)
        buffer=.4 if weights is None else .2
        began=time.perf_counter();base=simulate(targets,c,buffer);elapsed=time.perf_counter()-began
        # First run includes cold Numba compilation. Repeat to measure warm overhead.
        if i==0:
            report['model']['cold_first_simulation_s']=elapsed
            began=time.perf_counter();base=simulate(targets,c,buffer);elapsed=time.perf_counter()-began
        baseline_seconds+=elapsed
        began=time.perf_counter();new=simulate(targets,c,buffer,checkpoint_hours=24);first_capture=time.perf_counter()-began
        if i==0:report['model']['cold_first_capture_s']=first_capture
        began=time.perf_counter();new=simulate(targets,c,buffer,checkpoint_hours=24);capture_seconds+=time.perf_counter()-began
        np.testing.assert_array_equal(base.to_numpy(),reference.to_numpy())
        np.testing.assert_array_equal(new.to_numpy(),base.to_numpy())
        accounts.append(new);checkpoints.append(dict(points=new.attrs['checkpoints'],layout=new.attrs['checkpoint_layout']))
        rows={'leverages','funding_rates','require_rebalance','capital_flow_arr'}
        tail=dict(index=c['index'][-1:],simulation={k:v[-1:] if k in rows or k.endswith('_p') else v for k,v in c['simulation'].items()})
        ts={k:v.iloc[-1:] for k,v in targets.items()};seed=new.attrs['checkpoints'][c['index'][-2]]
        timings={0:[],24:[]}
        for _ in range(5):
            for hours in (0,24):
                began=time.perf_counter();simulate(ts,tail,buffer,seed,checkpoint_hours=hours);timings[hours].append(time.perf_counter()-began)
        hourly_overhead.append(float(np.median(timings[24])-np.median(timings[0])))
        print('MODEL checkpoint account',i+1,'passed',flush=True)
    captured['virtual_accounts']=accounts[:-1];captured['account']=accounts[-1]
    began=time.perf_counter();payload=pickle.dumps(checkpoints,protocol=pickle.HIGHEST_PROTOCOL)
    serialization_seconds=time.perf_counter()-began
    # Serialize only new checkpoint state, never copy the full model per hour.
    scratch=root/'research/checkpoint_trial/model_checkpoints.pkl'
    began=time.perf_counter();scratch.write_bytes(payload);write_seconds=time.perf_counter()-began
    report['model'].update(accounts=10,checkpoint_hours=24,checkpoint_bytes=len(payload),
        warm_full_simulation_s=baseline_seconds,with_checkpoints_s=capture_seconds,
        capture_overhead_s=capture_seconds-baseline_seconds,write_s=write_seconds,
        hourly_capture_overhead_s=sum(hourly_overhead),serialize_s=serialization_seconds)
    d=pd.read_pickle(root/'research/model_price_revision/overlap.pkl')['differences']
    corrected=dict(c,simulation=dict(c['simulation']),close=c['close'].copy())
    corrected['simulation']['swap_close_p']=c['simulation']['swap_close_p'].copy()
    columns=[s for s in c['columns'] if s.startswith('swap:')]
    rows=c['index'].get_indexer(d.time);j=pd.Index(columns).get_indexer(d.symbol)
    np.testing.assert_array_equal(corrected['simulation']['swap_close_p'][rows,j],d.saved)
    corrected['simulation']['swap_close_p'][rows,j]=d.current
    for row in d.itertuples(index=False):corrected['close'].loc[row.time,row.symbol]=row.current
    calc=hybrid_function(root/'positions/HybridRotationDriftStrategy.py')
    hybrid=calc(c['equities'],SimpleNamespace(params=b['params']));hybrid.attrs={}
    began=time.perf_counter();replayed,point=model_replay(corrected,hybrid,{},captured,d.time.min(),hours=24)
    report['model']['replay_s']=time.perf_counter()-began
    report['model']['restored_at']=str(point)
    for key in ('parent','base','delta'):
        np.testing.assert_allclose(replayed[key],previous[key],rtol=1e-12,atol=1e-10)
    for weights,actual in zip([previous['base']]+previous['views']+[None],
                               replayed['virtual_accounts']+[replayed['account']]):
        targets=previous['targets'] if weights is None else portfolio_targets(weights,c)
        full=simulate(targets,corrected,.4 if weights is None else .2)
        np.testing.assert_allclose(actual.to_numpy(),full.to_numpy(),rtol=1e-12,atol=1e-9)
        for a,z in zip(actual.attrs['simulation_state'],full.attrs['simulation_state']):
            for x,y in zip(a,z):np.testing.assert_allclose(x,y,rtol=1e-12,atol=1e-9,equal_nan=True)
    report['model']['real_revision_replay_passed']=True
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    source_section=next((snapshot/'sections').glob('*.pkl'));state=pd.read_pickle(source_section)
    symbols=sorted(set(state['ranks'][-1].index)|set(state['held']))
    times=pd.date_range(state['cutoff']+pd.Timedelta(hours=1),periods=24,freq='h')
    rng=np.random.default_rng(46);n=state['params'][0];column=state['output'].columns[-1]
    frame=pd.DataFrame(dict(candle_begin_time=np.repeat(times,len(symbols)),symbol=np.tile(symbols,24),
        is_spot=0,symbol_spot='',symbol_swap=np.tile(symbols,24),是否交易=1))
    frame[f'VolumeMaxBias_{n}']=rng.random(len(frame))
    # Real state/universe size with synthetic future ranks; no fabricated live prices.
    began=time.perf_counter();expected,normal=section.signal_with_state(frame,state['params'],column,state)
    baseline=time.perf_counter()-began
    began=time.perf_counter();actual,new=section_run(frame,state['params'],column,state,hours=24)
    duration=time.perf_counter()-began
    np.testing.assert_array_equal(actual[column],expected[column]);assert new['held']==normal['held']
    report['section']=dict(workload='real saved state; synthetic 24h ranks',symbols=len(symbols),
        existing_output_bytes=source_section.stat().st_size,baseline_24h_s=baseline,checkpoint_24h_s=duration,
        checkpoint_bytes=len(pickle.dumps(new['checkpoints'],protocol=pickle.HIGHEST_PROTOCOL)),passed=True)
    child_points=[]
    for folder in snapshot.iterdir():
        if folder.is_dir() and folder.name.startswith('S'):
            for path in folder.glob('lots_state*.pkl'):
                lots=pd.read_pickle(path)
                child_points.append(dict(child=folder.name,state=path.stem,time=lots['simulate_to'],
                    spot=lots['spot']['sim'].snapshot(),swap=lots['swap']['sim'].snapshot()))
    child_bytes=len(pickle.dumps(child_points,protocol=pickle.HIGHEST_PROTOCOL))
    report['children']=dict(states=len(child_points),one_point_bytes=child_bytes,
        estimated_24_points_bytes=24*child_bytes)
    total=report['model']['checkpoint_bytes']+report['section']['checkpoint_bytes']+24*child_bytes
    report['estimated_total_24h_per_offset_bytes']=total
    report['estimated_11_offsets_bytes']=total*11
    from positions.ModelRobust40Strategy import quarterly_predictions
    from sklearn.linear_model import LogisticRegression
    obs=previous['predictions'].drop(columns='MODEL');fit=LogisticRegression.fit;calls=[]
    def counted(self,*args,**kwargs):calls.append(1);return fit(self,*args,**kwargs)
    LogisticRegression.fit=counted
    try:
        began=time.perf_counter();cached,_,_=quarterly_predictions(obs,c['index'][-1],previous=previous)
        cached_s=time.perf_counter()-began;cached_fits=len(calls);calls.clear()
        began=time.perf_counter();fresh,_,_=quarterly_predictions(obs,c['index'][-1]);fresh_s=time.perf_counter()-began
        pd.testing.assert_frame_equal(cached,fresh)
        report['training']=dict(reuse_s=cached_s,full_fit_s=fresh_s,reuse_fit_calls=cached_fits,
                                full_fit_calls=len(calls),predictions_identical=True)
    finally:LogisticRegression.fit=fit
    report['production_model_unchanged']=source.stat().st_mtime_ns==version
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    import socket
    def blocked(*a,**k):raise RuntimeError('Network disabled for offline checkpoint benchmark')
    socket.socket.connect=blocked;socket.create_connection=blocked
    result=run(Path(__file__).resolve().parents[3])
    Path(a.output).write_text(json.dumps(result,indent=2),encoding='utf8');print(result,flush=True)
