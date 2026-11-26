"""S12 LOCAL, offline bootstrap and locked atomic continuation. No exchange imports."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import numpy as np
# Worker initializes its native runtime before pandas/numba. The live process never loads LightGBM.
if __name__ == '__main__' and 'worker' in sys.argv[1:2]:
    from lightgbm import LGBMRegressor
    _warm=LGBMRegressor(n_estimators=1,num_leaves=2,min_child_samples=1,n_jobs=1,verbosity=-1,force_col_wise=True)
    _warm.fit(np.array([[0.],[1.]]),np.array([0.,0.]))
import pandas as pd

VERSION = 's12-local-v1'
MEMBERS = (2, 4, 5, 7, 8, 9, 10, 11)
COLUMNS = [f'S{i+1}' for i in MEMBERS]
H, WARM, START, RETRAIN, LOOKBACK = 168, 6720, 1008, 672, 730*24
FLOOR = 1e-4

def features(prices):
    p = pd.DataFrame(prices); lp = np.log(p); r = lp.diff(); d = {}
    for w in (24,168,336,762):
        d[f'ret_{w}'] = lp.diff(w).shift(1).to_numpy()
        d[f'dev_{w}'] = (lp-np.log(p.rolling(w).mean())).shift(1).to_numpy()
        d[f'vol_{w}'] = np.log(np.sqrt(r.pow(2).rolling(w).mean()*H)+FLOOR).shift(1).to_numpy()
        d[f'down_{w}'] = np.log(np.sqrt(r.clip(upper=0).pow(2).rolling(w).mean()*H)+FLOOR).shift(1).to_numpy()
        d[f'dd_{w}'] = (p/p.rolling(w).max()-1).shift(1).to_numpy()
    d['negative_fraction'] = r.lt(0).rolling(168).mean().shift(1).to_numpy()
    d['inactive_fraction'] = r.abs().lt(1e-12).rolling(168).mean().shift(1).to_numpy()
    d['down_accel'] = d['down_24']-d['down_336']
    d['vol_accel'] = d['vol_168']-d['vol_762']
    for key in ('ret_168','ret_336','down_168','dd_336'):
        rel = np.full_like(prices,np.nan); rank = np.full_like(prices,np.nan)
        for group in ((0,1),(2,3,4,5,6,7)):
            v=d[key][:,group]; rel[:,group]=v-v.mean(axis=1,keepdims=True)
            rank[:,group]=pd.DataFrame(v).rank(axis=1,pct=True).to_numpy()
        d[key+'_relative']=rel; d[key+'_rank']=rank
    for j,member in enumerate(MEMBERS):
        v=np.zeros_like(prices);v[:,j]=1;d[f'is_S{member+1}']=v
    return np.stack(list(d.values()),axis=-1)

def labels(prices):
    r=pd.DataFrame(np.log(prices)).diff()
    return np.log(np.sqrt(r.clip(upper=0).pow(2).rolling(H).sum().shift(-H))+FLOOR).to_numpy()

def validate(frame):
    frame=frame.copy();frame.index=pd.to_datetime(frame.index,utc=True)
    if list(frame.columns)!=COLUMNS or len(frame)<2:
        raise ValueError('Expected hourly close matrix '+str(COLUMNS))
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError('Duplicate or unordered timestamps')
    if not (frame.index.to_series().diff().dropna()==pd.Timedelta(hours=1)).all():
        raise ValueError('Hourly gap: do not forward-fill missing equity')
    v=frame.to_numpy(dtype=float)
    if not np.isfinite(v).all() or (v<=0).any():raise ValueError('Nonpositive/nonfinite equity')
    return frame.astype(float)

def merge_history(old,new):
    old=validate(old);new=validate(new)
    overlap=old.index.intersection(new.index)
    # Skip the first 1000 incoming hours: moving-start warm-up is not stable.
    overlap=overlap.intersection(new.index[1000:])
    if len(overlap)<168:raise ValueError('Need >=168 stable overlapping hours after 1000h warmup')
    ro=np.log(old).diff().loc[overlap]; rn=np.log(new).diff().loc[overlap]
    if not np.allclose(ro,rn,atol=1e-8,rtol=1e-6):
        raise ValueError('Equity overlap returns changed; rebuild/audit, never silently splice')
    if new.index[-1]<old.index[-1]:raise ValueError('Stale input')
    if new.index[-1]==old.index[-1]:return old
    if old.index[-1] not in overlap:raise ValueError('History overlap does not reach current tail')
    tail=new.loc[new.index>old.index[-1]]* (old.iloc[-1]/new.loc[old.index[-1]])
    return validate(pd.concat([old,tail]))

def train(x,y,indices):
    from lightgbm import LGBMRegressor
    model=LGBMRegressor(objective='regression',n_estimators=100,learning_rate=.035,
        num_leaves=7,max_depth=3,min_child_samples=60,reg_alpha=.1,reg_lambda=5.,
        colsample_bytree=.85,subsample=.8,subsample_freq=1,max_bin=63,
        random_state=17,n_jobs=1,verbosity=-1,force_col_wise=True)
    model.fit(x[indices,7],y[indices,7]);return model.booster_

def advance(state,frame):
    """Replay missed scheduled decisions using only their then-mature labels."""
    frame=validate(frame)
    if str(frame.index[0])!=state['origin']:raise ValueError('Origin changed')
    n=len(frame); first=state['next_decision']; out=dict(state)
    out['predictions']=list(state['predictions'])
    if first>=n:return out
    x=features(frame.to_numpy());y=labels(frame.to_numpy())
    ts=np.arange(START,n,24)
    # Original common availability required a mature 20-neighbor feature with >=24 samples,
    # even though LOCAL did not use it. Preserve that eligibility, not the expensive distances.
    counts=np.searchsorted(ts,ts-H,side='left')
    available=np.isfinite(x[ts]).all(axis=(1,2)) & np.isfinite(y[ts]).all(axis=1) & (counts>=24)
    model=None
    if out['model']:
        from lightgbm import Booster
        model=Booster(model_str=out['model'])
    for t in range(first,n,H):
        hist=ts[available & (ts+H<t) & (ts>=t-LOOKBACK)]
        if len(hist)<100:raise ValueError('Insufficient mature training samples')
        if (t-WARM)%RETRAIN==0 or model is None:
            model=train(x,y,hist);out['model']=model.model_to_string()
            out['last_train']=t;out['training_label_end']=int(hist[-1]+H)
        pred=float(model.predict(x[t,7:8],num_threads=1)[0])
        if not np.isfinite(pred):raise ValueError('Invalid prediction')
        history=[r['prediction'] for r in out['predictions'] if r['t']+H<t and r['t']>=t-365*24]
        target=1.
        if frame.index[t]>=pd.Timestamp(out['activation']):
            if len(history)<40:raise ValueError('Need >=40 mature historical forecasts')
            a=np.asarray(history);q=float(((a<pred).sum()+.5*(a==pred).sum())/len(a))
            target=1-.2*q
        out['predictions'].append(dict(t=t,prediction=pred,target=target))
        out['next_decision']=t+H
    return out

def multipliers(state,index):
    index=pd.DatetimeIndex(pd.to_datetime(index,utc=True));origin=pd.Timestamp(state['origin'])
    hours=(index-origin)/pd.Timedelta(hours=1)
    if (hours<0).any() or not np.all(hours==np.floor(hours)):raise ValueError('Offset/origin mismatch')
    if len(index) and hours.max()>=state['next_decision']:raise ValueError('Forecast expired')
    rows=state['predictions']; current=1.;target=1.;prev=0;events=[]
    for row in rows:
        t=row['t'];current+=np.clip(target-current,-.01*(t-prev),.01*(t-prev))
        target=row['target'];events.append((t,current,target));prev=t
    result=np.ones(len(index));h=np.asarray(hours,dtype=int)
    for i,(t,start,target) in enumerate(events):
        end=events[i+1][0] if i+1<len(events) else state['next_decision']
        mask=(h>=t)&(h<end);steps=h[mask]-t+1
        result[mask]=start+np.clip(target-start,-.01*steps,.01*steps)
    return result

@contextlib.contextmanager
def lock(path):
    path=Path(str(path)+'.lock');path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd);yield
    finally:path.unlink(missing_ok=True)

def atomic_save(path,bundle):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp');os.close(fd)
    try:
        pd.to_pickle(bundle,tmp)
        with open(tmp,'rb+') as f:os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)


class DeferredAtomicSave:
    """Overlap slow pickle+fsync on a background thread while keeping the lock until commit/abort."""

    def __init__(self, path, bundle, lock_cm=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_cm = lock_cm
        self._lock_released = False
        self._committed = False
        self._aborted = False
        self._error = None
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.', suffix='.tmp')
        os.close(fd)
        self._tmp = Path(tmp)

        def _write():
            try:
                pd.to_pickle(bundle, self._tmp)
                with open(self._tmp, 'rb+') as f:
                    os.fsync(f.fileno())
            except BaseException as exc:
                self._error = exc

        self._thread = threading.Thread(
            target=_write,
            name=f'deferred-save-{self.path.name}',
            daemon=True,
        )
        self._thread.start()

    def _release_lock(self):
        if not self._lock_released and self._lock_cm is not None:
            self._lock_released = True
            self._lock_cm.__exit__(None, None, None)

    def commit(self) -> None:
        if self._committed:
            return
        if self._aborted:
            raise RuntimeError('DeferredAtomicSave already aborted')
        try:
            self._thread.join()
            if self._error is not None:
                raise self._error
            os.replace(self._tmp, self.path)
            self._committed = True
        finally:
            self._tmp.unlink(missing_ok=True)
            self._release_lock()

    def abort(self) -> None:
        self._aborted = True
        try:
            self._thread.join()
        except Exception:
            pass
        try:
            self._tmp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self._release_lock()
        except Exception:
            pass


_PRELOAD_LOCK = threading.Lock()
_PRELOAD_SLOT: dict | None = None


def start_model_state_preload(path: Path) -> None:
    """Start a single background thread to unpickle model/state.pkl post-fork."""
    global _PRELOAD_SLOT
    target = Path(path).resolve()
    if not target.is_file():
        return
    slot: dict = {'path': target, 'sig': None, 'bundle': None, 'thread': None}

    def _run():
        try:
            st0 = target.stat()
            sig0 = (st0.st_mtime_ns, st0.st_size)
            bundle = pd.read_pickle(target)
            st1 = target.stat()
            sig1 = (st1.st_mtime_ns, st1.st_size)
            if sig0 == sig1:
                slot['sig'] = sig1
                slot['bundle'] = bundle
        except Exception:
            slot['sig'] = None
            slot['bundle'] = None

    thread = threading.Thread(target=_run, daemon=True, name='model-state-preload')
    slot['thread'] = thread
    with _PRELOAD_LOCK:
        _PRELOAD_SLOT = slot
    thread.start()


def consume_preloaded_model_state(path: Path) -> dict | None:
    """Pop preloaded state if path and (mtime_ns, size) still match; caller should hold state lock."""
    global _PRELOAD_SLOT
    target = Path(path).resolve()
    with _PRELOAD_LOCK:
        slot = _PRELOAD_SLOT
        if slot is None or slot['path'] != target:
            return None
        _PRELOAD_SLOT = None
    thread = slot['thread']
    if thread is not None:
        thread.join()
    bundle = slot['bundle']
    saved_sig = slot['sig']
    slot['bundle'] = None
    if bundle is None or saved_sig is None:
        return None
    try:
        st_now = target.stat()
        if (st_now.st_mtime_ns, st_now.st_size) != saved_sig:
            return None
    except OSError:
        return None
    return bundle


def clear_preloaded_model_state() -> None:
    """Join any active preload thread and drop cached reference."""
    global _PRELOAD_SLOT
    with _PRELOAD_LOCK:
        slot = _PRELOAD_SLOT
        _PRELOAD_SLOT = None
    if slot is not None:
        thread = slot.get('thread')
        if thread is not None:
            thread.join(timeout=5.0)
        slot['bundle'] = None

def isolated_advance(state,frame,directory):
    if state['next_decision']>=len(frame):return state
    with tempfile.TemporaryDirectory(dir=directory,prefix='local-worker-') as folder:
        source=Path(folder)/'input.pkl';dest=Path(folder)/'output.pkl'
        pd.to_pickle(dict(state=state,history=frame),source)
        env=os.environ.copy();env['OMP_NUM_THREADS']='1';env['OPENBLAS_NUM_THREADS']='1'
        subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'worker',
            '--state',str(dest),'--csv',str(source)],check=True,timeout=120,env=env)
        return pd.read_pickle(dest)

def update(path,frame,identity):
    """Trusted local pickle only. Atomic state/history transaction; errors leave old state intact."""
    with lock(path):
        bundle=pd.read_pickle(path)
        if bundle['state']['identity']!=identity or bundle['state']['version']!=VERSION:
            raise ValueError('Account/offset/recipe mismatch')
        merged=merge_history(bundle['history'],frame)
        state=isolated_advance(bundle['state'],merged,Path(path).parent)
        if len(merged)!=len(bundle['history']):atomic_save(path,dict(state=state,history=merged))
        return state

def bootstrap(frame,path,identity,activation):
    frame=validate(frame)
    if len(frame)<18500:raise ValueError('Bootstrap requires >=18500 same-recipe hourly closes')
    state=dict(version=VERSION,identity=identity,origin=str(frame.index[0]),
        activation=str(pd.Timestamp(activation).tz_convert('UTC')),next_decision=WARM,
        model=None,predictions=[])
    with lock(path):
        if Path(path).exists():raise FileExistsError('Bootstrap never overwrites an existing state')
        state=isolated_advance(state,frame,Path(path).parent);atomic_save(path,dict(state=state,history=frame))
    return state

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['bootstrap','inspect','worker','export'])
    p.add_argument('--state');p.add_argument('--csv');p.add_argument('--identity');p.add_argument('--children')
    p.add_argument('--activation',default='2023-01-01T00:00:00Z')
    args=p.parse_args()
    if args.command=='export':
        if not args.children or not args.csv:p.error('--children and --csv required')
        root=Path(args.children);parts=[];names=[]
        for i in range(14):
            folders=list(root.glob(f'S{i+1}-*'))
            if len(folders)!=1:raise ValueError(f'Expected one S{i+1} folder')
            names.append(folders[0].name)
            if i in MEMBERS:
                df=pd.read_pickle(folders[0]/'equity_df.pkl')
                parts.append(pd.Series(df.close.to_numpy(),index=pd.to_datetime(df.candle_begin_time,utc=True),name=f'S{i+1}'))
        result=validate(pd.concat(parts,axis=1,join='inner'))
        dest=Path(args.csv)
        if dest.exists():raise FileExistsError(dest)
        dest.parent.mkdir(parents=True,exist_ok=True);result.to_csv(dest,index_label='candle_begin_time')
        print(json.dumps(dict(rows=len(result),local_expected_names=names),ensure_ascii=False));return
    if not args.state:p.error('--state required')
    if args.command=='worker':
        b=pd.read_pickle(args.csv);pd.to_pickle(advance(b['state'],b['history']),args.state);return
    if args.command=='bootstrap':
        if not args.csv or not args.identity:p.error('--csv and --identity required')
        f=pd.read_csv(args.csv,index_col=0);bootstrap(f,args.state,args.identity,args.activation)
    b=pd.read_pickle(args.state);s=b['state']
    print(json.dumps(dict(version=s['version'],identity=s['identity'],origin=s['origin'],
        rows=len(b['history']),last=str(b['history'].index[-1]),predictions=len(s['predictions']),
        next_decision=str(pd.Timestamp(s['origin'])+pd.Timedelta(hours=s['next_decision']))),ensure_ascii=False))

if __name__=='__main__':main()
