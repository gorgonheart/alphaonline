"""Tests for Issue A (A1 universe-expansion revision detection + A' checkpoint remapping)."""
import json

import numpy as np
import pandas as pd
import pytest

from tools.robust40 import handoff, telemetry


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError('Network forbidden in universe expansion tests')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def _capture_telemetry(monkeypatch):
    messages = []
    monkeypatch.setattr(telemetry, '_write', messages.append)
    return messages


def _last_event(messages):
    return json.loads(messages[-1].split('MODEL_REPLAY ', 1)[1])


# --- Task A1: handoff.first_revision distinguishes universe expansion from value revision ---


def test_added_zero_filled_funding_column_is_not_a_revision(monkeypatch):
    """T-A1: Reproduce 11-17 / 11-19 incident (new zero-filled funding_rate column)."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-10 09:35', periods=168, freq='h')
    old = {
        'pivot/swap/funding_rate': pd.DataFrame(
            {'BTCUSDT': 0.0001, 'ETHUSDT': -0.0002}, index=idx
        )
    }
    new = {
        'pivot/swap/funding_rate': old['pivot/swap/funding_rate'].assign(
            ANTUSDT=0.0, SOXXUSDT=0.0
        )
    }

    with telemetry.round_log('35m_t1104', 1794994500):
        revised = handoff.first_revision(old, new)

    assert revised is None
    end = _last_event(messages)
    assert end['added_columns'] == 2
    assert end['added_column_names'] == ['ANTUSDT', 'SOXXUSDT']
    assert 'changed_fields' not in end
    assert 'changed_cells' not in end


def test_added_series_and_column_with_real_value_revision_returns_real_bar(monkeypatch):
    """T-A2: New raw series (09:35) + new pivot col + real volume revision (08:35) -> 08:35."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-10 18:35', periods=168, freq='h')
    rev_bar = pd.Timestamp('2026-11-17 08:35')
    list_bar = pd.Timestamp('2026-11-17 09:35')

    old = {
        'raw/swap/AXSUSDT': pd.DataFrame({'volume': 10.0, 'close': 5.0}, index=idx),
        'pivot/swap/funding_rate': pd.DataFrame({'AXSUSDT': 0.0001}, index=idx),
    }
    new = {k: v.copy() for k, v in old.items()}
    new['raw/swap/AXSUSDT'].loc[rev_bar, 'volume'] = 12.5
    new['raw/swap/ANTUSDT'] = pd.DataFrame(
        {'volume': 1.0, 'close': 2.0},
        index=pd.date_range(list_bar, idx[-1], freq='h'),
    )
    new['pivot/swap/funding_rate']['ANTUSDT'] = 0.0

    with telemetry.round_log('35m_t1104', 1794994500):
        revised = handoff.first_revision(old, new)

    assert revised == rev_bar
    end = _last_event(messages)
    assert end['added_series'] == 1
    assert end['added_series_keys'] == ['raw/swap/ANTUSDT']
    assert end['added_columns'] == 1
    assert end['added_column_names'] == ['ANTUSDT']
    assert end['changed_series'] == 1
    assert end['changed_cells'] == 1
    assert end['changed_fields'] == ['volume']


