"""TDD tests for Q1 (Problem B): 24h price-impact strategy attribution backfill."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from core.utils import statis_func as sf
from core.utils import statistics as st


def _write_archive(root: Path, account: str, stem: str, rows: list[dict]):
    folder = root / account / 'select_coin'
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_pickle(folder / f'{stem}.pkl')


def _make_me_conf(tmp_path: Path, configs: list[tuple[str, list[dict]]], ratios: dict | None = None):
    factory_dir = tmp_path / 'factory'
    factory_dir.mkdir(parents=True, exist_ok=True)
    if ratios is None:
        ratios = {str(i): 1.0 for i in range(len(configs))}
    pd.DataFrame([ratios], index=[pd.Timestamp('2026-11-18 14:00:00')]).to_csv(factory_dir / '仓位比例.csv')
    conf_list = []
    for idx, (name, rows) in enumerate(configs):
        cdir = tmp_path / f'conf_{idx}'
        cdir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_pickle(cdir / '最新持仓周期选币结果.pkl')
        conf_list.append(SimpleNamespace(name=name, get_result_folder=lambda d=cdir: d))
    return SimpleNamespace(factory=SimpleNamespace(result_folder=factory_dir, config_list=conf_list))


def test_tb1_backfill_reads_recent_archives_for_fixed_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    acct = '25m-eart1106'
    _write_archive(tmp_path, acct, '2026-11-17_15', [
        dict(symbol_spot='', symbol_swap='AIOTUSDT', parent_name='S4-双均线trial1677', 方向=1),
    ])
    _write_archive(tmp_path, acct, '2026-11-18_02', [
        dict(symbol_spot='', symbol_swap='BEAMXUSDT', parent_name='S5-动量实盘多头', 方向=1),
        dict(symbol_spot='ETHUSDT', symbol_swap='', parent_name='S14-四饼', 方向=1),
    ])
    _write_archive(tmp_path, acct, '2026-11-18_10', [
        dict(symbol_spot='', symbol_swap='AIOTUSDT', parent_name='S12-回撤阈值空头', 方向=-1),
    ])

    now = datetime(2026, 11, 18, 14, 29, 0)
    spot_map, swap_map = sf.build_recent_strategy_backfill_map(acct, hours=24, max_strategy_display=5, now=now)

    assert spot_map == {'ETHUSDT': 'S14-四饼(L)'}
    assert swap_map['AIOTUSDT'] == 'S12-回撤阈值空头(S), S4-双均线trial1677(L)'
    assert swap_map['BEAMXUSDT'] == 'S5-动量实盘多头(L)'
    assert 'NEVERUSDT' not in swap_map


def test_tb2_current_cycle_takes_precedence_over_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    acct = '25m-eart1106'
    _write_archive(tmp_path, acct, '2026-11-18_01', [
        dict(symbol_spot='BTCUSDT', symbol_swap='', parent_name='OLD-Spot', 方向=1),
        dict(symbol_spot='', symbol_swap='AIOTUSDT', parent_name='OLD-Swap', 方向=-1),
        dict(symbol_spot='', symbol_swap='CLOSEDUSDT', parent_name='S5-动量实盘多头', 方向=1),
    ])
    me = _make_me_conf(tmp_path, [
        ('CUR-Spot', [dict(symbol_spot='BTCUSDT', symbol_swap='', 方向=1)]),
        ('CUR-Swap', [dict(symbol_spot='', symbol_swap='AIOTUSDT', 方向=1)]),
    ])

    now = datetime(2026, 11, 18, 14, 25, 0)
    spot_map, swap_map = sf.build_strategy_maps(me, acct, now=now, hours=24, max_strategy_display=5)

    assert spot_map['BTCUSDT'] == 'CUR-Spot(L)'
    assert swap_map['AIOTUSDT'] == 'CUR-Swap(L)'
    assert swap_map['CLOSEDUSDT'] == 'S5-动量实盘多头(L)'


def test_tb3_window_boundaries_and_55m_cross_hour_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    acct = '55m_o.rgonheart1104'
    # Exact 24h cutoff for run_time=2026-11-18 13:55 is 2026-11-17 13:00
    _write_archive(tmp_path, acct, '2026-11-17_12', [
        dict(symbol_spot='', symbol_swap='TOOOLDUSDT', parent_name='S1', 方向=1),
    ])
    _write_archive(tmp_path, acct, '2026-11-17_13', [
        dict(symbol_spot='', symbol_swap='ATBOUNDUSDT', parent_name='S2', 方向=1),
    ])
    _write_archive(tmp_path, acct, '2026-11-18_13', [
        dict(symbol_spot='', symbol_swap='SAMEHRUSDT', parent_name='S3', 方向=-1),
    ])
    _write_archive(tmp_path, acct, '2026-11-18_14', [
        dict(symbol_spot='', symbol_swap='FUTUREUSDT', parent_name='S4', 方向=1),
    ])

    # Naive run_time at 13:55 (even if observed_at crossed into 14:00:30)
    run_time = datetime(2026, 11, 18, 13, 55, 0)
    _, swap_map = sf.build_recent_strategy_backfill_map(acct, hours=24, now=run_time)
    assert 'TOOOLDUSDT' not in swap_map
    assert swap_map['ATBOUNDUSDT'] == 'S2(L)'
    assert swap_map['SAMEHRUSDT'] == 'S3(S)'
    assert 'FUTUREUSDT' not in swap_map

    # Timezone-aware anchor also works without raising TypeError
    aware = run_time.replace(tzinfo=timezone(timedelta(hours=8)))
    _, swap_map_aware = sf.build_recent_strategy_backfill_map(acct, hours=24, now=aware)
    assert swap_map_aware == swap_map


def test_tb4_missing_empty_corrupt_or_legacy_archives_never_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    acct = 'empty-acct'
    now = datetime(2026, 11, 18, 14, 0, 0)

    # Missing folder
    assert sf.build_recent_strategy_backfill_map(acct, now=now) == ({}, {})

    # Empty, corrupt, legacy (no parent_name), bad filename
    folder = tmp_path / acct / 'select_coin'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'not_a_date.pkl').write_bytes(b'garbage')
    (folder / '2026-11-18_10.pkl').write_bytes(b'corrupt pickle')
    pd.DataFrame().to_pickle(folder / '2026-11-18_11.pkl')
    pd.DataFrame([dict(symbol_swap='OLDUSDT', 方向=1)]).to_pickle(folder / '2026-11-18_12.pkl')
    assert sf.build_recent_strategy_backfill_map(acct, now=now) == ({}, {})


def test_tb5_format_parity_with_build_symbol_strategy_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    acct = 'fmt-acct'
    names = [f'S{i}' for i in range(1, 8)]  # 7 strategies -> triggers (+2 more) when max=5
    me = _make_me_conf(tmp_path, [(n, [dict(symbol_spot='', symbol_swap='SOLUSDT', 方向=1)]) for n in names])
    _write_archive(tmp_path, acct, '2026-11-18_10', [
        dict(symbol_spot='', symbol_swap='SOLUSDT', parent_name=n, 方向=1) for n in names
    ])

    _, cur_swap = sf.build_symbol_strategy_columns(me, max_strategy_display=5)
    _, bf_swap = sf.build_recent_strategy_backfill_map(acct, now=datetime(2026, 11, 18, 14, 0), max_strategy_display=5)
    assert bf_swap['SOLUSDT'] == cur_swap['SOLUSDT']
    assert '(+2 more)' in bf_swap['SOLUSDT']


def test_tb6_report_and_send_position_fallback_use_merged_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, 'data_path', tmp_path)
    monkeypatch.setattr(st, 'data_path', tmp_path)
    acct = 'report-acct'
    run_time = datetime(2026, 11, 18, 14, 25, 0)
    obs = datetime(2026, 11, 18, 14, 29, 0)

    # 24h ago held ROTATEDUSDT (now closed), currently holds LIVEUSDT
    _write_archive(tmp_path, acct, '2026-11-17_14', [
        dict(symbol_spot='', symbol_swap='ROTATEDUSDT', parent_name='S4-双均线trial1677', 方向=1),
    ])
    me = _make_me_conf(tmp_path, [
        ('S5-动量实盘多头', [dict(symbol_spot='', symbol_swap='LIVEUSDT', 方向=-1)]),
    ])

    # Build 24h swap position history containing ROTATEDUSDT (closed by now) + LIVEUSDT
    hist_time = datetime(2026, 11, 17, 14, 29, 0)
    hist_df = pd.DataFrame([
        dict(symbol='ROTATEDUSDT', pos_amount=100.0, cur_price=1.0, market_value=100.0, side=1),
        dict(symbol='LIVEUSDT', pos_amount=-50.0, cur_price=2.0, market_value=100.0, side=-1),
    ]).set_index('symbol')
    hist_df.attrs.update(observed_at=hist_time, run_time=datetime(2026, 11, 17, 14, 25, 0),
                         phase='post_trade', offset_minute=25, snapshot_source='binance_pm_actualEquity')
    history_cache = {
        'spot': {},
        'swap': {int(hist_time.timestamp() * 1000): hist_df},
    }

    current_swap = pd.DataFrame([
        dict(symbol='LIVEUSDT', pos_amount=-50.0, cur_price=1.8, market_value=90.0, side=-1),
    ]).set_index('symbol')
    current_spot = pd.DataFrame(columns=['pos_amount', 'cur_price', 'market_value', 'side'])
    current_spot.index.name = 'symbol'

    account = SimpleNamespace(
        name=acct,
        hour_offset_minute=25,
        wechat_webhook_url='https://example.invalid',
        bn=SimpleNamespace(
            market_info={'spot': {}, 'swap': {}},
            get_swap_ticker_price_series=lambda: pd.Series({'ROTATEDUSDT': 1.2, 'LIVEUSDT': 1.8}),
            get_spot_ticker_price_series=lambda: pd.Series(dtype=float),
        ),
    )

    sent_msgs = []
    monkeypatch.setattr(sf, 'send_wechat_work_msg', lambda msg, url: sent_msgs.append(msg))

    # Passing strategy_maps=None with me_conf should auto-build merged (current + 24h backfill)
    record = sf.calculate_and_send_pnl_report(
        account, pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float),
        me_conf=me, observed_at=obs, run_time=run_time,
        current_frames=(current_spot, current_swap),
        strategy_maps=None, total_equity=10000.0, notify=True,
        history_cache=history_cache, snapshot_source='binance_pm_actualEquity',
    )

    by_sym = {row['symbol']: row['strategy'] for row in record['24h']}
    assert by_sym['ROTATEDUSDT'] == 'S4-双均线trial1677(L)'
    assert by_sym['LIVEUSDT'] == 'S5-动量实盘多头(S)'
    assert sent_msgs and '[S4-双均线trial1677(L)]' in sent_msgs[0]
    assert '未匹配' not in sent_msgs[0]

    # send_position_result(strategy_maps=None) also uses merged maps (backfill covers rotated symbol)
    exported = []
    monkeypatch.setattr(st, 'send_img_for_dataframe', lambda df, url: exported.append(df))
    mixed_swap = pd.DataFrame([
        dict(symbol='ROTATEDUSDT', pos_amount=10.0, cur_price=1.2, market_value=12.0, side=1),
        dict(symbol='LIVEUSDT', pos_amount=-50.0, cur_price=1.8, market_value=90.0, side=-1),
    ]).set_index('symbol')
    st.send_position_result(
        account, pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float),
        me_conf=me, run_time=run_time, observed_at=obs,
        current_frames=(current_spot, mixed_swap), strategy_maps=None, notify=True,
    )
    assert len(exported) == 1
    assert exported[0].loc['ROTATEDUSDT', 'strategy'] == 'S4-双均线trial1677(L)'
    assert exported[0].loc['LIVEUSDT', 'strategy'] == 'S5-动量实盘多头(S)'


def test_tb6_statistics_run_populates_strategy_maps_even_when_notify_false(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(sf, 'data_path', tmp_path)
    monkeypatch.setattr(st, 'data_path', tmp_path)
    acct = 'silent-acct'
    run_time = datetime(2026, 11, 18, 14, 25, 0)
    _write_archive(tmp_path, acct, '2026-11-17_14', [
        dict(symbol_spot='', symbol_swap='ROTATEDUSDT', parent_name='S4-双均线trial1677', 方向=1),
    ])
    me = _make_me_conf(tmp_path, [
        ('S5-动量实盘多头', [dict(symbol_spot='', symbol_swap='LIVEUSDT', 方向=1)]),
    ])

    captured = {}

    def fake_pnl_report(*args, **kwargs):
        captured['strategy_maps'] = kwargs.get('strategy_maps')
        return {}

    account = SimpleNamespace(
        name=acct,
        hour_offset_minute=25,
        wechat_webhook_url='https://example.invalid',
        bn=SimpleNamespace(
            market_info={'spot': {}, 'swap': {}},
            get_account_overview=lambda **kw: {
                'account_equity': 10000.0,
                'spot_assets': {'equity': 1000.0, 'usdt': 1000.0, 'spot_position_df': pd.DataFrame(), 'prices': pd.Series(dtype=float)},
                'swap_assets': {'swap_position_df': pd.DataFrame()},
                'seed_coin_spot_equity': 0.0,
                'statistics': {'equity_source': 'binance_pm_actualEquity', 'equity_currency': 'USD'},
            },
        ),
    )

    monkeypatch.setattr(config, 'statistics_config', dict(enabled=True, notify=False, charts=False, details=False, price_impact=True))
    monkeypatch.setattr(st, 'load_multi_accounts', lambda: [Path(f'{acct}.py')])
    monkeypatch.setattr(st, 'init_system', lambda _: (account, me))
    monkeypatch.setattr(st, 'calculate_and_send_pnl_report', fake_pnl_report)
    monkeypatch.setattr(st.sys, 'argv', ['stats', str(int(run_time.timestamp())), acct])

    st.run()

    assert 'strategy_maps' in captured
    assert captured['strategy_maps'][1]['ROTATEDUSDT'] == 'S4-双均线trial1677(L)'
    assert captured['strategy_maps'][1]['LIVEUSDT'] == 'S5-动量实盘多头(L)'
