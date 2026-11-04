"""Cross-process oracle for native thread budgets and ambiguous mixed keys."""
import os
from pathlib import Path
import subprocess
import sys

import polars as pl


SCRIPT = r'''
from pathlib import Path
from types import SimpleNamespace
import sys
import polars as pl
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_streaming_selection import inputs,strategy
from core import select_coin as select
from core.utils.factor_data import FactorBlocks
folder=Path(sys.argv[1]);folder.mkdir()
mode=sys.argv[2]
for periods in (97,4097):
    data=inputs(folder/str(periods),periods=periods).with_columns(
        pl.lit('').alias('symbol_spot'),pl.lit('').alias('symbol_swap'))
    data=pl.concat([data,data.with_columns(pl.lit(1,dtype=data.schema['is_spot']).alias('is_spot'))])
    stg=strategy();stg.select_scope='mix';stg.order_first='swap'
    if mode=='original':
        result=select.process_strategy_thread(stg,folder,factor_source=data.lazy(),return_result=True)
    else:
        store=FactorBlocks(folder/f'blocks-{periods}',hours=24,minute=55);store.append(data,0)
        conf=SimpleNamespace(strategy_list=[stg],get_result_folder=lambda:folder,unified_time='2017-01-01')
        paths=select.select_factor_blocks([conf],store,folder/f'selected-{periods}',workers=1)[0][0]
        result=pl.concat([pl.read_ipc(p,memory_map=False) for p in paths])
        store.close()
    result.write_ipc(folder/f'{periods}.ipc')
'''


def test_original_one_thread_matches_shared_four_threads_for_unmapped_ties(tmp_path):
    root=Path(__file__).resolve().parents[1]
    for mode,threads in [('original',1),('shared',4)]:
        env=dict(os.environ,POLARS_MAX_THREADS=str(threads),OPENBLAS_NUM_THREADS='1',
                 OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMBA_NUM_THREADS='1')
        run=subprocess.run([sys.executable,'-X','utf8','-B','-c',SCRIPT,str(tmp_path/mode),mode],
                           cwd=root,env=env,capture_output=True,text=True,encoding='utf8',timeout=120)
        assert run.returncode==0,run.stdout+run.stderr
    for periods in (97,4097):
        old=pl.read_ipc(tmp_path/'original'/f'{periods}.ipc')
        new=pl.read_ipc(tmp_path/'shared'/f'{periods}.ipc')
        assert old.schema==new.schema and old.equals(new)
