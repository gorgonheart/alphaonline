from types import SimpleNamespace
import numpy as np
import pandas as pd
import polars as pl
import pytest
from core import select_coin as select


def inputs(root, periods=31):
    root.mkdir(exist_ok=True)
    rows=[]
    # Symbol-major inputs, including ties and changing availability.
    for i,symbol in enumerate(['AAAUSDT','BBBUSDT','CCCUSDT']):
        for j,t in enumerate(pd.date_range('2025-01-01 00:55',periods=periods,freq='h')):
            if i==2 and j%4==0:continue
            rows.append(dict(candle_begin_time=t,symbol=symbol,is_spot=0,close=10.,next_close=11.,
                             symbol_spot='',symbol_swap=symbol,是否交易=1,f=float((j+i)%3)))
    frame=pl.from_pandas(pd.DataFrame(rows))
    frame.select(select.KLINE_COLS).write_parquet(root/'all_factors_kline.parquet',row_group_size=9)
    frame.select('f').write_parquet(root/'factor_f.parquet',row_group_size=7)
    return frame


def strategy():
    return SimpleNamespace(name='oracle',factor_columns=['f'],select_scope='swap',order_first='swap',
        use_custom_func=False,select_mode='standard',long_factor='f',short_factor='f',
        long_cap_weight=1.,short_cap_weight=0.,long_select_coin_num=1,long_select_coin_num_max=None,
        select_inclusive='both',is_day_period=False,period_num=3,offset_list=[0,2],cap_weight=.8,
        get_fullname=lambda **kw:'oracle',calc_select_factor_polars_lazy=lambda x:x,
        filter_before_select_polars_lazy=lambda x:(x,x),filter_after_select_polars_lazy=lambda x:x)


def test_block_selection_matches_original_and_independent_oracle(tmp_path,monkeypatch):
    import config
    from tools.streaming_selection import run
    cache=tmp_path/'cache';frame=inputs(cache)
    target=tmp_path/'out';target.mkdir()
    monkeypatch.setattr(select,'get_file_path',lambda *p,**kw:cache/p[-1])
    monkeypatch.setattr(config,'selection_block_hours',6,raising=False)
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    stg=strategy()
    conf=SimpleNamespace(strategy_list=[stg],get_result_folder=lambda:target,unified_time='2017-01-01',name='test')
    select._GLOBAL_FACTOR_LF=frame.lazy()
    select.process_strategy_thread(stg,target,unified_time=conf.unified_time)
    expected=pd.read_pickle(target/'oracle.pkl')
    run(conf,False,False)
    actual=pd.read_pickle(target/'oracle.pkl')
    pd.testing.assert_frame_equal(actual,expected,check_categorical=False)
    source=frame.to_pandas()
    oracle=source[source.f==source.groupby('candle_begin_time').f.transform('min')].copy()
    offset=(((oracle.candle_begin_time-pd.Timestamp('2017-01-01')).dt.total_seconds()/3600)%3).astype('int16')
    oracle=oracle[((offset+1+3)%3).isin(stg.offset_list)]
    actual_keys=set(zip(actual.candle_begin_time,actual.symbol))
    assert actual_keys==set(zip(oracle.candle_begin_time,oracle.symbol))
    weights=oracle.groupby('candle_begin_time').size()
    for row in actual.itertuples():
        assert row.target_alloc_ratio==.8/2/weights[row.candle_begin_time]
    assert select._GLOBAL_FACTOR_LF is None


def test_unsupported_stateful_strategy_retains_original_path(tmp_path,monkeypatch):
    import config
    from tools.streaming_selection import run
    stg=strategy();stg.select_mode='entry_exit'
    conf=SimpleNamespace(strategy_list=[stg],name='legacy')
    calls=[]
    monkeypatch.setattr(select,'select_coin_with_conf',lambda *a,**kw:calls.append((a,kw)))
    run(conf,False,False)
    assert calls[0][0][0] is conf


def test_default_selection_entry_keeps_original_call(monkeypatch):
    import config
    monkeypatch.setattr(config,'selection_runner',None,raising=False)
    calls=[]
    monkeypatch.setattr(select,'select_coin_with_conf',lambda *a,**kw:calls.append((a,kw)))
    conf=object()
    select.select_coins(conf,multi_process=True,silent=True)
    assert calls==[((conf,),dict(multi_process=True,silent=True))]
