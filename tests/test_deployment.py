"""Portable deployment contracts; temporary data only, no exchange or service start."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_account_credentials_overlay_does_not_change_strategy_settings(tmp_path):
    from tools.deployment import account_credentials
    path=tmp_path/'accounts.local.json'
    path.write_text(json.dumps({'account':{'apiKey':'test-key','secret':'test-secret'}}))
    original={'apiKey':'','secret':'','hour_offset':'20m','buy_bnb_value':11}
    actual=account_credentials(path,'account',original)
    assert actual==dict(original,apiKey='test-key',secret='test-secret')
    assert original['apiKey']==''
    assert account_credentials(path,'other',original)==original
    assert account_credentials(tmp_path/'absent','account',original)==original


def test_local_credentials_cannot_override_trading_parameters(tmp_path):
    from tools.deployment import account_credentials
    path=tmp_path/'accounts.local.json'
    path.write_text(json.dumps({'account':{'leverage':100}}))
    with pytest.raises(ValueError,match='credentials'):
        account_credentials(path,'account',{})


def test_config_applies_machine_proxy_and_account_credentials(tmp_path):
    data=tmp_path/'market';(data/'preprocess_1h_resample').mkdir(parents=True);(data/'exginfo').mkdir()
    (tmp_path/'config.json').write_text(json.dumps({'realtime_data_path':str(data),'proxy':{},
        'error_webhook_url':'','is_simulate':'dry_run'}))
    (tmp_path/'accounts.local.json').write_text(json.dumps({'account':{'apiKey':'local-key','secret':'local-secret'}}))
    account=tmp_path/'account.py'
    account.write_text("account_config={'apiKey':'','secret':'','hour_offset':'40m','buy_bnb_value':11}\n")
    script='''import sys
from pathlib import Path
import core.utils.path_kit as p
p.PROJECT_ROOT=sys.argv[1]
import config
assert config.proxy==config.exchange_basic_config['proxies']=={}
assert config.account_config['apiKey']=='local-key'
assert config.account_config['secret']=='local-secret'
assert config.account_config['hour_offset']=='40m'
assert config.account_config['buy_bnb_value']==11
assert config.is_simulate=='dry_run'
'''
    env=dict(os.environ,X3S_TRADING_ACCOUNT=str(account),PYTHONPATH=str(ROOT),X3S_USE_FILE_LOGGING='0')
    env.pop('ROBUST40_SERVER_WORKER',None)
    result=subprocess.run([sys.executable,'-X','utf8','-B','-c',script,str(tmp_path)],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('column_limit',[1,80])
def test_timeseries_pool_explicit_spawn_preserves_values(tmp_path,monkeypatch,column_limit):
    import core.select_coin as select
    from concurrent.futures import ProcessPoolExecutor
    methods=[]
    def executor(*args,**kwargs):
        context=kwargs.get('mp_context')
        methods.append(context.get_start_method() if context else None)
        return ProcessPoolExecutor(*args,**kwargs)
    monkeypatch.setattr(select,'ProcessPoolExecutor',executor)
    monkeypatch.setattr(select,'job_num',2)
    monkeypatch.setattr(select,'factor_col_limit',column_limit)
    monkeypatch.setattr(select,'get_snapshot_time',lambda _:None)
    def path(*parts,**kwargs):
        p=tmp_path.joinpath(*parts);p.parent.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(select,'get_file_path',path)
    index=pd.date_range('2026-01-01',periods=6,freq='h')
    frames=[pd.DataFrame(dict(candle_begin_time=index,first_candle_time=index[0],symbol=s,
        is_spot=0,close=np.arange(6,dtype=float)+10,avg_price_1m=10.,symbol_spot='',symbol_swap=s,
        **{'是否交易':1})) for s in ('AAUSDT','BBUSDT')]
    pd.to_pickle(frames,path('data','cache','all_candle_df_list.pkl'))
    conf=SimpleNamespace(name='deployment_probe',factor_col_name_list=['Bias (2)_2','Bias (2)_3'],
        factor_params_dict={'Bias (2)':[2,3]},precompute_factor_configs={},min_kline_num=0,
        has_section_factor=False,strategy_list=[],is_day_period=False,avg_price_col='avg_price_1m',
        section_depend_factor_col_name_list=[],max_hold_period='1H',lookback_days=0,incremental_lookback_hours=0)
    select.calc_factors(conf)
    assert methods==['spawn']*(2 if column_limit==1 else 1)
    for n in (2,3):
        output=pd.read_parquet(path('data','cache',f'factor_Bias (2)_{n}.parquet'))
        expected=np.tile((frames[0].close/frames[0].close.rolling(n,min_periods=1).mean()-1).to_numpy(),2)
        np.testing.assert_array_equal(output[f'Bias (2)_{n}'],expected)
