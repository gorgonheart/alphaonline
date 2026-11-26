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
    minute=account_minute(config.account_config)
    configured,_=configured_s3({'strategy_pool':config.strategy_pool},minute)
    try:
        parsed=cutoff(origin)
    except (TypeError,ValueError) as exc:
        raise ValueError('S3 saved/profile origin differs from configured date; rebuild required') from exc
    if (pd.isna(parsed) or parsed<configured or parsed.hour!=0 or parsed.minute!=minute
            or parsed.second!=0 or parsed.microsecond!=0 or parsed.nanosecond!=0):
        raise ValueError('S3 saved/profile origin differs from configured date; rebuild required')
    config.strategy_pool=copy.deepcopy(config.strategy_pool)
    def resolve(value):
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):
            parts=list(value)
            if len(parts)>1 and parts[0]=='VolumeMaxBias_进出场阈值分离官方':
                # ISO basic format retains the minute but is legal in Windows
                # factor-cache filenames; core uses the parameter's literal repr.
                param=list(parts[1]);param[3]=parsed.strftime('%Y%m%dT%H%M%S');parts[1]=tuple(param)
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
        recover_interrupted(destination, True)
        status, reason = server.inspect(destination, account.stem, values,
                                        pd.Timestamp(stamp, unit='s')-pd.Timedelta(hours=1))
    if status != 'rebuild':
        print(f'{account.stem}: 准备期间状态已更新，取消本次初始化；status={status}', flush=True)
        return False
    initialize(account, workspace, stamp, destination, prepared_conf=prepared_conf)
    return True


def check_upstream_history_trim_date(bmac_data_path, minute, stamp, saved_trim):
    """Compare spot/swap Arrow header history_trim_date against saved snapshot value.

    Raises ValueError if spot and swap headers disagree (partial publish/crash).
    Returns (need_rebuild, saved_trim, upstream_trim); only advances strictly later
    than saved_trim trigger rebuild.
    """
    import pandas as pd
    from core.utils.arrow_reader import arrow_snapshot_path, read_arrow_history_trim_date
    from tools.robust40.server import cutoff
    run_time = pd.Timestamp(int(stamp), unit='s', tz='UTC').to_pydatetime()
    spot_path = arrow_snapshot_path(bmac_data_path, 'spot', run_time)
    swap_path = arrow_snapshot_path(bmac_data_path, 'swap', run_time)
    if run_time.minute != int(minute):
        folder = Path(bmac_data_path) / f'{int(minute)}m'
        spot_path = folder / f'spot_dict_{int(stamp)}.arrow'
        swap_path = folder / f'swap_dict_{int(stamp)}.arrow'
    if not spot_path.exists() or not swap_path.exists():
        return False, saved_trim, None
    spot_trim = read_arrow_history_trim_date(spot_path)
    swap_trim = read_arrow_history_trim_date(swap_path)
    if spot_trim != swap_trim:
        raise ValueError(
            f'spot/swap Arrow history_trim_date mismatch: spot={spot_trim!r}, swap={swap_trim!r}'
        )
    upstream_trim = spot_trim
    if upstream_trim is None or upstream_trim == saved_trim:
        return False, saved_trim, upstream_trim
    if saved_trim is None or cutoff(upstream_trim) > cutoff(saved_trim):
        return True, saved_trim, upstream_trim
    return False, saved_trim, upstream_trim


def initialize(account,workspace,stamp,destination,prepared_conf=None):
    from tools.robust40 import server, lifecycle
    # The caller already owns initialization lock. Snapshot lock excludes an
    # orphan compute process before reclaiming any old workspace.
    with server.account_lock(destination):
        lifecycle.sweep_initialization(ROOT,account,stamp)
    try:
        return _initialize(account,workspace,stamp,destination,prepared_conf)
    finally:
        lifecycle.finish_initialization(workspace,ROOT,account,stamp)


