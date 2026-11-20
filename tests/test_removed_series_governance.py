"""TDD tests for P0 consumer-side removed_series governance & per-market circuit breaker."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tools.robust40 import handoff, telemetry
from tools.robust40.replay import RebuildRequired


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError('Network forbidden in removed_series governance tests')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def _capture_telemetry(monkeypatch):
    messages = []
    monkeypatch.setattr(telemetry, '_write', messages.append)
    return messages


def _last_event(messages):
    return json.loads(messages[-1].split('MODEL_REPLAY ', 1)[1])


def _make_model_bundle(columns, main_spot_lots, main_swap_lots, virt_swap_target_lots=None):
    """Build minimal model/state.pkl bundle matching ModelRobust40Strategy layout."""
    def _acc(sp_lots, sw_lots, sp_tlots=None, sw_tlots=None):
        df = pd.DataFrame({'equity': [1000.0]})
        sp_l = np.asarray(sp_lots, dtype=np.int64)
        sw_l = np.asarray(sw_lots, dtype=np.int64)
        sp_t = np.zeros_like(sp_l) if sp_tlots is None else np.asarray(sp_tlots, dtype=np.int64)
        sw_t = np.zeros_like(sw_l) if sw_tlots is None else np.asarray(sw_tlots, dtype=np.int64)
        df.attrs['simulation_state'] = (
            (1000.0, sp_l, sp_t, np.ones(len(sp_l)), True),
            (0.0, sw_l, sw_t, np.ones(len(sw_l)), True),
        )
        return df

    main_acc = _acc(main_spot_lots, main_swap_lots)
    v_sw_t = [0] * len(main_swap_lots) if virt_swap_target_lots is None else virt_swap_target_lots
    virt_acc = _acc([0] * len(main_spot_lots), [0] * len(main_swap_lots), sw_tlots=v_sw_t)
    return {
        'context': {'columns': list(columns)},
        'result': {'account': main_acc, 'virtual_accounts': [virt_acc]},
    }


def test_r1_sndk_unheld_removed_series_stays_incremental_with_telemetry(tmp_path, monkeypatch):
    """T-R1: Reproduce 2026-11-20 SNDKBUSDT/SNDKUSDT disappearance -> incremental, zero rebuild."""
    messages = _capture_telemetry(monkeypatch)
    old_idx = pd.date_range('2026-11-13 00:25', periods=168, freq='h')
    new_idx = pd.date_range('2026-11-13 00:25', periods=169, freq='h')

    snapshot = tmp_path / 'snapshot' / '25m-eart1106'
    (snapshot / 'model').mkdir(parents=True)
    pd.to_pickle({'complete': True}, snapshot / 'warmup.pkl')
    # SNDK never entered context['columns']
    bundle = _make_model_bundle(['spot:BTCUSDT', 'swap:BTCUSDT'], [10], [-5])
    pd.to_pickle(bundle, snapshot / 'model' / 'state.pkl')

    old = {
        'raw/spot/BTCUSDT': pd.DataFrame({'close': 100.0}, index=old_idx),
        'raw/spot/SNDKBUSDT': pd.DataFrame({'close': 50.0}, index=old_idx),
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=old_idx),
        'raw/swap/SNDKUSDT': pd.DataFrame({'close': 50.0}, index=old_idx),
        'pivot/spot/close': pd.DataFrame({'BTCUSDT': 100.0, 'SNDKBUSDT': 50.0}, index=old_idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0, 'SNDKUSDT': 50.0}, index=old_idx),
    }
    pd.to_pickle(old, snapshot / 'model' / 'revision_inputs.pkl')

    current = {
        'raw/spot/BTCUSDT': pd.DataFrame({'close': 100.0}, index=new_idx),
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=new_idx),
        # alpha2 incremental pivot retains historical SNDK columns
        'pivot/spot/close': pd.DataFrame({'BTCUSDT': 100.0, 'SNDKBUSDT': 50.0}, index=new_idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0, 'SNDKUSDT': 50.0}, index=new_idx),
    }
    monkeypatch.setattr(handoff, 'read_inputs', lambda *a, **kw: current)

    with telemetry.round_log('25m-eart1106', 1795137900):
        stage, trimmed = handoff.prepare(snapshot, {}, None, 25, new_idx[-1])

    assert stage is None
    assert 'raw/spot/SNDKBUSDT' not in trimmed
    assert 'raw/swap/SNDKUSDT' not in trimmed
    end = _last_event(messages)
    assert end['mode'] == 'incremental'
    assert end['removed_series'] == 2
    assert end['removed_series_keys'] == ['raw/spot/SNDKBUSDT', 'raw/swap/SNDKUSDT']
    assert 'removed_held_symbols' not in end
    assert 'circuit_breaker' not in end


def test_r2_removed_series_plus_recent_value_edit_returns_recent_bar_not_168h(monkeypatch):
    """T-R2: Removed raw series + recent close edit at T-2h -> returns T-2h (lightweight replay)."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-13 00:25', periods=168, freq='h')
    edit_bar = idx[-2]

    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'raw/swap/SNDKUSDT': pd.DataFrame({'close': 50.0}, index=idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0, 'SNDKUSDT': 50.0}, index=idx),
    }
    new = {
        'raw/swap/BTCUSDT': old['raw/swap/BTCUSDT'].copy(),
        'pivot/swap/close': old['pivot/swap/close'].copy(),
    }
    new['raw/swap/BTCUSDT'].loc[edit_bar, 'close'] = 101.5

    with telemetry.round_log('25m-eart1106', 1795137900):
        revised = handoff.first_revision(old, new, held_symbols=set())

    assert revised == edit_bar
    end = _last_event(messages)
    assert end['removed_series'] == 1
    assert end['removed_series_keys'] == ['raw/swap/SNDKUSDT']
    assert end['changed_cells'] == 1


