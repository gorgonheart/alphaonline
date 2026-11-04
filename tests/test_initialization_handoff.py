"""Memory interfaces must preserve the existing filesystem calculation result."""
from types import SimpleNamespace
import inspect
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest


def test_initial_model_reads_projected_arrow_not_full_pkl(tmp_path,monkeypatch):
    import json
    from positions import ModelRobust40Strategy as model
    from test_robust40_contract import candles
    run=pd.Timestamp('2026-09-11 00:20',tz='UTC')
    frame=candles(220);frame['unused_factor']=123.
    folder=tmp_path/'20m';folder.mkdir()
    path=folder/f'swap_dict_{int(run.timestamp())}.arrow'
    batch=pa.RecordBatch.from_pandas(frame,preserve_index=False)
    with pa.ipc.new_file(str(path),batch.schema.with_metadata({b'symbols':b'["BTCUSDT"]',b'full_history':b'1'})) as out:out.write_batch(batch)
    def forbidden(*args,**kw):raise AssertionError('Full PKL read')
    monkeypatch.setattr(pd,'read_pickle',forbidden)
    actual=model.load_model_raw(tmp_path,'20m',run,run.tz_localize(None),live=False)['BTCUSDT']
    assert 'unused_factor' not in actual
    pd.testing.assert_frame_equal(model.coin_features(actual),model.coin_features(frame))


def test_core_memory_interfaces_are_explicit_and_optional():
    from core.equity import calc_equity
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.select_coin import concat_select_results,transfer_spot_to_swap
    assert inspect.signature(calc_equity).parameters['ratio_inputs'].default is None
    assert inspect.signature(calc_equity).parameters['market_inputs'].default is None
    assert inspect.signature(MultiEquityBacktestConfig.process_equities).parameters['inputs'].default is None
    assert inspect.signature(concat_select_results).parameters['frames'].default is None
    assert inspect.signature(transfer_spot_to_swap).parameters['selection'].default is None


def test_arrow_integer_promotion_does_not_hide_a_revision():
    from tools.robust40.handoff import first_revision
    index=pd.date_range('2026-01-01',periods=3,freq='h')
    old={'raw/swap/A':pd.DataFrame({'trade_num':np.array([1,2,3],dtype=np.int64)},index=index)}
    new={k:f.astype({'trade_num':'float64'}) for k,f in old.items()}
    assert first_revision(old,new) is None
    new['raw/swap/A'].iloc[1,0]+=1
    assert first_revision(old,new)==index[1]


def test_merge_and_transfer_memory_matches_file_path(tmp_path,monkeypatch):
    import core.select_coin as select
    strategy=SimpleNamespace(get_fullname=lambda **kw:'test')
    snapshot=tmp_path/'snapshot';snapshot.mkdir()
    conf=SimpleNamespace(strategy_list=[strategy],get_result_folder=lambda:tmp_path,
        get_snapshot_folder=lambda:snapshot,is_reserved=lambda _:False,has_snapshot=False,lookback_days=0)
    frame=pd.DataFrame(dict(candle_begin_time=pd.date_range('2026-01-01',periods=3,freq='h'),
        strategy='test',is_spot=0,order_first='swap',symbol='A',close=1.))
    frame.to_pickle(tmp_path/'test.pkl')
    expected=select.concat_select_results(conf)
    expected_transfer=select.transfer_spot_to_swap(conf)
    actual=select.concat_select_results(conf,frames=[frame],persist_result=False)
    actual_transfer=select.transfer_spot_to_swap(conf,selection=actual)
    pd.testing.assert_frame_equal(actual,expected)
    pd.testing.assert_frame_equal(actual_transfer,expected_transfer)
    assert not (tmp_path/'选币结果.pkl').exists()
    pd.testing.assert_frame_equal(pd.read_pickle(snapshot/'选币结果.pkl'),expected)
