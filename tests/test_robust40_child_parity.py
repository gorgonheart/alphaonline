"""Independent boundary oracles for research/live child-factor differences."""
import importlib
import math

import numpy as np
import pandas as pd
import polars as pl
import pytest


@pytest.mark.parametrize('engine', ['pandas', 'polars'])
def test_topk_exit_uses_research_2point1_boundary(engine):
    mod = importlib.import_module('sections.TopKTo2K')
    rows = []
    for t in range(3):
        for s in range(10):
            # A enters first, falls to second, then third. ceil(10*.21)=3.
            rank = s+1 if t == 0 else ([2,1,3,4,5,6,7,8,9,10][s] if t == 1 else [3,1,2,4,5,6,7,8,9,10][s])
            rows.append(dict(candle_begin_time=pd.Timestamp('2026-01-01')+pd.Timedelta(hours=t),
                symbol=str(s),is_spot=0,score_1=11-rank))
    frame = pd.DataFrame(rows);expected=[];history={}
    for _,row in frame.iterrows():
        group=frame[frame.candle_begin_time.eq(row.candle_begin_time)]
        rank=1+sum(group.score_1>row.score_1)
        previous=history.setdefault(row.symbol,[]);previous.append(rank<=math.ceil(len(group)*.1))
        expected.append(int(any(previous[-2:]) and rank>=math.ceil(len(group)*.21)))
    actual = (mod.signal(frame.copy(), (2,'score',1), 'factor') if engine == 'pandas'
              else mod.signal_polars(pl.from_pandas(frame).lazy(), (2,'score',1), 'factor').collect().to_pandas())
    np.testing.assert_array_equal(actual.factor,expected)
    assert actual.loc[10,'factor']==0 and actual.loc[20,'factor']==0


@pytest.mark.parametrize('engine', ['pandas', 'polars'])
def test_empty_volume_section_always_returns_zero_factor(engine):
    mod=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    frame=pd.DataFrame(dict(candle_begin_time=pd.date_range('2026-01-01',periods=3,freq='h'),
        symbol='A',is_spot=0,symbol_spot='',symbol_swap='A',VolumeMaxBias_1=np.nan,**{'是否交易':1}))
    params = (1,.1,.2,'2026-01-01 01:00')
    result = (mod.signal(frame, params, 'factor') if engine == 'pandas'
              else mod.signal_polars(pl.from_pandas(frame).lazy(), params, 'factor').collect().to_pandas())
    assert 'factor' in result
    np.testing.assert_array_equal(result.factor,[0,0,0])


def test_volume_zero_mask_preserves_continuation_merge():
    mod = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    start = pd.Timestamp('2026-01-01 01:00')
    rows = []
    for t in range(6):
        for i, symbol in enumerate(['A', 'B', 'C']):
            rows.append(dict(candle_begin_time=start+pd.Timedelta(hours=t-1),
                             symbol=symbol, is_spot=0, symbol_spot='', symbol_swap=symbol,
                             VolumeMaxBias_1=float((i+t//2)%3), **{'是否交易': 1}))
    frame = pd.DataFrame(rows)
    params = (1, .34, .7, str(start))
    full = mod.signal(frame.copy(), params, 'factor')
    # Before activation none are selected; the first active bar selects the lowest score.
    np.testing.assert_array_equal(full.factor.iloc[:6], [0,0,0,1,0,0])
    prev = mod.signal(frame.iloc[:12].copy(), params, 'factor')
    resumed = mod.signal(frame.copy(), params, 'factor', prev)
    assert 'factor_x' not in resumed and 'factor_y' not in resumed
    np.testing.assert_array_equal(resumed.factor, full.factor)
