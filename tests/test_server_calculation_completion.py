"""A slow, valid MODEL round completes instead of expiring at offset + 240s."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pyarrow as pa
import pytest

from tools.robust40 import server


@pytest.mark.parametrize('demand_enabled', [False, True])
def test_ready_publication_dispatches_after_four_minutes(tmp_path, monkeypatch, demand_enabled):
    from tools.robust40 import initialization_config
    monkeypatch.setattr(initialization_config, 'ARROW_DEMAND_ENABLED', demand_enabled)
    account = tmp_path/'account.py'
    run = datetime(2026, 10, 30, 22, 40)
    stamp = int(run.timestamp())
    values = dict(strategy_name='test', account=dict(hour_offset='40m'),
                  strategy_pool=[], precompute_factor_configs={})
    monkeypatch.setattr(server, 'enabled', lambda _: True)
    monkeypatch.setattr(server, 'settings', lambda _: (values, dict(minute=40)))
    monkeypatch.setattr(server, 'inspect', lambda *a: ('ready', ''))
    monkeypatch.setattr('time.time', lambda: stamp+301)
    folder = tmp_path/'market/40m'; folder.mkdir(parents=True)
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_'):
        (folder/f'{prefix}{stamp}.ready').touch()
    for kind in ('spot', 'swap'):
        table = pa.table({'close': [1.]})
        with pa.OSFile(str(folder/f'{kind}_dict_{stamp}.arrow'), 'wb') as sink:
            with pa.ipc.new_file(sink, table.schema) as writer:
                writer.write_table(table)
    meta=dict(origin='2025-04-25 00:40')
    if demand_enabled:meta['cutoff']=pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1)
    server.save_meta(server.location(tmp_path, account, values), meta)
    command, env = server.Coordinator(tmp_path).command(account, run, tmp_path/'market')
    assert command is not None and 'trade' in command
    assert env['ROBUST40_SERVER_WORKER']
    requests=list((folder/'.arrow-demand').glob('*/*.json'))
    assert len(requests)==int(demand_enabled)


@pytest.mark.parametrize('delay', [1, 301])
def test_worker_finishes_and_preserves_new_state_after_deadline(tmp_path, monkeypatch, delay):
    from tools.robust40.cli import server_robust40 as worker
    import config
    from tools.robust40 import handoff
    monkeypatch.setattr(config,'strategy_config',{'params':{'runtime':{}}})
    (tmp_path/'model').mkdir()
    monkeypatch.setattr(handoff,'prepare',lambda *a:(None,{}))
    stamp = int(pd.Timestamp('2026-10-30 14:40', tz='UTC').timestamp())
    end = pd.Timestamp('2026-10-30 13:40')
    old = dict(account='account', settings={}, complete=True,
               cutoff=end-pd.Timedelta(hours=1), origin='2025-04-25 00:40')
    server.save_meta(tmp_path, old)
    (tmp_path/'sections').mkdir(); (tmp_path/'sections/s3.pkl').write_bytes(b'state')
    strategy = SimpleNamespace(params={'runtime': dict(server_init=True, execution_stamp=stamp,
        state_path=str(tmp_path/'model/state.pkl'))},
        _robust40_result={'parent': pd.DataFrame(index=[end])})
    monkeypatch.setattr(server, 'settings', lambda _: ({'strategy_pool': []}, {}))
    monkeypatch.setattr(server, 'location', lambda *a: tmp_path)
    monkeypatch.setattr(server, 'inspect', lambda *a: ('ready', ''))
    monkeypatch.setattr('time.time', lambda: stamp+delay)
    monkeypatch.setattr(sys, 'argv', ['worker'])
    calls = []
    def account_main():
        assert pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is False
        # It can start in time and finish late; neither check may abort the round.
        monkeypatch.setattr('time.time', lambda: stamp+360)
        server.finish_live(strategy)
        calls.append('downstream reached')
        return end
    monkeypatch.setitem(sys.modules, 'core.account_exec', SimpleNamespace(main=account_main))
    worker.trade(Path('account.py'), stamp)
    assert calls == ['downstream reached']
    saved = pd.read_pickle(tmp_path/'warmup.pkl')
    assert saved['complete'] is True and saved['cutoff'] == end
    assert saved['origin'] == old['origin']


def test_late_round_does_not_mask_persistence_error(tmp_path, monkeypatch):
    server.save_meta(tmp_path, dict(account='a', settings={}, complete=False, origin='2025-04-25 00:40'))
    strategy = SimpleNamespace(params={'runtime': dict(server_init=True, execution_stamp=0,
        state_path=str(tmp_path/'model/state.pkl'))},
        _robust40_result={'parent': pd.DataFrame(index=[pd.Timestamp('2026-10-30 13:40')])})
    with pytest.raises(ValueError, match='S3 section state missing'):
        server.finish_live(strategy)
    assert pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is False
