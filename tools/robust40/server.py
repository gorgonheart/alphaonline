"""Per-account snapshot lifecycle. No trading imports or exchange operations."""
import ast
from pathlib import Path
import shutil
import pickle
from contextlib import contextmanager
from functools import wraps
from threading import RLock, Thread, Event
import pandas as pd
from .warmup import settings, cutoff, effective_settings


@contextmanager
def account_lock(destination):
    """OS releases the lock on process death; no stale O_EXCL file to clear."""
    import os
    destination=Path(destination).resolve()
    folder=destination.parent.parent/'server_account_locks'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/(destination.name+'.lock')).open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def enabled(account):
    tree=ast.parse(Path(account).read_text(encoding='utf-8-sig'))
    names={n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)}
    names.update(n.arg for n in ast.walk(tree) if isinstance(n,ast.keyword))
    if not {'ModelRobust40Strategy','server_init'}.issubset(names):return False
    return settings(account)[1].get('server_init') is True


def location(root,account,values):
    parent=(Path(root)/'data/snapshot').resolve()
    path=(parent/(Path(account).stem+'_'+values['strategy_name'])).resolve()
    if path.parent!=parent:raise ValueError('Snapshot must be an immediate account directory')
    return path


def required_files(values):
    paths=['snapshot_time.txt','model/state.pkl','model/revision_inputs.pkl']
    for i,spec in enumerate(values['strategy_pool']):
        folder=f'S{i+1}-{spec["name"]}'
        paths += [folder+'/'+n for n in ('选币结果.pkl','ratio_spot.pkl','ratio_swap.pkl','account_state0.pkl','lots_state0.pkl')]
        if spec.get('re_timing'):paths += [folder+'/'+n for n in ('account_state1.pkl','lots_state1.pkl')]
    return paths


def save_meta(path,meta):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    temp=path/'warmup.tmp';pd.to_pickle(meta,temp);temp.replace(path/'warmup.pkl')


def complete(path,account,values,end,origin,*,history_trim_date=None):
    sections=[str(p.relative_to(path)) for p in (Path(path)/'sections').glob('*.pkl')]
    if not sections:raise ValueError('S3 section state missing')
    save_meta(path,dict(account=account,settings=values,cutoff=cutoff(end),origin=str(origin),checkpoint_version=1,
                        complete=True,sections=sections,history_trim_date=history_trim_date))


def begin(path):
    meta=pd.read_pickle(Path(path)/'warmup.pkl')
    save_meta(path,dict(meta,complete=False))



def record_initialization_attempt(path,values,stamp):
    """One bounded entry in existing metadata, written only before full compute."""
    file=Path(path)/'warmup.pkl'
    try:meta=pd.read_pickle(file) if file.exists() else {}
    except (EOFError,pickle.UnpicklingError):meta={}
    if not isinstance(meta,dict):meta={}
    save_meta(path,dict(meta,complete=False,
                       initialization_attempt=dict(stamp=int(stamp),settings=values)))


def initialization_attempted(path,values,stamp):
    file=Path(path)/'warmup.pkl'
    if not file.exists():return False
    try:
        item=pd.read_pickle(file).get('initialization_attempt',{})
        return item.get('stamp')==int(stamp) and effective_settings(item.get('settings'))==effective_settings(values)
    except (EOFError,pickle.UnpicklingError,AttributeError):return False


