"""Keep feature mathematics; typed market dates must not be parsed again."""
import numpy as np
import pandas as pd
import pytest


def raw(kind, missing_first=False):
    rng=np.random.default_rng(17)
    times=pd.Series(pd.date_range('2025-01-01 00:45',periods=400,freq='h',tz='UTC'))
    frame=pd.DataFrame(dict(close=np.exp(rng.normal(0,.01,400).cumsum()),
        quote_volume=rng.uniform(1,100,400),candle_begin_time=times,
        first_candle_time=times.iloc[0],taker_buy_quote_asset_volume=rng.uniform(0,50,400),
        funding_fee=rng.normal(0,.001,400)))
    frame.loc[[3,99,210],['close','quote_volume']]=[np.nan,0.]
    if kind=='naive':
        for col in ('candle_begin_time','first_candle_time'):frame[col]=frame[col].dt.tz_localize(None)
    if kind=='strings':
        for col in ('candle_begin_time','first_candle_time'):frame[col]=frame[col].astype(str)
    if missing_first:frame=frame.drop(columns='first_candle_time')
    frame.index=np.arange(400)*3
    return frame


@pytest.mark.parametrize('kind',['utc','naive','strings'])
@pytest.mark.parametrize('missing_first',[False,True])
def test_feature_dates_keep_exact_output_and_skip_parsing_typed_series(monkeypatch,kind,missing_first):
    from positions.ModelRobust40Strategy import coin_features
    frame=raw(kind,missing_first);before=frame.copy(deep=True)
    expected=coin_features(frame.copy(deep=True))
    parser=pd.to_datetime;calls=[]
    def parse(value,*args,**kwargs):
        if isinstance(value,pd.Series) and pd.api.types.is_datetime64_any_dtype(value.dtype):
            pytest.fail('Typed Arrow datetime was passed through parsing/cache sampling again')
        calls.append(1)
        return parser(value,*args,**kwargs)
    monkeypatch.setattr(pd,'to_datetime',parse)
    actual=coin_features(frame)
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
    pd.testing.assert_frame_equal(frame,before,check_exact=True)
    first=pd.to_datetime(before.first_candle_time,cache=False) if 'first_candle_time' in before and kind=='strings' else (
        before.first_candle_time if 'first_candle_time' in before else parser(before.candle_begin_time,cache=False).iloc[0])
    times=parser(before.candle_begin_time,cache=False)
    pd.testing.assert_series_equal(actual.age_days,(times-first).dt.total_seconds().div(86400).rename('age_days'),check_exact=True)


@pytest.mark.parametrize('unit',['s','ns'])
def test_arrow_extension_dates_keep_original_parser_and_float_age(unit):
    import pyarrow as pa
    from positions.ModelRobust40Strategy import coin_features
    frame=raw('naive')
    frame.loc[frame.index[3],'first_candle_time']=pd.NaT
    for column in ('candle_begin_time','first_candle_time'):
        frame[column]=frame[column].astype(pd.ArrowDtype(pa.timestamp(unit)))
    original=frame.copy(deep=True)
    for column in ('candle_begin_time','first_candle_time'):
        original[column]=pd.to_datetime(original[column])
    expected=coin_features(original)
    actual=coin_features(frame)
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
