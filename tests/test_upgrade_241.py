"""Tests for v2.4.1 incremental upgrade (Phase A Tasks 1-3)."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_version_is_241():
    import core.version as version
    assert version.sys_version == '2.4.1'
    assert version.build_version == 'v2.4.1.20261120'


def test_datatools_resolve_exchange_paths_and_fast_check_flags(tmp_path, monkeypatch):
    from core.utils import datatools

    bn_root = tmp_path / 'bn_data'
    okx_root = tmp_path / 'okx_data'
    bn_bmac = bn_root / 'preprocess_1h_resample'
    okx_bmac = okx_root / 'preprocess_1h_resample'
    run_time = (datetime.now() + timedelta(minutes=2)).replace(second=0, microsecond=0)
    m_dir = f'{run_time.minute}m'
    (bn_bmac / m_dir).mkdir(parents=True)
    (okx_bmac / m_dir).mkdir(parents=True)
    ts = int(run_time.timestamp())

    (bn_bmac / m_dir / f'spot_dict_{ts}.ready').write_text('')
    (bn_bmac / m_dir / f'swap_dict_{ts}.ready').write_text('')
    (okx_bmac / m_dir / f'spot_dict_{ts}.ready').write_text('')
    # Delay okx swap_dict ready slightly to prove polling works and returns quickly (< 0.3s after ready)
    okx_swap_ready = okx_bmac / m_dir / f'swap_dict_{ts}.ready'

    monkeypatch.setattr(datatools, 'bmac_data_path', bn_bmac)
    monkeypatch.setattr(datatools, 'exchange_data_paths', {'okx': str(okx_root)}, raising=False)

    assert datatools.resolve_bmac_data_path('binance') == bn_bmac
    assert datatools.resolve_bmac_data_path('okx') == okx_bmac
    assert datatools.resolve_bmac_data_path(None) == bn_bmac

    ready_written_at = {}

    def _delayed_ready():
        time.sleep(0.15)
        ready_written_at['t'] = time.perf_counter()
        okx_swap_ready.write_text('')

    t = threading.Thread(target=_delayed_ready)
    t.start()
    ok = datatools.check_bmac_update_flag(run_time, ['binance', 'okx'])
    returned_at = time.perf_counter()
    t.join()

    assert ok is True
    assert (returned_at - ready_written_at['t']) < 0.3


def test_select_coin_no_longer_filters_is_trading_zero(tmp_path):
    from core import select_coin

    assert '是否交易' in select_coin.KLINE_COLS
    stg = SimpleNamespace(
        select_scope='swap',
        order_first='swap',
        factor_columns=['f1'],
    )
    df = pl.DataFrame({
        'candle_begin_time': [datetime(2026, 1, 1, 0), datetime(2026, 1, 1, 0)],
        'symbol': ['AAAUSDT', 'BBBUSDT'],
        'is_spot': [0, 0],
        'symbol_spot': ['', ''],
        'symbol_swap': ['AAAUSDT', 'BBBUSDT'],
        '是否交易': [0, 1],
        'f1': [1.5, 2.5],
    })
    filtered = df.filter(select_coin._selection_input_filters(stg))
    assert filtered['symbol'].to_list() == ['AAAUSDT', 'BBBUSDT']


def test_config_group_account_lookup_and_exchange_override(tmp_path):
    bn_data = tmp_path / 'bn_data'
    okx_data = tmp_path / 'okx_data'
    for root in (bn_data, okx_data):
        (root / 'preprocess_1h_resample').mkdir(parents=True)
        (root / 'exginfo').mkdir(parents=True)

    grp_dir = tmp_path / 'accounts' / 'grp_alpha'
    grp_dir.mkdir(parents=True)
    (grp_dir / '_manage.py').write_text("target_weights = {'member1': 1.0}\n", encoding='utf-8')
    (grp_dir / 'member1.py').write_text(
        "account_config = {'exchange_name': 'okx', 'hour_offset': '25m'}\n"
        "strategy_name = 'okx_strat'\n",
        encoding='utf-8',
    )
    (tmp_path / 'config.json').write_text(
        json.dumps({
            'realtime_data_path': str(bn_data),
            'exchange_data_paths': {'okx': str(okx_data)},
            'error_webhook_url': '',
            'is_simulate': 'dry_run',
        }),
        encoding='utf-8',
    )

    script = """import sys
from pathlib import Path
import core.utils.path_kit as p
p.PROJECT_ROOT = sys.argv[1]
import config
assert config.account_name == 'member1'
assert config.exchange_name == 'okx'
assert Path(config.realtime_data_path) == Path(sys.argv[2])
assert Path(config.bmac_data_path) == Path(sys.argv[2]) / 'preprocess_1h_resample'
assert config.lookback_save_interval_hours == 0
"""
    env = dict(os.environ, X3S_TRADING_ACCOUNT='member1', PYTHONPATH=str(ROOT), X3S_USE_FILE_LOGGING='0')
    env.pop('ROBUST40_SERVER_WORKER', None)
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', '-B', '-c', script, str(tmp_path), str(okx_data)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf8',
    )
    assert result.returncode == 0, result.stdout + result.stderr
