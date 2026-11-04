"""No live services: exercise initialization orchestration with frozen input stubs."""
import json
from types import SimpleNamespace
import pandas as pd


def test_initialize_cli_logs_unicode_when_output_is_redirected(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    probe = tmp_path/'probe.py'
    probe.write_text('''import sys
from tools.robust40.cli import server_robust40 as worker
def initialize(*args):
    from core.utils.log_kit import logger
    logger.debug('ℹ️ 初始化中文正常')
    print('stderr：初始化正常', file=sys.stderr, flush=True)
worker.initialize = initialize
from pathlib import Path
temporary=Path(__file__).resolve().parent
sys.argv = ['server_robust40.py', 'initialize', '--account', 'unused.py',
            '--workspace', str(temporary/'work'), '--destination', str(temporary/'snapshots/acct'), '--stamp', '1']
worker.main()
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING='gbk', X3S_USE_FILE_LOGGING='0')
    env.pop('ROBUST40_SERVER_WORKER', None)
    result = subprocess.run([sys.executable, str(probe)], cwd=root, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert 'ℹ️ 初始化中文正常' in result.stdout.decode('utf-8')
    assert 'stderr：初始化正常' in result.stderr.decode('utf-8')


def test_initializer_uses_only_market_calls_and_preserves_host_settings(tmp_path,monkeypatch):
    from tools.robust40.cli import server_robust40 as worker
    from tools.robust40 import market_inputs as warm
    from tools.robust40 import server
    import config
    import core.model.backtest_config as backtests
    import core.model.account_config as accounts
    import core.utils.exchange_hub as exchanges
    import subprocess
    account=tmp_path/'acct.py';account.write_text('')
    values=dict(strategy_pool=[dict(strategy_list=[dict(filter_list=[
        ('VolumeMaxBias_进出场阈值分离官方',(720,.1,.9,'2026-08-01'),'val:==1')])])])
    monkeypatch.setattr(server,'settings',lambda p:(values,{'minute':40}))
    stamp=int(pd.Timestamp('2026-10-30 09:40',tz='UTC').timestamp())
    monkeypatch.setattr(warm,'latest_ready',lambda *a:(stamp,1))
    def freeze(source,exg,destination,*args,**kwargs):
        assert kwargs == {'full_arrow':True}
        (destination/'40m').mkdir(parents=True)
        (destination/'exginfo').mkdir()
        frame=pd.DataFrame({'candle_begin_time':pd.date_range(end='2026-10-30 08:40',periods=14064,freq='h',tz='UTC')})
        pd.to_pickle({'BTCUSDT':frame},destination/'40m/swap_dict_batch1.pkl')
    monkeypatch.setattr(warm,'freeze_inputs',freeze)
    conf=SimpleNamespace(factor_params_dict={})
    monkeypatch.setattr(backtests,'MultiEquityBacktestConfig',lambda:SimpleNamespace(factory=SimpleNamespace(generate_all_factor_config=lambda:conf)))
    calls=[]
    bn=SimpleNamespace(market_info={kind:{'symbol_list':['AIUSDT'], 'min_qty':{'AIUSDT':1},
        'full_symbol_list':['AIUSDT'], 'price_precision':{}, 'min_notional':{}, 'last_update':1}
        for kind in ('spot','swap')},fetch_market_info=lambda k:calls.append(k))
    # Keep the account's bStocks refresh, but never use its restricted market list.
    restricted=SimpleNamespace(fetch_bstocks_coins=lambda:calls.append('bstocks'),
                               filter_bstocks=True,_bstocks_last_update=1)
    monkeypatch.setattr(accounts,'AccountConfig',lambda *a,**k:SimpleNamespace(
        bn=restricted,init_exchange=lambda c:calls.append('init_exchange')))
    monkeypatch.setattr(warm,'public_market_client',lambda:bn)
    monkeypatch.setattr(exchanges,'get_bstocks_coins',lambda:set())
    monkeypatch.setattr(config,'realtime_data_path','/srv/alpha/data')
    commands=[]
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:commands.append((a,k)))
    workspace=tmp_path/'scratch'
    with monkeypatch.context() as no_raw_prepass:
        def forbidden_read(*args, **kwargs):
            raise AssertionError('Initialization date must not unpickle raw history')
        no_raw_prepass.setattr(pd,'read_pickle',forbidden_read)
        worker.initialize(account,workspace,stamp,tmp_path/'snapshot/acct')
    assert calls==['init_exchange','bstocks','spot','swap']
    public=json.loads((workspace/'config.json').read_text())
    assert public['realtime_data_path']=='/srv/alpha/data'
    assert 'account_config' not in public and 'error_webhook_url' not in public
    rules=pd.read_pickle(workspace/'frozen_market/exginfo/market_rules.pkl')
    assert rules['bstocks']==[]
    assert rules['market_info']['spot']['symbol_list']==['AIUSDT']
    assert all(set(info)=={'symbol_list'} for info in rules['market_info'].values())
    assert commands[0][0][0][2]=='compute'
    profile=json.loads(commands[0][1]['env']['ROBUST40_SERVER_WORKER'])
    assert profile['initializing'] and profile['root']==str(workspace)
    assert profile['origin'] == '2026-08-01 00:40:00'


def test_public_client_does_not_inherit_or_mutate_account_cache(monkeypatch):
    from tools.robust40.market_inputs import public_market_client
    from core.utils import exchange_hub
    from core.binance.base_client import BinanceClient
    import time
    restricted={'spot':dict(symbol_list=['MARGINUSDT'],last_update=int(time.time()))}
    class Public:
        market_info=restricted
        get_market_info=BinanceClient.get_market_info
        @classmethod
        def get_dummy_client(cls):return cls()
        def fetch_market_info(self,kind,quote):
            self.market_info[kind]=dict(symbol_list=['MARGINUSDT','AIUSDT'],last_update=int(time.time()))
    monkeypatch.setattr(exchange_hub,'get_base_client_class',lambda:Public)
    public=public_market_client()
    assert public.get_market_info('spot')['symbol_list']==['MARGINUSDT','AIUSDT']
    assert restricted['spot']['symbol_list']==['MARGINUSDT']
    assert public.market_info is not restricted