def test_pure_expansion_series_and_columns_returns_none_with_telemetry(monkeypatch):
    """T-A3: Pure added series + added pivot columns -> None and telemetry populated."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-12 10:35', periods=168, freq='h')
    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0}, index=idx),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0}, index=idx),
    }
    new = {
        'raw/swap/BTCUSDT': old['raw/swap/BTCUSDT'].copy(),
        'raw/swap/SOXXUSDT': pd.DataFrame({'close': 50.0}, index=idx[-3:]),
        'pivot/swap/close': old['pivot/swap/close'].assign(SOXXUSDT=np.nan),
        'pivot/swap/funding_rate': old['pivot/swap/funding_rate'].assign(SOXXUSDT=0.0),
    }
    new['pivot/swap/close'].iloc[-3:, new['pivot/swap/close'].columns.get_loc('SOXXUSDT')] = 50.0

    with telemetry.round_log('35m_t1104', 1794994500):
        revised = handoff.first_revision(old, new)

    assert revised is None
    end = _last_event(messages)
    assert end['added_series'] == 1
    assert end['added_columns'] == 2
    assert end['added_column_names'] == ['SOXXUSDT']
    assert 'changed_cells' not in end
    assert 'changed_fields' not in end


def test_existing_column_real_value_change_still_detected():
    """T-A4: Real value edits in shared columns are still detected accurately."""
    idx = pd.date_range('2026-11-12 10:35', periods=10, freq='h')
    old = {
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0, 'ETHUSDT': 50.0}, index=idx),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'ETHUSDT': 0.0}, index=idx),
    }
    new = {
        'pivot/swap/close': old['pivot/swap/close'].assign(NEWUSDT=1.0),
        'pivot/swap/funding_rate': old['pivot/swap/funding_rate'].assign(NEWUSDT=0.0),
    }
    new['pivot/swap/close'].loc[idx[4], 'ETHUSDT'] = 51.0
    assert handoff.first_revision(old, new) == idx[4]


def test_legacy_raw_without_funding_fee_projection_still_works(monkeypatch):
    """T-A5: Legacy raw snapshot missing funding_fee keeps drop-column behavior and detects edits."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-12 10:35', periods=6, freq='h')
    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': [1., 2., 3., 4., 5., 6.]}, index=idx),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0}, index=idx),
    }
    new = {
        'raw/swap/BTCUSDT': pd.DataFrame(
            {'close': [1., 2., 3., 4., 5., 6.], 'funding_fee': [0., 0.001, 0., 0., 0., 0.]},
            index=idx,
        ),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'SOXXUSDT': 0.0}, index=idx),
    }

    with telemetry.round_log('35m_t1104', 1794994500):
        assert handoff.first_revision(old, new) is None

    end = _last_event(messages)
    assert end['added_columns'] == 1
    assert end['added_column_names'] == ['SOXXUSDT']

    new['raw/swap/BTCUSDT'].loc[idx[3], 'close'] = 4.5
    assert handoff.first_revision(old, new) == idx[3]


def test_removed_column_and_removed_series_remain_conservative_revisions(monkeypatch):
    """Removed columns and removed series must still trigger conservative window-start revision."""
    idx = pd.date_range('2026-11-12 10:35', periods=8, freq='h')

    # Case 1: Only a column removed from a shared pivot table
    messages = _capture_telemetry(monkeypatch)
    old = {
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'OLDUSDT': 0.0}, index=idx)
    }
    new = {
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0}, index=idx)
    }
    with telemetry.round_log('35m_t1104', 1794994500):
        revised = handoff.first_revision(old, new)
    assert revised == idx[0]
    end = _last_event(messages)
    assert end['removed_columns'] == 1
    assert end['removed_column_names'] == ['OLDUSDT']

    # Case 2: Single raw series removed within circuit-breaker limit -> P0 graded governance
    # keeps incremental (no look-ahead rebuild) while still recording removed_series telemetry;
    # losing a pivot/* table remains conservative window-start revision.
    messages.clear()
    old_series = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'raw/swap/OLDUSDT': pd.DataFrame({'close': 1.0}, index=idx),
    }
    new_series = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
    }
    with telemetry.round_log('35m_t1104', 1794994500):
        revised_series = handoff.first_revision(old_series, new_series)
    assert revised_series is None
    end_series = _last_event(messages)
    assert end_series['removed_series'] == 1
    assert end_series['removed_series_keys'] == ['raw/swap/OLDUSDT']

    # Case 3: Both column added and column removed -> still conservative window start
    messages.clear()
    new_both = {
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'NEWUSDT': 0.0}, index=idx)
    }
    with telemetry.round_log('35m_t1104', 1794994500):
        revised_both = handoff.first_revision(old, new_both)
    assert revised_both == idx[0]
    end_both = _last_event(messages)
    assert end_both['added_columns'] == 1
    assert end_both['removed_columns'] == 1