def inspect(path,account,values,end,max_gap=240):
    """Only a small metadata read and fixed-file stats; no model/ratio unpickle."""
    path=Path(path)
    try:
        meta=pd.read_pickle(path/'warmup.pkl')
        if meta['account']!=account or effective_settings(meta['settings'])!=effective_settings(values):return 'rebuild','configuration changed'
        if not meta['complete']:return 'rebuild','incomplete calculation'
        if meta.get('checkpoint_version')!=1:return 'rebuild','common checkpoints require initialization'
        last=cutoff(meta['cutoff']);end=cutoff(end)
        if last.minute!=end.minute or last.second or last.microsecond or last.nanosecond:
            return 'rebuild','cutoff offset mismatch'
        origin=cutoff(meta['origin'])
        if pd.isna(origin) or origin>last or origin.minute!=end.minute or origin.second or origin.microsecond or origin.nanosecond:
            return 'rebuild','invalid S3 origin'
        if last!=cutoff((path/'snapshot_time.txt').read_text().strip()):return 'rebuild','cutoff mismatch'
        if not meta['sections']:return 'rebuild','section state missing'
        for name in required_files(values)+meta['sections']:
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts:return 'rebuild','invalid state path'
            if (path/relative).stat().st_size==0:return 'rebuild','missing state'
        if last>end:return 'wait','incoming publication older than state'
        if (end-last)/pd.Timedelta(hours=1)>max_gap:return 'rebuild','gap exceeds incremental coverage'
        return 'ready',''
    except (OSError,ValueError,KeyError,EOFError,TypeError,pickle.UnpicklingError):
        return 'rebuild','missing or unreadable state'


def inspect_resume(path,account,values,end):
    """Use the same capacity as trade when deciding whether to cancel recovery."""
    status,reason=inspect(path,account,values,end)
    if status=='ready':return inspect(path,account,values,end,resume_capacity(values))
    return status,reason


def configured_s3(values,minute):
    """The section's fourth parameter is the sole date authority (UTC day)."""
    definitions=[]
    def visit(value):
        if isinstance(value,dict):
            for v in value.values():visit(v)
        elif isinstance(value,(tuple,list)):
            if len(value)>1 and value[0]=='VolumeMaxBias_进出场阈值分离官方':definitions.append(value[1])
            for v in value:visit(v)
    visit(values['strategy_pool'])
    if not definitions:raise ValueError('MODEL account has no S3 section definition')
    origins=set()
    for params in definitions:
        date=cutoff(params[3])
        if pd.isna(date) or date.hour or date.minute not in (0,minute) or date.second or date.microsecond or date.nanosecond:
            raise ValueError('S3 date must be a UTC day or 00:offset timestamp')
        origins.add(date.normalize()+pd.Timedelta(minutes=minute))
    if len(origins)!=1:raise ValueError('MODEL S3 definitions must use one configured date')
    return origins.pop(),max(int(p[0]) for p in definitions)


def resolve_initialization_s3_origin(configured_origin, earliest_bar, n, min_kline_num, end_bar, *, min_model_days=365):
    """Keep configured S3 origin when history covers min_kline_num+n; otherwise advance to 00:{minute}:00 UTC grid."""
    configured = cutoff(configured_origin)
    earliest = cutoff(earliest_bar)
    end = cutoff(end_bar)
    earliest_safe = earliest + pd.Timedelta(hours=int(min_kline_num) + int(n))
    if earliest_safe <= configured:
        resolved = configured
    else:
        day_grid = earliest_safe.normalize() + pd.Timedelta(minutes=configured.minute)
        resolved = day_grid if earliest_safe <= day_grid else day_grid + pd.Timedelta(days=1)
    if end - resolved < pd.Timedelta(days=int(min_model_days)):
        raise ValueError(
            f'Resolved S3 origin {resolved} leaves less than {min_model_days} days before end_bar {end}'
        )
    return resolved


def scan_arrow_earliest_bar(arrow_path, minimum_history_bars):
    """PyArrow-only scan of candle_begin_time[0] across batches meeting minimum_history_bars."""
    import pyarrow as pa
    earliest = None
    with pa.memory_map(str(arrow_path), 'r') as source:
        reader = pa.ipc.open_file(source)
        if 'candle_begin_time' not in reader.schema.names:
            raise ValueError('Full raw history is not ready for initialization')
        for index in range(reader.num_record_batches):
            batch = reader.get_batch(index)
            if batch.num_rows < minimum_history_bars:
                continue
            value = batch.column('candle_begin_time')[0].as_py()
            if value is None:
                continue
            ts = cutoff(value)
            if pd.isna(ts):
                continue
            if earliest is None or ts < earliest:
                earliest = ts
    if earliest is None:
        raise ValueError('Full raw history is not ready for initialization')
    return earliest


