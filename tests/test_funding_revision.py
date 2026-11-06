import pandas as pd
import pyarrow as pa
import pytest

from core.utils.arrow_reader import arrow_snapshot_path
from tools.robust40 import handoff


@pytest.fixture
def bars():
    index = pd.date_range('2026-10-30 18:25', periods=4, freq='h')
    return pd.DataFrame({
        'candle_begin_time': index,
        'close': [1., 2., 3., 4.],
        'funding_fee': [0., .001, 0., -.002],
    })


def revision_inputs(frame):
    return handoff.input_frames({'swap': {'A': frame}}, {}, frame.candle_begin_time.max())


def test_raw_funding_correction_detected_from_arrow(tmp_path, bars):
    old = revision_inputs(bars)
    changed = bars.copy()
    changed.loc[2, 'funding_fee'] = -.003
    end = bars.candle_begin_time.iloc[-1]
    run = (end + pd.Timedelta(hours=1)).tz_localize('UTC')
    path = arrow_snapshot_path(tmp_path, 'swap', run)
    path.parent.mkdir()
    table = pa.Table.from_pandas(changed, preserve_index=False)
    table = table.replace_schema_metadata({b'symbols': b'["A"]'})
    with pa.ipc.new_file(str(path), table.schema) as writer:
        writer.write_table(table)
    actual = handoff.read_arrow_tail(tmp_path, 'swap', run, end, indexed=True)
    assert handoff.first_revision(old, {'raw/swap/A': actual['A']}) == bars.candle_begin_time[2]


def test_legacy_raw_projection_adopts_fee_without_masking_other_revisions(bars):
    key = 'raw/swap/A'
    index = pd.DatetimeIndex(bars.candle_begin_time)
    current = revision_inputs(bars)
    assert 'funding_fee' in current[key]
    old = {key: current[key].drop(columns='funding_fee')}
    pivot = 'pivot/swap/funding_rate'
    old[pivot] = pd.DataFrame({'A': bars.funding_fee.to_numpy()}, index=index)
    current[pivot] = old[pivot].copy()
    assert handoff.first_revision(old, current) is None
    assert 'funding_fee' in current[key]
    current[pivot].iloc[2, 0] += .001
    assert handoff.first_revision(old, current) == index[2]
    current[key].loc[index[1], 'close'] += 1.
    assert handoff.first_revision(old, current) == index[1]


def test_missing_optional_raw_fee_is_observed_after_upgrade(bars):
    missing = revision_inputs(bars.drop(columns='funding_fee'))
    present = revision_inputs(bars)
    assert handoff.first_revision(missing, present) == bars.candle_begin_time[0]
    assert handoff.first_revision(present, missing) == bars.candle_begin_time[0]


def test_settled_funding_replay_matches_full_model_with_fixed_positions():
    from positions import ModelRobust40Strategy as model
    from tools.robust40.replay import common_before
    from test_robust40_runtime import context

    base = context('2026-10-01 00:25', 240)
    base['simulation'].update(spot_c_rate=0., swap_c_rate=0., spot_min_order_limit=10.,
                              swap_min_order_limit=5., min_margin_rate=.05, capital_flow_mode=-1)
    base['close']['swap:BTCUSDT'] = 100.
    index = base['index']
    corrected_at = index[-3]
    weights = pd.DataFrame(.1, index=index, columns=range(14))
    bars = pd.DataFrame(dict(candle_begin_time=index, first_candle_time=index[0],
                             close=100., avg_price_1m=100., quote_volume=1000., funding_fee=0.))
    raw = {'U': bars}
    pivot = pd.DataFrame({'U': 0.}, index=index)

    def inputs(raw_swap, funding_pivot):
        return handoff.input_frames({'swap': raw_swap}, {'swap': {'funding_rate': funding_pivot}}, index[-1])

    previous = model.calculate(base, weights, raw, checkpoint_hours=25)
    fixed_raw = {'U': bars.copy()}
    fixed_raw['U'].loc[bars.candle_begin_time == corrected_at, 'funding_fee'] = -.001
    fixed_pivot = pivot.copy()
    fixed_pivot.loc[corrected_at, 'U'] = -.001
    revised = handoff.first_revision(inputs(raw, pivot), inputs(fixed_raw, fixed_pivot))
    assert revised == corrected_at
    accounts = previous['virtual_accounts'] + [previous['account']]
    checkpoint = common_before([a.attrs['checkpoints'] for a in accounts], revised)
    assert checkpoint == index[-4]

    corrected = dict(base, simulation=dict(base['simulation'], funding_rates=fixed_pivot.to_numpy()))
    replayed, restored_at = model.model_replay(corrected, weights, fixed_raw, previous, revised,
                                              checkpoint_time=checkpoint)
    assert restored_at == checkpoint
    full = model.calculate(corrected, weights, fixed_raw)
    for actual, expected in zip(replayed['virtual_accounts'] + [replayed['account']],
                                full['virtual_accounts'] + [full['account']]):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)

    old = previous['account']
    new = replayed['account']
    pd.testing.assert_series_equal(old.short_pos_value, new.short_pos_value)
    pd.testing.assert_series_equal(old.long_pos_value, new.long_pos_value)
    notional = old.at[checkpoint, 'short_pos_value']
    assert notional == 1900.
    expected_charge = notional * .001
    assert old.at[corrected_at, 'funding_fee'] == 0.
    assert new.at[corrected_at, 'funding_fee'] == pytest.approx(expected_charge)
    assert new.equity.iloc[-1] == pytest.approx(old.equity.iloc[-1] - expected_charge)
