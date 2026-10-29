"""Independent numerical contracts for the MODEL_ROBUST40 port."""
import numpy as np
import pandas as pd
import pytest

from tools.robust40.features import coin_features, decision_days, select_quarter_model
from tools.robust40.execution import apply_s5_delta
from tools.robust40.parent import solve_sleeves, robust_weekly_mix
from tools.robust40.rebalance import RebRobust40


def candles(n=220, minute=20):
    x = np.arange(n, dtype=float)
    return pd.DataFrame(dict(
        candle_begin_time=pd.date_range(f'2026-09-01 00:{minute:02d}', periods=n, freq='h'),
        first_candle_time=pd.Timestamp('2025-01-01'), close=100 + x + np.sin(x),
        quote_volume=1000 + 13*x, taker_buy_quote_asset_volume=300 + 5*x,
        funding_fee=.00001*(1+x%7)))


def test_features_use_research_units_and_timestamp_age():
    d = candles()
    f = coin_features(d)
    r = d.close.pct_change(fill_method=None)
    recent = d.iloc[-24:]
    assert f.log_volume24.iloc[-1] == pytest.approx(np.log1p(recent.quote_volume.sum()))
    assert f.amihud24.iloc[-1] == pytest.approx(np.log1p((r.tail(24).abs()/recent.quote_volume*1e6).mean()))
    assert f.taker24.iloc[-1] == pytest.approx(recent.taker_buy_quote_asset_volume.sum()/recent.quote_volume.sum())
    assert f.funding24.iloc[-1] == pytest.approx(recent.funding_fee.sum())
    expected = (d.candle_begin_time.iloc[-1]-d.first_candle_time.iloc[-1]).total_seconds()/86400
    assert f.age_days.iloc[-1] == expected
    assert coin_features(d.tail(180)).age_days.iloc[-1] == expected
    assert f.drawdown168.iloc[:167].isna().all()
    assert f.vol_ratio.iloc[:120].isna().all()
    pd.testing.assert_frame_equal(coin_features(d.iloc[:180]), f.iloc[:180])


@pytest.mark.parametrize('minute', [0, 20, 40, 55])
def test_daily_decisions_follow_each_minute_grid(minute):
    times = pd.date_range(f'2026-10-01 00:{minute:02d}', periods=49, freq='h', tz='UTC')
    days = decision_days(times, minute)
    assert days[0] == times[0]
    assert days[23] == times[0]
    assert days[24] == times[24]
    assert set(days.minute) == {minute}
    with pytest.raises(ValueError, match='minute'):
        decision_days(times+pd.Timedelta(minutes=1), minute)


def test_no_future_quarter_fallback_and_utc_normalization():
    april, july = object(), object()
    models = {pd.Timestamp('2026-04-01'): april, pd.Timestamp('2026-07-01'): july}
    assert select_quarter_model(models, pd.Timestamp('2026-03-31', tz='UTC')) is None
    assert select_quarter_model(models, pd.Timestamp('2026-06-30', tz='UTC')) is april
    assert select_quarter_model(models, pd.Timestamp('2026-07-01', tz='UTC')) is july


def test_delta_applies_s5_contribution_by_symbol_without_opening_shorts():
    idx = pd.date_range('2026-10-01 00:20', periods=1, freq='h')
    net = pd.DataFrame([[.30, .05, -.20]], index=idx, columns=['A','B','C'])
    delta = pd.DataFrame([[-.10, -.10, -.10]], index=idx, columns=['C','A','B'])
    actual = apply_s5_delta(net, delta)
    np.testing.assert_allclose(actual.iloc[0], [.20, 0, -.20])
    np.testing.assert_allclose(net.iloc[0], [.30, .05, -.20])
    with pytest.raises(ValueError, match='time'):
        apply_s5_delta(net, delta.set_axis(idx+pd.Timedelta(hours=1)))