def _initialize(account,workspace,stamp,destination,prepared_conf=None):
    import pandas as pd
    from tools.robust40.market_inputs import freeze_inputs, freeze_extra, latest_ready, prepare_public_market, PublicationUnavailable
    from tools.robust40 import server
    from tools.robust40.initialization_config import INITIALIZATION
    values,options=server.settings(account)
    minute=options['minute']
    configured_origin,n=server.configured_s3(values,minute)
    end_bar=pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1)
    if configured_origin>end_bar:raise ValueError('S3 configured date is after the last closed candle')
    # Coordinator passes account explicitly; no credential/config files are copied.
    os.environ['X3S_TRADING_ACCOUNT']=str(account)
    import config
    from tools.robust40.arrow_demand import set_demand, release_demand
    set_demand(config.bmac_data_path,ROOT,account,minute,True,stamp=stamp)
    actual,ready=latest_ready(config.bmac_data_path/f'{minute}m',minute)
    if actual!=stamp:raise PublicationUnavailable('Publication changed before initialization; retry newest round')
    workspace.mkdir(parents=True,exist_ok=False)
    print(f'{account.stem}: 冻结同轮完整行情和外部输入，publication={stamp}',flush=True)
    # Preserve public host settings before the spawned worker imports config;
    # otherwise a Linux deployment could fall back to the Windows default path.
    public={k:getattr(config,k) for k in ('realtime_data_path','factor_col_limit','lookback_days','incremental_lookback_hours','shared_factor_pipeline')}
    (workspace/'config.json').write_text(json.dumps(public),encoding='utf8')
    freeze_inputs(config.bmac_data_path,config.bmac_exginfo_path,workspace/'frozen_market',minute,stamp,ready,
                  full_arrow=True)
    _,_,history_trim_date=check_upstream_history_trim_date(workspace/'frozen_market',minute,stamp,None)
    swap_arrow=workspace/'frozen_market'/f'{minute}m'/f'swap_dict_{stamp}.arrow'
    if swap_arrow.exists():
        min_kline_num=int(values.get('min_kline_num') or 0)
        earliest_bar=server.scan_arrow_earliest_bar(
            swap_arrow,
            INITIALIZATION['minimum_history_bars'],
        )
        origin=server.resolve_initialization_s3_origin(configured_origin,earliest_bar,n,min_kline_num,end_bar)
        if origin>configured_origin:
            print(f'{account.stem}: 上游全量历史起点={earliest_bar}（min_kline_num={min_kline_num}, n={n}），'
                  f'滚动推进 S3 origin: {configured_origin} -> {origin}',flush=True)
    else:
        origin=configured_origin
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
    payload=dict(account=str(account),root=str(workspace),origin=str(origin),initializing=True,
                 history_trim_date=history_trim_date)
    env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
    with server.account_lock(destination):
        server.record_initialization_attempt(destination,values,stamp)
    subprocess.run([sys.executable,str(Path(__file__).resolve()),'compute','--account',str(account),
                    '--workspace',str(workspace),'--stamp',str(stamp),'--destination',str(destination)],
                    cwd=ROOT,env=env,check=True)
    # compute returns only after validation and atomic installation. Never put
    # this notification in finally: failure must retain the full-history request.
    release_demand(config.bmac_data_path,ROOT,account,minute,stamp=stamp,values=values)
    server.cleanup_initialization(workspace,ROOT,account,stamp)



