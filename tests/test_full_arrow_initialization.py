"""Full-history input contract; no network or running producer needed."""
import importlib.util
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from core.utils import arrow_reader, datatools


def frame(n=30):
    times = pd.date_range('2025-01-01', periods=n, freq='h')
    return pd.DataFrame(dict(candle_begin_time=times, first_candle_time=times[0],
        last_candle_time=times[-1], close=np.arange(n, dtype=float)+1, symbol='BTCUSDT',
        symbol_swap='BTCUSDT', symbol_spot='', is_spot=0, 是否交易=1,
        CciV3_2=np.arange(n, dtype=float)))


def publish(path, data, full=True):
    batch=pa.RecordBatch.from_pandas(data, preserve_index=False)
    metadata=dict(batch.schema.metadata or {})
    metadata.update({b'symbols':json.dumps(['BTCUSDT']).encode(), b'full_history':b'1' if full else b'0'})
    with pa.ipc.new_file(str(path),batch.schema.with_metadata(metadata)) as out:
        out.write_batch(batch)


def test_producer_full_publication_and_legacy_tail(tmp_path, producer_root):
    spec=importlib.util.spec_from_file_location('producer_publish',producer_root/'core/arrow_publish.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    data=frame()
    for count, expected in [(0,30),(7,7)]:
        path=tmp_path/f'{count}.arrow'
        module.publish_arrow_snapshot({'BTCUSDT':data},path,count)
        with pa.memory_map(str(path),'r') as src:
            reader=pa.ipc.open_file(src)
            assert reader.get_batch(0).num_rows == expected
            assert reader.schema.metadata[b'full_history'] == (b'1' if count==0 else b'0')
        pd.testing.assert_frame_equal(arrow_reader.read_arrow_market_dict(path)['BTCUSDT'],data.tail(expected).reset_index(drop=True))


def test_reader_limits_before_pandas_and_strict_full(tmp_path):
    path=tmp_path/'x.arrow'; publish(path,frame())
    actual=arrow_reader.read_arrow_market_dict(path, max_rows=7)['BTCUSDT']
    pd.testing.assert_frame_equal(actual,frame().tail(7).reset_index(drop=True))
    pairs=list(arrow_reader.iter_arrow_market(path, require_full=True, required_columns=['CciV3_2']))
    assert len(pairs[0][1]) == 30
    publish(path,frame(),full=False)
    with pytest.raises(arrow_reader.ArrowReadError,match='full'):
        list(arrow_reader.iter_arrow_market(path,require_full=True))
    publish(path,frame().drop(columns='CciV3_2'))
    with pytest.raises(arrow_reader.ArrowReadError,match='CciV3_2'):
        list(arrow_reader.iter_arrow_market(path,require_full=True,required_columns=['CciV3_2']))


def test_full_strict_merge_never_recomputes(monkeypatch):
    from core import factor
    def forbidden(*a,**k):raise AssertionError('long factor recomputed')
    monkeypatch.setattr(factor,'calc_factor_vals',forbidden)
    conf=SimpleNamespace(input_mode='arrow_full_strict', precompute_factor_configs={'CciV3':[2]},
        black_list=[],white_list=[],min_kline_num=1, get_kline_num=lambda:2)
    _, actual=datatools.read_and_merge_bmac_data(conf,'swap',frame(),pd.Timestamp('2026-01-01'))
    assert len(actual)==30
    with pytest.raises(ValueError,match='CciV3_2'):
        datatools.read_and_merge_bmac_data(conf,'swap',frame().drop(columns='CciV3_2'),pd.Timestamp('2026-01-01'))


@pytest.mark.parametrize('n', [30, 70000])
def test_batched_factors_match_legacy_files(tmp_path, monkeypatch, n):
    import config
    import polars as pl
    import core.select_coin as select
    from concurrent.futures import ThreadPoolExecutor
    from tools import streaming_factors
    candles=[]
    for symbol in ['BTCUSDT','ETHUSDT','EMPTYUSDT']:
        data=frame(1 if symbol=='EMPTYUSDT' else n)
        data['symbol']=symbol
        candles.append(data)
    conf=SimpleNamespace(input_mode='full_pkl',name='isolated',min_kline_num=2,
        factor_params_dict={'CciV3':[2],'Bias':[3,5]}, precompute_factor_configs={'CciV3':[2]},
        factor_col_name_list=['Bias_3','CciV3_2','Bias_5'],has_section_factor=True,
        section_depend_factor_col_name_list=['Bias_3'],strategy_list=[],avg_price_col='close',is_day_period=False)
    current=[tmp_path/'legacy']; current[0].mkdir()
    def path(*parts,**kw): return current[0]/parts[-1]
    monkeypatch.setattr(select,'get_file_path',path)
    monkeypatch.setattr(select,'get_snapshot_time',lambda _:None)
    monkeypatch.setattr(select,'ProcessPoolExecutor',lambda **kw:ThreadPoolExecutor(max_workers=2))
    monkeypatch.setattr(select,'factor_col_limit',2)
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    pd.to_pickle(candles,path('all_candle_df_list.pkl'))
    select.calc_factors(conf)
    current[0]=tmp_path/'stream'; current[0].mkdir()
    batches=current[0]/'candle_batches'; batches.mkdir()
    pd.to_pickle(candles[:1],batches/'000000.pkl')
    pd.to_pickle(candles[1:],batches/'000001.pkl')
    conf.input_mode='arrow_full_strict'
    streaming_factors.calc_factors(conf)
    expected=sorted(p.name for p in (tmp_path/'legacy').glob('*.parquet'))
    assert expected==sorted(p.name for p in current[0].glob('*.parquet'))
    for name in expected:
        assert pl.read_parquet(tmp_path/'legacy'/name).equals(pl.read_parquet(current[0]/name))
    import pyarrow.parquet as pq
    # Match the framework's native writer for high-cardinality float factors:
    # dictionary decoding otherwise inflates downstream full-history selection.
    encodings=pq.ParquetFile(current[0]/'factor_Bias_3.parquet').metadata.row_group(0).column(0).encodings
    assert 'RLE_DICTIONARY' not in encodings
    # All columns use common fixed row boundaries, including a final short group.
    assert pq.ParquetFile(current[0]/'factor_Bias_3.parquet').num_row_groups == (1 if n==30 else 2)


def test_freeze_includes_same_round_full_arrow(tmp_path):
    import os
    from tools.robust40.market_inputs import freeze_inputs
    source=tmp_path/'source'; folder=source/'20m'; folder.mkdir(parents=True)
    exg=source/'exginfo'; exg.mkdir()
    for kind in ('spot','swap'):
        publish(folder/f'{kind}_dict_0.arrow',frame())
        (folder/f'{kind}_dict_batch0.pkl').write_bytes(b'raw')
        (folder/f'market_pivot_{kind}_0.pkl').write_bytes(b'pivot')
        (exg/f'exginfo_{kind}.pkl').write_bytes(b'units')
    for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
        (folder/f'{prefix}0.ready').touch()
    ready=max(p.stat().st_mtime_ns for p in folder.glob('*.ready'))
    dest=tmp_path/'frozen'
    freeze_inputs(source,exg,dest,20,0,ready,full_arrow=True)
    assert os.path.samefile(folder/'swap_dict_0.arrow',dest/'20m/swap_dict_0.arrow')
    assert not (dest/'20m/swap_dict_batch0.pkl').exists()
    publish(folder/'swap_dict_0.arrow',frame(),full=False)
    with pytest.raises(ValueError,match='full-history'):
        freeze_inputs(source,exg,tmp_path/'failed',20,0,ready,full_arrow=True)


def test_strict_reuse_survives_mixed_parameters_and_detects_removed_column(monkeypatch):
    import core.select_coin as select
    from unittest.mock import Mock
    conf=SimpleNamespace(input_mode='arrow_full_strict',name='isolated',min_kline_num=0,
        factor_params_dict={'CciV3':[2,3]},precompute_factor_configs={'CciV3':[2]},has_section_factor=False)
    monkeypatch.setattr(select,'get_snapshot_time',lambda _:None)
    calc=Mock(return_value={'CciV3_3':np.arange(30,dtype=float)})
    monkeypatch.setattr(select,'calc_factor_vals',calc)
    select.calc_factors_by_candle(frame(),conf,['CciV3_2','CciV3_3'])
    assert calc.call_args.args[2]==[3]
    calc.reset_mock()
    with pytest.raises(ValueError,match='removed'):
        select.calc_factors_by_candle(frame().drop(columns='CciV3_2'),conf,['CciV3_2'])
    calc.assert_not_called()
