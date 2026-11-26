"""Tests for consumer batch 2 optimizations (Phase B: Stages 5, 6, 7)."""
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_synth_arrow(path: Path, symbols: list[str], times: pd.DatetimeIndex, extra_cols: dict | None = None,
                       trim_date: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    batches = []
    schema = None
    for sym in symbols:
        data = {
            'candle_begin_time': times.tz_localize(None),
            'first_candle_time': np.full(len(times), times[0].tz_localize(None)),
            'open': np.linspace(100.0, 105.0, len(times)),
            'high': np.linspace(101.0, 106.0, len(times)),
            'low': np.linspace(99.0, 104.0, len(times)),
            'close': np.linspace(100.5, 105.5, len(times)),
            'volume': np.full(len(times), 1000.0),
            'quote_volume': np.full(len(times), 100000.0),
            'trade_num': np.full(len(times), 100, dtype=np.int64),
            'taker_buy_base_asset_volume': np.full(len(times), 500.0),
            'taker_buy_quote_asset_volume': np.full(len(times), 50000.0),
            'avg_price_1m': np.linspace(100.2, 105.2, len(times)),
            'funding_rate': np.full(len(times), 0.0001),
            '是否交易': np.full(len(times), 1, dtype=np.int8),
            'symbol_spot': np.full(len(times), sym if 'spot' in path.name else sym),
            'symbol_swap': np.full(len(times), sym),
            'is_spot': np.full(len(times), 1 if 'spot' in path.name else 0, dtype=np.int8),
            'funding_fee': np.full(len(times), 0.0),
        }
        if extra_cols:
            for k, v in extra_cols.items():
                data[k] = v
        df = pd.DataFrame(data)
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if schema is None:
            meta = {b'symbols': json.dumps(symbols).encode('utf-8')}
            if trim_date is not None:
                meta[b'history_trim_date'] = trim_date.encode('utf-8')
            schema = tbl.schema.with_metadata(meta)
        batches.extend(tbl.to_batches())
    with pa.OSFile(str(path), 'wb') as sink:
        with ipc.new_file(sink, schema) as writer:
            for b in batches:
                writer.write_batch(b)


def _write_synth_pivots(folder: Path, kind: str, symbols: list[str], times: pd.DatetimeIndex):
    idx = times.tz_localize(None)
    pivot = {
        field: pd.DataFrame({s: np.linspace(100.0, 101.0, len(idx)) for s in symbols}, index=idx)
        for field in ('open', 'close', 'vwap1m', 'funding_rate')
    }
    pd.to_pickle(pivot, folder / f'market_pivot_{kind}_2026.pkl')


def test_stage5_streaming_spot_first_finishes_before_swap_ready_and_matches_oracle(tmp_path, monkeypatch):
    from tools.robust40 import handoff
    import config

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=10), end, freq='1h')
    start = times[0]
    syms = ['BTCUSDT', 'ETHUSDT']

    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', syms, times, extra_cols={'Bias_5': np.ones(len(times))})
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', syms, times, extra_cols={'Bias_5': np.ones(len(times))})
    _write_synth_pivots(folder, 'spot', syms, times)
    _write_synth_pivots(folder, 'swap', syms, times)

    # First write only spot .ready files
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    spot_done = threading.Event()
    orig_read_arrow_tail = handoff.read_arrow_tail

    def _spy_read_arrow_tail(root, kind, *args, **kwargs):
        res = orig_read_arrow_tail(root, kind, *args, **kwargs)
        if kind == 'spot':
            spot_done.set()
        return res

    monkeypatch.setattr(handoff, 'read_arrow_tail', _spy_read_arrow_tail)

    def _release_swap():
        assert spot_done.wait(timeout=2.0), 'spot read must finish before swap.ready arrives'
        time.sleep(0.05)
        (folder / f'swap_dict_{stamp}.ready').write_text('')
        (folder / f'market_pivot_swap_{stamp}.ready').write_text('')

    t = threading.Thread(target=_release_swap)
    t.start()
    status, streamed, trims, stats = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, start, {'Bias_5'}, deadline=time.time() + 5.0, poll_interval=0.02
    )
    t.join()

    assert status == 'ok'
    assert stats['spot_early_lead_s'] > 0
    assert trims == (None, None)

    monkeypatch.setattr(config, 'bmac_data_path', bmac)
    oracle = handoff.read_inputs(run_time, 25, end, start=start)
    assert set(streamed.keys()) == set(oracle.keys())
    for k in oracle:
        pd.testing.assert_frame_equal(streamed[k], oracle[k], check_exact=True, obj=k)


def test_stage5_wait_ready_releases_account_lock_and_excludes_wait_from_total_s(tmp_path, monkeypatch):
    from tools.robust40 import handoff, server, telemetry

    dest = tmp_path / 'data' / 'snapshot' / 'acct_s'
    dest.mkdir(parents=True)

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')
    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    lock_acquired_during_wait = {}

    def _delayed_swap_with_lock_probe():
        time.sleep(0.15)
        with server.account_lock(dest):
            lock_acquired_during_wait['ok'] = True
        (folder / f'swap_dict_{stamp}.ready').write_text('')
        (folder / f'market_pivot_swap_{stamp}.ready').write_text('')

    t = threading.Thread(target=_delayed_swap_with_lock_probe)
    t.start()
    status, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], set(), deadline=time.time() + 5.0, poll_interval=0.02
    )
    t.join()
    assert status == 'ok'
    assert lock_acquired_during_wait.get('ok') is True

    emitted = []
    monkeypatch.setattr(telemetry, '_write', emitted.append)
    with telemetry.round_log('acct', stamp):
        time.sleep(0.25)
        telemetry.reset_timer(wait_ready_s=0.35, prewarm_s=0.1)
    end_payload = json.loads(emitted[-1].split('MODEL_REPLAY ', 1)[1])
    assert end_payload['wait_ready_s'] == pytest.approx(0.35)
    assert end_payload['total_s'] < 0.15


