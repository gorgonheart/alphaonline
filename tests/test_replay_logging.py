"""Offline observability: logging must not alter recovery decisions."""
import json
import pandas as pd
import pytest


def capture(monkeypatch):
    from tools.robust40 import telemetry
    messages=[]
    monkeypatch.setattr(telemetry, '_write', messages.append)
    return telemetry, messages


def test_round_records_commit_separately_from_exchange_failure(monkeypatch):
    t, messages=capture(monkeypatch)
    with pytest.raises(ValueError):
        with t.round_log('account', 1793436000):
            with t.phase('compare_s'):pass
            t.commit()
            raise ValueError('exchange failed')
    events=[json.loads(m.split('MODEL_REPLAY ',1)[1]) for m in messages]
    assert [e['event'] for e in events]==['start','calculation_committed','end']
    end=events[-1]
    assert end['status']=='error' and end['committed'] is True
    assert end['compare_s']>=0 and end['total_s']>=end['calculation_s']
    assert end['error_type']=='ValueError' and 'exchange failed' not in messages[-1]
    assert t.active() is False


@pytest.mark.parametrize('changed', [False, True])
def test_prepare_logs_read_compare_and_replay_or_rebuild(tmp_path,monkeypatch,changed):
    from tools.robust40 import handoff
    t,messages=capture(monkeypatch)
    idx=pd.date_range('2026-10-31 00:40',periods=3,freq='h')
    old={'raw/swap/U':pd.DataFrame({'close':[1.,2.,3.]},index=idx)}
    (tmp_path/'model').mkdir();pd.to_pickle(old,tmp_path/'model/revision_inputs.pkl')
    pd.to_pickle({'complete':True},tmp_path/'warmup.pkl')
    new={k:v.copy() for k,v in old.items()}
    if changed:new['raw/swap/U'].iloc[-1,0]=4.
    monkeypatch.setattr(handoff,'read_inputs',lambda *a,**kw:new)
    monkeypatch.setattr(handoff,'restore_snapshot',lambda *a:idx[-2])
    with t.round_log('account',1793436000):
        stage,_=handoff.prepare(tmp_path,{},None,40,idx[-1])
    end=json.loads(messages[-1].split('MODEL_REPLAY ',1)[1])
    assert end['mode']==('replay' if changed else 'incremental')
    assert all(end[k]>=0 for k in ('old_read_s','input_read_s','compare_s','prepare_s'))
    if changed:
        assert stage is not None and end['replay_hours']==1
        assert end['changed_cells']==1 and end['changed_fields']==['close']
        assert end['copy_s']>=0 and end['restore_s']>=0
    else:assert stage is None


def test_prepare_failure_records_stage_without_swallowing(tmp_path,monkeypatch):
    from tools.robust40 import handoff
    t,messages=capture(monkeypatch)
    with pytest.raises(FileNotFoundError):
        with t.round_log('account',1793436000):
            handoff.prepare(tmp_path,{},None,40,pd.Timestamp('2026-10-31 02:40'))
    end=json.loads(messages[-1].split('MODEL_REPLAY ',1)[1])
    assert end['failed_phase']=='old_read_s' and end['committed'] is False


def test_training_log_matches_actual_fit_calls(monkeypatch):
    import numpy as np
    from positions.ModelRobust40Strategy import quarterly_predictions
    from positions.ModelRobust40Strategy import MODEL_FEATURES
    t,messages=capture(monkeypatch)
    days=pd.date_range('2025-01-01','2026-04-03',freq='D')
    obs=pd.DataFrame(np.random.default_rng(11).normal(size=(len(days),len(MODEL_FEATURES))),columns=MODEL_FEATURES)
    obs['time']=days;obs['symbol']='U';obs['forward24']=np.where(np.arange(len(days))%2,-.1,.1)
    pred,models,_=quarterly_predictions(obs,days[-1])
    assert messages==[]  # Ordinary/offline callers do not create logs or files.
    with t.round_log('account',1793436000):
        actual,_,_=quarterly_predictions(obs,days[-1],previous=dict(predictions=pred,models=models))
    pd.testing.assert_frame_equal(actual,pred)
    end=json.loads(messages[-1].split('MODEL_REPLAY ',1)[1])
    assert end['trained_quarters']==0 and end['reused_quarters']==len(models)
    assert end['scoring_calls']==1 and end['fit_s']==0 and end['scoring_s']>=0
    assert set(end['quarter_actions'].values())=={'reused'}


def test_logger_failure_does_not_break_calculation(monkeypatch):
    from tools.robust40 import telemetry as t
    def fail(*a):raise OSError('log full')
    monkeypatch.setattr(t,'_write',fail)
    with t.round_log('account',1):t.commit()
    assert not t.active()


def test_summary_deduplicates_logs_and_separates_unfinished(monkeypatch):
    from tools.robust40.cli.summarize_replay_logs import summarize
    t,messages=capture(monkeypatch)
    with t.round_log('account',1793436000):
        t.record(mode='replay')
        t.commit()
    result=summarize(messages+messages)
    assert result['groups'][0]['attempts']==1 and result['groups'][0]['committed']==1
    assert result['unfinished']==[]
    incomplete=summarize(messages[:-1])
    assert incomplete['groups']==[] and incomplete['unfinished'][0]['committed'] is True
    assert summarize(messages,1793436001)['groups']==[]