def validate_initial_history(raw,origin,n,min_kline_num):
    """Reuse MODEL's already loaded/UTC-normalized raw data; never scan on resume."""
    from .initialization_config import INITIALIZATION
    starts=[frame.candle_begin_time.min() for frame in raw.values()
            if len(frame)>=INITIALIZATION['minimum_history_bars']]
    if not starts:raise ValueError('Full raw history is not ready for initialization')
    # S3 checks market-wide coverage; later listings enter only when their
    # factor is valid. A coin reaching 14000 bars must not move the origin.
    earliest=min(starts)+pd.Timedelta(hours=min_kline_num+n)
    if origin<earliest:
        raise ValueError(f'S3 configured date {origin} lacks history; earliest safe time is {earliest}. '
                         'Restore older history or explicitly change the configured S3 date and rebuild')


def check_dependencies(values,bars=4050):
    """Known non-precomputed nested dependency; never silently tune the strategy."""
    def visit(item):
        if isinstance(item,dict):
            for value in item.values():visit(value)
        elif isinstance(item,(tuple,list)):
            if len(item)>1 and item[0]=='TopKTo2K':
                window,name,n=item[1]
                if str(name).startswith('Cci过滤') and 2*int(n)-1+int(window)-1>bars:
                    needed=2*int(n)+int(window)-2
                    raise ValueError(f'{name}({n}) -> TopK requires {needed} bars, exceeds {bars}; tune and validate parameters')
            for value in item:visit(value)
    visit(values['strategy_pool'])


def resume_capacity(values,bars=4050):
    """Reserve both nested factor warmup and holding-period overlap."""
    capacity=240
    for spec in values['strategy_pool']:
        for child in spec.get('strategy_list',[]):
            hold=int(pd.Timedelta(child.get('hold_period','1h'))/pd.Timedelta(hours=1))
            for key,items in child.items():
                if 'filter_list' not in key:continue
                for item in items:
                    if item[0]=='TopKTo2K' and str(item[1][1]).startswith('Cci过滤'):
                        window,_,n=item[1]
                        capacity=min(capacity,bars-(2*int(n)+int(window)-2)-hold)
    if capacity<1:raise ValueError('No 4050-bar capacity remains after factor warmup and holding period')
    return capacity


def install(stage,destination,parent):
    """Same-filesystem switch with recoverable prior directory; no production reads."""
    stage,destination,parent=map(lambda p:Path(p).resolve(),(stage,destination,parent))
    if destination.parent!=parent or stage==destination or stage.is_relative_to(destination):
        raise ValueError('Unsafe snapshot installation paths')
    parent.mkdir(parents=True,exist_ok=True)
    backup=parent/(destination.name+'.previous')
    if backup.exists():
        if not destination.exists():backup.replace(destination)
        else:shutil.rmtree(backup)  # Validated immediate child, only this account.
    moved=destination.exists()
    if moved:destination.replace(backup)
    try:stage.replace(destination)
    except BaseException:
        if moved and not destination.exists():backup.replace(destination)
        raise


def cleanup_initialization(workspace,root,account,stamp):
    """Drop only coordinator-owned successful scratch; keep research replays."""
    workspace=Path(workspace).resolve()
    expected=Path(root).resolve()/'data/server_initialization'/Path(account).stem/str(stamp)
    if workspace==expected and workspace.exists():
        from .lifecycle import discard
        discard(workspace,expected.parent)


