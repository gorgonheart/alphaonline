"""Offline live handoff contracts; no exchange operations."""
import importlib
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def blocked(*args,**kwargs):raise AssertionError('Network forbidden in recovery tests')
    monkeypatch.setattr(socket.socket,'connect',blocked)
    monkeypatch.setattr(socket,'create_connection',blocked)


def test_child_points_are_merged_and_not_automatically_rewound():
    from core.utils.checkpoints import merge_points
    times = pd.date_range('2026-10-01 00:20', periods=28, freq='h')
    old = [dict(time=t, spot={}, swap={}) for t in times[:25]]
    new = [dict(time=t, spot={}, swap={}) for t in times[25:]]
    result = merge_points(old, new, 25)
    assert [r['time'] for r in result] == list(times[-25:])
    assert len(old) == 25
    from core.model.backtest_config import BacktestConfig
    conf = BacktestConfig('offline')
    assert conf.checkpoint_hours == conf.incremental_lookback_hours == 0


def test_revision_detection_catches_volume_and_missing_values():
    from tools.robust40.handoff import first_revision
    idx = pd.date_range('2026-10-01 00:40', periods=168, freq='h')
    old = {'raw/swap/U': pd.DataFrame({'volume': 1., 'close': 5.}, index=idx)}
    new = {k:v.copy() for k,v in old.items()}
    new['raw/swap/U'].loc[idx[-3], 'volume'] = 2
    assert first_revision(old,new) == idx[-3]
    new['raw/swap/U'].loc[idx[-5], 'close'] = np.nan
    assert first_revision(old,new) == idx[-5]
    assert first_revision(old,old) is None
    with pytest.raises(ValueError, match='coverage'):
        first_revision(old, {'raw/swap/U': old['raw/swap/U'].iloc[-2:]})


def test_revision_detection_ignores_category_encoding_but_not_nullable_changes():
    from tools.robust40.handoff import first_revision
    idx=pd.date_range('2026-10-01 00:40',periods=2,freq='h')
    a=pd.DataFrame({'mapping':pd.Categorical(['A','B'],categories=['A','B'])},index=idx)
    b=pd.DataFrame({'mapping':pd.Categorical(['A','B'],categories=['B','A','C'])},index=idx)
    assert first_revision({'raw':a},{'raw':b}) is None
    a=pd.DataFrame({'count':pd.array([1,None],dtype='Int64')},index=idx)
    b=pd.DataFrame({'count':pd.array([1,2],dtype='Int64')},index=idx)
    assert first_revision({'raw':a},{'raw':b})==idx[-1]


def test_section_protocol_captures_bounded_points(tmp_path):
    from core.utils.section_state import run_section
    from test_section_resume_state import candles, oracle
    frame = candles(20)
    params = (17,.25,.6,str(frame.candle_begin_time.min()+pd.Timedelta(hours=17)))
    conf = SimpleNamespace(section_state=True, incremental_lookback_hours=0,
                           checkpoint_hours=25, get_snapshot_folder=lambda: tmp_path)
    module = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    out = run_section(conf,module,frame,params,'chosen')
    np.testing.assert_array_equal(out.chosen,oracle(frame,params))
    saved = pd.read_pickle(tmp_path/'sections/chosen.pkl')
    assert len(saved['checkpoints']) == 25


def test_initial_section_checkpoints_do_not_truncate_full_history():
    from tools.robust40.replay import section_run
    section=importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    times=pd.date_range('2025-01-01 00:40',periods=4200,freq='h')
    frame=pd.DataFrame(dict(candle_begin_time=np.repeat(times,2),symbol=['A','B']*len(times),
        is_spot=0,symbol_spot='',symbol_swap='',是否交易=1,VolumeMaxBias_17=[1.,2.]*len(times)))
    params=(17,.5,.9,str(times[17]))
    # Independent expected holdings: A is permanently the sole best-ranked coin.
    expected=np.tile([1,0],len(times))
    expected[:34]=0
    full=section.signal(frame.copy(),params,'chosen')
    np.testing.assert_array_equal(full.chosen,expected)
    actual,state=section_run(frame,params,'chosen',hours=25)
    np.testing.assert_array_equal(actual.chosen,expected)
    assert state['output'].candle_begin_time.nunique()==4050


