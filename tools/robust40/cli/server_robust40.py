"""Isolated full initialization or strict-Arrow trading worker (explicit CLI modes).

initialize/compute never import the trading entry point. trade is dispatched only
by startup after ready checks; it retains the account's normal BNB policy.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))

# Before pandas/Polars imports and before fork/spawn: descendants inherit limits.
from tools.robust40.resources import configure_process
configure_process(sys.argv[1] if len(sys.argv) > 1 else '')


def configure_output():
    os.environ['PYTHONIOENCODING']='utf-8'
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',line_buffering=True)


def apply_profile(config, origin, snapshot, initializing):
    import pandas as pd
    from tools.robust40.server import configured_s3, cutoff
    from tools.robust40.initialization_config import LIVE_BARS, MODEL_JOB_NUM, CHECKPOINT_POINTS, INITIALIZATION, RESOURCE_POLICY
    from tools.robust40.warmup import account_minute
    configured,_=configured_s3({'strategy_pool':config.strategy_pool},account_minute(config.account_config))
    if cutoff(origin)!=configured:raise ValueError('S3 saved/profile origin differs from configured date; rebuild required')
    config.strategy_pool=copy.deepcopy(config.strategy_pool)
    def resolve(value):
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):
            parts=list(value)
            if len(parts)>1 and parts[0]=='VolumeMaxBias_进出场阈值分离官方':
                # ISO basic format retains the minute but is legal in Windows
                # factor-cache filenames; core uses the parameter's literal repr.
                param=list(parts[1]);param[3]=pd.Timestamp(origin).strftime('%Y%m%dT%H%M%S');parts[1]=tuple(param)
            else:parts=[resolve(v) for v in parts]
            return tuple(parts) if isinstance(value,tuple) else parts
        return value
    config.strategy_pool=resolve(config.strategy_pool)
    config.input_mode='arrow_full_strict' if initializing else 'arrow_strict'
    config.section_state=True
    config.checkpoint_hours=CHECKPOINT_POINTS
    config.required_history_start=None
    config.get_kline_num=LIVE_BARS
    config.data_config['get_kline_num']=LIVE_BARS
    if config.incremental_lookback_hours:raise ValueError('Server section state requires incremental_lookback_hours=0')
    options=config.strategy_config['params']['runtime']
    options.update(server_init=True,initializing=initializing,checkpoint_hours=CHECKPOINT_POINTS,state_path=str(snapshot/'model/state.pkl'),
                   bootstrap_path=str(snapshot/'model/unused_bootstrap.pkl'))
    # Applied before core imports, including each Windows-spawned factor worker.
    config.job_num=MODEL_JOB_NUM
    config.backtest_job_num=min(MODEL_JOB_NUM, RESOURCE_POLICY[
        'initialization_backtest_workers' if initializing else 'trading_backtest_workers'])
    config.select_coin_inner_threads=1
    from tools.robust40.resources import wait_for_window, phase
    config.compute_gate=wait_for_window if initializing else None
    config.compute_phase=phase if initializing else None
    config.selection_pool_runner=None
    if initializing and INITIALIZATION['selection_block_hours'] > 0:
        from tools.streaming_selection import run_pool
        config.selection_pool_runner=run_pool
        # 共享一块输入，不复制整个策略池；转换/读取仍受MODEL总上限约束。
        config.select_coin_inner_threads=min(2,MODEL_JOB_NUM)
    if initializing:
        config.selection_block_hours=INITIALIZATION['selection_block_hours']
        config.factor_symbol_batch_size=INITIALIZATION['factor_symbol_batch_size']
        config.factor_col_limit=INITIALIZATION['factor_col_limit']
        config.cross_section_chunk_size=INITIALIZATION['cross_section_chunk_size']
        config.is_debug=True;config.is_simulate='debug'
        config.error_webhook_url=''


def configure_worker():
    """Executed again by Windows spawn before any framework calculation imports."""
    spec=json.loads(os.environ['ROBUST40_SERVER_WORKER'])
    os.environ['X3S_TRADING_ACCOUNT']=spec['account']
    import core.utils.path_kit as paths
    paths.PROJECT_ROOT=spec['root']
    import config
    from tools.robust40.server import location
    from tools.robust40.warmup import settings
    values,_=settings(spec['account'])
    snapshot=location(spec['root'],spec['account'],values)
    apply_profile(config,spec['origin'],snapshot,spec['initializing'])
    if spec['initializing']:
        config.bmac_data_path=Path(spec['root'])/'frozen_market'
        config.bmac_exginfo_path=config.bmac_data_path/'exginfo'
        for name,(loader,_) in list(config.data_source_dict.items()):
            frozen=config.bmac_data_path/'extra'/name
            if frozen.exists():config.data_source_dict[name]=(loader,frozen)
        # Freeze the selection universe as well; initialization never queries balances/orders.
        import pandas as pd
        from core.utils import exchange_hub
        rules=pd.read_pickle(config.bmac_exginfo_path/'market_rules.pkl')
        info=rules['market_info']
        dummy=SimpleNamespace(get_market_info=lambda kind:info[kind])
        exchange_hub.get_dummy_client=lambda:dummy
        exchange_hub.get_base_client_class().bstocks_coins=set(rules['bstocks'])
    else:
        # Public selection must not inherit the account cache on fork platforms.
        from core.utils import exchange_hub
        from tools.robust40.market_inputs import public_market_client
        exchange_hub.get_dummy_client=public_market_client
    return config


if os.environ.get('ROBUST40_SERVER_WORKER'):
    configure_output()
    configure_worker()


def prepare(account, workspace, stamp, destination):
    """Preload code, then bind only a complete publication; no scratch before ready."""
    import time
    import pandas as pd
    from tools.robust40 import server
    from tools.robust40.resources import PREFIXES
    from tools.robust40.initialization_config import DATA_READY_WAIT_SECONDS
    values, options = server.settings(account)
    os.environ['X3S_TRADING_ACCOUNT'] = str(account)
    import config
    # Reused by the freezing/controller phase; compute retains its isolated root.
    # No history scan, model fit or exchange request occurs in this preparation.
    from core.model.backtest_config import MultiEquityBacktestConfig
    prepared_conf = MultiEquityBacktestConfig().factory.generate_all_factor_config()
    minute = options['minute']
    folder = config.bmac_data_path/f'{minute}m'
    print(f'{account.stem}: 初始化代码准备完成，等待同轮四ready；未创建正式工作目录', flush=True)
    while not all((folder/f'{prefix}{stamp}.ready').is_file() for prefix in PREFIXES):
        remaining = stamp+DATA_READY_WAIT_SECONDS-time.time()
        if remaining <= 0:
            print(f'{account.stem}: 准备阶段等待ready超时；未修改snapshot', flush=True)
            return False
        time.sleep(min(1., remaining))
    if server.settings(account)[0] != values:
        raise ValueError('Configuration changed during initialization preparation; retry with new configuration')
    from tools.robust40.handoff import recover_interrupted
    # Do not hold this lock while the later compute subprocess acquires it.
    with server.account_lock(destination):
        recover_interrupted(destination)
        status, reason = server.inspect(destination, account.stem, values,
                                        pd.Timestamp(stamp, unit='s')-pd.Timedelta(hours=1))
    if status != 'rebuild':
        print(f'{account.stem}: 准备期间状态已更新，取消本次初始化；status={status}', flush=True)
        return False
    initialize(account, workspace, stamp, destination, prepared_conf=prepared_conf)
    return True


def initialize(account,workspace,stamp,destination,prepared_conf=None):
    import pandas as pd
    from tools.robust40.market_inputs import freeze_inputs, freeze_extra, latest_ready, prepare_public_market
    from tools.robust40 import server
    values,options=server.settings(account)
    minute=options['minute']
    origin,_=server.configured_s3(values,minute)
    if origin>pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1):raise ValueError('S3 configured date is after the last closed candle')
    # Coordinator passes account explicitly; no credential/config files are copied.
    os.environ['X3S_TRADING_ACCOUNT']=str(account)
    import config
    actual,ready=latest_ready(config.bmac_data_path/f'{minute}m',minute)
    if actual!=stamp:raise ValueError('Publication changed before initialization; retry newest round')
    workspace.mkdir(parents=True,exist_ok=False)
    print(f'{account.stem}: 冻结同轮完整行情和外部输入，publication={stamp}',flush=True)
    # Preserve public host settings before the spawned worker imports config;
    # otherwise a Linux deployment could fall back to the Windows default path.
    public={k:getattr(config,k) for k in ('realtime_data_path','factor_col_limit','lookback_days','incremental_lookback_hours','shared_factor_pipeline')}
    (workspace/'config.json').write_text(json.dumps(public),encoding='utf8')
    freeze_inputs(config.bmac_data_path,config.bmac_exginfo_path,workspace/'frozen_market',minute,stamp,ready,
                  full_arrow=True)
    # Freeze auxiliary factor inputs actually requested by this strategy as well.
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.utils.factor_hub import FactorHub
    conf=prepared_conf if prepared_conf is not None else MultiEquityBacktestConfig().factory.generate_all_factor_config()
    extras={key for name in conf.factor_params_dict for key in getattr(FactorHub.get_by_name(name),'extra_data_dict',{})}
    for name in extras:
        loader,path=config.data_source_dict[name]
        source=Path(path)
        if not source.is_dir():raise ValueError(f'Missing factor input directory: {name}')
        freeze_extra(source,workspace/'frozen_market/extra'/name,loader,minute)
    # Same public selection client as core.utils.datatools in the live worker.
    # Account-specific permissions remain exclusively in the execution layer.
    from core.utils.exchange_hub import get_bstocks_coins
    public_client=prepare_public_market()
    rules=dict(market_info={kind:{'symbol_list':info['symbol_list']}
                           for kind,info in public_client.market_info.items()},
               bstocks=sorted(get_bstocks_coins()))
    pd.to_pickle(rules,workspace/'frozen_market/exginfo/market_rules.pkl')
    if server.settings(account)[0]!=values:raise ValueError('Configuration changed while freezing initialization inputs')
    print(f'{account.stem}: 输入和市场规则已冻结，启动完整计算；S3 origin={origin}',flush=True)
    payload=dict(account=str(account),root=str(workspace),origin=str(origin),initializing=True)
    env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable,str(Path(__file__).resolve()),'compute','--account',str(account),
                    '--workspace',str(workspace),'--stamp',str(stamp),'--destination',str(destination)],
                    cwd=ROOT,env=env,check=True)
    server.cleanup_initialization(workspace,ROOT,account,stamp)


def compute(account,workspace,stamp,destination):
    import pandas as pd
    import numpy as np
    import config
    from positions import ModelRobust40Strategy as live
    from tools.robust40 import server
    from tools.robust40.warmup import validate_snapshot, account_minute
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.utils.datatools import snapshot_bmac_exginfo
    values,_=server.settings(account)
    rules=pd.read_pickle(workspace/'frozen_market/exginfo/market_rules.pkl')
    if rules.get('offline_fixture') and destination.is_relative_to(ROOT/'data/snapshot'):
        raise ValueError('Offline market-rule fixtures cannot be installed in active snapshots')
    minute=account_minute(config.account_config)
    snapshot=server.location(workspace,account,values)
    snapshot_bmac_exginfo()
    mef=MultiEquityBacktestConfig()
    acct=SimpleNamespace(name=account.stem,hour_offset=f'{minute}m',hour_offset_minute=minute,
        leverage=config.leverage,rebalance_mode=SimpleNamespace(**config.account_config['rebalance_mode']))
    run=(pd.Timestamp(stamp,unit='s')+pd.Timedelta(hours=config.utc_offset)).to_pydatetime()
    from tools.robust40.resources import phase
    from tools.robust40.initialization_config import INITIALIZATION
    from tools import initialization_pipeline
    inputs=None
    if (config.shared_factor_pipeline or INITIALIZATION.get('data_pipeline',False)) and initialization_pipeline.supported(mef.factory,INITIALIZATION['selection_block_hours']):
        inputs=initialization_pipeline.run(mef,run,minute)
    else:
        with phase('initialization.child_backtests'):
            mef.backtest_strategies(run)
        with phase('initialization.equity_inputs'):
            mef.process_equities(run,acct.hour_offset)
    with phase('initialization.model'):
        if inputs is None:live.prepare(mef,acct,run)
        else:live.prepare(mef,acct,run,inputs=inputs)
    with phase('initialization.target_aggregation'):
        ratios=mef.calc_ratios() if inputs is None else mef.calc_ratios(timing_inputs=inputs['timing'])
        spot,swap=mef.apply_position_limits(*mef.agg_pos_ratio(ratios))
    live.validate_targets(spot,swap,mef.strategy)
    # The on-disk round-trip remains mandatory; release old full matrices before
    # reading the serialized bundle rather than validating the in-memory copy.
    del mef,inputs,ratios,spot,swap
    import gc
    gc.collect()
    bundle=pd.read_pickle(snapshot/'model/state.pkl')
    end=validate_snapshot(snapshot,bundle)
    for p in (snapshot/'sections').glob('*.pkl'):
        if server.cutoff(pd.read_pickle(p)['cutoff'])!=end:raise ValueError('Section cutoff differs')
    if server.settings(account)[0]!=values:raise ValueError('Configuration changed during initialization')
    origin=json.loads(os.environ['ROBUST40_SERVER_WORKER'])['origin']
    server.complete(snapshot,account.stem,values,end,origin)
    server.install(snapshot,destination,destination.parent)
    print(f'Initialized {account.stem}: {bundle["context"]["index"][0]} -> {end}; origin={origin}',flush=True)


def trade(account,stamp):
    from tools.robust40 import telemetry
    with telemetry.round_log(account.stem,stamp):
        return _trade(account,stamp)


def _trade(account,stamp):
    import pandas as pd
    import pickle
    import config
    from contextlib import nullcontext
    from tools.robust40 import handoff
    from tools.robust40 import server
    from tools.robust40 import telemetry
    values,_=server.settings(account)
    destination=server.location(ROOT,account,values)
    server.check_dependencies(values)
    status,reason=server.inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1),server.resume_capacity(values))
    if status!='ready':
        telemetry.record(mode='not_ready')
        raise ValueError(reason)
    # Import errors happen before any calculation and must not invalidate state.
    from core.account_exec import main
    original=pd.read_pickle(destination/'warmup.pkl')
    end=pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1)
    run=(pd.Timestamp(stamp,unit='s')+pd.Timedelta(hours=config.utc_offset)).to_pydatetime()
    try:
        stage,inputs=handoff.prepare(destination,values,run,end.minute,end)
    except (ValueError, OSError, KeyError, EOFError, pickle.UnpicklingError) as exc:
        # Unrecoverable revision/coverage follows the existing initializer route.
        telemetry.record(mode='rebuild_required',recovery_reason=str(exc)[:500])
        server.begin(destination)
        raise
    config.strategy_config['params']['runtime']['replaying']=stage is not None
    with handoff.transaction(destination,stage) if stage is not None else nullcontext():
        server.begin(destination)
        with telemetry.phase('input_write_s'):
            pd.to_pickle(inputs,destination/'model/revision_inputs.pending.pkl')
        # Original BNB and execution flow; never submit historical replay orders.
        sys.argv=[sys.argv[0],str(stamp)]
        with telemetry.phase('account_flow_s'):
            result=main()
        if result is None and stage is None:
            server.save_meta(destination,original)
        if result is None:telemetry.record(account_skipped=True)


def main():
    # initialize has no ROBUST40_SERVER_WORKER profile yet. Configure its output
    # before framework loggers wrap stdout (Windows redirected streams use GBK).
    configure_output()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','initialize','compute','trade'])
    p.add_argument('--account',required=True)
    p.add_argument('--stamp',required=True,type=int)
    p.add_argument('--workspace')
    p.add_argument('--destination')
    a=p.parse_args();account=Path(a.account).resolve()
    if a.mode in ('compute','trade'):
        spec=json.loads(os.environ.get('ROBUST40_SERVER_WORKER','{}'))
        if not spec or Path(spec['account']).resolve()!=account or spec['initializing']!=(a.mode=='compute'):
            raise ValueError('Worker must be launched with its matching isolated profile')
        expected_root=Path(a.workspace).resolve() if a.mode=='compute' else ROOT
        if Path(spec['root']).resolve()!=expected_root or (a.mode=='trade' and spec.get('stamp')!=a.stamp):
            raise ValueError('Worker root/publication differs from its profile')
    from tools.robust40 import server
    from tools.robust40 import telemetry
    if a.mode=='prepare':
        destination=Path(a.destination).resolve()
        with server.account_lock(destination.with_name(destination.name+'.initialization')):
            with telemetry.round_log(account.stem,a.stamp,kind='initialize'):
                completed=prepare(account,Path(a.workspace).resolve(),a.stamp,destination)
                telemetry.record(initialization_completed=completed)
    elif a.mode=='initialize':
        destination=Path(a.destination).resolve()
        with server.account_lock(destination.with_name(destination.name+'.initialization')):
            with telemetry.round_log(account.stem,a.stamp,kind='initialize'):
                initialize(account,Path(a.workspace).resolve(),a.stamp,destination)
                telemetry.record(initialization_completed=True)
    elif a.mode=='compute':
        with server.account_lock(a.destination):
            with telemetry.round_log(account.stem,a.stamp,kind='compute'):
                compute(account,Path(a.workspace).resolve(),a.stamp,Path(a.destination).resolve())
    else:
        with server.account_lock(server.location(ROOT,account,server.settings(account)[0])):
            trade(account,a.stamp)


if __name__=='__main__':main()
