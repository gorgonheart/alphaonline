"""Common factor data ownership, shared by initialization and live computation.

Time blocks contain the entire market; symbol shards only bound factor tasks.
Rows carry their original order so storage never changes ranking tie semantics.
No strategy parameters, live cache, or persistent state schema lives here.
"""
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
from contextlib import contextmanager
from contextlib import nullcontext
import gc
import os
import time


@contextmanager
def temporary_gc(collection_context=None):
    """Reclaim a bounded conversion's cycles without scanning the old heap.

    Used only in non-nested scopes by the serial stage coordinator. Pausing automatic
    collection keeps newly created owners in generation zero. References to
    large outputs must be dropped before exit; retained outputs remain valid.
    Restore the caller's setting even on conversion/cleanup failure.
    """
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        try:
            with collection_context if collection_context is not None else nullcontext():
                gc.collect(0)
        finally:
            if enabled:
                gc.enable()
            else:
                gc.disable()


def read_vmstat_swap(path=Path('/proc/vmstat')):
    """Read Linux (pswpin, pswpout) page counters; return (0, 0) when unavailable."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return 0, 0
    pswpin = pswpout = 0
    for line in text.splitlines():
        if line.startswith('pswpin '):
            pswpin = int(line.split()[1])
        elif line.startswith('pswpout '):
            pswpout = int(line.split()[1])
    return pswpin, pswpout


@contextmanager
def compute_phase(label):
    """Actual shared-stage boundaries, including child startup and completion.

    Initializers may supply their existing cgroup-aware callback instead.
    The same structured marker is consumed by the existing log observer.
    """
    started = time.perf_counter()
    def mark(event):
        print('INIT_PHASE '+json.dumps(dict(event=event, phase=label, pid=os.getpid(),
            time=time.time(), elapsed_s=time.perf_counter()-started)), flush=True)
    mark('start')
    try:
        yield
    finally:
        mark('end')


def ordered_results(executor, function, arguments, *, max_pending, before_submit=None):
    """Bound submitted work *and* completed results while preserving source order.

    Waiting for the oldest task deliberately applies backpressure. An unbounded
    reorder dictionary would otherwise retain the whole market behind one slow coin.
    """
    from collections import deque
    if max_pending < 1:
        raise ValueError('max_pending must be positive')
    source = iter(arguments)
    pending = deque()
    def submit():
        try:
            args = next(source)
        except StopIteration:
            return False
        if before_submit:
            before_submit()
        pending.append(executor.submit(function, *args))
        return True
    try:
        for _ in range(max_pending):
            if not submit():
                break
        while pending:
            future = pending.popleft()
            result = future.result()
            yield result
            # Do not retain the previous frame while waiting for the next one.
            del result, future
            submit()
    finally:
        for future in pending:
            future.cancel()


def read_market(path, *, columns=None, start=None, end=None, require_full=False, required_columns=()):
    """Project/slice Arrow before pandas allocation, preserving source dtypes.

Optional columns may be absent (e.g. funding_fee); required columns cannot.
The caller owns yielded frames. The immutable publication stays memory mapped
only for the lifetime of this iterator.
"""
    from core.utils.arrow_reader import ArrowReadError
    with pa.memory_map(str(path),'r') as source:
        reader=pa.ipc.open_file(source)
        metadata=reader.schema.metadata or {}
        if require_full and metadata.get(b'full_history') != b'1':
            raise ArrowReadError('Initialization requires full history Arrow')
        names=json.loads(metadata[b'symbols'])
        if len(names)!=reader.num_record_batches:
            raise ArrowReadError('Arrow symbol metadata differs from batch count')
        missing=set(required_columns)-set(reader.schema.names)
        if missing:raise ArrowReadError(f'Missing precomputed columns: {sorted(missing)}')
        projection=None if columns is None else [c for c in columns if c in reader.schema.names]
        for i,symbol in enumerate(names):
            batch=reader.get_batch(i)
            if start is not None or end is not None:
                index=pd.DatetimeIndex(batch.column('candle_begin_time').to_numpy())
                index=pd.to_datetime(index,utc=True).tz_localize(None)
                left=0 if start is None else index.searchsorted(pd.Timestamp(start).tz_localize(None) if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start).tz_convert('UTC').tz_localize(None))
                right=len(index) if end is None else index.searchsorted(pd.to_datetime(end,utc=True).tz_localize(None),side='right')
                batch=batch.slice(int(left),int(right-left))
            if projection is not None:batch=batch.select(projection)
            yield symbol,batch.to_pandas(use_threads=False)


class FactorBlocks:
    """One physical layout: time block × symbol shard, projected columns.