def test_r3_batch_delist_announcement_within_per_market_limit_allowed(monkeypatch):
    """T-R3: 20 spot + 20 swap delisted out of 520/764 passes max(30, 8%) limit cleanly."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-19 00:25', periods=4, freq='h')
    frame = pd.DataFrame({'close': 1.0}, index=idx)

    old = {f'raw/spot/S{i}USDT': frame for i in range(520)}
    old.update({f'raw/swap/U{i}USDT': frame for i in range(764)})
    new = {k: v for k, v in old.items()
           if not any(k == f'raw/spot/S{i}USDT' or k == f'raw/swap/U{i}USDT' for i in range(20))}

    with telemetry.round_log('25m-eart1106', 1795137900):
        revised = handoff.first_revision(old, new, held_symbols=set())

    assert revised is None
    end = _last_event(messages)
    assert end['removed_series'] == 40
    assert 'circuit_breaker' not in end


@pytest.mark.parametrize(
    'n_spot,rm_spot,n_swap,rm_swap,should_trip',
    [
        (519, 41, 764, 0, False),   # exactly at max(30, int(0.08*519))=41 -> pass
        (519, 42, 764, 0, True),    # 42 > 41 -> trip!
        (519, 0, 764, 61, False),   # exactly at max(30, int(0.08*764))=61 -> pass
        (519, 0, 764, 62, True),    # 62 > 61 -> trip!
        (100, 30, 100, 30, False),  # floor max(30, 8)=30 -> both at 30 pass
        (100, 0, 100, 31, True),    # 31 > 30 -> trip!
    ],
)
def test_r4_per_market_circuit_breaker_thresholds(monkeypatch, n_spot, rm_spot, n_swap, rm_swap, should_trip):
    """T-R4: Single-market threshold max(30, int(0.08 * old_market_count)) catches feed outage."""
    messages = _capture_telemetry(monkeypatch)
    idx = pd.date_range('2026-11-19 00:25', periods=3, freq='h')
    frame = pd.DataFrame({'close': 1.0}, index=idx)

    old = {f'raw/spot/S{i}USDT': frame for i in range(n_spot)}
    old.update({f'raw/swap/U{i}USDT': frame for i in range(n_swap)})
    drop = {f'raw/spot/S{i}USDT' for i in range(rm_spot)} | {f'raw/swap/U{i}USDT' for i in range(rm_swap)}
    new = {k: v for k, v in old.items() if k not in drop}

    if should_trip:
        with pytest.raises(handoff.UpstreamUniverseDropError) as exc_info:
            with telemetry.round_log('25m-eart1106', 1795137900):
                handoff.first_revision(old, new, held_symbols=lambda: (_ for _ in ()).throw(AssertionError('should not load state')))
        assert not isinstance(exc_info.value, RebuildRequired)
        end = _last_event(messages)
        assert end['status'] == 'error'
        assert end['error_type'] == 'UpstreamUniverseDropError'
        assert end['circuit_breaker'] == 'removed_series_surge'
        assert end['removed_raw_spot'] == rm_spot
        assert end['removed_raw_swap'] == rm_swap
    else:
        with telemetry.round_log('25m-eart1106', 1795137900):
            assert handoff.first_revision(old, new, held_symbols=set()) is None


def test_r5_held_symbol_removal_checks_pivot_retention_and_emits_alert(tmp_path, monkeypatch):
    """T-R5: Held symbol removed from raw/ stays incremental if pivot retains history (with alert),
    and triggers conservative rebuild if pivot column is also lost."""
    idx = pd.date_range('2026-11-13 00:25', periods=8, freq='h')
    bundle = _make_model_bundle(
        ['spot:BTCUSDT', 'swap:BTCUSDT', 'swap:HELDUSDT'],
        main_spot_lots=[0],
        main_swap_lots=[0, 0],
        virt_swap_target_lots=[0, -42],  # non-zero target_lots in virtual account!
    )
    assert ('swap', 'HELDUSDT') in handoff._extract_held_symbols(bundle)
    assert ('swap', 'BTCUSDT') not in handoff._extract_held_symbols(bundle)

    # Case A: pivot retains HELDUSDT -> incremental + removed_held_symbols telemetry alert
    messages = _capture_telemetry(monkeypatch)
    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'raw/swap/HELDUSDT': pd.DataFrame({'close': 5.0}, index=idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0, 'HELDUSDT': 5.0}, index=idx),
    }
    new_pivot_intact = {
        'raw/swap/BTCUSDT': old['raw/swap/BTCUSDT'].copy(),
        'pivot/swap/close': old['pivot/swap/close'].copy(),
    }
    with telemetry.round_log('25m-eart1106', 1795137900):
        rev = handoff.first_revision(old, new_pivot_intact, held_symbols=handoff._extract_held_symbols(bundle))
    assert rev is None
    end = _last_event(messages)
    assert end['removed_held_symbols'] == ['swap:HELDUSDT']
    assert end['removed_series'] == 1

    # Case B: pivot ALSO dropped HELDUSDT -> conservative idx[0] rebuild!
    messages.clear()
    new_pivot_dropped = {
        'raw/swap/BTCUSDT': old['raw/swap/BTCUSDT'].copy(),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0}, index=idx),
    }
    with telemetry.round_log('25m-eart1106', 1795137900):
        rev_drop = handoff.first_revision(old, new_pivot_dropped, held_symbols=handoff._extract_held_symbols(bundle))
    assert rev_drop == idx[0]
    end_drop = _last_event(messages)
    assert end_drop['removed_held_symbols'] == ['swap:HELDUSDT']
    assert end_drop['removed_columns'] == 1

    # Case C: held symbol with NO pivot table at all -> conservative idx[0]
    messages.clear()
    old_no_piv = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'raw/swap/HELDUSDT': pd.DataFrame({'close': 5.0}, index=idx),
    }
    new_no_piv = {'raw/swap/BTCUSDT': old_no_piv['raw/swap/BTCUSDT'].copy()}
    with telemetry.round_log('25m-eart1106', 1795137900):
        rev_no_piv = handoff.first_revision(old_no_piv, new_no_piv, held_symbols={('swap', 'HELDUSDT')})
    assert rev_no_piv == idx[0]


def test_r6_missing_pivot_series_remains_conservative():
    """T-R6: Losing an entire pivot/* table is never relaxed and returns window start."""
    idx = pd.date_range('2026-11-13 00:25', periods=8, freq='h')
    old = {
        'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx),
        'pivot/swap/close': pd.DataFrame({'BTCUSDT': 100.0}, index=idx),
    }
    new = {'raw/swap/BTCUSDT': old['raw/swap/BTCUSDT'].copy()}
    assert handoff.first_revision(old, new, held_symbols=set()) == idx[0]


def test_r7_held_loader_not_called_when_no_removed_raw():
    """T-R7: Zero overhead on normal rounds — held_symbols loader is strictly lazy."""
    idx = pd.date_range('2026-11-19 00:25', periods=4, freq='h')
    old = {'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx)}
    new = {'raw/swap/BTCUSDT': pd.DataFrame({'close': 100.0}, index=idx)}

    def _forbidden():
        raise AssertionError('held_symbols loader must not be called when no raw series removed')

    assert handoff.first_revision(old, new, held_symbols=_forbidden) is None