def test_stage5_timeout_returns_76_and_missing_columns_returns_75(tmp_path, monkeypatch):
    from tools.robust40 import handoff, server
    from tools.robust40.cli import server_robust40
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')

    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')

    status, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], set(), deadline=time.time() + 0.12, poll_interval=0.02
    )
    assert status == 'timeout'

    # Now write swap ready, but require a missing column -> missing_arrow
    (folder / f'swap_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_swap_{stamp}.ready').write_text('')
    status2, _, _, _ = handoff.wait_and_read_inputs_streaming(
        bmac, run_time, 25, stamp, end, times[0], {'MISSING_COL_1'}, deadline=time.time() + 1.0, poll_interval=0.02
    )
    assert status2 == 'missing_arrow'

    # Coordinator.handle_result on exit 76 returns False without raising
    c = server.Coordinator(ROOT)
    cmd = [sys.executable, str(ROOT / 'tools/robust40/cli/server_robust40.py'), 'trade', '--account', 'a.py', '--stamp', str(stamp)]
    assert c.handle_result(tmp_path / 'a.py', run_time, bmac, cmd, {}, WAIT_FOR_DATA_EXIT) is False
    assert hasattr(c, 'launch_prewarmed_trade')
    assert REBUILD_REQUIRED_EXIT == 75
    assert WAIT_FOR_DATA_EXIT == 76


def test_stage5_wait_ready_end_to_end_timeout_sig_change_and_trim_mismatch(tmp_path, monkeypatch):
    import config
    from tools.robust40 import handoff, server
    from tools.robust40.cli import server_robust40
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT

    acct = tmp_path / 'acct_m.py'
    acct.write_text("account_config={'hour_offset':'25m'}\nstrategy_name='s'\n", encoding='utf-8')
    values = {'account': {'hour_offset': '25m'}, 'strategy_name': 's', 'strategy_pool': [], 'precompute_factor_configs': {}}
    dest = server.location(tmp_path, acct, values)
    (dest / 'model').mkdir(parents=True)
    run_time = datetime(2026, 1, 1, 12, 25)
    stamp = int(run_time.timestamp())
    end = pd.Timestamp(stamp, unit='s') - pd.Timedelta(hours=1)
    times = pd.date_range(end - pd.Timedelta(hours=4), end, freq='1h')

    warmup_meta = {'account': 'acct_m', 'settings': values, 'cutoff': end, 'origin': '2026-01-01 00:25:00',
                   'checkpoint_version': 1, 'complete': True, 'sections': [], 'history_trim_date': None}
    pd.to_pickle(warmup_meta, dest / 'warmup.pkl')
    pd.to_pickle({'raw/spot/BTCUSDT': pd.DataFrame({'close': [1.0]}, index=[times[0]])}, dest / 'model/revision_inputs.pkl')

    bmac = tmp_path / 'preprocess_1h_resample'
    folder = bmac / '25m'
    _write_synth_arrow(folder / f'spot_dict_{stamp}.arrow', ['BTCUSDT'], times, trim_date='2025-01-01')
    _write_synth_arrow(folder / f'swap_dict_{stamp}.arrow', ['BTCUSDT'], times, trim_date='2025-01-02')
    _write_synth_pivots(folder, 'spot', ['BTCUSDT'], times)
    _write_synth_pivots(folder, 'swap', ['BTCUSDT'], times)

    monkeypatch.setattr(server_robust40, 'ROOT', tmp_path)
    monkeypatch.setattr('tools.robust40.initialization_config.DATA_READY_WAIT_SECONDS', 0.1)
    monkeypatch.setattr(config, 'bmac_data_path', bmac)
    monkeypatch.setattr(server, 'settings', lambda a: (values, {'minute': 25}))

    # 1. Timeout -> exit 76 and warmup.pkl stays complete=True
    rc = server_robust40.trade(acct, stamp, wait_ready=True)
    assert rc == WAIT_FOR_DATA_EXIT
    assert pd.read_pickle(dest / 'warmup.pkl')['complete'] is True

    # 2. Ready with mismatched trim dates -> ValueError
    (folder / f'spot_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_spot_{stamp}.ready').write_text('')
    (folder / f'swap_dict_{stamp}.ready').write_text('')
    (folder / f'market_pivot_swap_{stamp}.ready').write_text('')
    monkeypatch.setattr('tools.robust40.initialization_config.DATA_READY_WAIT_SECONDS', 2.0)
    with pytest.raises(ValueError, match='history_trim_date mismatch'):
        server_robust40.trade(acct, stamp, wait_ready=True)

    # 3. Warmup signature changes during wait -> discards stale preloaded inputs and falls back to _trade
    called = {}
    orig_wait = handoff.wait_and_read_inputs_streaming

    def _mutate_warmup_during_wait(*args, **kwargs):
        res = orig_wait(*args, **kwargs)
        pd.to_pickle(dict(warmup_meta, marker='updated_by_installer'), dest / 'warmup.pkl')
        return res

    monkeypatch.setattr(handoff, 'wait_and_read_inputs_streaming', _mutate_warmup_during_wait)
    monkeypatch.setattr(server_robust40, '_trade', lambda *a, **kw: (called.setdefault('kw', kw), REBUILD_REQUIRED_EXIT)[1])
    rc2 = server_robust40.trade(acct, stamp, wait_ready=True)
    assert rc2 == REBUILD_REQUIRED_EXIT
    assert 'preloaded_current' not in called['kw']

