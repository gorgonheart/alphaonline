from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import polars as pl
import pytest


def test_parse_vmstat_swap_extracts_pswpin_and_pswpout(tmp_path):
    from core.utils.factor_data import read_vmstat_swap

    vmstat = tmp_path / 'vmstat'
    vmstat.write_text('nr_free_pages 123\npswpin 45\npswpout 678\npgfault 999\n', encoding='utf-8')
    assert read_vmstat_swap(vmstat) == (45, 678)
    assert read_vmstat_swap(tmp_path / 'missing') == (0, 0)


def test_calc_factor_blocks_emits_profile_log(monkeypatch, tmp_path):
    import core.select_coin as sc
    from core.utils.factor_data import FactorBlocks

    messages = []
    monkeypatch.setattr(sc.logger, 'info', lambda msg: messages.append(str(msg)))

    ts = pd.date_range('2026-01-01 00:25:00', periods=3, freq='1h')
    sample_pl = pl.DataFrame({
        'candle_begin_time': ts,
        'symbol': ['BTC-USDT'] * 3,
        'close': [100.0, 101.0, 102.0],
    })

    def fake_ordered_results(executor, function, arguments, *, max_pending, before_submit=None, **kwargs):
        for idx, _ in enumerate(arguments):
            yield idx, sample_pl.clone()

    class DummyPool:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr('core.utils.factor_data.ordered_results', fake_ordered_results)
    monkeypatch.setattr(sc, 'ProcessPoolExecutor', DummyPool)
    monkeypatch.setattr(sc, 'iter_candle_descriptors', lambda conf, run_time: [('fake.arrow', 0, 'swap'), ('fake.arrow', 1, 'swap')])

    conf = SimpleNamespace(input_mode='arrow_strict', factor_col_name_list=['close'])
    store = FactorBlocks(tmp_path / 'factors', hours=2016, minute=25)
    try:
        sc._calc_factor_blocks(conf, pd.Timestamp('2026-01-01 03:25:00'), store)
        assert store.rows == 6
    finally:
        store.close()

    profile_lines = [m for m in messages if m.startswith('FACTOR_BLOCKS_PROFILE ')]
    assert len(profile_lines) == 1
    line = profile_lines[0]
    for key in ('rows=6', 'batches=1', 'symbols=2', 'nonempty=2',
                'elapsed_s=', 'ordered_wait_s=', 'parent_concat_write_s=',
                'parent_cpu_s=', 'swap_out_pages='):
        assert key in line


def test_step5_aggregate_emits_agg_profile(monkeypatch, tmp_path):
    import core.backtest as bt

    messages = []
    monkeypatch.setattr(bt.logger, 'info', lambda msg: messages.append(str(msg)))
    monkeypatch.setattr(bt.logger, 'debug', lambda msg: None)
    monkeypatch.setattr(bt.logger, 'ok', lambda msg: None)

    df_sel = pd.DataFrame({'candle_begin_time': [pd.Timestamp('2026-01-01 00:25:00')], 'symbol': ['BTC-USDT']})
    df_ratio = pd.DataFrame({'BTC-USDT': [0.5]}, index=[pd.Timestamp('2026-01-01 00:25:00')])

    monkeypatch.setattr(bt, 'concat_select_results', lambda conf: None)
    monkeypatch.setattr(bt, 'transfer_spot_to_swap', lambda conf: df_sel)
    monkeypatch.setattr(bt, 'save_latest_period', lambda conf, sr, mct: None)
    monkeypatch.setattr(bt, 'agg_multi_strategy_ratio', lambda conf, sr, mct: (df_ratio, df_ratio))

    conf = SimpleNamespace(name='S1-test', get_result_folder=lambda: tmp_path)
    spot_r, swap_r = bt.step5_aggregate_select_results(conf, pd.Timestamp('2026-01-01 01:25:00'), save_final_result=False)
    assert spot_r.equals(df_ratio)
    assert swap_r.equals(df_ratio)

    profile_lines = [m for m in messages if m.startswith('AGG_PROFILE ')]
    assert len(profile_lines) == 1
    line = profile_lines[0]
    for key in ('conf=S1-test', 'rows=1', 'concat_s=', 'transfer_s=', 'agg_s=', 'save_s=', 'total_s=', 'swap_out_pages='):
        assert key in line


def test_abba_harness_helpers():
    from tools.benchmark_phase0_abba import build_abba_order, parse_profile_line, summarize_runs

    assert build_abba_order('A', 'B', repeats=1) == ['A', 'B', 'B', 'A']
    assert build_abba_order('A', 'B', repeats=2) == ['A', 'B', 'B', 'A', 'A', 'B', 'B', 'A']

    parsed = parse_profile_line(
        'FACTOR_BLOCKS_PROFILE rows=100 batches=4 symbols=10 nonempty=10 '
        'elapsed_s=12.345 ordered_wait_s=9.000 parent_concat_write_s=3.345 '
        'parent_cpu_s=2.100 swap_out_pages=16'
    )
    assert parsed['rows'] == 100
    assert parsed['elapsed_s'] == pytest.approx(12.345)
    assert parsed['swap_out_pages'] == 16

    summary = summarize_runs([
        {'label': 'A', 'wall_s': 10.0, 'cpu_s': 28.0, 'rss_peak_mib': 1500.0, 'swap_out_pages': 0},
        {'label': 'B', 'wall_s': 8.0, 'cpu_s': 24.0, 'rss_peak_mib': 1520.0, 'swap_out_pages': 0},
        {'label': 'B', 'wall_s': 8.4, 'cpu_s': 24.5, 'rss_peak_mib': 1510.0, 'swap_out_pages': 2},
        {'label': 'A', 'wall_s': 10.4, 'cpu_s': 28.5, 'rss_peak_mib': 1505.0, 'swap_out_pages': 0},
    ])
    assert summary['A']['median_wall_s'] == pytest.approx(10.2)
    assert summary['B']['median_wall_s'] == pytest.approx(8.2)
    assert summary['B']['median_eff_cores'] == pytest.approx(24.25 / 8.2, rel=1e-2)