def rebuild(account,stamp,destination):
    """Called after resource admission; never place an order from this worker."""
    import pandas as pd
    from tools.robust40 import server,telemetry
    from tools.robust40.market_inputs import full_publication, PublicationUnavailable
    from tools.robust40.replay import WAIT_FOR_DATA_EXIT
    values,options=server.settings(account)
    os.environ['X3S_TRADING_ACCOUNT']=str(account)
    import config
    required={f'{name}_{p}' for name,ps in (values.get('precompute_factor_configs') or {}).items() for p in ps}
    actual,reason=full_publication(config.bmac_data_path,options['minute'],required)
    if actual is None:
        telemetry.record(status='waiting_data',recovery_reason=reason)
        return WAIT_FOR_DATA_EXIT
    with server.account_lock(destination):
        from tools.robust40.handoff import recover_interrupted
        recover_interrupted(destination,True)
        status,reason=server.inspect_resume(destination,account.stem,values,pd.Timestamp(actual,unit='s')-pd.Timedelta(hours=1))
    if status!='rebuild':
        telemetry.record(initialization_completed=False,recovery_reason='state already updated')
        return 0
    if server.initialization_attempted(destination,values,actual):
        telemetry.record(status='blocked',recovery_reason='same configuration/publication already attempted')
        return 1
    workspace=ROOT/'data/server_initialization'/account.stem/str(actual)
    telemetry.record(publication_stamp=actual)
    try:
        initialize(account,workspace,actual,destination)
    except PublicationUnavailable as exc:
        telemetry.record(status='waiting_data',recovery_reason=str(exc))
        return WAIT_FOR_DATA_EXIT
    telemetry.record(initialization_completed=True)
    return 0


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
    worker_spec=json.loads(os.environ['ROBUST40_SERVER_WORKER'])
    origin=worker_spec['origin']
    history_trim_date=worker_spec.get('history_trim_date')
    server.complete(snapshot,account.stem,values,end,origin,history_trim_date=history_trim_date)
    server.install(snapshot,destination,destination.parent)
    print(f'Initialized {account.stem}: {bundle["context"]["index"][0]} -> {end}; origin={origin}',flush=True)


def trade(account,stamp,stage='all',scale=1.0,wait_ready=False):
    if stage=='trade':
        from core.account_exec import main
        sys.argv=[sys.argv[0],str(stamp),'--stage','trade','--scale',repr(scale)]
        main()
        return 0
    if wait_ready:
        return _trade_wait_ready(account,stamp,stage=stage)
    from tools.robust40 import telemetry
    with telemetry.round_log(account.stem,stamp):
        return _trade(account,stamp,stage=stage)


def _trade_wait_ready(account,stamp,stage='all'):
    import time
    import pandas as pd
    import config
    from tools.robust40 import handoff, server, telemetry
    from tools.robust40.initialization_config import DATA_READY_WAIT_SECONDS
    from tools.robust40.replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT

    prewarm_start=time.perf_counter()
    # Pre-import trading entry point without holding account_lock during idle prewarm window.
    from core.account_exec import main as _account_exec_main  # noqa: F401
    values,_=server.settings(account)
    destination=server.location(ROOT,account,values)
    warmup_path=destination/'warmup.pkl'
    rev_path=destination/'model/revision_inputs.pkl'
    try:
        with server.account_lock(destination):
            st=warmup_path.stat()
            warmup_sig=(st.st_mtime_ns,st.st_size)
            original=pd.read_pickle(warmup_path)
            old_inputs=pd.read_pickle(rev_path)
    except OSError:
        with server.account_lock(destination):
            with telemetry.round_log(account.stem,stamp):
                return _trade(account,stamp,stage=stage)

    end=pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1)
    run=(pd.Timestamp(stamp,unit='s')+pd.Timedelta(hours=config.utc_offset)).to_pydatetime()
    start=min(f.index.min() for f in old_inputs.values())
    required={f'{name}_{p}' for name,ps in values['precompute_factor_configs'].items() for p in ps}
    deadline=stamp+DATA_READY_WAIT_SECONDS

    stream_status,current_inputs,trims,stream_stats=handoff.wait_and_read_inputs_streaming(
        config.bmac_data_path,run,end.minute,stamp,end,start,required,deadline=deadline
    )
    prewarm_s=round(time.perf_counter()-prewarm_start,6)

    with telemetry.round_log(account.stem,stamp):
        telemetry.reset_timer(prewarm_s=prewarm_s,**stream_stats)
        if stream_status=='timeout':
            telemetry.record(mode='waiting_data_timeout',status='waiting_data_timeout')
            return WAIT_FOR_DATA_EXIT
        with server.account_lock(destination):
            try:
                st_now=warmup_path.stat()
                sig_now=(st_now.st_mtime_ns,st_now.st_size)
            except OSError:
                sig_now=None
            if sig_now!=warmup_sig:
                return _trade(account,stamp,stage=stage)
            if stream_status=='missing_arrow':
                telemetry.record(mode='rebuild_required',status='rebuild_required',recovery_reason='Arrow unavailable or incomplete')
                return REBUILD_REQUIRED_EXIT
            spot_trim,swap_trim=trims
            if spot_trim!=swap_trim:
                raise ValueError(f'spot/swap Arrow history_trim_date mismatch: spot={spot_trim!r}, swap={swap_trim!r}')
            old_t=original.get('history_trim_date')
            if spot_trim is not None and spot_trim!=old_t and (old_t is None or server.cutoff(spot_trim)>server.cutoff(old_t)):
                server.begin(destination)
                telemetry.record(
                    mode='rebuild_required',
                    status='rebuild_required',
                    recovery_reason=f'upstream history_trim_date changed: {old_t} -> {spot_trim}',
                )
                return REBUILD_REQUIRED_EXIT
            telemetry.add(
                revision_arrow_spot_s=stream_stats['revision_arrow_spot_s'],
                revision_arrow_swap_s=stream_stats['revision_arrow_swap_s'],
                revision_pivot_s=stream_stats['revision_pivot_s'],
            )
            return _trade(
                account,
                stamp,
                stage=stage,
                preloaded_old=old_inputs,
                preloaded_current=current_inputs,
                skip_trim_check=True,
            )


