"""Account-locked common recovery. No exchange calls and no historical orders."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import numpy as np
import pandas as pd
from .warmup import cutoff
from .initialization_config import REVISION_HISTORY_HOURS
from . import telemetry

# Raw values feeding price/volume factors and the S3 eligibility mask. Derived
# Arrow factors and external sources still have their existing strict validators.
RAW_COLUMNS = ('open','high','low','close','volume','quote_volume','trade_num',
               'taker_buy_base_asset_volume','taker_buy_quote_asset_volume',
               'avg_price_1m','funding_rate','是否交易','symbol_spot','symbol_swap',
               'is_spot','first_candle_time')


def utc_times(values):
    index=pd.DatetimeIndex(values)
    return index.tz_convert('UTC').tz_localize(None) if index.tz is not None else index


def input_frames(raw, pivots, end, start=None):
    end = cutoff(end)
    start = cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out = {}
    for kind, sources in raw.items():
        for symbol, frame in sources.items():
            idx = utc_times(frame.candle_begin_time)
            left,right=idx.searchsorted(start),idx.searchsorted(end,side='right')
            columns = [c for c in RAW_COLUMNS if c in frame]
            value = frame.iloc[left:right][columns].copy();value.index = idx[left:right]
            if 'first_candle_time' in value:
                value['first_candle_time']=utc_times(value.first_candle_time).to_numpy()
            if not value.index.is_unique:raise ValueError('Duplicate revision input time')
            if len(value):out[f'raw/{kind}/{symbol}'] = value.sort_index()
    for kind, tables in pivots.items():
        for field in ('open','close','vwap1m','funding_rate'):
            if field not in tables:continue
            value = tables[field]
            idx = utc_times(value.index)
            value = value.loc[(idx>=start)&(idx<=end)].copy()
            value.index = idx[(idx>=start)&(idx<=end)]
            out[f'pivot/{kind}/{field}'] = value
    return out


def first_revision(old, new):
    """Exact values, NaN equals NaN. Return earliest changed closed input bar."""
    if not old:raise ValueError('Missing revision input coverage')
    end = max(f.index.max() for f in old.values())
    earliest = []
    for key in old.keys() | new.keys():
        if key not in old:
            times = new[key].index[new[key].index<=end]
            if len(times):
                earliest.append(times.min())
                telemetry.add(added_series=1)
            continue
        a = old[key]
        if key not in new:
            earliest.append(a.index.min());telemetry.add(removed_series=1);continue
        b = new[key]
        if not a.index.equals(b.index):
            positions=b.index.get_indexer(a.index)
            if (positions<0).any():raise ValueError('Revision input coverage is incomplete')
            b=b.take(positions)
        if a.equals(b):continue
        columns = a.columns.union(b.columns)
        a = a.reindex(columns=columns);b = b.reindex(index=a.index,columns=columns)
        # Dictionary encodings are not market revisions; compare their values.
        categorical=[c for c in columns if isinstance(a[c].dtype,pd.CategoricalDtype)
                     or isinstance(b[c].dtype,pd.CategoricalDtype)]
        if categorical:
            a=a.astype({c:object for c in categorical});b=b.astype({c:object for c in categorical})
        different = ~(a.eq(b).fillna(False) | (a.isna() & b.isna()))
        changed = different.any(axis=1)
        if changed.any():
            earliest.append(a.index[changed].min())
            if telemetry.active():
                telemetry.add(changed_cells=int(different.to_numpy().sum()),changed_series=1)
                telemetry.fields(different.columns[different.any(axis=0)])
    return min(earliest) if earliest else None


def read_inputs(run_time, minute, end, start=None, initializing=False, raw_swap=None, pivots=None):
    from config import bmac_data_path
    from core.utils.datatools import read_bmac_market_pivot
    from .offline import canonical
    from core.utils.arrow_reader import arrow_snapshot_path
    if raw_swap is None and (not initializing or all(
            arrow_snapshot_path(bmac_data_path,k,run_time).exists() for k in ('spot','swap'))):
        # Arrow already projects the required rows/columns: normalize that result
        # in place, rather than sending 1,000+ tiny frames through input_frames again.
        out={}
        for kind in ('spot','swap'):
            with telemetry.phase(f'revision_arrow_{kind}_s'):
                source=read_arrow_tail(bmac_data_path,kind,run_time,end,start,indexed=True)
            if not source:raise ValueError(f'Missing same-round {kind} Arrow for revision checks')
            out.update({f'raw/{kind}/{canonical(s)}':f for s,f in source.items()})
        with telemetry.phase('revision_pivot_s'):
            out.update(input_frames({},pivots,end,start) if pivots is not None else
                       read_pivot_inputs(bmac_data_path,minute,end,start))
        return out
    raw = {}
    for kind in ('spot','swap'):
        if kind=='swap' and raw_swap is not None:
            raw[kind]=raw_swap;continue
        if initializing:
            sources = (pd.read_pickle(p) for p in sorted((Path(bmac_data_path)/f'{minute}m').rglob(f'{kind}_dict_batch*.pkl')))
            raw[kind]={s:f for source in sources for s,f in source.items()}
        else:
            raw[kind]=read_arrow_tail(bmac_data_path,kind,run_time,end,start)
            if not raw[kind]:raise ValueError(f'Missing same-round {kind} Arrow for revision checks')
    if pivots is None:pivots={k:read_bmac_market_pivot(f'{minute}m',k) for k in ('spot','swap')}
    # Normalize the symbol spelling exactly as MODEL does.
    raw={k:{canonical(s):f for s,f in source.items()} for k,source in raw.items()}
    return input_frames(raw,pivots,end,start)


def read_arrow_tail(root, kind, run_time, end, start=None, indexed=False, columns=None, tail=None):
    """Select columns/rows before pandas conversion; do not load 4050 factor rows."""
    import json
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path
    end=cutoff(end)
    start=cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out={}
    with pa.memory_map(str(arrow_snapshot_path(root,kind,run_time)),'r') as source:
        reader=pa.ipc.open_file(source)
        names=json.loads(reader.schema.metadata[b'symbols'])
        if len(names) != reader.num_record_batches:
            raise ValueError('Arrow symbol metadata differs from record batch count')
        columns=['candle_begin_time']+[c for c in (RAW_COLUMNS if columns is None else columns)
                                      if c != 'candle_begin_time' and c in reader.schema.names]
        data_columns=[c for c in columns if not indexed or c!='candle_begin_time']
        for i,symbol in enumerate(names):
            batch=reader.get_batch(i)
            idx=utc_times(batch.column('candle_begin_time').to_numpy())
            right=idx.searchsorted(end,side='right')
            # MODEL features retain the original last-N-rows convention even
            # for sparse histories. Revision checks keep their time window.
            left=max(0,right-tail) if tail is not None else idx.searchsorted(start)
            if right<=left:continue
            frame=batch.select(data_columns).slice(int(left),int(right-left)).to_pandas(use_threads=False)
            if indexed:
                # The time index already exists. Do not convert a duplicate
                # timestamp column or retain its full-history backing buffer.
                frame.index=idx[left:right].copy(deep=True).rename('candle_begin_time')
                if not frame.index.is_unique:raise ValueError('Duplicate revision input time')
                if 'first_candle_time' in frame:
                    frame['first_candle_time']=utc_times(frame.first_candle_time).to_numpy()
                if not frame.index.is_monotonic_increasing:frame=frame.sort_index()
            out[symbol]=frame
    return out


def read_pivot_inputs(root, minute, end, start=None):
    """Read each existing PKL once, but concatenate only the monitored interval."""
    end=cutoff(end)
    start=cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    parts={}
    for kind in ('spot','swap'):
        for path in (Path(root)/f'{minute}m').rglob(f'market_pivot_{kind}_*.pkl'):
            for field,frame in pd.read_pickle(path).items():
                if field not in ('open','close','vwap1m','funding_rate'):continue
                idx=utc_times(frame.index)
                value=frame.loc[(idx>=start)&(idx<=end)].copy()
                value.index=idx[(idx>=start)&(idx<=end)]
                parts.setdefault(f'pivot/{kind}/{field}',[]).append(value)
    return {key:pd.concat(frames,copy=False).sort_index() for key,frames in parts.items()}


def trim_inputs(inputs, end):
    end=cutoff(end);start=end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out={}
    for key,value in inputs.items():
        tail=value.loc[start:end]
        if len(tail):out[key]=tail.copy()
    return out


def restore_child(child, time):
    """Restore both untimed/timed accounts and truncate their common selection."""
    child=Path(child);time=cutoff(time)
    for path in child.glob('*.pkl'):
        value=pd.read_pickle(path)
        if path.name.startswith('lots_'):
            point=next(p for p in value['snapshot_queue'] if cutoff(p['time'])==time)
            for kind in ('spot','swap'):value[kind]['sim'].restore(point[kind])
            value['simulate_to']=point['time']
            value['snapshot_queue']=[p for p in value['snapshot_queue'] if cutoff(p['time'])<=time]
            pd.to_pickle(value,path)
        else:
            if not isinstance(value,pd.DataFrame):raise ValueError(f'Unsupported child state: {path.name}')
            idx=value.candle_begin_time if 'candle_begin_time' in value else value.index
            keep=pd.to_datetime(idx,utc=True)<=time.tz_localize('UTC')
            value.loc[np.asarray(keep)].to_pickle(path)


def restore_snapshot(snapshot, revised_from, values):
    """Only mutate an isolated copy. Validate a common time BEFORE writing."""
    from positions.ModelRobust40Strategy import restore_model
    from tools.robust40.replay import common_before
    from positions.ModelRobust40Strategy import slice_context
    from .warmup import validate_snapshot
    snapshot=Path(snapshot)
    bundle=pd.read_pickle(snapshot/'model/state.pkl')
    meta=pd.read_pickle(snapshot/'warmup.pkl')
    if bundle.get('account_identity')!=meta['account'] or bundle['minute']!=cutoff(meta['cutoff']).minute:
        raise ValueError('Checkpoint account/offset differs')
    sections={p:pd.read_pickle(p) for p in (snapshot/'sections').glob('*.pkl')}
    lots={p:pd.read_pickle(p) for p in snapshot.glob('S*/lots_state*.pkl')}
    expected=sum(2 if s.get('re_timing') else 1 for s in values['strategy_pool'])
    if len(lots)!=expected or not sections:raise ValueError('Incomplete common checkpoint modules')
    groups=[{cutoff(p['time']) for p in s.get('snapshot_queue',[])} for s in lots.values()]
    groups += [{cutoff(t) for t in s.get('checkpoints',{})} for s in sections.values()]
    groups += [a.attrs.get('checkpoints',{}) for a in bundle['result']['virtual_accounts']+[bundle['result']['account']]]
    time=common_before(groups,cutoff(revised_from))
    if time.minute!=bundle['minute']:raise ValueError('Common checkpoint offset differs')
    restored,_=restore_model(bundle['context'],bundle['result'],revised_from,time)
    for child in {p.parent for p in lots}:
        restore_child(child,time)
    for path,state in sections.items():
        points=state['checkpoints'];key=next(t for t in points if cutoff(t)==time)
        output=state['output'];keep=pd.to_datetime(output.candle_begin_time,utc=True)<=time.tz_localize('UTC')
        pd.to_pickle(dict(points[key], output=output.loc[keep].copy(),
                          checkpoints={t:s for t,s in points.items() if cutoff(t)<=time}),path)
    context=slice_context(bundle['context'],bundle['context']['index'][bundle['context']['index']<=time])
    bundle=dict(bundle,context=context,result=restored)
    pd.to_pickle(bundle,snapshot/'model/state.pkl')
    (snapshot/'snapshot_time.txt').write_text(str(time.tz_localize('UTC')))
    validate_snapshot(snapshot,bundle)
    return time


@contextmanager
def transaction(destination, stage):
    """Caller holds account_lock. complete=True is the computation commit point.

    Keep one previous complete directory. On failure retain the failed workspace
    for diagnosis and put the old snapshot back. Exchange failure after commit
    must not rewind a calculation whose orders may already have been submitted.
    """
    destination,stage=Path(destination).resolve(),Path(stage).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    if stage==destination or stage.is_relative_to(destination):raise ValueError('Invalid replay stage')
    if backup.exists():
        if backup.parent!=destination.parent:raise ValueError('Invalid replay backup')
        shutil.rmtree(backup)
    destination.replace(backup)
    try:stage.replace(destination)
    except BaseException:
        backup.replace(destination);raise
    try:
        yield
    finally:
        try:committed=pd.read_pickle(destination/'warmup.pkl').get('complete') is True
        except Exception:committed=False
        if not committed:
            destination.replace(stage)
            backup.replace(destination)
        else:
            retire_backup(destination)


def retire_backup(destination):
    destination=Path(destination).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    previous=destination.with_name(destination.name+'.previous')
    if previous.exists():shutil.rmtree(previous)
    backup.replace(previous)


def recover_interrupted(destination):
    """A process killed during the directory transaction leaves this unique backup."""
    destination=Path(destination).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    if not backup.exists():return
    from .server import account_lock
    with account_lock(destination):
        try:committed=pd.read_pickle(destination/'warmup.pkl').get('complete') is True
        except Exception:committed=False
        try:valid_backup=pd.read_pickle(backup/'warmup.pkl').get('complete') is True
        except Exception:valid_backup=False
        if not valid_backup:
            parent=destination.parent.parent/'model_replay';parent.mkdir(exist_ok=True)
            failed=Path(tempfile.mkdtemp(prefix=destination.name+'-unreadable-',dir=parent))
            backup.replace(failed/'snapshot')
            return  # inspect() can now request full initialization as usual.
        if committed:
            retire_backup(destination);return
        if destination.exists():
            parent=destination.parent.parent/'model_replay';parent.mkdir(exist_ok=True)
            failed=Path(tempfile.mkdtemp(prefix=destination.name+'-interrupted-',dir=parent))
            destination.replace(failed/'snapshot')
        backup.replace(destination)


def prepare(destination, values, run_time, minute, end):
    """Check bounded inputs before any child state or exchange action is changed."""
    with telemetry.phase('prepare_s'):
        return _prepare(destination,values,run_time,minute,end)


def _prepare(destination, values, run_time, minute, end):
    destination=Path(destination)
    telemetry.record(offset=minute,cutoff=end)
    with telemetry.phase('old_read_s'):
        old=pd.read_pickle(destination/'model/revision_inputs.pkl')
    start=min(f.index.min() for f in old.values())
    telemetry.record(input_start=start,input_frames=len(old))
    with telemetry.phase('input_read_s'):
        current=read_inputs(run_time,minute,end,start=start)
    with telemetry.phase('compare_s'):
        revised=first_revision(old,current)
    if revised is None:
        telemetry.record(mode='incremental')
        return None,trim_inputs(current,end)
    telemetry.record(mode='replay',revised_from=revised)
    telemetry.emit('revision_detected')
    parent=destination.parent.parent/'model_replay'
    parent.mkdir(exist_ok=True)
    workspace=Path(tempfile.mkdtemp(prefix=destination.name+'-',dir=parent))
    stage=workspace/'snapshot'
    with telemetry.phase('copy_s'):
        shutil.copytree(destination,stage)
    with telemetry.phase('restore_s'):
        restored=restore_snapshot(stage,revised,values)
    telemetry.record(restored_at=restored,replay_hours=float((cutoff(end)-restored)/pd.Timedelta(hours=1)))
    telemetry.emit('restored')
    from .server import save_meta
    meta=pd.read_pickle(stage/'warmup.pkl')
    save_meta(stage,dict(meta,complete=False,cutoff=restored))
    print(f'MODEL历史修订 {revised}；共同恢复至 {restored}，子策略/S3/MODEL重新计算',flush=True)
    return stage,trim_inputs(current,end)