def test_replay_transaction_restores_original_on_failure(tmp_path):
    from tools.robust40.handoff import transaction
    live = tmp_path/'account';live.mkdir()
    pd.to_pickle({'complete': True},live/'warmup.pkl')
    (live/'value').write_text('old')
    stage = tmp_path/'stage';stage.mkdir()
    pd.to_pickle({'complete':False},stage/'warmup.pkl')
    (stage/'value').write_text('new')
    with pytest.raises(RuntimeError):
        with transaction(live,stage):
            assert (live/'value').read_text() == 'new'
            raise RuntimeError('calculation failed before targets')
    assert (live/'value').read_text() == 'old'
    assert pd.read_pickle(live/'warmup.pkl')['complete']


def test_completed_calculation_survives_order_failure(tmp_path):
    from tools.robust40.handoff import transaction
    live=tmp_path/'account';live.mkdir();pd.to_pickle({'complete':True},live/'warmup.pkl')
    stage=tmp_path/'stage';stage.mkdir();pd.to_pickle({'complete':False},stage/'warmup.pkl')
    (stage/'value').write_text('new')
    with pytest.raises(RuntimeError):
        with transaction(live,stage):
            pd.to_pickle({'complete':True},live/'warmup.pkl')
            raise RuntimeError('exchange failure after targets validated')
    assert (live/'value').read_text()=='new'
    assert not live.with_name('account.replay-previous').exists()
    assert live.with_name('account.previous').exists()


def test_killed_replay_recovers_complete_snapshot(tmp_path):
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'snapshot/account';live.mkdir(parents=True)
    pd.to_pickle({'complete':False},live/'warmup.pkl')
    backup=live.with_name('account.replay-previous');backup.mkdir()
    pd.to_pickle({'complete':True},backup/'warmup.pkl')
    (backup/'old').write_text('preserved')
    recover_interrupted(live)
    assert (live/'old').read_text()=='preserved'
    assert pd.read_pickle(live/'warmup.pkl')['complete'] is True
    assert not backup.exists()


def test_unreadable_interrupted_backup_allows_regular_reinitialization(tmp_path):
    from tools.robust40.handoff import recover_interrupted
    live=tmp_path/'snapshot/account';live.mkdir(parents=True)
    pd.to_pickle({'complete':False},live/'warmup.pkl')
    backup=live.with_name('account.replay-previous');backup.mkdir()
    (backup/'warmup.pkl').write_bytes(b'broken')
    recover_interrupted(live)
    assert pd.read_pickle(live/'warmup.pkl')['complete'] is False
    assert not backup.exists()
    assert any(p.read_bytes()==b'broken' for p in (tmp_path/'model_replay').rglob('warmup.pkl'))


def test_unrecoverable_revision_marks_rebuild_before_account_calls(tmp_path,monkeypatch):
    import sys
    from tools.robust40.cli import server_robust40 as worker
    from tools.robust40 import server,handoff
    server.save_meta(tmp_path,dict(complete=True))
    monkeypatch.setattr(server,'settings',lambda *a:({'strategy_pool':[]},{}))
    monkeypatch.setattr(server,'location',lambda *a:tmp_path)
    monkeypatch.setattr(server,'inspect',lambda *a:('ready',''))
    def fail(*args):raise ValueError('No checkpoint precedes revision')
    monkeypatch.setattr(handoff,'prepare',fail)
    calls=[]
    monkeypatch.setitem(sys.modules,'core.account_exec',SimpleNamespace(main=lambda:calls.append('account')))
    with pytest.raises(ValueError,match='No checkpoint'):
        worker.trade(Path('fake.py'),1793436000)
    assert calls==[] and pd.read_pickle(tmp_path/'warmup.pkl')['complete'] is False


@pytest.mark.parametrize('minute',list(range(0,60,5)))
def test_arrow_tail_reads_same_values_without_factor_columns(tmp_path,minute):
    import json
    import pyarrow as pa
    from tools.robust40.handoff import read_arrow_tail
    from core.utils.arrow_reader import arrow_snapshot_path
    run=pd.Timestamp(f'2026-10-31 08:{minute:02}',tz='Asia/Hong_Kong').to_pydatetime()
    end=pd.Timestamp(f'2026-10-30 23:{minute:02}')
    frame=pd.DataFrame(dict(candle_begin_time=pd.date_range(end-pd.Timedelta(hours=4049),periods=4050,freq='h'),
                            close=np.arange(4050.),UnneededFactor=np.ones(4050)))
    table=pa.Table.from_pandas(frame,preserve_index=False)
    table=table.replace_schema_metadata({b'symbols':json.dumps(['U']).encode()})
    path=arrow_snapshot_path(tmp_path,'swap',run);path.parent.mkdir()
    with pa.OSFile(str(path),'wb') as f:
        with pa.ipc.new_file(f,table.schema) as w:w.write_table(table)
    actual=read_arrow_tail(tmp_path,'swap',run,end)['U']
    pd.testing.assert_frame_equal(actual.reset_index(drop=True),frame[['candle_begin_time','close']].tail(168).reset_index(drop=True))


