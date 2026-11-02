"""Independent membership oracle: never calls the production numba kernel."""
import importlib
import numpy as np
import pandas as pd
import pytest

section = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')


def candles(minute=20, n=17):
    rng = np.random.default_rng(812)
    times = pd.date_range(f'2025-01-01 00:{minute:02}', periods=160, freq='h')
    rows = []
    for i, t in enumerate(times):
        for j in range(12):
            rows.append((t, f'C{j}', 0, '', f'C{j}', int(not 80 <= i < 115), rng.random()))
    return pd.DataFrame(rows, columns=['candle_begin_time','symbol','is_spot',
                                     'symbol_spot','symbol_swap','是否交易',f'VolumeMaxBias_{n}'])


def oracle(df, params):
    n, enter, leave, start = params
    out = pd.Series(0, index=df.index, dtype='int64')
    valid = df[(df.candle_begin_time >= pd.Timestamp(start)) & df['是否交易'].eq(1)]
    held, history = set(), []
    for k, (_, frame) in enumerate(valid.groupby('candle_begin_time', sort=True)):
        rank = frame.set_index('symbol')[f'VolumeMaxBias_{n}'].rank(method='min', pct=True).sort_index()
        target = max(1, int(len(frame)*enter))
        if k == 0:
            held = set(rank.nsmallest(target).index)
        else:
            held = {s for s in held if s in rank and rank[s] <= leave}
            delayed = rank if n % 11 == 0 else (history[-(n % 11)] if len(history) >= n % 11 else pd.Series(dtype=float))
            candidates = delayed.reindex([s for s in rank.index if s not in held]).dropna()
            held.update(candidates.nsmallest(max(0,target-len(held))).index)
            history.append(rank)
        out.loc[frame.index] = frame.symbol.isin(held).astype(int)
    return out


@pytest.mark.parametrize('minute', range(0,60,5))
@pytest.mark.parametrize('n', [17,22])
def test_full_and_resumed_match_independent_oracle_across_empty_sections(minute,n):
    df = candles(minute,n)
    params = (n,.25,.60,str(df.candle_begin_time.min()+pd.Timedelta(hours=n)))
    expected = oracle(df,params)
    full = section.signal(df.copy(),params,'chosen')
    np.testing.assert_array_equal(full.chosen,expected)
    state = None
    # Includes >24h empty sections, repeated cutoff, and a restart after empty bars.
    for stop in (75, 100, 115, 115, 130, 160):
        chunk = df[df.candle_begin_time < df.candle_begin_time.min()+pd.Timedelta(hours=stop)].copy()
        if state is not None:
            chunk = chunk[chunk.candle_begin_time >= chunk.candle_begin_time.max()-pd.Timedelta(hours=50)]
        actual, state = section.signal_with_state(chunk,params,'chosen',state)
        np.testing.assert_array_equal(actual.chosen, expected.loc[actual.index])
    assert state['cutoff'] == df.candle_begin_time.max()


def test_state_rejects_parameter_changes():
    df = candles()
    params = (17,.25,.6,'2025-01-02 00:20')
    _, state = section.signal_with_state(df.copy(),params,'chosen',None)
    with pytest.raises(ValueError, match='parameters'):
        section.signal_with_state(df.copy(),(17,.2,.6,params[3]),'chosen',state)


def test_tied_ranks_are_prefix_invariant_and_independent_of_input_order():
    df=candles();df['VolumeMaxBias_17']=1.
    params=(17,.25,.6,'2025-01-02 00:20')
    expected=oracle(df,params)
    shuffled=df.sample(frac=1,random_state=6)
    full=section.signal(shuffled.copy(),params,'chosen').sort_index()
    np.testing.assert_array_equal(full.chosen,expected)
    prefix=shuffled[shuffled.candle_begin_time<pd.Timestamp('2025-01-04')]
    _,state=section.signal_with_state(prefix,params,'chosen')
    resumed,_=section.signal_with_state(shuffled,params,'chosen',state)
    np.testing.assert_array_equal(resumed.sort_index().chosen,expected)


@pytest.mark.parametrize('minute', [20,40,55])
def test_14050_upstream_cleanup_and_4050_resume_past_original_start(minute):
    """Roll beyond the S3 origin; recalculate the base factor from each short window."""
    n = 720
    times = pd.date_range(f'2025-01-01 00:{minute}', periods=16000, freq='h')
    rng = np.random.default_rng(1022)
    frames = []
    for symbol in ('A','B','C','D'):
        volume = pd.Series(rng.lognormal(size=len(times)))
        frames.append(pd.DataFrame(dict(candle_begin_time=times, symbol=symbol, is_spot=0,
            symbol_spot='', symbol_swap=symbol, 是否交易=1, volume=volume,
            VolumeMaxBias_720=volume/volume.rolling(n,min_periods=1).max())))
    df = pd.concat(frames,ignore_index=True)
    params = (n,.25,.6,str(times[721]))
    expected = section.signal(df.copy(),params,'chosen')
    prefix = df[df.candle_begin_time<=times[14049]].copy()
    _, state = section.signal_with_state(prefix,params,'chosen')
    for stop in (14289,14529,14769,15009,15249,15489,15729,15969,15999):
        # Upstream physically retains only 14050 bars; trading reads the last 4050.
        retained = df[df.candle_begin_time.between(times[stop-14049],times[stop])]
        chunk = retained[retained.candle_begin_time>=times[stop-4049]].copy()
        chunk['VolumeMaxBias_720'] = chunk.groupby('symbol').volume.transform(
            lambda s:s/s.rolling(n,min_periods=1).max())
        actual,state = section.signal_with_state(chunk,params,'chosen',state)
        np.testing.assert_array_equal(actual.chosen,expected.loc[actual.index,'chosen'])
    assert retained.candle_begin_time.min()>pd.Timestamp(params[3])
    # Without saved state the same retained inputs cannot reconstruct the old origin.
    with pytest.raises(ValueError,match='K线数量不足'):
        section.signal(retained.copy(),params,'chosen')