def clean_startup(accounts,root,snapshot_root):
    """Preserve opted-in accounts in place; legacy accounts retain clean-start behavior."""
    parent=Path(snapshot_root).resolve()
    keep={location(root,a,settings(a)[0]) for a in accounts if enabled(a)}
    keep |= {p.with_name(p.name+'.previous') for p in list(keep)}
    keep |= {p.with_name(p.name+'.replay-previous') for p in list(keep) if not p.name.endswith('.previous')}
    if parent.exists():
        for p in parent.iterdir():
            resolved=p.resolve()
            if resolved in keep:continue
            if resolved.parent!=parent:raise ValueError('Snapshot cleanup escaped root')
            if p.is_dir():shutil.rmtree(p)
            else:p.unlink()


def finish_live(strategy):
    """Called after target parity passes, before orders; order failure keeps computation."""
    opts=strategy.params['runtime']
    if not opts.get('server_init'):return
    pending_save=getattr(strategy,'_robust40_pending_save',None)
    if pending_save is not None:
        delattr(strategy,'_robust40_pending_save')
        pending_save.commit()
    path=Path(opts['state_path']).parent.parent
    meta=pd.read_pickle(path/'warmup.pkl')
    pending=path/'model/revision_inputs.pending.pkl'
    if pending.exists():pending.replace(path/'model/revision_inputs.pkl')
    if opts.get('replaying'):
        from .warmup import validate_snapshot
        bundle = getattr(strategy, '_robust40_bundle', None)
        try:
            if bundle is None:
                bundle = pd.read_pickle(path/'model/state.pkl')
            end = validate_snapshot(path, bundle)
        finally:
            if hasattr(strategy, '_robust40_bundle'):
                delattr(strategy, '_robust40_bundle')
        for section in (path/'sections').glob('*.pkl'):
            if cutoff(pd.read_pickle(section)['cutoff'])!=end:raise ValueError('Replayed S3 cutoff differs')
    elif hasattr(strategy, '_robust40_bundle'):
        delattr(strategy, '_robust40_bundle')
    complete(path,meta['account'],meta['settings'],strategy._robust40_result['parent'].index[-1],meta['origin'],
             history_trim_date=meta.get('history_trim_date'))
    from . import telemetry
    telemetry.commit()



def _serialized(function):
    @wraps(function)
    def call(self,*args,**kwargs):
        with self._mutex:return function(self,*args,**kwargs)
    return call