def test_index_alignment_with_new_bar_and_added_column_returns_none():
    """New round appends a closed bar AND adds a new column; index aligns before diff."""
    old_idx = pd.date_range('2026-11-12 10:35', periods=6, freq='h')
    new_idx = pd.date_range('2026-11-12 10:35', periods=7, freq='h')
    old = {'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0}, index=old_idx)}
    new = {
        'pivot/swap/funding_rate': pd.DataFrame(
            {'BTCUSDT': 0.0, 'SOXXUSDT': 0.0}, index=new_idx
        )
    }
    assert handoff.first_revision(old, new) is None


def test_prepare_on_expansion_round_stays_incremental_and_refreshes_baseline(tmp_path, monkeypatch):
    """T-A8: Full handoff.prepare stays incremental on expansion and refreshes baseline."""
    messages = _capture_telemetry(monkeypatch)
    old_idx = pd.date_range('2026-11-12 10:35', periods=4, freq='h')
    new_idx = pd.date_range('2026-11-12 10:35', periods=5, freq='h')
    snapshot = tmp_path / 'snapshot' / '35m_t1104'
    (snapshot / 'model').mkdir(parents=True)

    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=old_idx),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0}, index=old_idx),
    }
    pd.to_pickle(old, snapshot / 'model' / 'revision_inputs.pkl')
    pd.to_pickle({'complete': True}, snapshot / 'warmup.pkl')

    current = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=new_idx),
        'raw/swap/SOXXUSDT': pd.DataFrame({'close': 50.0}, index=new_idx[-2:]),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'SOXXUSDT': 0.0}, index=new_idx),
    }
    monkeypatch.setattr(handoff, 'read_inputs', lambda *a, **kw: current)

    with telemetry.round_log('35m_t1104', 1794994500):
        stage, trimmed = handoff.prepare(snapshot, {}, None, 35, new_idx[-1])

    assert stage is None
    assert not (tmp_path / 'model_replay').exists()
    assert 'raw/swap/SOXXUSDT' in trimmed
    assert 'SOXXUSDT' in trimmed['pivot/swap/funding_rate'].columns
    end = _last_event(messages)
    assert end['mode'] == 'incremental'
    assert end['added_columns'] == 1
    assert end['added_series'] == 1

    # Next round using refreshed baseline reports no expansion and stays incremental
    messages.clear()
    pd.to_pickle(trimmed, snapshot / 'model' / 'revision_inputs.pkl')
    next_idx = pd.date_range('2026-11-12 10:35', periods=6, freq='h')
    next_current = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=next_idx),
        'raw/swap/SOXXUSDT': pd.DataFrame({'close': 50.0}, index=next_idx[-3:]),
        'pivot/swap/funding_rate': pd.DataFrame({'BTCUSDT': 0.0, 'SOXXUSDT': 0.0}, index=next_idx),
    }
    monkeypatch.setattr(handoff, 'read_inputs', lambda *a, **kw: next_current)

    with telemetry.round_log('35m_t1104', 1794998100):
        stage2, _ = handoff.prepare(snapshot, {}, None, 35, next_idx[-1])

    assert stage2 is None
    end2 = _last_event(messages)
    assert end2['mode'] == 'incremental'
    assert 'added_columns' not in end2
    assert 'added_series' not in end2


# --- Task A': ModelRobust40Strategy.expand_result remaps checkpoints on universe expansion ---