def test_common_snapshot_restores_children_s3_and_model(tmp_path,monkeypatch):
    from test_robust40_runtime import context
    from positions.ModelRobust40Strategy import calculate, advance
    from tools.robust40.handoff import restore_snapshot
    from positions.ModelRobust40Strategy import merge_context
    from core.simulator import Simulator
    from tools.robust40 import server
    c=context('2026-10-01 00:40',240)
    c['simulation'].update(spot_c_rate=.001,swap_c_rate=.0006,spot_min_order_limit=10.,
        swap_min_order_limit=5.,min_margin_rate=.05,capital_flow_mode=-1)
    c['close']['swap:BTCUSDT']=100.
    hybrid=pd.DataFrame(.1,index=c['index'],columns=range(14))
    raw={'U':pd.DataFrame(dict(candle_begin_time=c['index'],first_candle_time=c['index'][0],
                             close=100.,quote_volume=1000.,avg_price_1m=100.))}
    result=calculate(c,hybrid,raw,checkpoint_hours=25)
    pool=[dict(name=f'child{i}',re_timing=(True if i in (4,13) else None)) for i in range(14)]
    bundle=dict(context=c,result=result,minute=40,account_identity='account',strategy_pool=pool)
    (tmp_path/'model').mkdir();pd.to_pickle(bundle,tmp_path/'model/state.pkl')
    index=c['index'].tz_localize('UTC')
    # Real factor/selector output: a raw close correction at t changes the
    # stored next_close at t-1 even though the common checkpoint remains t-1.
    from test_full_arrow_initialization import frame as raw_frame
    from test_shared_factor_stage import configuration
    from test_streaming_selection import strategy
    from core import select_coin as select
    from core.utils.factor_data import FactorBlocks
    import polars as pl
    from types import SimpleNamespace
    monkeypatch.setattr(select,'get_snapshot_time',lambda _:None)
    raw_selection=raw_frame(len(index)).assign(candle_begin_time=c['index'],
        first_candle_time=c['index'][0],last_candle_time=c['index'][-1],close=100.)
    factor_conf=configuration()
    stg=strategy();stg.factor_columns=['Bias_3'];stg.long_factor=stg.short_factor='Bias_3'
    stg.period_num=1;stg.offset_list=[0]
    def full_selection(raw):
        _,factors=select.process_candle_df_return_polars(raw.copy(),factor_conf,['Bias_3'],0)
        return select.process_strategy_thread(stg,tmp_path,factor_source=factors.lazy(),return_result=True).to_pandas()
    old_selection=full_selection(raw_selection)
    for i,spec in enumerate(pool):
        child=tmp_path/f'S{i+1}-{spec["name"]}';child.mkdir()
        pd.DataFrame({'candle_begin_time':index}).to_pickle(child/'选币结果.pkl')
        if i==0:old_selection.to_pickle(child/'选币结果.pkl')
        for kind,symbol,ratio in [('spot','S',.1),('swap','U',-.1)]:
            pd.DataFrame(ratio,index=index,columns=[symbol]).to_pickle(child/f'ratio_{kind}.pkl')
        for suffix in (('state0','state1') if spec['re_timing'] else ('state0',)):
            state={k:dict(columns=pd.Index([s]),sim=Simulator(10000.,np.array([1.]),.001,np.zeros(1,dtype=np.int64),5.))
                   for k,s in [('spot','S'),('swap','U')]}
            state.update(simulate_to=index[-1],snapshot_queue=[dict(time=t,**{k:state[k]['sim'].snapshot() for k in ('spot','swap')}) for t in index[-25:]])
            pd.to_pickle(state,child/f'lots_{suffix}.pkl')
            pd.DataFrame({'candle_begin_time':index,'equity':10000.}).to_pickle(child/f'account_{suffix}.pkl')
    (tmp_path/'sections').mkdir()
    points={t:dict(cutoff=t,held=[],ranks=[],params=(17,.1,.9,'2026-01-01'),initialized=True,version=1) for t in c['index'][-25:]}
    section=dict(points[c['index'][-1]],checkpoints=points,output=pd.DataFrame({'candle_begin_time':c['index'],'chosen':1}))
    pd.to_pickle(section,tmp_path/'sections/s3.pkl')
    (tmp_path/'snapshot_time.txt').write_text(str(index[-1]))
    server.complete(tmp_path,'account',{'strategy_pool':pool},c['index'][-1],'2026-01-01 00:40')
    revised=c['index'][-3];time=restore_snapshot(tmp_path,revised,{'strategy_pool':pool})
    assert time==c['index'][-4]
    restored=pd.read_pickle(tmp_path/'model/state.pkl')
    assert restored['context']['index'][-1]==time
    for path in tmp_path.glob('S*/lots_state*.pkl'):
        assert pd.Timestamp(pd.read_pickle(path)['simulate_to']).tz_localize(None)==time
    assert pd.read_pickle(tmp_path/'sections/s3.pkl')['cutoff']==time
    corrected_raw=raw_selection.copy()
    corrected_raw.loc[corrected_raw.candle_begin_time==revised,'close']=102.
    from tools.robust40.handoff import first_revision
    assert first_revision({'raw':raw_selection.set_index('candle_begin_time')},
                          {'raw':corrected_raw.set_index('candle_begin_time')})==revised
    expected_selection=full_selection(corrected_raw)
    _,candidate_factors=select._factor_symbol(corrected_raw.copy(),factor_conf,['Bias_3'],0)
    blocks=FactorBlocks(tmp_path/'replay-factors',hours=19,minute=40)
    blocks.append(candidate_factors,0)
    child=tmp_path/'S1-child0';report=tmp_path/'selection-report';report.mkdir()
    conf=SimpleNamespace(strategy_list=[stg],unified_time='2017-01-01',has_snapshot=True,
        lookback_days=0,get_result_folder=lambda:report,get_snapshot_folder=lambda:child)
    start=time-pd.Timedelta('3h')  # Same snapshot minus holding window as core.
    paths=select.select_factor_blocks([conf],blocks,tmp_path/'replayed-selection',workers=1,start=start)[0][0]
    fresh=pl.concat([pl.read_ipc(p,memory_map=False) for p in paths]).to_pandas()
    monkeypatch.setattr(select,'clean_start',False)
    select.concat_select_results(conf,frames=[fresh])
    actual_selection=pd.read_pickle(child/'选币结果.pkl')
    pd.testing.assert_frame_equal(actual_selection,expected_selection.reset_index(drop=True),check_categorical=False)
    prefix=actual_selection[actual_selection.candle_begin_time<start].reset_index(drop=True)
    pd.testing.assert_frame_equal(prefix,old_selection[old_selection.candle_begin_time<start].reset_index(drop=True),check_categorical=False)
    affected=revised-pd.Timedelta('1h')
    assert actual_selection.loc[actual_selection.candle_begin_time==affected,'next_close'].item()==102.
    stale=actual_selection.copy()
    stale.loc[stale.candle_begin_time==affected,'next_close']=100.
    # A negative control proves this oracle rejects a stale persisted t-1 row.
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(stale,expected_selection.reset_index(drop=True),check_categorical=False)
    blocks.close()
    corrected=dict(c,simulation=dict(c['simulation']),close=c['close'].copy())
    corrected['simulation']['swap_close_p']=c['simulation']['swap_close_p'].copy()
    corrected['simulation']['swap_close_p'][-3,0]=102.
    corrected['close'].loc[revised,'swap:U']=102.
    joined=merge_context(restored['context'],corrected,minimum_overlap=168,warmup=0)
    actual=advance(joined,hybrid,raw,restored['result'],checkpoint_hours=25)
    full=calculate(corrected,hybrid,raw)
    for a,b in zip(actual['virtual_accounts']+[actual['account']],full['virtual_accounts']+[full['account']]):
        np.testing.assert_allclose(a,b,rtol=1e-12,atol=1e-10)
    for k in ('parent','delta'):
        np.testing.assert_allclose(actual[k],full[k],rtol=1e-12,atol=1e-10)
