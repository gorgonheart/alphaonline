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


def test_bn_subaccount_transfer_direction_uses_sub_side():
    from core.binance.standard_client import StandardClient
    from core.binance.portfolio_margin_client import PortfolioMarginClient

    assert hasattr(PortfolioMarginClient, 'get_statistics_overview')

    rows_out = [
        {
            'asset': 'USDT',
            'qty': '100',
            'time': 1700000000000,
            'status': 'SUCCESS',
            'tranId': 1,
            'fromAccountType': 'MARGIN',
            'toAccountType': 'SPOT',
        },
        {
            'asset': 'USDT',
            'qty': '200',
            'time': 1700000000000,
            'status': 'SUCCESS',
            'tranId': 2,
            'fromAccountType': 'SPOT',
            'toAccountType': 'SPOT',
        },
    ]

    class _FakeExg:
        def sapi_get_asset_transfer(self, params):
            return {'total': 0, 'rows': []}

        def sapi_get_sub_account_transfer_subuserhistory(self, params):
            return rows_out if params['type'] == 2 else []

    for cls, expected_val in [(StandardClient, -200.0), (PortfolioMarginClient, -100.0)]:
        cli = object.__new__(cls)
        cli.base_margin = {'USDT': 1}
        cli.exchange = _FakeExg()
        df = cli.fetch_transfer_history(datetime(2026, 1, 1, 12))
        assert len(df) == 1
        assert float(df['账户总净值'].iloc[0]) == pytest.approx(expected_val)


def test_okx_tradfi_limit_51138_and_transfer_history():
    import pandas as pd
    from core.okx.base_client import OkxClient
    from core.okx.portfolio_margin_client import OkxPortfolioMarginClient

    assert OkxPortfolioMarginClient.spot_inst_type == 'MARGIN'

    cli = OkxClient()
    assert cli._is_blocked_tradfi({'instCategory': '3'}, 'spot') is True
    assert cli._is_blocked_tradfi({'instCategory': '4'}, 'swap') is True

    limit_sell = cli._parse_okx_price_limit(
        'OKX下单失败 sCode=51138 The lowest price limit for the sell leg is 100.5.', 'sell'
    )
    assert limit_sell == pytest.approx(100.5)

    cli.market_info = {'swap': {'price_precision': {'BTC-USDT-SWAP': 2}}}
    cli._swap_ticker_price = pd.Series({'BTC-USDT-SWAP': 101.0})
    sell_p = cli.retry_order_param({'instId': 'BTC-USDT-SWAP', 'side': 'sell', 'sz': '1'}, 'swap', 0.015, 1, 100.0)
    buy_p = cli.retry_order_param({'instId': 'BTC-USDT-SWAP', 'side': 'buy', 'sz': '1'}, 'swap', 0.015, 1, 100.0)
    assert sell_p['px'] == '100.10'
    assert buy_p['px'] == '99.90'

    ts_ms = int(datetime(2026, 1, 1, 10, 5).timestamp() * 1000)

    class _FakeOkxExg:
        def private_get_account_bills(self, params):
            return {
                'data': [
                    {'billId': 'b1', 'ccy': 'USDT', 'subType': '11', 'balChg': '50', 'ts': str(ts_ms)},
                ]
            }

        def private_get_account_bills_archive(self, params):
            return {
                'data': [
                    {'billId': 'b1', 'ccy': 'USDT', 'subType': '11', 'balChg': '50', 'ts': str(ts_ms)},
                    {'billId': 'b2', 'ccy': 'USDT', 'subType': '12', 'balChg': '-20', 'ts': str(ts_ms + 1000)},
                ]
            }

    cli.exchange = _FakeOkxExg()
    tdf = cli.fetch_transfer_history(datetime(2026, 1, 2, 0))
    assert list(tdf.columns) == ['time', '账户总净值', 'type']
    assert float(tdf['账户总净值'].sum()) == pytest.approx(30.0)


