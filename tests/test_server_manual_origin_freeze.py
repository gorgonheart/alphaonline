"""Offline contract for manual S3 dates and read-only initialization inputs."""
import errno
import os
from pathlib import Path

import pandas as pd
import pytest

from tools.robust40 import market_inputs as warm
from tools.robust40 import server


def values(date='2026-08-01', n=720):
    return {'strategy_pool': [{'filter_list': [
        ('VolumeMaxBias_进出场阈值分离官方', (n, .1, .9, date), 'val:==1')]}]}


@pytest.mark.parametrize('minute', range(0, 60, 5))
def test_manual_date_is_only_authority_and_offset_is_utc(minute):
    origin, n = server.configured_s3(values(), minute)
    assert origin == pd.Timestamp('2026-08-01') + pd.Timedelta(minutes=minute)
    assert n == 720


@pytest.mark.parametrize('date', ['bad', 'NaT', '2026-08-01 01:20', '2026-08-01 00:55'])
def test_reject_mistyped_dates_instead_of_silently_moving_them(date):
    with pytest.raises(ValueError):
        server.configured_s3(values(date), 20)


def test_manual_date_requires_history_only_on_reinitialization():
    times = pd.date_range('2025-03-01 00:20', periods=14050, freq='h')
    raw = {'BTCUSDT': pd.DataFrame({'candle_begin_time': times}),
           'NEWUSDT': pd.DataFrame({'candle_begin_time': times[-100:]})}
    server.validate_initial_history(raw, times[721], 720, 1)
    with pytest.raises(ValueError, match='S3.*history'):
        server.validate_initial_history(raw, times[720], 720, 1)
    with pytest.raises(ValueError, match='Full raw history'):
        server.validate_initial_history({'BTC': raw['BTCUSDT'].tail(4050)}, times[-1], 720, 1)


def test_hardlink_survives_upstream_replace_and_stage_cleanup(tmp_path):
    source = tmp_path/'public.pkl'; source.write_bytes(b'old candles')
    stage = tmp_path/'frozen.pkl'
    warm.freeze_file(source, stage)
    assert os.path.samefile(source, stage)
    source.unlink(); source.write_bytes(b'new candles')
    assert stage.read_bytes() == b'old candles'
    stage.unlink()
    assert source.read_bytes() == b'new candles'


def test_cross_volume_falls_back_to_copy_without_overwriting_links(tmp_path, monkeypatch):
    source = tmp_path/'public.pkl'; source.write_bytes(b'candles')
    stage = tmp_path/'frozen.pkl'
    def cross_volume(*args):
        raise OSError(errno.EXDEV, 'different volumes')
    monkeypatch.setattr(os, 'link', cross_volume)
    warm.freeze_file(source, stage)
    assert stage.read_bytes() == source.read_bytes()
    stage.write_bytes(b'isolated')
    assert source.read_bytes() == b'candles'
    with pytest.raises(FileExistsError):
        warm.freeze_file(source, stage)


def test_extra_btc_keeps_only_current_offset_and_all_loader_fallbacks(tmp_path):
    source = tmp_path/'btc'; source.mkdir()
    (source/'BTC-USDT.csv').write_text('csv')
    for offset in ('20m', '40m', '55m'):
        (source/offset).mkdir()
        for name in ('BTCUSDT.pkl', 'BTC-USDT.pkl', 'ETHUSDT.pkl', 'BTCUSDT_1.ready'):
            (source/offset/name).write_text(name)
    dest = tmp_path/'frozen'
    warm.freeze_extra(source, dest, 'load_coin_btc', 20)
    assert {str(p.relative_to(dest)).replace('\\', '/') for p in dest.rglob('*') if p.is_file()} == {
        'BTC-USDT.csv', '20m/BTCUSDT.pkl', '20m/BTC-USDT.pkl'}
    assert os.path.samefile(source/'20m/BTCUSDT.pkl', dest/'20m/BTCUSDT.pkl')
    assert not os.path.samefile(source/'BTC-USDT.csv', dest/'BTC-USDT.csv')


