"""Optional offline C++ experiment; no native library is required by production."""
import importlib
import os

import numpy as np
import pandas as pd
import pytest

from test_section_resume_state import candles, oracle
from tools.benchmarks.s3_memory import native_kernel


@pytest.mark.parametrize('minute',[5,20,55])
@pytest.mark.parametrize('n',[17,22])
def test_cpp_membership_and_restart_match_independent_oracle(minute,n,monkeypatch):
    library=os.environ.get('S3_BENCH_LIBRARY')
    if not library:pytest.skip('Offline C++ candidate not built; set S3_BENCH_LIBRARY')
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    monkeypatch.setattr(section,'compute_holdings_numba_pct',native_kernel(library))
    frame=candles(minute,n)
    frame[f'VolumeMaxBias_{n}']=frame[f'VolumeMaxBias_{n}'].round(1)
    params=(n,.25,.6,str(frame.candle_begin_time.min()+pd.Timedelta(hours=n)))
    expected=oracle(frame,params)
    full,state=section.signal_with_checkpoints(frame.copy(),params,'chosen',hours=25)
    np.testing.assert_array_equal(full.chosen,expected)
    assert len(state['checkpoints'])==25
    state=None
    for stop in (75,115,130,160):
        chunk=frame[frame.candle_begin_time<frame.candle_begin_time.min()+pd.Timedelta(hours=stop)].copy()
        actual,state=section.signal_with_state(chunk,params,'chosen',state)
        np.testing.assert_array_equal(actual.chosen,expected.loc[actual.index])