def test_group_statistics_builds_6_subplots_with_missing_member_transfer(tmp_path, monkeypatch):
    import pandas as pd
    from core.utils import group_statistics

    for name, eq1, eq2 in [('mem_a', 1000.0, 1100.0), ('mem_b', 1000.0, 1000.0)]:
        d = tmp_path / 'data' / name / '账户信息'
        d.mkdir(parents=True)
        pd.DataFrame({
            'time': ['2026-01-01 00:25:00', '2026-01-01 01:25:00'],
            '账户总净值': [eq1, eq2],
            '多头仓位': [500.0, 500.0],
            '空头仓位': [-500.0, -500.0],
            '多头选币': [5, 5],
            '空头选币': [5, 5],
            'observed_at': ['2026-01-01 00:25:01', '2026-01-01 01:25:01'],
            'equity_source': ['live', 'live'],
        }).to_csv(d / 'equity.csv', encoding='gbk', index=False)

    pd.DataFrame({
        'time': ['2026-01-01 00:40:00'],
        '账户总净值': [100.0],
        'type': ['transfer'],
    }).to_csv(tmp_path / 'data' / 'mem_a' / '账户信息' / 'transfer.csv', encoding='gbk', index=False)

    def _fake_folder(*parts, **kwargs):
        p = tmp_path.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(group_statistics, 'get_folder_path', _fake_folder)

    group = {
        'name': 'grp_test',
        'members': {'mem_a': 0.5, 'mem_b': 0.5},
        'hour_offset': '25m',
        'threshold_mode': 'relative',
        'threshold': 0.15,
        'webhook': '',
    }
    df, df_members, df_all, transfers = group_statistics.build_group_equity(group)
    assert df['净值'].iloc[-1] == pytest.approx(1.0)
    assert transfers['mem_b'].empty

    pic_path = tmp_path / 'group_equity.png'
    group_statistics.draw_group_equity(group, df, df_all, transfers, pic_path)
    assert pic_path.exists() and pic_path.stat().st_size > 0


def test_account_group_scales_and_transfer_thresholds():
    from core.account_group import calc_scales, build_transfer_msg

    scales = calc_scales({'a': 40.0, 'b': 960.0}, {'a': 0.05, 'b': 0.95}, max_scale=2.0)
    assert scales['a'] == pytest.approx(1.25)
    assert scales['b'] == pytest.approx(950.0 / 960.0)

    capped = calc_scales({'a': 10.0, 'b': 990.0}, {'a': 0.5, 'b': 0.5}, max_scale=2.0)
    assert capped['a'] == pytest.approx(2.0)

    grp_abs = {
        'name': 'g_abs',
        'members': {'a': 0.05, 'b': 0.95},
        'threshold_mode': 'absolute',
        'threshold': 0.05,
        'max_scale': 2.0,
    }
    assert build_transfer_msg(grp_abs, {'a': 40.0, 'b': 960.0}, scales) is None

    grp_rel = {
        'name': 'g_rel',
        'members': {'a': 0.05, 'b': 0.95},
        'threshold_mode': 'relative',
        'threshold': 0.15,
        'max_scale': 2.0,
    }
    msg = build_transfer_msg(grp_rel, {'a': 40.0, 'b': 960.0}, scales)
    assert msg is not None and '【账户组划转提醒】g_rel' in msg


def test_calc_target_position_scale_caps_spot_and_keeps_backward_compat(tmp_path, monkeypatch):
    import pandas as pd
    from core import position as pos_mod
    from core.model.rebalance_mode import RebalanceMode
    from positions.ModelRobust40Strategy import register_execution

    register_execution()

    df_spot = pd.DataFrame({'BTCUSDT': [0.5]}, index=[pd.Timestamp('2026-01-01 00:00:00')])
    df_swap = pd.DataFrame({'BTCUSDT': [0.5]}, index=[pd.Timestamp('2026-01-01 00:00:00')])
    df_spot.to_pickle(tmp_path / 'df_spot_ratio.pkl')
    df_swap.to_pickle(tmp_path / 'df_swap_ratio.pkl')
    monkeypatch.setattr(pos_mod, 'runtime_folder', tmp_path, raising=False)

    class _FakeBn:
        market_info = {
            'spot': {'symbol_list': ['BTCUSDT'], 'min_qty': {'BTCUSDT': 3}},
            'swap': {'symbol_list': ['BTCUSDT'], 'min_qty': {'BTCUSDT': 3}},
        }

        def get_spot_ticker_price_series(self):
            return pd.Series({'BTCUSDT': 100.0})

        def get_swap_ticker_price_series(self):
            return pd.Series({'BTCUSDT': 100.0})

    acct = SimpleNamespace(
        name='test_scale',
        bn=_FakeBn(),
        spot_position=pd.DataFrame(),
        swap_position=pd.DataFrame(),
        spot_equity=5000.0,
        swap_equity=5000.0,
        leverage=1.0,
        buy_bnb_value=0.0,
        rebalance_mode=RebalanceMode.init({'mode': 'RebRobust40', 'params': {'min_order_usdt_ratio': 0.4, 'execution_cap': 2.45}}),
    )

    # Backward-compatible call (reading from runtime_folder, scale=1.0)
    res_base = pos_mod.calc_target_position(acct, is_debug=True)
    spot_base = float(res_base.loc[res_base['symbol_type'] == 'spot', '目标持仓量'].iloc[0])
    swap_base = float(res_base.loc[res_base['symbol_type'] == 'swap', '目标持仓量'].iloc[0])

    # Explicit DataFrame call with scale=1.5 > 1: spot capped (not scaled up), swap scaled by 1.5x
    res_scaled = pos_mod.calc_target_position(acct, df_spot, df_swap, is_debug=True, scale=1.5)
    spot_scaled = float(res_scaled.loc[res_scaled['symbol_type'] == 'spot', '目标持仓量'].iloc[0])
    swap_scaled = float(res_scaled.loc[res_scaled['symbol_type'] == 'swap', '目标持仓量'].iloc[0])

    assert spot_scaled == pytest.approx(spot_base)
    assert swap_scaled == pytest.approx(swap_base * 1.5, rel=0.02)


