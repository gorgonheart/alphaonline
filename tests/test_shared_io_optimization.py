"""Original whole-history math is the oracle for bounded input changes."""
import importlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl
import pytest

from core.utils.factor_data import FactorBlocks


def make_frame(minute=5):
    times = pd.date_range(f'2026-01-01 00:{minute:02}', periods=13, freq='h')
    frame = pd.DataFrame([(t,s,k) for s in ['B','A','C'] for t in times for k in [0,1]],
                         columns=['candle_begin_time','symbol','is_spot'])
    frame['是否交易'] = np.where(np.arange(len(frame)) % 5, 1, 0)
    frame['symbol_spot'] = frame.symbol
    frame['symbol_swap'] = frame.symbol
    frame.loc[frame.index % 7 == 0, 'symbol_spot'] = ''
    frame['close'] = np.arange(len(frame),dtype=float)
    frame['next_close'] = frame.close.shift(-1)
    frame['当前回撤_3'] = np.resize([-.5,-.25,0.,.5,np.nan],len(frame))
    return pl.from_pandas(frame)


@pytest.mark.parametrize('minute',[0,5,15,25,45,55])
@pytest.mark.parametrize('name,params',[
    ('动态回撤阈值',3),('固定回撤阈值触发币数占比开仓门槛',(3,-.2,.5))])
def test_time_local_sections_use_blocks_with_whole_history_oracle(tmp_path,monkeypatch,minute,name,params):
    from core import select_coin
    factor=importlib.import_module('sections.'+name)
    store=FactorBlocks(tmp_path/'blocks',hours=4,minute=minute)
    frame=make_frame(minute)
    for i,part in enumerate(frame.iter_slices(31)):store.append(part,i)
    column=f'{name}_{params}'
    old=store.read_history(frame.columns)
    expected=factor.signal_polars(old.lazy(),params,column).collect()[column]
    independent=factor.signal(old.to_pandas().copy(),params,column)[column]
    np.testing.assert_allclose(expected.to_numpy(),independent.to_numpy(),rtol=1e-14,atol=1e-14,equal_nan=True)
    read=store.read_history
    monkeypatch.setattr(store,'read_history',lambda *a,**k:pytest.fail('whole history materialized'))
    conf=SimpleNamespace(section_params_dict={name:[params]},factor_col_name_list=[column])
    select_coin.calc_cross_sections(conf,store=store)
    actual=read([column])[column]
    assert actual.equals(expected)
    del actual,expected,old
    store.close()


@pytest.mark.parametrize('indexed,tail',[(True,None),(False,None),(True,7)])
def test_revision_arrow_projection_preserves_values_without_time_column_roundtrip(tmp_path,monkeypatch,indexed,tail):
    import json
    import pyarrow as pa
    from tools.robust40.handoff import read_arrow_tail
    run=pd.Timestamp('2026-01-02 08:05',tz='Asia/Hong_Kong')
    times=pd.date_range('2025-12-31 00:05',periods=40,freq='h',tz='UTC').delete([4,9])
    raw=pd.DataFrame({'candle_begin_time':times,'close':np.arange(len(times),dtype=float),
                      'first_candle_time':times[0]})
    table=pa.Table.from_pandas(raw.assign(unused_precomputed_factor=12.5),preserve_index=False)
    metadata=dict(table.schema.metadata or {},symbols=json.dumps(['A']).encode())
    table=table.replace_schema_metadata(metadata)
    folder=tmp_path/'5m';folder.mkdir()
    path=folder/f'swap_dict_{int(run.timestamp())}.arrow'
    with pa.OSFile(str(path),'wb') as stream:
        with pa.ipc.new_file(stream,table.schema) as writer:writer.write_table(table)
    end=pd.Timestamp('2026-01-01 09:05');start=end-pd.Timedelta(hours=12)
    expected=raw.loc[raw.candle_begin_time<=end.tz_localize('UTC')].copy()
    expected=expected.tail(tail) if tail else expected.loc[expected.candle_begin_time>=start.tz_localize('UTC')]
    if indexed:
        expected=expected.set_index('candle_begin_time')
        expected.index=expected.index.tz_localize(None)
        expected.first_candle_time=expected.first_candle_time.dt.tz_localize(None)
        # Old Arrow has no settled-fee column; revision frames explicitly mark
        # it unknown rather than inventing zero funding or dropping the field.
        expected['funding_fee'] = np.nan
        original=pd.DataFrame.pop
        def pop(frame,column):
            if column=='candle_begin_time':pytest.fail('indexed reader converted and discarded timestamp column')
            return original(frame,column)
        monkeypatch.setattr(pd.DataFrame,'pop',pop)
    else:expected=expected.reset_index(drop=True)
    import tools.robust40.handoff as handoff
    source_indexes=[]
    normalize=handoff.utc_times
    def capture(values):
        result=normalize(values);source_indexes.append(result);return result
    monkeypatch.setattr(handoff,'utc_times',capture)
    actual=read_arrow_tail(tmp_path,'swap',run,end,start,indexed=indexed,tail=tail)['A']
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
    if indexed:
        assert not np.shares_memory(actual.index.to_numpy(),source_indexes[0].to_numpy())


def test_default_shared_phase_marks_actual_success_and_failure(capsys):
    from core.utils.factor_data import compute_phase
    with pytest.raises(ValueError):
        with compute_phase('test.phase'):raise ValueError('test')
    import json
    rows=[json.loads(line.split('INIT_PHASE ',1)[1]) for line in capsys.readouterr().out.splitlines()]
    assert [r['event'] for r in rows]==['start','end']
    assert rows[1]['time']>=rows[0]['time']


@pytest.mark.parametrize('start',['2026-01-01 06:05','2027-01-01',None])
def test_filtered_history_matches_original_order_and_empty_schema(tmp_path,start):
    frame=make_frame()
    store=FactorBlocks(tmp_path/'blocks',hours=4,minute=5)
    for i,part in enumerate(frame.iter_slices(31)):store.append(part,i)
    predicate=(pl.col('is_spot')==0)&(pl.col('symbol_spot')!='')
    columns=['symbol','candle_begin_time','close','is_spot','symbol_spot']
    expected=store.read_history(columns).filter(predicate)
    if start is not None:expected=expected.filter(pl.col('candle_begin_time')>=pd.Timestamp(start))
    actual=store.read_history(columns,predicate=predicate,start=None if start is None else pd.Timestamp(start))
    assert actual.equals(expected)
    del actual,expected
    store.close()