def _expanded_context(start, periods, with_new=False, new_nan_before=None):
    from scipy import sparse

    index = pd.date_range(start, periods=periods, freq='h')
    if with_new:
        cols = ['spot:B', 'spot:S', 'swap:A', 'swap:U']
        row_weights = [0.0, 0.1, 0.0, -0.1]
        spot_lots = np.array([0.5, 1.0], dtype=float)
        swap_lots = np.array([2.0, 1.0], dtype=float)
    else:
        cols = ['spot:S', 'swap:U']
        row_weights = [0.1, -0.1]
        spot_lots = np.array([1.0], dtype=float)
        swap_lots = np.array([1.0], dtype=float)
    values = np.tile(row_weights, (periods, 1))
    equities = [
        pd.DataFrame(
            dict(
                candle_begin_time=index,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                Bias_48=1.0,
            )
        )
        for _ in range(14)
    ]
    sim = dict(
        init_capital=10000.0,
        leverages=np.ones(periods),
        require_rebalance=np.ones(periods, dtype=np.int8),
        capital_flow_arr=np.zeros(periods),
        spot_c_rate=0.001,
        swap_c_rate=0.0006,
        spot_min_order_limit=10.0,
        swap_min_order_limit=5.0,
        min_margin_rate=0.05,
        capital_flow_mode=-1,
        spot_lot_sizes=spot_lots,
        swap_lot_sizes=swap_lots,
    )
    for kind, n in (('spot', len(spot_lots)), ('swap', len(swap_lots))):
        for field in ('open', 'close', 'vwap1m'):
            arr = np.full((periods, n), 100.0, dtype=float)
            if with_new and new_nan_before is not None:
                # Newly listed coin (col 0 for both spot:B and swap:A) has NaN before listing
                arr[:new_nan_before, 0] = np.nan
            sim[f'{kind}_{field}_p'] = arr
    sim['funding_rates'] = np.zeros((periods, len(swap_lots)), dtype=float)
    close = pd.DataFrame(100.0, index=index, columns=cols)
    if with_new and new_nan_before is not None:
        close.iloc[:new_nan_before, close.columns.get_loc('spot:B')] = np.nan
        close.iloc[:new_nan_before, close.columns.get_loc('swap:A')] = np.nan
    close['swap:BTCUSDT'] = 100.0
    return dict(
        index=index,
        minute=index.minute[0],
        columns=cols,
        leverage=pd.DataFrame(1.0, index=index, columns=range(14)),
        matrices=[sparse.csr_matrix(values) for _ in range(14)],
        equities=equities,
        simulation=sim,
        close=close,
    )


