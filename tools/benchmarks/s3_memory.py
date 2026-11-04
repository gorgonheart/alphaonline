"""Offline S3 comparison. Does not import config, contact exchanges or read live state.

Compile the optional C++ candidate explicitly; pass --library to compare it.
Each backend should run in a fresh process with identical input dimensions.
--baseline is a frozen pre-change section .py exported from Git, not a new golden.
"""
import argparse
import ctypes
import gc
import importlib.util
import importlib
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def native_kernel(path):
    lib = ctypes.CDLL(str(Path(path).resolve()))
    fn = lib.s3_holdings
    array = np.ctypeslib.ndpointer
    fn.argtypes = [array(np.int64,flags='C_CONTIGUOUS'),array(np.int32,flags='C_CONTIGUOUS'),
        array(np.float64,flags='C_CONTIGUOUS'),ctypes.c_int64,ctypes.c_int,
        array(np.float64,flags='C_CONTIGUOUS'),ctypes.c_double,ctypes.c_double,
        ctypes.c_int,ctypes.c_int,array(np.int32,flags='C_CONTIGUOUS')]
    fn.restype = None
    def run(times,symbols,ranks,count,previous,enter=.1,leave=.9,active_start=0,delay=1):
        times=np.ascontiguousarray(times,dtype=np.int64)
        symbols=np.ascontiguousarray(symbols,dtype=np.int32)
        ranks=np.ascontiguousarray(ranks,dtype=np.float64)
        previous=np.ascontiguousarray(previous,dtype=np.float64)
        out=np.empty(len(times),dtype=np.int32)
        fn(times,symbols,ranks,len(times),count,previous,enter,leave,active_start,delay,out)
        return out
    return run


def load_section(path):
    spec=importlib.util.spec_from_file_location('s3_benchmark_section',path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['baseline','numba','cpp'],required=True)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--library',type=Path)
    p.add_argument('--hours',type=int,default=14050)
    p.add_argument('--symbols',type=int,default=128)
    p.add_argument('--extra-cols',type=int,default=8)
    p.add_argument('--polars-input',action='store_true',help='Include production-like Polars to pandas conversion')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    module=(load_section(args.baseline) if args.backend=='baseline' else
            importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方'))
    if args.backend=='cpp':module.compute_holdings_numba_pct=native_kernel(args.library)
    # Compilation is reported separately; steady-state section timings use a warmed kernel.
    start=time.perf_counter()
    module.compute_holdings_numba_pct(np.array([0,0],dtype=np.int64),np.array([0,1],dtype=np.int32),
        np.array([.5,1.]),2,np.array([-1.,-1.]),.1,.9,0,720)
    warmup=time.perf_counter()-start
    rng=np.random.default_rng(20261102)
    index=pd.date_range('2025-01-01 00:55',periods=args.hours,freq='h')
    symbols=np.array([f'C{i:04}' for i in range(args.symbols)])
    nrows=args.hours*args.symbols
    frame=pd.DataFrame(dict(candle_begin_time=np.repeat(index,args.symbols),
        symbol=np.tile(symbols,args.hours),is_spot=0,symbol_spot='',symbol_swap='',
        是否交易=1,VolumeMaxBias_720=rng.integers(0,100,nrows).astype(float)/100))
    # A long empty interval exercises the rule that empty sections do not advance delay.
    frame.loc[frame.candle_begin_time.between(index[1000],index[1040]),'是否交易']=0
    for i in range(args.extra_cols):frame[f'unused_{i}']=float(i)
    params=(720,.1,.9,str(index[721]))
    if args.polars_input:
        import polars as pl
        frame=pl.from_pandas(frame).lazy()
    gc.collect()
    process=psutil.Process()
    before=process.memory_info().rss
    peak=[before];stop=threading.Event()
    phase=['conversion'];stage_peaks={}
    def wrap(function,name):
        def measured(*a,**kw):
            previous=phase[0];phase[0]=name
            try:return function(*a,**kw)
            finally:phase[0]=previous
        return measured
    for name in ('signal','signal_with_state','compute_holdings_numba_pct'):
        setattr(module,name,wrap(getattr(module,name),name))
    def sample():
        while not stop.wait(.01):
            rss=process.memory_info().rss
            peak[0]=max(peak[0],rss)
            stage_peaks[phase[0]]=max(stage_peaks.get(phase[0],0),rss)
    watcher=threading.Thread(target=sample,daemon=True);watcher.start()
    start=time.perf_counter()
    # Mirrors core projection in the new path; baseline used the entire wide input.
    if args.polars_input:
        source=(frame if args.backend=='baseline' else frame.select(module.get_state_input_columns(params))).collect().to_pandas()
    else:
        source=frame if args.backend=='baseline' else frame[module.get_state_input_columns(params)]
    phase[0]='checkpoints'
    out,state=module.signal_with_checkpoints(source,params,'chosen',hours=25)
    elapsed=time.perf_counter()-start
    peak[0]=max(peak[0],process.memory_info().rss);stop.set();watcher.join()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    pd.to_pickle(dict(chosen=out.chosen.to_numpy(),state=state),args.output.with_suffix('.pkl'))
    result=dict(backend=args.backend,rows=nrows,hours=args.hours,symbols=args.symbols,polars_input=args.polars_input,
        warmup_s=warmup,elapsed_s=elapsed,baseline_rss_mib=before/2**20,peak_rss_mib=peak[0]/2**20,
        peak_increment_mib=(peak[0]-before)/2**20,stage_peaks_mib={k:v/2**20 for k,v in stage_peaks.items()})
    args.output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))


if __name__=='__main__':main()