def _trade(account,stamp,stage='all',*,preloaded_old=None,preloaded_current=None,skip_trim_check=False):
    import pandas as pd
    import config
    from contextlib import nullcontext
    from tools.robust40 import handoff
    from tools.robust40 import server
    from tools.robust40 import telemetry
    from tools.robust40.replay import RebuildRequired, REBUILD_REQUIRED_EXIT
    values,_=server.settings(account)
    destination=server.location(ROOT,account,values)
    server.check_dependencies(values)
    status,reason=server.inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1),server.resume_capacity(values))
    if status=='rebuild':
        telemetry.record(mode='rebuild_required',status='rebuild_required',recovery_reason=reason)
        return REBUILD_REQUIRED_EXIT
    if status!='ready':
        telemetry.record(mode='not_ready')
        raise ValueError(reason)
    # Import errors happen before any calculation and must not invalidate state.
    from core.account_exec import main
    original=pd.read_pickle(destination/'warmup.pkl')
    end=pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1)
    run=(pd.Timestamp(stamp,unit='s')+pd.Timedelta(hours=config.utc_offset)).to_pydatetime()
    if not skip_trim_check:
        need_rebuild,old_t,new_t=check_upstream_history_trim_date(
            config.bmac_data_path,end.minute,stamp,original.get('history_trim_date')
        )
        if need_rebuild:
            server.begin(destination)
            telemetry.record(
                mode='rebuild_required',
                status='rebuild_required',
                recovery_reason=f'upstream history_trim_date changed: {old_t} -> {new_t}',
            )
            return REBUILD_REQUIRED_EXIT
    try:
        if preloaded_old is not None or preloaded_current is not None:
            stage_replay,inputs=handoff.prepare(
                destination,values,run,end.minute,end,
                preloaded_old=preloaded_old,preloaded_current=preloaded_current,
            )
        else:
            stage_replay,inputs=handoff.prepare(destination,values,run,end.minute,end)
    except RebuildRequired as exc:
        # No orders have run; release the account lock before the parent queues initialization.
        server.begin(destination)
        telemetry.record(mode='rebuild_required',status='rebuild_required',recovery_reason=str(exc)[:500])
        return REBUILD_REQUIRED_EXIT
    config.strategy_config['params']['runtime']['replaying']=stage_replay is not None
    with handoff.transaction(destination,stage_replay) if stage_replay is not None else nullcontext():
        server.begin(destination)
        with telemetry.phase('input_write_s'):
            pd.to_pickle(inputs,destination/'model/revision_inputs.pending.pkl')
        # Original BNB and execution flow; never submit historical replay orders.
        sys.argv=[sys.argv[0],str(stamp)] if stage=='all' else [sys.argv[0],str(stamp),'--stage',stage]
        with telemetry.phase('account_flow_s'):
            result=main()
        if result is None and stage_replay is None:
            server.save_meta(destination,original)
        if result is None:telemetry.record(account_skipped=True)