The backend argument is for offline layout comparisons. Production uses IPC.
Memory chunks use the exact same row contract.
Section outputs form one small column group per time block, not another full
factor cache. Nothing here modifies public inputs or published snapshots.
"""
    def __init__(self,directory,*,hours,minute,backend='ipc'):
        if hours<=0 or minute not in range(60):raise ValueError('Invalid time block grid')
        if backend not in ('memory','ipc','lz4','parquet'):raise ValueError('Unknown block layout')
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=False)
        self.hours=hours;self.minute=minute;self.backend=backend
        self.parts={};self.sections={};self.shards=set();self.rows=0;self.columns=[]
        self.section_columns=[]

    def _partition(self,frame):
        # UTC aligned half-open intervals. Subtract the offset before dividing;
        # a 20m bar at the block boundary belongs to the new block.
        nanos=frame['candle_begin_time'].cast(pl.Datetime('ns')).cast(pl.Int64)
        keys=(nanos-self.minute*60*10**9)//(self.hours*3600*10**9)
        return frame.with_columns(keys.alias('_block')).partition_by('_block',as_dict=True,maintain_order=True)

    def _save(self,frame,name):
        if self.backend=='memory':return frame
        path=self.directory/(name+('.parquet' if self.backend=='parquet' else '.ipc'))
        if self.backend=='parquet':frame.write_parquet(path,compression='zstd')
        else:frame.write_ipc(path,compression='lz4' if self.backend=='lz4' else 'uncompressed')
        return path

    def _read(self,value,columns):
        if isinstance(value,pl.DataFrame):return value.select(columns)
        if value.suffix=='.parquet':return pl.read_parquet(value,columns=columns,parallel='none')
        return pl.read_ipc(value,columns=columns,memory_map=self.backend=='ipc',rechunk=False)

    def append(self,frame,shard):
        if shard in self.shards:raise ValueError('Duplicate factor shard')
        if not len(frame):
            self.shards.add(shard)
            return
        frame=frame.with_row_index('_row',offset=self.rows)
        if self.columns and self.columns!=frame.columns:raise ValueError('Factor shard columns differ')
        saved={}
        for (key,),part in self._partition(frame).items():
            saved[key]=self._save(part.drop('_block'),f'{key}-{shard}')
        # Failed writes remain private scratch; incomplete shards are not visible.
        for key,value in saved.items():self.parts.setdefault(key,[]).append(value)
        self.columns=frame.columns
        self.rows+=len(frame)
        self.shards.add(shard)

    def block_keys(self):
        return tuple(sorted(self.parts))

    def row_order(self,key,keys=('candle_begin_time','symbol'),*,start=None):
        """Sort narrow keys once; no wide panel remains in the store."""
        frame=self.read_block(key,list(keys)).with_row_index('_order')
        if start is not None:
            frame=frame.filter(pl.col('candle_begin_time') >= start)
        return frame.sort(list(keys))['_order'].to_numpy()

    def read_block(self,key,columns,*,order=None):
        """Return projected rows owned by the caller, without caching frames."""
        extra=[c for c in columns if c not in self.columns]
        base=list(dict.fromkeys((['_row'] if extra else [])+[c for c in columns if c in self.columns]))
        frame=pl.concat([self._read(p,base) for p in self.parts[key]])
        if extra:
            grouped={}
            for column in extra:
                source=self.sections[key][column]
                grouped.setdefault(id(source),(source,[]))[1].append(column)
            for source,names in grouped.values():
                section=self._read(source,['_row',*names])
                if not frame['_row'].equals(section['_row']):raise ValueError('Section block row order differs')
                frame=frame.with_columns(section.select(names))
        frame=frame.select(columns)
        return frame if order is None else frame[order]

    def read_history(self,columns,*,predicate=None,start=None):
        """Full history ONLY for a section's dependencies, in original row order."""
        selected=list(dict.fromkeys(['_row',*columns,
            *(predicate.meta.root_names() if predicate is not None else []),
            *(['candle_begin_time'] if start is not None else [])]))
        keys=self.block_keys()
        first=None if start is None else (pd.Timestamp(start).value-self.minute*60*10**9)//(self.hours*3600*10**9)
        values=[]
        for key in keys:
            if first is not None and key<first:continue
            frame=self.read_block(key,selected)
            if start is not None:frame=frame.filter(pl.col('candle_begin_time')>=start)
            if predicate is not None:frame=frame.filter(predicate)
            values.append(frame.select(list(dict.fromkeys(['_row',*columns]))))
        if not values:
            # Preserve the exact IPC schema when the requested window is empty.
            return self.read_block(keys[0],columns).head(0)
        return pl.concat(values).sort('_row').select(columns)

    def add_sections(self,frame):
        if self.sections:raise ValueError('Section outputs already installed')
        if len(frame)!=self.rows or not np.array_equal(frame['_row'].to_numpy(),np.arange(self.rows,dtype=np.uint32)):
            raise ValueError('Section rows differ from factors')
        if 'candle_begin_time' not in frame:
            frame=self.read_history(['_row','candle_begin_time']).with_columns(frame.drop('_row'))
        names=[c for c in frame.columns if c not in ('_row','candle_begin_time')]
        saved={}
        for (key,),part in self._partition(frame).items():
            path=self._save(part.select('_row',*names),f'{key}-sections')
            saved[key]={name:path for name in names}
        self.sections=saved
        self.section_columns=names

    def add_section(self,column,parts):
        """Install a column only after all blocks have been checked and written."""
        if column in self.columns or column in self.section_columns:raise ValueError('Duplicate section column')
        if set(parts)!=set(self.parts):raise ValueError('Section block rows are incomplete')
        for key,part in parts.items():
            if part.columns!=['_row',column] or not part['_row'].equals(self.read_block(key,['_row'])['_row']):
                raise ValueError('Section rows differ from factors')
        saved={key:self._save(part,f'{key}-section-{len(self.section_columns)}') for key,part in parts.items()}
        for key,path in saved.items():self.sections.setdefault(key,{})[column]=path
        self.section_columns.append(column)

    def iter_blocks(self,columns,*,sort_time=True,before_read=None):
        for key in self.block_keys():
            if before_read:before_read()
            order=self.row_order(key,('candle_begin_time','_row')) if sort_time else None
            # No frame local stays alive while the consumer processes this block.
            yield self.read_block(key,columns,order=order)

    def close(self):
        self.parts.clear();self.sections.clear()
        # This object created this exact directory exclusively; never public data.
        shutil.rmtree(self.directory)
