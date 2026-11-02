"""Offline checkpoint trials. No live entry points or network calls."""
import importlib
import pickle
import numpy as np
import pandas as pd
import pytest

from tools.robust40 import replay
from positions import ModelRobust40Strategy as model_strategy
from positions.ModelRobust40Strategy import calculate, advance, slice_context
from positions.ModelRobust40Strategy import quarterly_predictions
from positions.ModelRobust40Strategy import MODEL_FEATURES
from test_section_resume_state import candles, oracle
from test_robust40_runtime import context


def test_common_checkpoint_requires_every_module_before_revision():
    times=pd.date_range('2026-10-31 00:40',periods=4,freq='h')
    assert replay.common_before([times[:3],times[:2],times],times[-1])==times[1]
    assert replay.common_before([times,times],times[2])==times[1]
    with pytest.raises(ValueError,match='checkpoint'):
        replay.common_before([times,times+pd.Timedelta(minutes=15)],times[-1])


@pytest.mark.parametrize('minute', [5, 15, 20, 40, 55])
def test_section_revision_replays_from_small_checkpoint_against_oracle(minute):
    df = candles(minute)
    params = (17, .25, .6, str(df.candle_begin_time.min()+pd.Timedelta(hours=17)))
    out, state = replay.section_run(df, params, 'chosen', hours=48)
    np.testing.assert_array_equal(out.chosen, oracle(df, params))
    assert len(state['checkpoints']) == 48
    assert all('output' not in c and 'checkpoints' not in c for c in state['checkpoints'].values())
    changed = df.copy()
    first = df.candle_begin_time.max()-pd.Timedelta(hours=20)
    changed.loc[(changed.candle_begin_time == first) & changed.symbol.eq('C0'), 'VolumeMaxBias_17'] = 99.
    before = pickle.loads(pickle.dumps(state))
    actual, updated = replay.section_replay(changed, params, 'chosen', state, first, hours=48,
                                             checkpoint_time=first-pd.Timedelta(hours=2))
    np.testing.assert_array_equal(actual.chosen, oracle(changed, params))
    pd.testing.assert_frame_equal(state['output'], before['output'])
    assert state['held'] == before['held'] and state['cutoff'] == before['cutoff']
    assert state['checkpoints'].keys() == before['checkpoints'].keys()
    for t, saved in before['checkpoints'].items():
        assert state['checkpoints'][t]['held'] == saved['held']
        for a,b in zip(state['checkpoints'][t]['ranks'],saved['ranks']):pd.testing.assert_series_equal(a,b)
    assert updated['cutoff'] == state['cutoff']
    with pytest.raises(ValueError, match='checkpoint'):
        replay.section_replay(changed, params, 'chosen', state, df.candle_begin_time.min(), hours=48)


@pytest.mark.parametrize('revision_age', [8,30])
def test_model_replay_matches_corrected_full_calculation(revision_age):
    c = context('2026-10-01 00:40', 240)
    c['simulation'].update(spot_c_rate=.001, swap_c_rate=.0006,
        spot_min_order_limit=10., swap_min_order_limit=5., min_margin_rate=.05, capital_flow_mode=-1)
    c['close']['swap:BTCUSDT'] = 100.
    hybrid = pd.DataFrame(.1, index=c['index'], columns=range(14))
    raw = {'U': pd.DataFrame(dict(candle_begin_time=c['index'], first_candle_time=c['index'][0],
                                  close=100., quote_volume=1000., avg_price_1m=100.))}
    original = calculate(c, hybrid, raw, checkpoint_hours=48)
    change = c['index'][-revision_age]
    corrected = dict(c, simulation=dict(c['simulation']), close=c['close'].copy())
    corrected['simulation']['swap_close_p'] = c['simulation']['swap_close_p'].copy()
    corrected['simulation']['swap_close_p'][-revision_age, 0] = 102.
    corrected['close'].loc[change, 'swap:U'] = 102.
    full = calculate(corrected, hybrid, raw)
    reads=[]
    def raw_loader():reads.append(True);return raw
    actual, restored_at = model_strategy.model_replay(corrected, hybrid, raw_loader, original, change, hours=48,
                                               checkpoint_time=change-pd.Timedelta(hours=2))
    assert len(reads) == (1 if revision_age == 30 else 0)
    assert restored_at < change
    for key in ['parent', 'base', 'delta', 'account']:
        np.testing.assert_allclose(actual[key], full[key], rtol=1e-12, atol=1e-10)
    for a, b in zip(actual['virtual_accounts']+[actual['account']], full['virtual_accounts']+[full['account']]):
        np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-10)
        for x, y in zip(a.attrs['simulation_state'], b.attrs['simulation_state']):
            for u, v in zip(x, y): np.testing.assert_allclose(u, v, rtol=1e-12, atol=1e-10)
    assert len(actual['account'].attrs['checkpoints']) == 48
    wrong = dict(corrected, columns=['spot:S','swap:RENAMED'])
    with pytest.raises(ValueError, match='layout/units'):
        model_strategy.model_replay(wrong,hybrid,raw,original,change,hours=48)


def test_unchanged_quarter_cache_skips_fit_but_revised_training_refits(monkeypatch):
    from sklearn.linear_model import LogisticRegression
    days = pd.date_range('2025-01-01', '2026-04-03', freq='D')
    rng = np.random.default_rng(11)
    obs = pd.DataFrame(rng.normal(size=(len(days),len(MODEL_FEATURES))), columns=MODEL_FEATURES)
    obs['time'] = days; obs['symbol'] = 'U'; obs['forward24'] = np.where(np.arange(len(days))%2, -.1, .1)
    pred, models, windows = quarterly_predictions(obs, days[-1])
    previous = dict(predictions=pred, models=models, train_windows=windows)
    fit = LogisticRegression.fit; calls = []
    def counted(self, *a, **kw):
        calls.append(1);return fit(self,*a,**kw)
    monkeypatch.setattr(LogisticRegression, 'fit', counted)
    got, reused, _ = quarterly_predictions(obs, days[-1], previous=previous)
    pd.testing.assert_frame_equal(got, pred)
    assert not calls and all(reused[q] is models[q] for q in models)
    revised = obs.copy(); revised.loc[days == pd.Timestamp('2026-02-01'), 'ret24'] += 1
    actual, _, _ = quarterly_predictions(revised, days[-1], previous=previous)
    assert len(calls) == 1
    expected, _, _ = quarterly_predictions(revised, days[-1])
    pd.testing.assert_frame_equal(actual, expected)
