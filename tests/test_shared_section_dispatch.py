"""Section adapters preserve parameter-local mutation and stable row identity."""
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest


def setup(tmp_path, monkeypatch, factors, parameters):
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    monkeypatch.setattr(select, 'KLINE_COLS', ['candle_begin_time', 'symbol'])
    monkeypatch.setattr(select.FactorHub, 'get_by_name', lambda name: factors[name])
    frame = pl.DataFrame({'candle_begin_time': pd.date_range('2026-01-01', periods=4, freq='h'),
                          'symbol': ['A']*4, 'f_1': [1., 2., 3., 4.]})
    store = FactorBlocks(tmp_path/'blocks', hours=2, minute=0)
    store.append(frame, 0)
    conf = SimpleNamespace(section_params_dict=parameters,
        factor_col_name_list=[f'{name}_{p}' for name, ps in parameters.items() for p in ps])
    return select, conf, store


def native(calls):
    def calculate(frame, params, column):
        calls.append('native:polars')
        return frame.with_columns((pl.col('f_1')*params).alias(column))
    return SimpleNamespace(signal_polars=calculate, get_factor_list=lambda p:[('f',1)], is_cross=True)


def test_one_pandas_section_does_not_force_native_sections_to_pandas(tmp_path,monkeypatch):
    calls=[]
    def calculate(frame,params,column):
        calls.append('legacy:pandas');frame[column]=frame.f_1+1;return frame
    legacy=SimpleNamespace(signal=calculate,get_factor_list=lambda p:[('f',1)],is_cross=True)
    select,conf,store=setup(tmp_path,monkeypatch,{'N':native(calls),'L':legacy},{'N':[1],'L':[1]})
    select.calc_cross_sections(conf,store=store)
    assert calls==['native:polars','legacy:pandas']
    assert store.read_history(['N_1','L_1']).rows()==[(1.,2.),(2.,3.),(3.,4.),(4.,5.)]
    store.close()


def test_shared_dependencies_read_once_per_group(tmp_path,monkeypatch):
    select,conf,store=setup(tmp_path,monkeypatch,{'N':native([])},{'N':[1,2]})
    reads=[];original=store.read_history
    def read(columns):
        if 'f_1' in columns:reads.append(tuple(columns))
        return original(columns)
    monkeypatch.setattr(store,'read_history',read)
    select.calc_cross_sections(conf,store=store)
    assert len(reads)==1
    store.close()


def test_unknown_section_mutation_keeps_legacy_semantics(tmp_path,monkeypatch):
    def calculate(frame,params,column):
        frame['f_1']+=params
        frame[column]=frame.f_1
        return frame
    factor=SimpleNamespace(signal=calculate,get_factor_list=lambda p:[('f',1)],is_cross=True)
    select,conf,store=setup(tmp_path,monkeypatch,{'L':factor},{'L':[1,2]})
    select.calc_cross_sections(conf,store=store)
    assert store.read_history(['L_1','L_2']).rows()==[(2.,4.),(3.,5.),(4.,6.),(5.,7.)]
    store.close()


def test_output_rows_match_dependency_rows(tmp_path,monkeypatch):
    factor=native([])
    factor.signal_polars=lambda frame,p,c:frame.with_columns(pl.lit(1).alias(c)).reverse()
    select,conf,store=setup(tmp_path,monkeypatch,{'N':factor},{'N':[1]})
    with pytest.raises(ValueError,match='row'):
        select.calc_cross_sections(conf,store=store)
    assert not store.section_columns
    store.close()
