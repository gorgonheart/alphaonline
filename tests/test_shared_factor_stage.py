"""The shared factor producer preserves original per-symbol mathematics."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import pandas as pd
import polars as pl
import pytest


def inputs():
    from test_full_arrow_initialization import frame
    return [frame(30).assign(symbol=s) for s in ('BTCUSDT','ETHUSDT','XRPUSDT')]


def configuration():
    return SimpleNamespace(input_mode='full_pkl', name='shared-test', min_kline_num=2,
        factor_params_dict={'Bias':[3,5]}, precompute_factor_configs={},
        factor_col_name_list=['Bias_3','Bias_5'],has_section_factor=True,
        strategy_list=[],avg_price_col='close',is_day_period=False)


def setup(monkeypatch):
    import config
    import core.select_coin as select
    monkeypatch.setattr(select,'ProcessPoolExecutor',lambda **kw:ThreadPoolExecutor(max_workers=2))
    monkeypatch.setattr(select,'get_snapshot_time',lambda _:None)
    monkeypatch.setattr(select,'job_num',2)
    monkeypatch.setattr(select,'factor_col_limit',1)
    monkeypatch.setattr(config,'factor_symbol_batch_size',2,raising=False)
    monkeypatch.setattr(config,'compute_gate',None,raising=False)
    return select


def test_store_path_does_not_write_temporary_candle_pickle(tmp_path,monkeypatch):
    from core.utils import functions
    from core.utils.factor_data import FactorBlocks
    select=setup(monkeypatch);conf=configuration()
    monkeypatch.setattr(functions,'iter_preprocessed_candles',lambda *a:iter(inputs()),raising=False)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=0)
    expected=pl.concat([select.process_candle_df_return_polars(f.copy(),conf,conf.factor_col_name_list,i)[1]
                        for i,f in enumerate(inputs())])
    def forbidden(*args,**kwargs):raise AssertionError('Temporary candle pickle is unnecessary')
    monkeypatch.setattr(pd,'to_pickle',forbidden)
    select.calc_factors(conf,run_time=pd.Timestamp('2026-01-01'),store=store)
    assert store.read_history(expected.columns).equals(expected)
    assert not list(tmp_path.rglob('*.pkl'))
    store.close()


def test_out_of_order_completion_keeps_original_rows(tmp_path,monkeypatch):
    from core.utils import functions
    from core.utils.factor_data import FactorBlocks
    select=setup(monkeypatch);conf=configuration()
    monkeypatch.setattr(functions,'iter_preprocessed_candles',lambda *a:iter(inputs()),raising=False)
    original=select.process_candle_df_return_polars
    def calculate(frame,config,columns,index):
        if index==0:time.sleep(.03)
        return original(frame,config,columns,index)
    monkeypatch.setattr(select,'process_candle_df_return_polars',calculate)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=0)
    select.calc_factors(conf,run_time=pd.Timestamp('2026-01-01'),store=store)
    assert store.read_history(['symbol'])['symbol'].unique(maintain_order=True).to_list()==['BTCUSDT','ETHUSDT','XRPUSDT']
    store.close()


def test_factor_sharding_keeps_parameter_order(tmp_path,monkeypatch):
    from core.utils import functions
    from core.utils.factor_data import FactorBlocks
    select=setup(monkeypatch);conf=configuration();calls=[]
    monkeypatch.setattr(functions,'iter_preprocessed_candles',lambda *a:iter(inputs()[:1]),raising=False)
    original=select.process_candle_df_return_polars
    def calculate(frame,config,columns,index):
        calls.append(tuple(columns))
        return original(frame,config,columns,index)
    monkeypatch.setattr(select,'process_candle_df_return_polars',calculate)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=0)
    select.calc_factors(conf,run_time=pd.Timestamp('2026-01-01'),store=store)
    assert calls==[('Bias_3',),('Bias_5',)]
    store.close()


def test_pending_factor_results_are_bounded():
    from core.utils.factor_data import ordered_results
    produced=[]
    def arguments():
        for i in range(100):
            produced.append(i)
            yield (i,)
    with ThreadPoolExecutor(max_workers=2) as pool:
        iterator=ordered_results(pool,lambda i:i,arguments(),max_pending=4)
        assert next(iterator)==0
        assert len(produced)<=4
        assert list(iterator)==list(range(1,100))

def test_parameter_shards_defer_section_columns_to_section_stage(tmp_path,monkeypatch):
    from core.utils import functions
    from core.utils.factor_data import FactorBlocks
    select=setup(monkeypatch);conf=configuration()
    # The real config list includes section outputs alongside their dependencies.
    conf.factor_col_name_list=['Bias_3','TopKTo2K_(3,)','Bias_5']
    conf.factor_params_dict['TopKTo2K']=[(3,)]
    monkeypatch.setattr(functions,'iter_preprocessed_candles',lambda *a:iter(inputs()),raising=False)
    store=FactorBlocks(tmp_path/'blocks',hours=6,minute=0)
    expected=pl.concat([select.process_candle_df_return_polars(f.copy(),conf,conf.factor_col_name_list,i)[1]
                        for i,f in enumerate(inputs())])
    select.calc_factors(conf,run_time=pd.Timestamp('2026-01-01'),store=store)
    assert store.read_history(expected.columns).equals(expected)
    assert 'TopKTo2K_(3,)' not in store.columns
    store.close()