def warm_numba_kernels() -> None:
    """Compile and cache live/sim Numba kernels without touching account state."""
    import numpy as np
    from positions.ModelRobust40Strategy import LiveRebRobust40, RebRobust40, start_simulation

    n_bars, n_syms = 2, 2
    sizes = np.ones(n_syms, dtype=np.float64)
    lots = np.zeros(n_syms, dtype=np.int64)
    px_row = np.full(n_syms, 100.0, dtype=np.float64)
    prices = np.full((n_bars, n_syms), 100.0, dtype=np.float64)
    spot_ratio = np.full((n_bars, n_syms), 0.1, dtype=np.float64)
    swap_ratio = np.full((n_bars, n_syms), -0.1, dtype=np.float64)
    funding = np.zeros((n_bars, n_syms), dtype=np.float64)
    leverages = np.ones(n_bars, dtype=np.float64)
    req = np.ones(n_bars, dtype=np.int64)
    cflow = np.zeros(n_bars, dtype=np.float64)

    LiveRebRobust40(sizes, sizes).calc_lots(10000.0, px_row, lots, spot_ratio[0], px_row, lots, swap_ratio[0])
    RebRobust40(sizes, sizes).calc_lots(10000.0, px_row, lots, spot_ratio[0], px_row, lots, swap_ratio[0])
    start_simulation(
        10000.0, leverages, sizes, sizes, 0.0005, 0.0005, 5.0, 5.0, 0.05,
        spot_ratio, swap_ratio, prices, prices, prices, prices, prices, prices, funding,
        0.4, 2.45, req, cflow, -1, resume_state=None,
    )
    resume_one = (10000.0, lots, lots.copy(), px_row, False)
    start_simulation(
        10000.0, leverages, sizes, sizes, 0.0005, 0.0005, 5.0, 5.0, 0.05,
        spot_ratio, swap_ratio, prices, prices, prices, prices, prices, prices, funding,
        0.4, 2.45, req, cflow, -1, resume_state=(resume_one, resume_one),
    )
    print('warm-numba: Numba JIT cache ready')


def main():
    # initialize has no ROBUST40_SERVER_WORKER profile yet. Configure its output
    # before framework loggers wrap stdout (Windows redirected streams use GBK).
    configure_output()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','initialize','rebuild','compute','trade','warm-numba'])
    p.add_argument('--account')
    p.add_argument('--stamp',type=int)
    p.add_argument('--workspace')
    p.add_argument('--destination')
    p.add_argument('--stage',choices=['all','calc','trade'],default='all')
    p.add_argument('--scale',type=float,default=1.0)
    p.add_argument('--wait-ready',action='store_true')
    a=p.parse_args()
    if a.mode=='warm-numba':
        warm_numba_kernels()
        return 0
    if not a.account or a.stamp is None:
        p.error('--account and --stamp are required')
    account=Path(a.account).resolve()
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
    elif a.mode=='rebuild':
        destination=Path(a.destination).resolve()
        with server.account_lock(destination.with_name(destination.name+'.initialization')):
            with telemetry.round_log(account.stem,a.stamp,kind='initialize'):
                return rebuild(account,a.stamp,destination)
    elif a.mode=='compute':
        with server.account_lock(a.destination):
            with telemetry.round_log(account.stem,a.stamp,kind='compute'):
                compute(account,Path(a.workspace).resolve(),a.stamp,Path(a.destination).resolve())
    else:
        if a.wait_ready and a.stage!='trade':
            return trade(account,a.stamp,stage=a.stage,scale=a.scale,wait_ready=True)
        with server.account_lock(server.location(ROOT,account,server.settings(account)[0])):
            return trade(account,a.stamp,stage=a.stage,scale=a.scale)


if __name__=='__main__':sys.exit(main())