def test_two_sleeve_projection_obeys_each_exposure_bound():
    exposure = np.array([[.6, 0], [0, -.4]])
    q = solve_sleeves(exposure, [np.array([1., 0]), np.array([0., 1.])], 2.5)
    np.testing.assert_allclose(q, [.5, .75], rtol=0, atol=1e-12)
    assert abs(exposure @ q).sum() == pytest.approx(.6)


def test_weekly_mix_reads_only_prior_complete_days():
    days = pd.date_range('2025-01-01', periods=250, freq='D')
    net = pd.DataFrame(np.column_stack([np.zeros(250), np.full((250, 8), .001)]), index=days)
    momentum = pd.Series(np.sin(np.arange(250)), index=days)
    full = robust_weekly_mix(net, momentum)
    np.testing.assert_array_equal(full.iloc[:180, 0], np.ones(180))
    assert full.iloc[-1, 0] == .5
    np.testing.assert_allclose(full.iloc[-1, 1:4], np.full(3, 1/6))
    np.testing.assert_array_equal(full.iloc[-1, 4:], np.zeros(5))
    changed = net.copy();changed.iloc[210:] = 100
    pd.testing.assert_frame_equal(full.iloc[:210], robust_weekly_mix(changed, momentum).iloc[:210])


def test_execution_buffer_and_gross_cap_are_applied_after_netting():
    reb = RebRobust40(np.ones(1), np.ones(2), .4, 2.45)
    spot, swap = reb.calc_lots(1000., np.array([100.]), np.array([0], dtype=np.int64), np.array([0.]),
        np.array([100., 100.]), np.array([10, -10], dtype=np.int64), np.array([1.3, -1.3]))
    np.testing.assert_array_equal(spot, [0])
    np.testing.assert_array_equal(swap, [10, -10])
    _, swap = reb.calc_lots(1000., np.array([100.]), np.array([0], dtype=np.int64), np.array([0.]),
        np.array([100., 100.]), np.array([0, 0], dtype=np.int64), np.array([2., -2.]))
    np.testing.assert_array_equal(swap, [12, -12])


def test_virtual_account_resume_preserves_pending_orders_and_fees():
    from tools.robust40.engine import simulate
    index = pd.date_range('2026-01-01 00:20', periods=4, freq='h')
    price = np.array([[100.], [110.], [105.], [120.]])
    args = dict(init_capital=10000., leverages=np.ones(4), spot_lot_sizes=np.ones(1), swap_lot_sizes=np.ones(1),
        spot_c_rate=.001, swap_c_rate=.0006, spot_min_order_limit=10., swap_min_order_limit=5., min_margin_rate=.05,
        require_rebalance=np.ones(4, dtype=np.int8), capital_flow_arr=np.zeros(4), capital_flow_mode=-1,
        funding_rates=np.zeros((4, 1)), spot_open_p=price, spot_close_p=price, spot_vwap1m_p=price,
        swap_open_p=price, swap_close_p=price, swap_vwap1m_p=price)
    targets = dict(spot=pd.DataFrame(0., index=index, columns=['S']), swap=pd.DataFrame(.1, index=index, columns=['U']))
    full = simulate(targets, dict(index=index, simulation=args), .4)
    def part(start, stop, state=None):
        fields = {'leverages', 'require_rebalance', 'capital_flow_arr', 'funding_rates'}
        subset = {k: (v[start:stop] if k in fields or k.endswith('_p') else v) for k, v in args.items()}
        return simulate({k: v.iloc[start:stop] for k, v in targets.items()},
                        dict(index=index[start:stop], simulation=subset), .4, state)
    first = part(0, 2);second = part(2, 4, first.attrs['simulation_state'])
    np.testing.assert_allclose(second.to_numpy(), full.iloc[2:].to_numpy(), rtol=0, atol=1e-12)
    assert full.fee.iloc[0] == 0
    assert full.fee.iloc[1] > 0
