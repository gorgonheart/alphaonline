import pandas as pd
import pytest
from tools.robust40.server import validate_initial_history


@pytest.mark.parametrize('minute',range(0,60,5))
def test_later_listing_over_14000_bars_does_not_shift_global_origin(minute):
    times=pd.date_range(f'2025-03-29 05:{minute:02}',periods=14063,freq='h')
    raw={'BTCUSDT':pd.DataFrame({'candle_begin_time':times}),
         'GUNUSDT':pd.DataFrame({'candle_begin_time':times[55:]})}
    origin=pd.Timestamp(f'2025-04-30 00:{minute:02}')
    validate_initial_history(raw,origin,720,1)
    # The result must not depend on symbol iteration order.
    validate_initial_history(dict(reversed(list(raw.items()))),origin,720,1)


@pytest.mark.parametrize('minute',range(0,60,5))
def test_real_market_warmup_shortage_still_rejected(minute):
    times=pd.date_range(f'2025-03-31 12:{minute:02}',periods=14063,freq='h')
    raw={'BTCUSDT':pd.DataFrame({'candle_begin_time':times})}
    with pytest.raises(ValueError,match='S3.*history'):
        validate_initial_history(raw,pd.Timestamp(f'2025-04-30 00:{minute:02}'),720,1)


def test_only_4050_rows_is_not_full_initialization():
    times=pd.date_range('2025-01-01',periods=4050,freq='h')
    with pytest.raises(ValueError,match='Full raw history'):
        validate_initial_history({'BTC':pd.DataFrame({'candle_begin_time':times})},times[-1],720,1)
