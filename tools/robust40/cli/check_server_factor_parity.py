"""Offline comparison of published Arrow factors against full raw-PKL recomputation.

No producer launch, trading calls, or source mutations. Failures are reported, never
blessed as new golden data. Run against a quiescent publication or a frozen copy.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def compare(folder, params, symbols, extension_dir=None):
    from core.factor import calc_factor_vals
    records = []
    for kind in ('spot','swap'):
        files = sorted(folder.glob(f'{kind}_dict_*.arrow'),key=lambda p:int(p.stem.rsplit('_',1)[1]))
        if not files:raise ValueError(f'{kind}: no Arrow publication')
        path = files[-1]
        stamp = int(path.stem.rsplit('_',1)[1])
        if not all((folder/f'{prefix}{stamp}.ready').exists() for prefix in
                   ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_')):
            raise ValueError('Publication does not have a common ready cutoff')
        raw_paths = sorted(folder.glob(f'{kind}_dict_batch*.pkl'))
        before = {p:(p.stat().st_size,p.stat().st_mtime_ns) for p in raw_paths+[path]}
        ready_ns = max(p.stat().st_mtime_ns for p in folder.glob(f'*_{stamp}.ready'))
        if any(v[1]>ready_ns for v in before.values()):raise ValueError('PKL publication newer than Arrow')
        arrow = {}
        with pa.memory_map(str(path),'r') as source:
            reader = pa.ipc.open_file(source)
            names = json.loads(reader.schema.metadata[b'symbols'])
            for i,s in enumerate(names):
                if not symbols or s in symbols:arrow[s] = reader.get_batch(i).to_pandas()
        found = set()
        for raw_path in raw_paths:
            for symbol,df in pd.read_pickle(raw_path).items():
                if symbol not in arrow:continue
                found.add(symbol)
                a = arrow[symbol].copy()
                df = df.sort_values('candle_begin_time').drop_duplicates('candle_begin_time',keep='last')
                df = df.reset_index(drop=True)
                at = pd.to_datetime(a.candle_begin_time,utc=True)
                dt = pd.to_datetime(df.candle_begin_time,utc=True)
                closed = pd.Timestamp(stamp,unit='s',tz='UTC')
                idx = pd.DatetimeIndex(at[at<closed].tail(4050))
                for name,parameters in params.items():
                    calculated = calc_factor_vals(df.copy(),name,parameters)
                    for p in parameters:
                        col = f'{name}_{p}'
                        if col not in a:raise ValueError(f'Arrow missing {col}')
                        x = pd.Series(np.asarray(calculated[col]),index=pd.DatetimeIndex(dt)).reindex(idx).to_numpy(float)
                        y = pd.Series(a[col].to_numpy(),index=pd.DatetimeIndex(at)).reindex(idx).to_numpy(float)
                        mask = np.isfinite(x)&np.isfinite(y)
                        ok = np.allclose(x,y,rtol=1e-10,atol=1e-10,equal_nan=True)
                        record=dict(kind=kind,symbol=symbol,column=col,rows=len(idx),
                            raw_rows=len(df),passed=bool(ok),nan_equal=bool(np.array_equal(np.isnan(x),np.isnan(y))),
                            max_abs=float(np.max(np.abs(x[mask]-y[mask]))) if mask.any() else 0.)
                        if extension_dir and name=='ILLQStdBbi45208':
                            import importlib.util
                            candidates=list(Path(extension_dir).glob('_illq_core*.pyd'))+list(Path(extension_dir).glob('_illq_core*.so'))
                            spec=importlib.util.spec_from_file_location('_illq_core',candidates[0])
                            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
                            arrays=[np.ascontiguousarray(df[c],dtype=float) for c in ('open','high','low','close','quote_volume')]
                            out=np.empty(len(df));ptr=lambda v:mod.ffi.cast('double *',v.ctypes.data)
                            mod.lib.compute_illq(*map(ptr,arrays),int(p),len(df),ptr(out))
                            z=pd.Series(out,index=pd.DatetimeIndex(dt)).reindex(idx).to_numpy()
                            record['rebuilt_c_passed']=bool(np.allclose(x,z,rtol=1e-10,atol=1e-10,equal_nan=True))
                            record['rebuilt_c_max_abs']=float(np.nanmax(np.abs(x-z)))
                        records.append(record)
                del arrow[symbol]
        if not found:raise ValueError(f'{kind}: requested symbols absent')
        after = {p:(p.stat().st_size,p.stat().st_mtime_ns) for p in before}
        if before!=after:raise ValueError('Inputs changed during comparison; repeat on frozen inputs')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--folder',required=True)
    parser.add_argument('--symbols',nargs='*',default=['BTCUSDT','ETHUSDT','0GUSDT'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--extension-dir',help='Optional isolated rebuilt ILLQ extension; never replaces published data')
    parser.add_argument('--factor',help='Restrict a follow-up audit to a named factor')
    args = parser.parse_args()
    from tools.robust40.warmup import settings
    values,_ = settings(args.account)
    params=values['precompute_factor_configs']
    if args.factor:params={args.factor:params[args.factor]}
    rows = compare(Path(args.folder),params,set(args.symbols),args.extension_dir)
    Path(args.output).write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf8')
    failed = [r for r in rows if not r['passed']]
    print(f'{len(rows)} comparisons; {len(failed)} failed; report: {args.output}')
    if failed:raise SystemExit(1)


if __name__=='__main__':main()
