"""Projected revision reads must match the existing full readers exactly."""
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from tools.robust40 import handoff as h


def publication(root,minute):
    from core.utils.arrow_reader import arrow_snapshot_path
    run=pd.Timestamp(f'2026-10-31 08:{minute:02}',tz='Asia/Hong_Kong').to_pydatetime()
    end=pd.Timestamp(run).tz_convert('UTC').tz_localize(None)-pd.Timedelta(hours=1)
    idx=pd.date_range(end-pd.Timedelta(hours=450),periods=452,freq='h',tz='UTC')
    frame=pd.DataFrame(dict(candle_begin_time=idx,close=np.arange(len(idx),dtype=float),
        first_candle_time=idx[0],symbol_swap=pd.Categorical(['U-USDT']*len(idx)),unused=9.))
    raw={};pivots={}
    for kind in ('spot','swap'):
        path=arrow_snapshot_path(root,kind,run);path.parent.mkdir(exist_ok=True)
        table=pa.Table.from_pandas(frame,preserve_index=False)
        table=table.replace_schema_metadata(dict(table.schema.metadata or {},symbols=json.dumps(['U-USDT']).encode()))
        with pa.OSFile(str(path),'wb') as f:
            with pa.ipc.new_file(f,table.schema) as writer:writer.write_table(table)
        # Separate historical pieces, deliberately unsorted across files.
        source={}
        for name,part in [('recent',idx[220:]),('older',idx[:220])]:
            value=pd.DataFrame({'U-USDT':np.arange(len(part),dtype=float)},index=part)
            fields={k:value.copy() for k in ('open','close','vwap1m','funding_rate','unused')}
            pd.to_pickle(fields,path.parent/f'market_pivot_{kind}_{name}.pkl')
            for k,v in fields.items():source.setdefault(k,[]).append(v)
        raw[kind]={'UUSDT':frame}
        pivots[kind]={k:pd.concat(v).sort_index() for k,v in source.items()}
    return run,end,raw,pivots


@pytest.mark.parametrize('minute',[5,15,20,40,55])
@pytest.mark.parametrize('hours',[168,410])
def test_normal_reads_project_once_and_match_reference(tmp_path,monkeypatch,minute,hours):
    import config
    from core.utils import datatools
    run,end,raw,pivots=publication(tmp_path,minute)
    start=end-pd.Timedelta(hours=hours-1)
    reference=h.input_frames(raw,pivots,end,start)
    monkeypatch.setattr(config,'bmac_data_path',tmp_path)
    def forbidden(*a,**kw):raise AssertionError('Normal check must not rebuild full frames/pivots')
    monkeypatch.setattr(h,'input_frames',forbidden)
    monkeypatch.setattr(datatools,'read_bmac_market_pivot',forbidden)
    actual=h.read_inputs(run,minute,end,start=start)
    assert actual.keys()==reference.keys()
    for k in actual:pd.testing.assert_frame_equal(actual[k],reference[k])
    assert h.first_revision(reference,actual) is None
    actual['raw/swap/UUSDT'].iloc[-2,0]+=1
    assert h.first_revision(reference,actual)==end-pd.Timedelta(hours=1)


def test_indexed_arrow_duplicate_time_still_rejected(tmp_path):
    from core.utils.arrow_reader import arrow_snapshot_path
    run=pd.Timestamp('2026-10-31 08:40',tz='Asia/Hong_Kong').to_pydatetime()
    end=pd.Timestamp('2026-10-30 23:40')
    path=arrow_snapshot_path(tmp_path,'swap',run);path.parent.mkdir()
    table=pa.table({'candle_begin_time':[end,end],'close':[1.,2.]})
    table=table.replace_schema_metadata({b'symbols':b'["U"]'})
    with pa.OSFile(str(path),'wb') as f:
        with pa.ipc.new_file(f,table.schema) as writer:writer.write_table(table)
    with pytest.raises(ValueError,match='Duplicate'):
        h.read_arrow_tail(tmp_path,'swap',run,end,indexed=True)