def test_run_group_with_model_member_exit75_skips_whole_group_and_dry_run_guards(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import pandas as pd
    from core import account_group as ag
    from core import account_exec as ae
    from core import real_trading as rt

    sent_msgs = []
    monkeypatch.setattr(ag, 'send_wechat_work_msg', lambda msg, url: sent_msgs.append((msg, url)))
    monkeypatch.setattr(ag, 'get_handoff_folder', lambda name: tmp_path / name / 'handoff')

    m1_dir = tmp_path / 'm1_model' / 'handoff'
    m1_dir.mkdir(parents=True)
    # m2_legacy writes handoff, m1_model exits 75 -> Coordinator.handle_result returns False, no handoff written
    group = {
        'name': 'grp_model',
        'members': {'m1_model': 0.5, 'm2_legacy': 0.5},
        'paths': {'m1_model': tmp_path / 'm1_model.py', 'm2_legacy': tmp_path / 'm2_legacy.py'},
        'threshold': 0.05,
        'threshold_mode': 'absolute',
        'max_scale': 2.0,
        'webhook': 'https://wh-main',
        'transfer_webhook': 'https://wh-transfer',
        'hour_offset': '25m',
    }
    dispatched = []
    run_time = datetime(2026, 1, 1, 12, 25)

    def _dispatcher(path, rt_arg, *extra_args):
        dispatched.append((path.stem, extra_args))
        if path.stem == 'm2_legacy' and extra_args == ('--stage', 'calc'):
            h_dir = tmp_path / 'm2_legacy' / 'handoff'
            h_dir.mkdir(parents=True, exist_ok=True)
            (h_dir / 'handoff.json').write_text(
                json.dumps({'run_time': int(run_time.timestamp()), 'equity': 1000.0}),
                encoding='utf-8',
            )
        # m1_model simulates exit 75 handled gracefully (no handoff written, no exception)

    ag.run_group(group, run_time, dispatcher=_dispatcher)
    assert all('--stage' in args and 'trade' not in args for _, args in dispatched)
    assert any('【账户组跳过】' in m and url == 'https://wh-transfer' for m, url in sent_msgs)

    # dry_run guards on prepare_account, trade_by_account, and lookback_save_interval_hours == 0
    monkeypatch.setattr(ae, 'is_debug', False, raising=False)
    monkeypatch.setattr(ae, 'is_simulate', 'dry_run', raising=False)
    fake_bn = Mock()
    acct = SimpleNamespace(
        name='dry_acct',
        hour_offset='25m',
        hour_offset_minute=25,
        bn=fake_bn,
        spot_equity=1000.0,
        swap_equity=1000.0,
        leverage=1.0,
        update_account_info=Mock(return_value=True),
        calc_order_amount=Mock(return_value=pd.DataFrame()),
        proceed_spot_order=Mock(),
        proceed_swap_order=Mock(),
    )
    assert ae.prepare_account(acct) is True
    fake_bn.reset_max_leverage.assert_not_called()
    acct.update_account_info.assert_called_once_with(is_operate=False)

    monkeypatch.setattr(rt, 'is_debug', False, raising=False)
    monkeypatch.setattr(rt, 'is_simulate', 'dry_run', raising=False)
    monkeypatch.setattr(rt, 'lookback_save_interval_hours', 0, raising=False)
    monkeypatch.setattr(rt, 'calc_target_position', lambda *a, **k: pd.DataFrame({'目标持仓量': [0.0], 'symbol_type': ['swap'], 'symbol': ['BTCUSDT'], 'price': [100.0]}))
    monkeypatch.setattr(rt, 'save_position_results', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'save_final_select_results', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'save_symbol_order', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'save_order_history', Mock())
    sub_conf = SimpleNamespace(lookback_days=7)
    me_conf = SimpleNamespace(
        factory=SimpleNamespace(config_list=[sub_conf]),
        strategy=SimpleNamespace(params={}, core_funcs={}),
        backtest_strategies=lambda *a: None,
        process_equities=lambda *a, **k: None,
        calc_ratios=lambda: pd.DataFrame(),
        agg_pos_ratio=lambda r: (pd.DataFrame({'BTCUSDT': [0.0]}), pd.DataFrame({'BTCUSDT': [0.5]})),
        apply_position_limits=lambda s, w: (s, w),
    )
    monkeypatch.setattr(rt, 'refresh_diff_time', lambda: None)
    monkeypatch.setattr(rt, 'snapshot_bmac_exginfo', lambda: None)
    monkeypatch.setattr(rt, 'save_position_snapshot', lambda *a: None)
    monkeypatch.setattr(rt, 'runtime_folder', tmp_path)

    assert rt.calc_by_account(acct, me_conf, run_time) is True
    assert sub_conf.lookback_days == 7  # lookback_save_interval_hours=0 must NOT overwrite lookback_days

    rt.trade_by_account(acct, me_conf, run_time, ratio_folder=tmp_path, scale=1.0)
    fake_bn.cancel_all_swap_orders.assert_not_called()
    fake_bn.cancel_all_spot_orders.assert_not_called()
    fake_bn.adjust_leverage_by_target_notional.assert_not_called()
    acct.proceed_spot_order.assert_not_called()
    acct.proceed_swap_order.assert_not_called()
    fake_bn.collect_asset.assert_not_called()
    fake_bn.adjust_base_margin.assert_not_called()
    rt.save_order_history.assert_not_called()


def test_coordinator_command_and_server_robust40_stage_trade_bypass(tmp_path, monkeypatch):
    import pandas as pd
    from tools.robust40 import server
    from tools.robust40.cli import server_robust40

    acct = tmp_path / 'model_m1.py'
    acct.write_text(
        "from positions.ModelRobust40Strategy import *\n"
        "account_config = {'hour_offset': '25m'}\n"
        "strategy_name = 's'\n"
        "strategy_config = {'params': {'runtime': {'server_init': True}}}\n"
        "strategy_pool = []\n"
        "precompute_factor_configs = {}\n",
        encoding='utf-8',
    )
    values = {
        'account': {'hour_offset': '25m'},
        'strategy_name': 's',
        'strategy_pool': [],
        'precompute_factor_configs': {},
    }
    dest = server.location(tmp_path, acct, values)
    dest.mkdir(parents=True)
    pd.to_pickle({'origin': '2026-01-01 00:25:00', 'cutoff': '2026-01-01 11:25:00', 'complete': True}, dest / 'warmup.pkl')

    c = server.Coordinator(tmp_path)
    monkeypatch.setattr(server, 'enabled', lambda a: True)
    monkeypatch.setattr(server, 'settings', lambda a: (values, {'minute': 25}))

    # --stage trade must NOT require .ready / Arrow files
    run_time = datetime(2026, 1, 1, 12, 25)
    cmd, env = c.command(acct, run_time, tmp_path / 'absent_market', '--stage', 'trade', '--scale', '1.25')
    assert cmd is not None and '--stage' in cmd and 'trade' in cmd and '--scale' in cmd and '1.25' in cmd
    assert env is not None and 'ROBUST40_SERVER_WORKER' in env

    # Verify server_robust40.trade(..., stage='trade', scale=1.25) skips handoff.prepare and calls account_exec.main
    called = {}
    import core.account_exec as account_exec
    monkeypatch.setattr(account_exec, 'main', lambda: called.setdefault('argv', list(sys.argv)) or run_time)
    monkeypatch.setattr(
        'tools.robust40.handoff.prepare',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('handoff.prepare must be skipped in --stage trade')),
    )
    rc = server_robust40.trade(acct, int(run_time.timestamp()), stage='trade', scale=1.25)
    assert rc in (0, None)
    assert '--stage' in called['argv'] and 'trade' in called['argv'] and '1.25' in called['argv']