def test_expand_result_remaps_checkpoints_and_allows_pre_expansion_restore_and_replay(tmp_path):
    """T-A6: Expansion keeps 26 checkpoints remapped by symbol; restore_model & replay work before expansion."""
    from positions import ModelRobust40Strategy as model

    old_ctx = _expanded_context('2026-08-01 00:40', 1200, with_new=False)
    idx_old = old_ctx['index']
    parent_params = dict(
        cap_ratios=[0.1] * 14,
        direction_list=['long'] * 7 + ['short'] * 7,
    )
    weights_old = model.calc_hybrid_ratio(
        old_ctx['equities'], type('P', (), {'params': parent_params})()
    )
    weights_old.attrs = {}
    raw_old = {
        'U': pd.DataFrame(
            dict(
                candle_begin_time=idx_old,
                first_candle_time=idx_old[0],
                close=100.0,
                quote_volume=1000.0,
                avg_price_1m=100.0,
            )
        )
    }
    old_res = model.calculate(old_ctx, weights_old, raw_old, checkpoint_hours=26)
    assert len(old_res['account'].attrs['checkpoints']) == 26
    old_snapshot_cp = {
        t: (
            (s0[0], s0[1].copy(), s0[2].copy(), s0[3].copy(), s0[4]),
            (s1[0], s1[1].copy(), s1[2].copy(), s1[3].copy(), s1[4]),
        )
        for t, (s0, s1) in old_res['account'].attrs['checkpoints'].items()
    }

    # New round adds 1 bar + expands universe with spot:B and swap:A (NaN prices before listing bar)
    new_ctx = _expanded_context('2026-08-01 00:40', 1201, with_new=True, new_nan_before=1200)
    idx_new = new_ctx['index']
    merged = model.merge_context(old_ctx, new_ctx, minimum_overlap=168, warmup=0)

    expanded = model.expand_result(
        old_res, old_ctx['columns'], merged['columns'], merged['simulation']
    )
    expected_layout = (
        ('spot', ('B', 'S'), (0.5, 1.0)),
        ('swap', ('A', 'U'), (2.0, 1.0)),
    )

    for acct_old, acct_exp in zip(
        old_res['virtual_accounts'] + [old_res['account']],
        expanded['virtual_accounts'] + [expanded['account']],
    ):
        assert acct_exp.attrs['checkpoint_layout'] == expected_layout
        assert set(acct_exp.attrs['checkpoints']) == set(acct_old.attrs['checkpoints'])
        for t, (spot_s, swap_s) in acct_exp.attrs['checkpoints'].items():
            orig_spot, orig_swap = acct_old.attrs['checkpoints'][t]
            assert spot_s[0] == orig_spot[0] and spot_s[4] == orig_spot[4]
            assert swap_s[0] == orig_swap[0] and swap_s[4] == orig_swap[4]
            assert spot_s[1].dtype == np.int64 and spot_s[2].dtype == np.int64 and spot_s[3].dtype == np.float64
            assert swap_s[1].dtype == np.int64 and swap_s[2].dtype == np.int64 and swap_s[3].dtype == np.float64
            np.testing.assert_array_equal(spot_s[1], [0, orig_spot[1][0]])
            np.testing.assert_array_equal(spot_s[2], [0, orig_spot[2][0]])
            np.testing.assert_array_equal(spot_s[3], [0.0, orig_spot[3][0]])
            np.testing.assert_array_equal(swap_s[1], [0, orig_swap[1][0]])
            np.testing.assert_array_equal(swap_s[2], [0, orig_swap[2][0]])
            np.testing.assert_array_equal(swap_s[3], [0.0, orig_swap[3][0]])

    # Verify original checkpoints were not mutated
    for t, (orig_spot, orig_swap) in old_snapshot_cp.items():
        cur_spot, cur_swap = old_res['account'].attrs['checkpoints'][t]
        np.testing.assert_array_equal(cur_spot[1], orig_spot[1])
        np.testing.assert_array_equal(cur_swap[1], orig_swap[1])

    # Run full update_state on disk so _update_state -> expand_result -> advance path is exercised
    state_path = tmp_path / 'state.pkl'
    bootstrap_path = tmp_path / 'bootstrap.pkl'
    bundle = dict(
        version=1,
        minute=40,
        account_identity='35m_t1104',
        params=parent_params,
        context=old_ctx,
        result=old_res,
    )
    pd.to_pickle(bundle, state_path)
    raw_new = {
        'U': pd.DataFrame(
            dict(
                candle_begin_time=idx_new,
                first_candle_time=idx_new[0],
                close=100.0,
                quote_volume=1000.0,
                avg_price_1m=100.0,
            )
        ),
        'A': pd.DataFrame(
            dict(
                candle_begin_time=idx_new[-1:],
                first_candle_time=idx_new[-1],
                close=100.0,
                quote_volume=100.0,
                avg_price_1m=100.0,
            )
        ),
    }
    updated = model.update_state(
        state_path,
        bootstrap_path,
        '35m_t1104',
        40,
        new_ctx,
        raw_new,
        idx_new[-1],
        checkpoint_hours=26,
    )
    # Depth stays at 26 (not collapsed to 1!)
    assert len(updated['result']['account'].attrs['checkpoints']) == 26
    assert updated['result']['account'].attrs['checkpoint_layout'] == expected_layout

    # Restore to a checkpoint strictly BEFORE the expansion bar and replay forward
    revised_from = idx_old[-3]
    restored, restored_at = model.restore_model(
        updated['context'], updated['result'], revised_from
    )
    assert restored_at == idx_old[-4]
    assert restored['account'].attrs['checkpoint_layout'] == expected_layout

    weights_new = model.calc_hybrid_ratio(
        updated['context']['equities'], type('P', (), {'params': parent_params})()
    )
    weights_new.attrs = {}
    replayed, replay_at = model.model_replay(
        updated['context'],
        weights_new,
        raw_new,
        updated['result'],
        revised_from,
        hours=26,
    )
    assert replay_at == idx_old[-4]
    assert np.isfinite(replayed['account']['equity']).all()
    pd.testing.assert_frame_equal(
        replayed['account'], updated['result']['account'], check_exact=True
    )


