from types import SimpleNamespace

import pandas as pd
import pytest

from core.select_coin import agg_multi_strategy_ratio, agg_multi_strategy_ratio_v2


@pytest.mark.parametrize('aggregate', [agg_multi_strategy_ratio, agg_multi_strategy_ratio_v2])
@pytest.mark.parametrize('minute', [5, 15, 20, 25, 40, 45, 55])
@pytest.mark.parametrize('has_signal', [False, True])
def test_resume_keeps_empty_hours_since_snapshot(tmp_path, aggregate, minute, has_signal):
    child = tmp_path/'S10-test'
    child.mkdir()
    previous = pd.Timestamp(f'2026-10-29 21:{minute:02}')
    end = previous + pd.Timedelta(hours=3)
    (tmp_path/'snapshot_time.txt').write_text(str(previous.tz_localize('UTC')))
    conf = SimpleNamespace(strategy_list=[SimpleNamespace(name='s', hold_period='1H', period_type='H')],
                           is_day_period=False, hold_period_type='H', max_hold_period='1H',
                           incremental_lookback_hours=0, has_snapshot=True, iter_round=10,
                           get_snapshot_folder=lambda: child)
    rows = [dict(candle_begin_time=end, symbol='AAAUSDT', strategy='s', is_spot=0,
                 target_alloc_ratio=.25, **{'方向': 1})] if has_signal else []
    selected = pd.DataFrame(rows, columns=['candle_begin_time','symbol','strategy','is_spot','target_alloc_ratio','方向'])
    spot, swap = aggregate(conf, selected, end)
    expected = pd.date_range(previous+pd.Timedelta(hours=1), end, freq='h')
    # Independent hourly oracle: no signal in the first two hours, then .25 or zero.
    assert spot.index.equals(expected)
    assert swap.index.equals(expected)
    assert swap.iloc[:-1].to_numpy().sum() == 0
    assert swap.iloc[-1].sum() == (.25 if has_signal else 0)
    conf.has_snapshot = False
    _, cold = aggregate(conf, selected, end)
    assert cold.index.equals(pd.date_range(end-pd.Timedelta(hours=1), end, freq='h'))


@pytest.mark.parametrize('aggregate', [agg_multi_strategy_ratio, agg_multi_strategy_ratio_v2])
@pytest.mark.parametrize('lookback', [0, 96])
def test_standalone_daily_snapshot_and_existing_rollback_window(tmp_path, aggregate, lookback):
    previous = pd.Timestamp('2026-10-26 23:45', tz='UTC')
    end = previous + pd.Timedelta(days=3)
    (tmp_path/'snapshot_time.txt').write_text(str(previous))
    conf = SimpleNamespace(strategy_list=[SimpleNamespace(name='s', hold_period='1D', period_type='D')],
                           is_day_period=True, hold_period_type='D', max_hold_period='1D',
                           incremental_lookback_hours=lookback, has_snapshot=True, iter_round=0,
                           get_snapshot_folder=lambda: tmp_path)
    selected = pd.DataFrame(columns=['candle_begin_time','symbol','strategy','is_spot','target_alloc_ratio','方向'])
    selected['candle_begin_time'] = pd.to_datetime(selected.candle_begin_time, utc=True)
    _, actual = aggregate(conf, selected, end)
    start = previous + pd.Timedelta(days=1) if not lookback else end-pd.Timedelta(hours=lookback)
    assert actual.index.equals(pd.date_range(start, end, freq='D'))
