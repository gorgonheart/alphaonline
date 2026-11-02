"""Offline contracts for lazy inputs, single locked read and cross-process cache."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from positions import ModelRobust40Strategy as model


def test_simulator_reuses_cache_in_fresh_processes(tmp_path):
    # Fail before doing expensive compilation when disk caching is not enabled.
    from numba.core.caching import FunctionCache
    assert isinstance(model.start_simulation._cache, FunctionCache)
    probe = tmp_path/'cache_probe.py'
    probe.write_text('''import json
import numpy as np
import pandas as pd
from positions import ModelRobust40Strategy as m
index = pd.date_range('2026-10-01', periods=2, freq='h')
p = np.array([[100.], [101.]])
a = dict(init_capital=10000., leverages=np.ones(2), spot_lot_sizes=np.ones(1), swap_lot_sizes=np.ones(1),
    spot_c_rate=.001, swap_c_rate=.0006, spot_min_order_limit=10., swap_min_order_limit=5., min_margin_rate=.05,
    require_rebalance=np.ones(2,dtype=np.int8), capital_flow_arr=np.zeros(2), capital_flow_mode=-1,
    funding_rates=np.zeros((2,1)), **{k+'_'+f+'_p':p for k in ('spot','swap') for f in ('open','close','vwap1m')})
t = dict(spot=pd.DataFrame(0.,index=index,columns=['S']),swap=pd.DataFrame(.1,index=index,columns=['U']))
r = m.simulate(t,dict(index=index,simulation=a),.4)
q = m.simulate(t,dict(index=index,simulation=a),.4,r.attrs['simulation_state'])
assert r.fee.iloc[1] > 0
print('CACHE_RESULT '+json.dumps(dict(hits=sum(m.start_simulation.stats.cache_hits.values()),
    misses=sum(m.start_simulation.stats.cache_misses.values()), equity=q.equity.tolist())))
''', encoding='utf8')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), NUMBA_CACHE_DIR=str(tmp_path/'cache'), X3S_USE_FILE_LOGGING='0')
    env.pop('ROBUST40_SERVER_WORKER', None)
    outputs = []
    for _ in range(2):
        p = subprocess.run([sys.executable,'-X','utf8','-B',str(probe)], env=env, cwd=root,
                           capture_output=True, text=True, encoding='utf8', timeout=180)
        assert p.returncode == 0, p.stdout+p.stderr
        assert 'Cannot cache' not in p.stderr
        outputs.append(json.loads(next(l.split('CACHE_RESULT ',1)[1] for l in p.stdout.splitlines() if l.startswith('CACHE_RESULT '))))
    assert outputs[0]['misses'] == 2
    assert outputs[1]['hits'] == 2 and outputs[1]['misses'] == 0
    assert outputs[0]['equity'] == outputs[1]['equity']


def test_prepare_reads_state_once_under_lock(tmp_path, monkeypatch):
    path = tmp_path/'state.pkl'
    pd.to_pickle({'saved': True}, path)
    options = dict(state_path=str(path),bootstrap_path=str(tmp_path/'bootstrap.pkl'))
    me = SimpleNamespace(strategy=SimpleNamespace(params={'runtime':options}))
    reads = []
    real = pd.read_pickle
    def read(p, *a, **kw):
        assert Path(str(path)+'.lock').exists()
        reads.append(p)
        return real(p,*a,**kw)
    monkeypatch.setattr(pd,'read_pickle',read)
    def prepared(*a, saved, started):
        assert saved == {'saved': True}
        assert Path(str(path)+'.lock').exists()
        raise RuntimeError('input preparation failed')
    monkeypatch.setattr(model,'_prepare',prepared,raising=False)
    with pytest.raises(RuntimeError,match='input preparation failed'):
        model.prepare(me,None,None)
    assert reads == [path]
    assert not Path(str(path)+'.lock').exists()
    assert real(path) == {'saved': True}


def test_initial_prepare_does_not_read_or_double_lock(tmp_path, monkeypatch):
    path=tmp_path/'state.pkl'
    me=SimpleNamespace(strategy=SimpleNamespace(params={'runtime':dict(initializing=True,
        state_path=str(path),bootstrap_path=str(tmp_path/'unused.pkl'))}))
    def prepared(*a, saved, started):
        assert saved is None and not Path(str(path)+'.lock').exists()
        return 'initializing'
    monkeypatch.setattr(model,'_prepare',prepared,raising=False)
    assert model.prepare(me,None,None)=='initializing'


@pytest.mark.parametrize('minute',range(0,60,5))
def test_raw_projection_keeps_features_but_excludes_factors(tmp_path,minute):
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path
    end=pd.Timestamp(f'2026-10-31 23:{minute:02d}')
    run=(end.tz_localize('UTC')+pd.Timedelta(hours=1)).to_pydatetime()
    index=pd.date_range(end-pd.Timedelta(hours=4055),periods=4058,freq='h',tz='UTC')
    frame=pd.DataFrame(dict(candle_begin_time=index,first_candle_time=pd.Timestamp('2025-01-01',tz='UTC'),
        close=np.arange(len(index))+100.,quote_volume=1000.,funding_fee=.0001,
        taker_buy_quote_asset_volume=400.,avg_price_1m=101.,unused_factor=3.))
    table=pa.Table.from_pandas(frame,preserve_index=False).replace_schema_metadata({b'symbols':b'["U"]'})
    path=arrow_snapshot_path(tmp_path,'swap',run);path.parent.mkdir(parents=True)
    with pa.OSFile(str(path),'wb') as f:
        with pa.ipc.new_file(f,table.schema) as w:w.write_table(table)
    raw=model.load_model_raw(tmp_path,f'{minute}m',run,end,live=True)
    expected=frame[frame.candle_begin_time<=end.tz_localize('UTC')].tail(4050).copy()
    expected['candle_begin_time']=expected.candle_begin_time.dt.tz_localize(None)
    expected['first_candle_time']=expected.first_candle_time.dt.tz_localize(None)
    assert 'unused_factor' not in raw['U']
    assert len(raw['U'])==4050
    np.testing.assert_array_equal(raw['U'].avg_price_1m,expected.avg_price_1m)
    pd.testing.assert_frame_equal(model.coin_features(raw['U']).reset_index(drop=True),
                                  model.coin_features(expected).reset_index(drop=True))


@pytest.mark.parametrize('prefix,loads',[(239,0),(216,1),(192,1)])
def test_lazy_raw_hour_day_quarter_and_gap_match_full(prefix,loads):
    from test_robust40_runtime import context
    # Includes the October quarter boundary and a two-day outage.
    c=context('2026-09-22 00:40',240)
    c['simulation'].update(spot_c_rate=.001,swap_c_rate=.0006,spot_min_order_limit=10.,
        swap_min_order_limit=5.,min_margin_rate=.05,capital_flow_mode=-1)
    c['close']['swap:BTCUSDT']=100.
    hybrid=pd.DataFrame(.1,index=c['index'],columns=range(14))
    raw={'U':pd.DataFrame(dict(candle_begin_time=c['index'],first_candle_time=c['index'][0],
                              close=100.,quote_volume=1000.,avg_price_1m=100.))}
    full=model.calculate(c,hybrid,raw,checkpoint_hours=25)
    prev=model.calculate(model.slice_context(c,c['index'][:prefix]),hybrid.iloc[:prefix],raw,checkpoint_hours=25)
    calls=[]
    def load():calls.append(True);return raw
    resumed=model.advance(c,hybrid,load,prev,checkpoint_hours=25)
    assert len(calls)==loads
    for key in ('parent','base','delta','account'):
        np.testing.assert_allclose(resumed[key],full[key],rtol=1e-12,atol=1e-10)
    for kind in ('spot','swap'):
        pd.testing.assert_frame_equal(resumed['targets'][kind],full['targets'][kind])
    # A duplicate round must never invoke the loader.
    assert model.advance(c,hybrid,lambda:pytest.fail('duplicate read'),resumed) is resumed


def test_full_prepare_single_read_save_and_lock(tmp_path,monkeypatch):
    import config
    from core.utils import datatools
    from test_robust40_runtime import context
    c=context('2026-10-01 00:40',1301)
    path=tmp_path/'state.pkl';original=dict(version=1,minute=40,account_identity='acct',params={},
        simulator_config={},strategy_pool=[{'strategy_list':[]} for _ in range(14)],context=c,
        result={'parent':pd.DataFrame(.1,index=c['index'],columns=range(14))})
    pd.to_pickle(original,path)
    strategy=SimpleNamespace(hold_period='1H',rebalance_cap_step=1,symbol_ratio_limit={},
        params=dict(enable_live_hooks=True,parent={},runtime=dict(server_init=True,state_path=str(path),bootstrap_path=str(tmp_path/'unused.pkl'))))
    me=SimpleNamespace(strategy=strategy,equity_dfs=c['equities'],ratio_dfs=[],
        factory=SimpleNamespace(config_list=[SimpleNamespace(timing=None,strategy_list_raw=[]) for _ in range(14)]))
    account=SimpleNamespace(name='acct',hour_offset_minute=40,hour_offset='40m',leverage=1,
        rebalance_mode=SimpleNamespace(mode='RebRobust40',params=dict(min_order_usdt_ratio=.4,execution_cap=2.45)))
    monkeypatch.setattr(config,'utc_offset',0);monkeypatch.setattr(config,'simulator_config',{})
    monkeypatch.setattr(datatools,'read_bmac_market_pivot',lambda *a:{})
    monkeypatch.setattr(model,'virtual_lot_sizes',lambda *a:{})
    monkeypatch.setattr(model,'build_context',lambda *a:c)
    monkeypatch.setattr(model,'merge_context',lambda *a:c)
    monkeypatch.setattr(model,'expand_result',lambda r,*a:r)
    monkeypatch.setattr(model,'calc_hybrid_ratio',lambda *a:pd.DataFrame())
    monkeypatch.setattr(model,'load_model_raw',lambda *a,**k:pytest.fail('unused raw read'))
    monkeypatch.setattr(model,'advance',lambda c,h,raw,prev,**kw:prev)
    real_read=pd.read_pickle;real_save=model.atomic_save;reads=[]
    def read(p,*a,**k):
        assert Path(str(path)+'.lock').exists();reads.append(p)
        return real_read(p,*a,**k)
    def save(p,b):
        assert Path(str(path)+'.lock').exists()
        real_save(p,b)
    monkeypatch.setattr(pd,'read_pickle',read);monkeypatch.setattr(model,'atomic_save',save)
    model.prepare(me,account,c['index'][-1]+pd.Timedelta(hours=1))
    assert reads==[path] and not Path(str(path)+'.lock').exists()
    pd.testing.assert_frame_equal(strategy._robust40_result['parent'],original['result']['parent'])


def test_raw_initialization_retains_full_history_and_required_age(tmp_path):
    folder=tmp_path/'20m';folder.mkdir()
    index=pd.date_range('2025-01-01 00:20',periods=4100,freq='h')
    frame=pd.DataFrame(dict(candle_begin_time=index,first_candle_time=index[0],close=100.,quote_volume=1000.))
    pd.to_pickle({'U':frame},folder/'swap_dict_batch1.pkl')
    assert len(model.load_model_raw(tmp_path,'20m',None,index[-1],live=False)['U'])==4100
    pd.to_pickle({'U':frame.drop(columns='first_candle_time')},folder/'swap_dict_batch1.pkl')
    with pytest.raises(ValueError,match='first_candle_time'):
        model.load_model_raw(tmp_path,'20m',None,index[-1],live=False)