def test_expand_result_drops_checkpoints_when_simulation_omitted_or_layout_mismatched():
    """Legacy 3-arg callers or mismatched checkpoint_layout safely drop checkpoints instead of corrupting."""
    from positions import ModelRobust40Strategy as model

    t = pd.Timestamp('2026-10-01 00:40')
    state = (
        (100.0, np.array([1], dtype=np.int64), np.array([2], dtype=np.int64), np.array([10.0]), True),
        (0.0, np.array([-3], dtype=np.int64), np.array([-4], dtype=np.int64), np.array([20.0]), True),
    )
    frame = pd.DataFrame({'equity': [100.0]})
    frame.attrs['simulation_state'] = state
    frame.attrs['checkpoints'] = {t: state}
    frame.attrs['checkpoint_layout'] = (
        ('spot', ('S',), (1.0,)),
        ('swap', ('Z',), (1.0,)),
    )
    result = dict(
        virtual_accounts=[frame.copy()],
        account=frame.copy(),
        targets={'spot': pd.DataFrame({'S': [0.1]}), 'swap': pd.DataFrame({'Z': [-0.1]})},
        delta=pd.DataFrame({'Z': [0.0]}),
    )

    # Legacy 3-arg call with expanded columns -> drops checkpoints safely
    out_legacy = model.expand_result(result, ['spot:S', 'swap:Z'], ['spot:S', 'swap:A', 'swap:Z'])
    assert 'checkpoints' not in out_legacy['account'].attrs
    assert 'checkpoint_layout' not in out_legacy['account'].attrs

    # Mismatched lot size in simulation vs checkpoint_layout -> drops checkpoints safely
    bad_sim = {'spot_lot_sizes': np.array([1.0]), 'swap_lot_sizes': np.array([2.0, 99.0])}
    out_bad = model.expand_result(
        result, ['spot:S', 'swap:Z'], ['spot:S', 'swap:A', 'swap:Z'], bad_sim
    )
    assert 'checkpoints' not in out_bad['account'].attrs
    assert 'checkpoint_layout' not in out_bad['account'].attrs

    # Same columns -> retains checkpoints untouched even without simulation
    out_same = model.expand_result(result, ['spot:S', 'swap:Z'], ['spot:S', 'swap:Z'])
    assert set(out_same['account'].attrs['checkpoints']) == {t}


