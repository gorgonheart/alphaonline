"""A real expert simulation, a changing selection universe, and a fee revision."""
from types import SimpleNamespace

import numpy as np
import pandas as pd


def test_first_hour_after_initialization_keeps_the_inclusive_24h_replay_window(tmp_path, monkeypatch):
    from core import equity
    from core.model.rebalance_mode import RebalanceMode
    from tools.robust40.handoff import restore_child
    from tools.robust40.initialization_config import CHECKPOINT_POINTS

    times = pd.date_range('2026-02-01 00:25', periods=64, freq='h', tz='UTC')
    kinds = ('spot', 'swap')
    prices = pd.DataFrame({'AAA': 20.+np.arange(64)/100, 'ZZZ': 50.+np.arange(64)/20}, index=times)
    fees = prices * 0.
    monkeypatch.setattr(equity, 'read_bmac_market_pivot', lambda off, kind:
        dict(open=prices, close=prices, vwap1m=prices, funding_rate=fees))
    monkeypatch.setattr(equity, 'read_bmac_exginfo', lambda kind:
        pd.DataFrame({'symbol':['AAA','ZZZ'], 'lot_size':[.01,.1]}))

    def config(name):
        root = tmp_path/name
        snap, result = root/'snap', root/'result'
        snap.mkdir(parents=True); result.mkdir()
        return SimpleNamespace(get_result_folder=lambda:result, get_snapshot_folder=lambda:snap,
            checkpoint_hours=CHECKPOINT_POINTS, incremental_lookback_hours=0, initial_usdt=10000.,
            is_day_period=False, leverage=1., margin_rate=.05, spot_c_rate=.002, swap_c_rate=.0006,
            spot_min_order_limit=20., swap_min_order_limit=20., rebalance_mode=RebalanceMode.init(None))

    def run(cfg, count, add_symbol):
        for kind in kinds:
            mix = pd.DataFrame({'ZZZ': .25 if kind=='spot' else -.35}, index=times[:count].tz_localize(None))
            if add_symbol:
                mix['AAA'] = np.where(np.arange(count)>=61, .1 if kind=='spot' else -.1, 0.)
            mix.to_pickle(cfg.get_result_folder()/f'df_{kind}_ratio.pkl')
        return equity.calc_equity(cfg,times[count-1]+pd.Timedelta(hours=1),state_name='state0')

    live, full = config('continued'), config('from-scratch')
    run(live,61,False)
    def points(cfg):
        return pd.read_pickle(cfg.get_snapshot_folder()/'lots_state0.pkl')['snapshot_queue']
    # Fresh publication must cover a revision AT cutoff-24h, not just AFTER it.
    assert times[60]-pd.Timedelta(hours=25) in {p['time'] for p in points(live)}
    run(live,62,True)  # Alphabetically earlier new instrument shifts both simulator layouts.
    repaired_bar = times[61]-pd.Timedelta(hours=24)
    before = repaired_bar-pd.Timedelta(hours=1)
    old = next((p for p in points(live) if p['time']==before), None)
    assert old is not None, 'adding the first selected AAA erased the pre-revision checkpoint'
    for kind in kinds:
        assert old[kind]['lots'][0] == old[kind]['target_lots'][0] == 0
        assert old[kind]['last_prices'][0] == prices.at[before,'AAA']
    fees.at[repaired_bar,'ZZZ'] = -.01
    restore_child(live.get_snapshot_folder(),before)
    repaired, oracle = run(live,63,True), run(full,63,True)
    assert oracle.loc[oracle.candle_begin_time==repaired_bar,'funding_fee'].abs().sum() > 1.
    pd.testing.assert_frame_equal(repaired,oracle,check_exact=True)
    for kind in kinds:
        for col in ('lots','target_lots','last_prices','equity'):
            np.testing.assert_array_equal(points(live)[-1][kind][col],points(full)[-1][kind][col])
