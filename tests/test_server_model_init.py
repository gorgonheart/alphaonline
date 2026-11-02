import pandas as pd
from positions import ModelRobust40Strategy as runtime


def test_initialize_without_bootstrap_validates_and_writes(tmp_path,monkeypatch):
    index=pd.date_range('2026-01-01 00:20',periods=3,freq='h')
    context=dict(index=index,minute=20,equities=[])
    result={'parent':pd.DataFrame(1.,index=index,columns=[0])}
    seen=[]
    monkeypatch.setattr(runtime,'validate_context',lambda c:seen.append(c))
    monkeypatch.setattr(runtime,'calc_hybrid_ratio',lambda e,s:pd.DataFrame(index=index))
    monkeypatch.setattr(runtime,'calculate',lambda *a,**k:result)
    path=tmp_path/'model/state.pkl'
    bundle=runtime.initialize_state(path,'account',20,context,{},index[-1],{},[],{})
    assert pd.read_pickle(path)['account_identity']=='account'
    assert bundle['context'] is context and seen==[context]
    assert bundle['result'] is result