def test_new_symbol_with_nan_price_and_zero_weight_keeps_finite_equity_and_unchanged_holdings():
    """T-A7: Newly expanded symbol with NaN price and 0 weight keeps equity finite and old holdings exact."""
    from positions import ModelRobust40Strategy as model

    base_ctx = _expanded_context('2026-10-01 00:40', 20, with_new=False)
    exp_ctx = _expanded_context('2026-10-01 00:40', 20, with_new=True, new_nan_before=20)

    base_targets = {
        'spot': pd.DataFrame({'S': 0.1}, index=base_ctx['index']),
        'swap': pd.DataFrame({'U': -0.1}, index=base_ctx['index']),
    }
    exp_targets = {
        'spot': pd.DataFrame({'B': 0.0, 'S': 0.1}, index=exp_ctx['index']),
        'swap': pd.DataFrame({'A': 0.0, 'U': -0.1}, index=exp_ctx['index']),
    }

    base_sim = model.simulate(base_targets, base_ctx, 0.2)
    exp_sim = model.simulate(exp_targets, exp_ctx, 0.2)

    assert np.isfinite(exp_sim['equity']).all()
    pd.testing.assert_frame_equal(exp_sim, base_sim, check_exact=True)
    # Existing symbol lots/targets/last_prices match bitwise; new symbol stays 0
    b_spot, b_swap = base_sim.attrs['simulation_state']
    e_spot, e_swap = exp_sim.attrs['simulation_state']
    np.testing.assert_array_equal(e_spot[1], [0, b_spot[1][0]])
    np.testing.assert_array_equal(e_swap[1], [0, b_swap[1][0]])


def test_expansion_with_prior_prices_survives_next_round_and_pre_expansion_replay(tmp_path):
    """Critical review fix: new symbol with prior overlap prices survives Round T+1 and pre-expansion replay."""
    from positions import ModelRobust40Strategy as model

    parent_params = dict(
        cap_ratios=[0.1] * 14,
        direction_list=['long'] * 7 + ['short'] * 7,
    )
    old_ctx = _expanded_context('2026-08-01 00:40', 1200, with_new=False)
    idx_old = old_ctx['index']
    weights_old = model.calc_hybrid_ratio(
        old_ctx['equities'], type('P', (), {'params': parent_params})()
    )
    weights_old.attrs = {}
    raw_old = {
        'U': pd.DataFrame(
            dict(
                candle_begin_time=idx_old,
                first_candle_time=idx_old[0],
                close=100.0,
                quote_volume=1000.0,
                avg_price_1m=100.0,
            )
        )
    }
    old_res = model.calculate(old_ctx, weights_old, raw_old, checkpoint_hours=26)

    state_path = tmp_path / 'state.pkl'
    bootstrap_path = tmp_path / 'bootstrap.pkl'
    pd.to_pickle(
        dict(
            version=1,
            minute=40,
            account_identity='35m_t1104',
            params=parent_params,
            context=old_ctx,
            result=old_res,
        ),
        state_path,
    )

    def make_incoming(periods, active_from):
        ctx = _expanded_context(
            '2026-08-01 00:40', periods, with_new=True, new_nan_before=1192
        )
        # Non-zero funding rate on newly added swap:A during prior overlap bars
        ctx['simulation']['funding_rates'][1192:, 0] = 0.0003
        # Non-zero target weight on newly added spot:B and swap:A starting at expansion bar
        from scipy import sparse

        weights_arr = np.tile([0.0, 0.1, 0.0, -0.1], (periods, 1))
        weights_arr[active_from:, 0] = 0.05
        weights_arr[active_from:, 2] = -0.05
        ctx['matrices'] = [sparse.csr_matrix(weights_arr) for _ in range(14)]
        idx = ctx['index']
        raw = {
            'U': pd.DataFrame(
                dict(
                    candle_begin_time=idx,
                    first_candle_time=idx[0],
                    close=100.0,
                    quote_volume=1000.0,
                    avg_price_1m=100.0,
                )
            ),
            'A': pd.DataFrame(
                dict(
                    candle_begin_time=idx[1192:],
                    first_candle_time=idx[1192],
                    close=100.0,
                    quote_volume=100.0,
                    avg_price_1m=100.0,
                )
            ),
        }
        return ctx, raw

    # Round T: universe expands with spot:B and swap:A having 8 prior hours of prices + non-zero weight at T
    ctx_t, raw_t = make_incoming(1201, active_from=1200)
    updated_t = model.update_state(
        state_path,
        bootstrap_path,
        '35m_t1104',
        40,
        ctx_t,
        raw_t,
        ctx_t['index'][-1],
        checkpoint_hours=26,
    )
    assert len(updated_t['result']['account'].attrs['checkpoints']) == 26
    assert np.isfinite(updated_t['result']['account']['equity']).all()

    # Round T+1: next incremental round must NOT crash with "Historical revision in spot_open_p"
    ctx_t1, raw_t1 = make_incoming(1202, active_from=1200)
    updated_t1 = model.update_state(
        state_path,
        bootstrap_path,
        '35m_t1104',
        40,
        ctx_t1,
        raw_t1,
        ctx_t1['index'][-1],
        checkpoint_hours=26,
    )
    assert len(updated_t1['result']['account'].attrs['checkpoints']) == 26
    assert np.isfinite(updated_t1['result']['account']['equity']).all()

    # Pre-expansion restore + merge_context + advance (exact restore_snapshot -> _update_state flow)
    revised_from = idx_old[-3]
    restored_res, restored_at = model.restore_model(
        updated_t1['context'], updated_t1['result'], revised_from
    )
    assert restored_at == idx_old[-4]
    sliced_ctx = model.slice_context(
        updated_t1['context'],
        updated_t1['context']['index'][updated_t1['context']['index'] <= restored_at],
    )
    merged_replay = model.merge_context(sliced_ctx, ctx_t1)
    weights_t1 = model.calc_hybrid_ratio(
        merged_replay['equities'], type('P', (), {'params': parent_params})()
    )
    weights_t1.attrs = {}
    replayed = model.advance(
        merged_replay, weights_t1, raw_t1, restored_res, checkpoint_hours=26
    )
    assert np.isfinite(replayed['account']['equity']).all()
    pd.testing.assert_frame_equal(
        replayed['account'], updated_t1['result']['account'], check_exact=True
    )


