"""Offline contracts shared by initialization, continuation and replay callers."""
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError('Shared computation tests must stay offline')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)


def test_replay_effective_start_covers_next_close():
    from tools.robust40.handoff import first_revision
    from tools.robust40.replay import common_before
    index = pd.date_range('2026-10-01 00:20', periods=10, freq='h')
    raw = pd.DataFrame({'close': range(10, 20)}, index=index)
    corrected = raw.copy()
    corrected.loc[index[7], 'close'] = 99
    # A future-valued output needs a different boundary than its raw input.
    derived = raw.rename(columns={'close': 'next_close'}).shift(-1)
    changed = corrected.rename(columns={'close': 'next_close'}).shift(-1)
    assert first_revision({'raw': raw}, {'raw': corrected}) == index[7]
    affected = first_revision({'factor': derived}, {'factor': changed})
    assert affected == index[6]
    point = common_before([index, index[:-1]], affected)
    assert point == index[5]
    replayed = pd.concat([derived.loc[:point], changed.loc[changed.index > point]])
    pd.testing.assert_frame_equal(replayed, changed)


def test_trade_commit_survives_exchange_failure(tmp_path):
    from tools.robust40.handoff import transaction
    original, stage = tmp_path/'live', tmp_path/'stage'
    original.mkdir(); stage.mkdir()
    old, new = pd.Timestamp('2026-10-01 00:20'), pd.Timestamp('2026-10-01 01:20')
    pd.to_pickle(dict(complete=True, cutoff=old), original/'warmup.pkl')
    pd.to_pickle(dict(complete=False, cutoff=old), stage/'warmup.pkl')
    with pytest.raises(RuntimeError, match='exchange'):
        with transaction(original, stage):
            pd.to_pickle(dict(complete=True, cutoff=new), original/'warmup.pkl')
            raise RuntimeError('exchange failure after calculation commit')
    assert pd.read_pickle(original/'warmup.pkl')['cutoff'] == new
    assert pd.read_pickle(tmp_path/'live.previous/warmup.pkl')['cutoff'] == old


def test_seven_hour_gap_and_rebuild_boundary(tmp_path, monkeypatch):
    from tools.robust40 import server
    end = pd.Timestamp('2026-10-01 07:20')
    previous = end - pd.Timedelta(hours=7)
    monkeypatch.setattr(server, 'required_files', lambda values: ['snapshot_time.txt'])
    (tmp_path/'snapshot_time.txt').write_text(str(previous))
    (tmp_path/'sections').mkdir()
    (tmp_path/'sections/S3.pkl').write_bytes(b'existing state')
    server.save_meta(tmp_path, dict(account='20m-example', settings={}, complete=True,
        checkpoint_version=1, cutoff=previous, origin=str(previous-pd.Timedelta(days=1)),
        sections=['sections/S3.pkl']))
    assert server.inspect(tmp_path, '20m-example', {}, end, max_gap=7)[0] == 'ready'
    status, reason = server.inspect(tmp_path, '20m-example', {}, end, max_gap=6)
    assert status == 'rebuild' and 'coverage' in reason
    assert pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is True