def test_cap_retains_all_symbol_alias_files_but_not_ready(tmp_path):
    source = tmp_path/'cap'; source.mkdir()
    for name in ('BTCUSDT.pkl', 'BTC-USDT.pkl', 'OLD.pkl', 'BTC_1.ready'):
        (source/name).write_text(name)
    dest = tmp_path/'frozen'
    warm.freeze_extra(source, dest, 'load_coin_cap', 20)
    assert {p.name for p in dest.iterdir()} == {'BTCUSDT.pkl', 'BTC-USDT.pkl', 'OLD.pkl'}


def test_unknown_loader_copies_without_assuming_writer_contract(tmp_path):
    source = tmp_path/'custom'; source.mkdir(); (source/'data.csv').write_text('1')
    dest = tmp_path/'frozen'
    warm.freeze_extra(source, dest, 'custom_loader', 20)
    assert (dest/'data.csv').read_text() == '1'
    assert not os.path.samefile(source/'data.csv', dest/'data.csv')


def test_link_race_is_rejected_even_when_size_and_mtime_match(tmp_path, monkeypatch):
    source = tmp_path/'cap'; source.mkdir(); path = source/'BTC.pkl'; path.write_bytes(b'old')
    original = path.stat(); link = warm.freeze_file
    def replace_after_link(src, dst):
        link(src, dst)
        src.unlink(); src.write_bytes(b'new')
        os.utime(src, ns=(original.st_atime_ns, original.st_mtime_ns))
    monkeypatch.setattr(warm, 'freeze_file', replace_after_link)
    with pytest.raises(ValueError, match='changed'):
        warm.freeze_extra(source, tmp_path/'frozen', 'load_coin_cap', 20)


def test_full_freeze_links_market_and_metadata_without_copying_large_inputs(tmp_path, monkeypatch):
    source = tmp_path/'public'; folder = source/'20m'; folder.mkdir(parents=True)
    exg = source/'exginfo'; exg.mkdir()
    for kind in ('spot', 'swap'):
        (folder/f'{kind}_dict_batch0.pkl').write_bytes(b'raw')
        (folder/f'market_pivot_{kind}_2026.pkl').write_bytes(b'pivot')
        (exg/f'exginfo_{kind}.pkl').write_bytes(b'units')
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_'):
        (folder/f'{prefix}0.ready').touch()
    ready = max(p.stat().st_mtime_ns for p in folder.glob('*.ready'))
    copy = warm.shutil.copy2; calls = []
    def small_copy(src, dst):
        calls.append(Path(src).suffix)
        return copy(src, dst)
    monkeypatch.setattr(warm.shutil, 'copy2', small_copy)
    dest = tmp_path/'frozen'
    warm.freeze_inputs(source, exg, dest, 20, 0, ready)
    assert calls == ['.ready']*4
    for path in folder.glob('*.pkl'):
        assert os.path.samefile(path, dest/'20m'/path.name)
    assert os.path.samefile(exg/'exginfo_swap.pkl', dest/'exginfo/exginfo_swap.pkl')


def test_profile_rejects_old_automatic_origin_and_preserves_account_pool(tmp_path):
    from types import SimpleNamespace
    from tools.robust40.cli.server_robust40 import apply_profile
    pool = values()['strategy_pool']
    config = SimpleNamespace(strategy_pool=pool, incremental_lookback_hours=0, data_config={},
                             account_config={'hour_offset':'20m'}, strategy_config={'params': {'runtime': {}}})
    with pytest.raises(ValueError, match='configured date'):
        apply_profile(config, '2025-04-26 00:20', tmp_path, True)
    apply_profile(config, '2026-08-01 00:20', tmp_path, True)
    assert pool[0]['filter_list'][0][1][3] == '2026-08-01'
    assert config.strategy_pool[0]['filter_list'][0][1][3] == '20260801T002000'