class Coordinator:
    """One isolated initializer; ready accounts continue on the normal serial loop."""
    def __init__(self,root):
        self.root=Path(root).resolve();self.jobs={};self.attempts={}
        self.pending={};self._mutex=RLock();self._pending_thread=None;self._stop=Event()

    def _monitor(self,account,process,log):
        """Report exit immediately; polling at a later offset only reaps bookkeeping."""
        from threading import Thread
        def report():
            from core.utils.log_kit import logger
            code=process.wait()
            from .replay import WAIT_FOR_DATA_EXIT
            if code==WAIT_FOR_DATA_EXIT:
                logger.info(f'{Path(account).stem}: 发布已切换，返回等待完整数据；日志：{log}')
            elif code:
                try:
                    with Path(log).open('rb') as source:
                        source.seek(0,2);source.seek(max(0,source.tell()-8192))
                        tail=source.read().decode('utf-8',errors='replace').strip()
                except OSError:tail='无法读取初始化日志'
                logger.error(f'{Path(account).stem}: 初始化失败，退出码={code}；日志：{log}\n{tail}')
            else:
                logger.info(f'{Path(account).stem}: 初始化任务结束（完成或准备取消以任务日志为准）；下一合法offset继续状态检查；日志：{log}')
        thread=Thread(target=report,daemon=True,name=f'init-{Path(account).stem}')
        thread.start()
        return thread

    @_serialized
    def _reap(self):
        for key,(process,handle) in list(self.jobs.items()):
            if process.poll() is not None:
                from .replay import WAIT_FOR_DATA_EXIT
                code=process.poll()
                handle.close();del self.jobs[key]
                if code==WAIT_FOR_DATA_EXIT:
                    self.attempts.pop(key,None)
                    if key in self.pending:
                        item=self.pending[key]
                        item['retry_version']=(item['cache'].get('version'),item.get('values_repr'))
                elif code==0:self.pending.pop(key,None)

    def _ensure_pending_thread(self):
        if self._pending_thread is not None and self._pending_thread.is_alive():return
        def watch():
            while not self._stop.is_set():
                with self._mutex:
                    if not self.pending:
                        self._pending_thread=None;return
                self.drain_pending()
                self._stop.wait(2.)
        self._pending_thread=Thread(target=watch,daemon=True,name='initialization-ready')
        self._pending_thread.start()

    def close(self):
        """Stop only this coordinator's metadata watcher, never an active initializer."""
        self._stop.set()
        if self._pending_thread is not None:self._pending_thread.join(timeout=3)

    @_serialized
    def request_rebuild(self,account,run_time,market,reason='rebuild required'):
        account=Path(account).resolve()
        if not enabled(account):raise ValueError('Only MODEL accounts can request this recovery')
        if account not in self.pending:
            self.pending[account]=dict(run_time=run_time,market=Path(market),reason=reason,cache={})
        self.drain_pending()
        self._ensure_pending_thread()

    @_serialized
    def drain_pending(self):
        from .arrow_demand import set_demand
        from .market_inputs import full_publication
        from core.utils.log_kit import logger
        self._reap()
        for account,item in list(self.pending.items()):
            try:
                if not account.exists() or not enabled(account):
                    self.pending.pop(account,None);continue
                if account in self.jobs:continue
                values,options=settings(account);minute=options['minute']
                required={f'{name}_{p}' for name,ps in (values.get('precompute_factor_configs') or {}).items() for p in ps}
                item['values_repr']=repr(values)
                stamp,reason=full_publication(item['market'],minute,required,item['cache'])
                destination=location(self.root,account,values)
                # A completed external initializer must cancel pending even when
                # the producer has already switched back to short Arrow.
                check_stamp=stamp if stamp is not None else int(item['run_time'].timestamp())
                try:
                    with account_lock(destination.with_name(destination.name+'.dispatch')), account_lock(destination.with_name(destination.name+'.initialization')), account_lock(destination):
                        from .handoff import recover_interrupted
                        recover_interrupted(destination,True)
                        status,_=inspect_resume(destination,account.stem,values,pd.Timestamp(check_stamp,unit='s')-pd.Timedelta(hours=1))
                except BlockingIOError:continue
                if status!='rebuild':
                    self.pending.pop(account,None);continue
                set_demand(item['market'],self.root,account,minute,True,stamp=int(item['run_time'].timestamp()))
                if stamp is None:
                    if item.get('waiting')!=reason:
                        logger.info(f'REBUILD_WAIT account={account.stem} reason={reason}; 不占初始化计算槽；若未启用Arrow联动，需上游配置全量发布')
                        item['waiting']=reason
                    continue
                if item.get('retry_version')==(item['cache'].get('version'),repr(values)):continue
                if self.jobs:continue
                if initialization_attempted(destination,values,stamp):
                    if item.get('blocked_stamp')!=stamp:
                        logger.error(f'REBUILD_BLOCKED account={account.stem} stamp={stamp}: 同配置同轮初始化已尝试，等待新配置/新发布')
                        item['blocked_stamp']=stamp
                    continue
                run=pd.Timestamp(stamp,unit='s',tz='UTC').to_pydatetime()
                self._launch(account,run,values,item['reason'],'rebuild',item['market'])
            except Exception as exc:
                # Background failures remain visible; no generic failure is
                # reinterpreted as successful recovery or repeated computation.
                message=f'{type(exc).__name__}: {exc}'
                if item.get('error')!=message:
                    logger.error(f'REBUILD_BLOCKED account={account.stem} reason={message}')
                    item['error']=message

    def prepare(self,accounts,run_time,market):
        """Metadata-only preflight also restores pending invalid accounts after restart."""
        import config as _cfg
        from core.account_manager import read_exchange_from_config
        from core.utils import datatools as _dt
        from .initialization_config import ARROW_DEMAND_ENABLED
        self._reap()
        ex_paths=(getattr(_dt,'exchange_data_paths',{}) or getattr(_cfg,'exchange_data_paths',{})) or {}
        for account in accounts:
            if not enabled(account):continue
            account=Path(account).resolve();values,options=settings(account)
            if account in self.jobs:continue
            # Initializing outside its trade offset is allowed; orders remain scheduled.
            candidate=pd.Timestamp(run_time).replace(minute=options['minute'],second=0,microsecond=0)
            if candidate>pd.Timestamp(run_time):candidate-=pd.Timedelta(hours=1)
            candidate_dt=candidate.to_pydatetime()
            candidate_stamp=int(candidate_dt.timestamp())
            if account in self.pending:
                if candidate_dt>self.pending[account]['run_time']:
                    self.pending[account]['run_time']=candidate_dt
                    self.drain_pending()
                continue
            account_exchange=read_exchange_from_config(account)
            account_market=_dt.resolve_bmac_data_path(account_exchange) if account_exchange in ex_paths else market
            destination=location(self.root,account,values)
            meta=None
            try:
                with account_lock(destination.with_name(destination.name+'.initialization')), account_lock(destination):
                    status,reason=inspect(destination,account.stem,values,
                        pd.Timestamp(candidate_stamp,unit='s')-pd.Timedelta(hours=1))
                    if status=='ready' and ARROW_DEMAND_ENABLED:
                        meta=pd.read_pickle(destination/'warmup.pkl')
            except OSError:continue
            if status=='rebuild':
                self.request_rebuild(account,candidate_dt,account_market,reason)
            elif status=='ready' and ARROW_DEMAND_ENABLED and isinstance(meta,dict) and meta.get('cutoff') is not None:
                from .arrow_demand import release_demand
                release_demand(account_market,self.root,account,options['minute'],
                               stamp=int(cutoff(meta['cutoff']).tz_localize('UTC').timestamp())+3600,
                               values=values)

    @_serialized
    def _launch(self,account,run_time,values,reason,mode,market):
        import os
        import subprocess
        import sys
        from core.utils.log_kit import logger
        from .resources import supervisor_command
        from .arrow_demand import set_demand
        stamp=int(run_time.timestamp())
        try:
            set_demand(market,self.root,account,run_time.minute,True,stamp=stamp)
        except (OSError,ValueError) as exc:
            logger.warning(f'ARROW_DEMAND full request deferred: {account.stem}: {exc}')
            return
        if self.jobs:
            logger.info(f'{account.stem}: 等待初始化槽位；当前账户：{[p.stem for p in self.jobs]}')
            return
        token=(stamp,repr(values)) if mode=='rebuild' else (stamp,reason,repr(values))
        if self.attempts.get(account)==token:return
        work=self.root/'data/server_initialization'/account.stem/str(stamp)
        if work.exists():
            self.attempts[account]=token
            logger.info(f'{account.stem}: 本轮初始化目录已存在，保留现场并跳过；目录：{work}')
            return
        work.parent.mkdir(parents=True,exist_ok=True)
        handle=(work.parent/f'{stamp}.log').open('a',encoding='utf8')
        cmd=[sys.executable,str(self.root/'tools/robust40/cli/server_robust40.py'),mode,'--account',str(account),
             '--stamp',str(stamp),'--workspace',str(work),'--destination',str(location(self.root,account,values))]
        cmd=supervisor_command(cmd,stamp,self.root,market)
        env=dict(os.environ,PYTHONIOENCODING='utf-8')
        env.pop('ROBUST40_SERVER_WORKER',None)
        env.pop('ROBUST40_WARMUP_WORKER',None)
        env['X3S_TRADING_ACCOUNT']=str(account)
        try:
            process=subprocess.Popen(cmd,cwd=self.root,stdout=handle,stderr=subprocess.STDOUT,env=env)
        except BaseException:
            handle.close();raise
        self.attempts[account]=token
        self.jobs[account]=(process,handle)
        handle.close()
        self._monitor(account,process,Path(handle.name))
        logger.info(f'{account.stem}: {reason}; 启动后台初始化/{mode}；日志：{handle.name}')

    def handle_result(self,account,run_time,market,command,env,returncode):
        """Only the known MODEL trade CLI may request recovery via its exit code."""
        import subprocess
        from .replay import REBUILD_REQUIRED_EXIT, WAIT_FOR_DATA_EXIT
        if returncode==0:return True
        model_cli=self.root/'tools/robust40/cli/server_robust40.py'
        if len(command)>2 and Path(command[1]).resolve()==model_cli and command[2]=='trade':
            if returncode==REBUILD_REQUIRED_EXIT:
                self.request_rebuild(account,run_time,market,'worker requested full initialization')
                return False
            if returncode==WAIT_FOR_DATA_EXIT:
                from core.utils.log_kit import logger
                logger.warning(f'{Path(account).stem}: 数据中心数据未更新，跳过当前，{run_time}')
                return False
        raise subprocess.CalledProcessError(returncode,command)

    def launch_prewarmed_trade(self,account,run_time,market):
        """Launch trade worker at run_time - PREWARM_LEAD_SECONDS when snapshot is ready."""
        import json
        import os
        import subprocess
        import sys
        if not enabled(account):return None
        from core.utils.log_kit import logger
        account=Path(account).resolve();values,options=settings(account)
        minute=options['minute'];stamp=int(run_time.timestamp())
        if values['account']['hour_offset']!=f'{minute}m' or run_time.minute!=minute:
            raise ValueError('Account and runtime offset differ')
        self._reap()
        if account in self.pending or account in self.jobs:
            return None
        destination=location(self.root,account,values)
        from .handoff import recover_interrupted
        try:recover_interrupted(destination)
        except OSError:return None
        status,_=inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1))
        if status!='ready':return None
        try:
            check_dependencies(values)
            capacity=resume_capacity(values)
        except ValueError as exc:
            logger.info(f'{account.stem}: blocked: {exc}')
            return None
        status,_=inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1),capacity)
        if status!='ready':return None
        try:
            meta=pd.read_pickle(destination/'warmup.pkl')
        except OSError:return None
        from .initialization_config import ARROW_DEMAND_ENABLED
        if ARROW_DEMAND_ENABLED:
            from .arrow_demand import release_demand
            release_demand(market,self.root,account,minute,
                           stamp=int(cutoff(meta['cutoff']).tz_localize('UTC').timestamp())+3600,
                           values=values)
        payload=dict(account=str(account),root=str(self.root),origin=meta['origin'],initializing=False,stamp=stamp)
        env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
        cmd=[sys.executable,str(self.root/'tools/robust40/cli/server_robust40.py'),'trade',
             '--account',str(account),'--stamp',str(stamp),'--wait-ready']
        process=subprocess.Popen(cmd,cwd=self.root,env=env)
        return process,cmd,env

    def command(self,account,run_time,market,*extra_args):
        import json
        import os
        import subprocess
        import sys
        import time
        import pyarrow as pa
        from .initialization_config import DATA_READY_WAIT_SECONDS
        if not enabled(account):return [sys.executable,str(self.root/'core/account_exec.py'),str(int(run_time.timestamp())),*extra_args],None
        from core.utils.log_kit import logger
        account=Path(account).resolve();values,options=settings(account)
        minute=options['minute'];stamp=int(run_time.timestamp())
        if values['account']['hour_offset']!=f'{minute}m' or run_time.minute!=minute:
            raise ValueError('Account and runtime offset differ')
        self._reap()
        if account in self.pending or account in self.jobs:
            logger.info(f'{account.stem}: 后台重建已登记，数据就绪后计算，交易仍等待合法offset')
            return None,None
        is_trade_stage=any(tok=='--stage' and i+1<len(extra_args) and extra_args[i+1]=='trade' for i,tok in enumerate(extra_args))
        destination=location(self.root,account,values)
        if is_trade_stage:
            meta=pd.read_pickle(destination/'warmup.pkl')
            payload=dict(account=str(account),root=str(self.root),origin=meta['origin'],initializing=False,stamp=stamp)
            env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
            return [sys.executable,str(self.root/'tools/robust40/cli/server_robust40.py'),'trade','--account',str(account),'--stamp',str(stamp),*extra_args],env
        deadline=stamp+DATA_READY_WAIT_SECONDS
        folder=Path(market)/f'{minute}m'
        # The shared startup gate checks dicts only; pivots are published shortly
        # afterwards. Bound missing-input waiting, not a ready round's execution.
        waiting=False
        while True:
            missing=[f'{prefix}{stamp}.ready' for prefix in
                     ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_')
                     if not (folder/f'{prefix}{stamp}.ready').exists()]
            if not missing:break
            remaining=deadline-time.time()
            if remaining<=0:
                logger.info(f'{account.stem}: 等待同轮完整数据超时，跳过；缺失：{missing}')
                return None,None
            if not waiting:
                logger.info(f'{account.stem}: 等待同轮完整数据，剩余{remaining:.0f}秒；缺失：{missing}')
                waiting=True
            time.sleep(min(1.,remaining))
        from .handoff import recover_interrupted
        try:recover_interrupted(destination)
        except OSError:
            logger.info(f'{account.stem}: 账户恢复事务仍被占用，等待下一轮')
            return None,None
        status,reason=inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1))
        if status=='wait':
            logger.info(f'{account.stem}: 等待更新数据；{reason}')
            return None,None
        if status=='ready':
            try:
                check_dependencies(values)
                capacity=resume_capacity(values)
            except ValueError as exc:
                logger.info(f'{account.stem}: blocked: {exc}')
                return None,None
            status,reason=inspect(destination,account.stem,values,pd.Timestamp(stamp,unit='s')-pd.Timedelta(hours=1),capacity)
        if status=='ready':
            required={f'{name}_{p}' for name,ps in values['precompute_factor_configs'].items() for p in ps}
            try:
                for kind in ('spot','swap'):
                    with pa.memory_map(str(folder/f'{kind}_dict_{stamp}.arrow'),'r') as source:
                        reader=pa.ipc.open_file(source)
                        if not required.issubset(reader.schema.names):raise ValueError('Arrow missing columns')
                meta=pd.read_pickle(destination/'warmup.pkl')
                # Also repairs a lost success notification after controller death.
                from .initialization_config import ARROW_DEMAND_ENABLED
                if ARROW_DEMAND_ENABLED:
                    from .arrow_demand import release_demand
                    release_demand(market,self.root,account,minute,
                                   stamp=int(cutoff(meta['cutoff']).tz_localize('UTC').timestamp())+3600,
                                   values=values)
                payload=dict(account=str(account),root=str(self.root),origin=meta['origin'],initializing=False,stamp=stamp)
                env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
                return [sys.executable,str(self.root/'tools/robust40/cli/server_robust40.py'),'trade','--account',str(account),'--stamp',str(stamp),*extra_args],env
            except (OSError,ValueError,pa.ArrowException):reason='Arrow unavailable or incomplete'
        self.request_rebuild(account,run_time,market,reason)
        return None,None
