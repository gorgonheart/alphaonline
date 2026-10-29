import numpy as np
import pandas as pd
import pytest
from tools.local_risk_model import validate,merge_history,multipliers,atomic_save,update,COLUMNS,VERSION

def frame(n=3000):
    rng=np.random.default_rng(71)
    return pd.DataFrame(np.exp(np.cumsum(rng.normal(0,.001,(n,8)),axis=0)),
        index=pd.date_range('2021-01-01',periods=n,freq='h',tz='UTC'),columns=COLUMNS)

def test_splice_scale_and_revision():
    f=frame();old=f.iloc[:2600];new=f.iloc[500:]*3
    np.testing.assert_allclose(merge_history(old,new),f,rtol=1e-14)
    new=new.copy();new.iloc[-600,0]*=1.01
    with pytest.raises(ValueError,match='returns changed'):merge_history(old,new)

def test_gaps_and_duplicate():
    f=frame()
    with pytest.raises(ValueError,match='gap'):validate(f.drop(f.index[40]))
    with pytest.raises(ValueError,match='Duplicate'):validate(pd.concat([f,f.iloc[-1:]]))

def test_smoothing_restart_and_expiry():
    f=frame(400)
    state=dict(origin=str(f.index[0]),next_decision=400,
        predictions=[dict(t=168,target=.8),dict(t=336,target=1.)])
    out=multipliers(state,f.index)
    expected=[];cur=1.
    for t in range(400):
        target=1 if t<168 or t>=336 else .8
        cur+=np.clip(target-cur,-.01,.01);expected.append(cur)
    np.testing.assert_allclose(out,expected,atol=2e-15)
    np.testing.assert_array_equal(multipliers(state,f.index[250:]),out[250:])
    with pytest.raises(ValueError,match='expired'):multipliers(state,[f.index[0]+pd.Timedelta(hours=400)])

def test_transaction_error_preserves_state(tmp_path):
    f=frame();path=tmp_path/'state.pkl'
    s=dict(identity='test',version=VERSION,origin=str(f.index[0]),next_decision=9999,model=None,predictions=[])
    atomic_save(path,dict(state=s,history=f.iloc[:2600]));before=path.read_bytes()
    bad=f.iloc[500:].copy();bad.iloc[-600,0]*=1.1
    with pytest.raises(ValueError):update(path,bad,'test')
    assert path.read_bytes()==before
    assert not path.with_suffix('.pkl.lock').exists()

def test_resume_predictions_equal_full(tmp_path):
    from tools.local_risk_model import isolated_advance,WARM
    f=frame(8100)
    s=dict(origin=str(f.index[0]),activation='2030-01-01T00:00:00+00:00',
        next_decision=WARM,model=None,predictions=[])
    full=isolated_advance(s,f,tmp_path)
    partial=isolated_advance(s,f.iloc[:7500],tmp_path);resumed=isolated_advance(partial,f,tmp_path)
    np.testing.assert_array_equal([r['prediction'] for r in full['predictions']],
                                  [r['prediction'] for r in resumed['predictions']])
    assert resumed['training_label_end']<resumed['last_train']

def test_wrapper_only_s12_and_shadow(monkeypatch,tmp_path):
    import sys,types,importlib.util
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'positions/HybridLocalRiskStrategy.py'
    spec=importlib.util.spec_from_file_location('local_wrapper_test',source)
    wrapper=importlib.util.module_from_spec(spec);spec.loader.exec_module(wrapper)
    f=frame();base=pd.DataFrame(.2,index=f.index,columns=range(14))
    fake=types.ModuleType('positions.HybridRotationDriftStrategy');fake.calc_ratio=lambda *a:base.copy()
    package=types.ModuleType('positions');package.HybridRotationDriftStrategy=fake
    monkeypatch.setitem(sys.modules,'positions',package)
    monkeypatch.setattr(wrapper,'update',lambda *a:{})
    monkeypatch.setattr(wrapper,'multipliers',lambda s,i:np.full(len(i),.8))
    names=[f'S{i+1}' for i in range(14)];names[11]='S12-回撤阈值空头'
    cfg=types.SimpleNamespace(hold_period='1H',rebalance_cap_step=1,symbol_ratio_limit={},
        strategy_cfg_list=[types.SimpleNamespace(name=n) for n in names],
        params=dict(local_mode='active',local_expected_names=names,local_state_path=str(tmp_path/'s.pkl'),local_identity='test'))
    dfs=[pd.DataFrame(dict(candle_begin_time=f.index,close=f.iloc[:,0])) for _ in range(14)]
    actual=wrapper.calc_ratio(dfs,cfg)
    np.testing.assert_array_equal(actual.drop(columns=11),base.drop(columns=11))
    np.testing.assert_allclose(actual[11],base[11]*.8)
    cfg.params['local_mode']='shadow'
    pd.testing.assert_frame_equal(wrapper.calc_ratio(dfs,cfg),base)