def test_expand_result_remaps_checkpoints_when_one_side_starts_empty():
    """Empty spot (or swap) universe expanding to non-empty preserves checkpoints and exact dtypes."""
    from positions import ModelRobust40Strategy as model

    t = pd.Timestamp('2026-10-01 00:40')
    empty_state = (
        (1000.0, np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([], dtype=np.float64), True),
        (500.0, np.array([-3], dtype=np.int64), np.array([-4], dtype=np.int64), np.array([20.0], dtype=np.float64), True),
    )
    frame = pd.DataFrame({'equity': [1500.0]})
    frame.attrs['simulation_state'] = empty_state
    frame.attrs['checkpoints'] = {t: empty_state}
    frame.attrs['checkpoint_layout'] = (
        ('spot', (), ()),
        ('swap', ('U',), (1.0,)),
    )
    result = dict(
        virtual_accounts=[frame.copy()],
        account=frame.copy(),
        targets={'spot': pd.DataFrame(index=[t]), 'swap': pd.DataFrame({'U': [-0.1]}, index=[t])},
        delta=pd.DataFrame({'U': [0.0]}, index=[t]),
    )
    sim = {
        'spot_lot_sizes': np.array([0.5], dtype=float),
        'swap_lot_sizes': np.array([2.0, 1.0], dtype=float),
    }
    out = model.expand_result(result, ['swap:U'], ['spot:B', 'swap:A', 'swap:U'], sim)
    assert out['account'].attrs['checkpoint_layout'] == (
        ('spot', ('B',), (0.5,)),
        ('swap', ('A', 'U'), (2.0, 1.0)),
    )
    spot_s, swap_s = out['account'].attrs['checkpoints'][t]
    assert spot_s[1].dtype == np.int64 and spot_s[2].dtype == np.int64 and spot_s[3].dtype == np.float64
    np.testing.assert_array_equal(spot_s[1], [0])
    np.testing.assert_array_equal(swap_s[1], [0, -3])
    np.testing.assert_array_equal(swap_s[3], [0.0, 20.0])
