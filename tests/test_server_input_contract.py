from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock
import pandas as pd
import pytest
from core.utils import datatools


@pytest.mark.parametrize('arrow', [None, {'X':pd.DataFrame({'close':[1.]})}])
def test_strict_arrow_does_not_silently_recompute(monkeypatch, arrow):
    conf = SimpleNamespace(input_mode='arrow_strict', required_history_start='2020-01-01',
                           precompute_factor_configs={'CciV3':[1176]})
    monkeypatch.setattr(datatools,'get_dummy_client', lambda:SimpleNamespace(get_market_info=lambda _:{}))
    monkeypatch.setattr(datatools,'read_arrow_market_dict_for_run_time',lambda *a:arrow)
    with pytest.raises(ValueError,match='Arrow'):
        datatools.load_bmac_data('swap',datetime(2026,10,30,0,20),conf)


def test_full_pkl_recomputes_declared_factors_before_slicing(monkeypatch):
    from core import factor
    calls = []
    monkeypatch.setattr(factor,'calc_factor_vals',lambda df,name,params: calls.append(len(df)) or {f'{name}_{p}':df.close for p in params})
    conf = SimpleNamespace(input_mode='full_pkl', precompute_factor_configs={'CciV3':[10]},
        required_history_start=None, black_list=[],white_list=[],min_kline_num=1,get_kline_num=lambda:2)
    times = pd.date_range('2025-01-01',periods=30,freq='h')
    frame = pd.DataFrame(dict(candle_begin_time=times, first_candle_time=times[0],last_candle_time=times[-1],
                             close=1.,symbol='BTCUSDT',symbol_swap='BTCUSDT',symbol_spot=''))
    _, actual = datatools.read_and_merge_bmac_data(conf,'swap',frame,datetime(2026,1,1))
    assert calls == [30]
    assert len(actual) == 30


def test_section_dispatch_only_opted_in(tmp_path):
    from core.utils.section_state import run_section
    legacy = Mock(return_value='legacy')
    factor = SimpleNamespace(signal=legacy, signal_with_state=Mock(return_value=('new',{'cutoff':1})))
    conf = SimpleNamespace(section_state=False,get_snapshot_folder=lambda:tmp_path)
    assert run_section(conf,factor,None,(1,),'x') == 'legacy'
    factor.signal_with_state.assert_not_called()
    conf.section_state = True
    assert run_section(conf,factor,None,(1,),'x') == 'new'
    assert len(list((tmp_path/'sections').glob('*.pkl'))) == 1
