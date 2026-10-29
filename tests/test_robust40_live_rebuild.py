from types import SimpleNamespace

import pandas as pd
import pytest

from tools.rebuild_robust40_live_baseline import read_children


def test_rebuild_rejects_children_from_old_blacklist(tmp_path):
    folder = tmp_path/'children'/'S1-test'
    folder.mkdir(parents=True)
    pd.to_pickle(SimpleNamespace(black_list=['BTCUSDT'], white_list=[]), folder/'config.pkl')
    with pytest.raises(ValueError, match='saved black_list differs'):
        read_children(tmp_path/'children', tmp_path/'snapshots', [{'name':'test'}], 20,
                      dict(black_list=[], white_list=[]))


def test_rebuild_reads_same_offset_outputs_and_rejects_mixed_ends(tmp_path):
    for i, name in enumerate(['one','two'], 1):
        folder = tmp_path/'children'/f'S{i}-{name}'
        snapshot = tmp_path/'snapshots'/f'S{i}-{name}'
        folder.mkdir(parents=True);snapshot.mkdir(parents=True)
        pd.to_pickle(SimpleNamespace(black_list=['BADUSDT'], white_list=[]), folder/'config.pkl')
        idx = pd.date_range('2026-10-01 00:20', periods=4, freq='h')
        pd.DataFrame(dict(candle_begin_time=idx, close=100.)).to_pickle(folder/'equity_df.pkl')
        for kind in ('spot','swap'):
            pd.DataFrame({'U': .1}, index=idx).to_pickle(snapshot/f'ratio_{kind}.pkl')
    args = (tmp_path/'children', tmp_path/'snapshots', [{'name':'one'}, {'name':'two'}], 20,
            dict(black_list=['BAD-USDT'], white_list=[]))
    equities, ratios = read_children(*args)
    assert len(equities) == len(ratios) == 2
    p = tmp_path/'children'/'S2-two'/'equity_df.pkl'
    df = pd.read_pickle(p);df.iloc[:-1].to_pickle(p)
    with pytest.raises(ValueError, match='same closed bar'):read_children(*args)
