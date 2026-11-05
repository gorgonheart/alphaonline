"""Per-account snapshot lifecycle. No trading imports or exchange operations."""
import ast
from pathlib import Path
import shutil
import pickle
from contextlib import contextmanager
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


def complete(path,account,values,end,origin):
    sections=[str(p.relative_to(path)) for p in (Path(path)/'sections').glob('*.pkl')]
    if not sections:raise ValueError('S3 section state missing')
    save_meta(path,dict(account=account,settings=values,cutoff=cutoff(end),origin=str(origin),checkpoint_version=1,
                        complete=True,sections=sections))


def begin(path):
    meta=pd.read_pickle(Path(path)/'warmup.pkl')
    save_meta(path,dict(meta,complete=False))


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
    path=Path(opts['state_path']).parent.parent
    meta=pd.read_pickle(path/'warmup.pkl')
    pending=path/'model/revision_inputs.pending.pkl'
    if pending.exists():pending.replace(path/'model/revision_inputs.pkl')
    if opts.get('replaying'):
        from .warmup import validate_snapshot
        end=validate_snapshot(path,pd.read_pickle(path/'model/state.pkl'))
        for section in (path/'sections').glob('*.pkl'):
            if cutoff(pd.read_pickle(section)['cutoff'])!=end:raise ValueError('Replayed S3 cutoff differs')
    complete(path,meta['account'],meta['settings'],strategy._robust40_result['parent'].index[-1],meta['origin'])
    from . import telemetry
    telemetry.commit()


class Coordinator:
    """One isolated initializer; ready accounts continue on the normal serial loop."""
    def __init__(self,root):
        self.root=Path(root).resolve();self.jobs={};self.attempts={}

    def _monitor(self,account,process,log):
        """Report exit immediately; polling at a later offset only reaps bookkeeping."""
        from threading import Thread
        def report():
            from core.utils.log_kit import logger
            code=process.wait()
            if code:
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

    def _reap(self):
        for key,(process,handle) in list(self.jobs.items()):
            if process.poll() is not None:
                handle.close();del self.jobs[key]

    def prepare(self,accounts,run_time,market):
        """Cheap local preflight before the shared ready gate; no market reads."""
        self._reap()
        if self.jobs:return
        for account in accounts:
            if not enabled(account):continue
            account=Path(account).resolve();values,options=settings(account)
            if run_time.minute!=options['minute']:continue
            destination=location(self.root,account,values)
            # A running owner may have an intentionally incomplete snapshot.
            try:
                with account_lock(destination.with_name(destination.name+'.initialization')), account_lock(destination):
                    status,reason=inspect(destination,account.stem,values,
                        pd.Timestamp(run_time.timestamp(),unit='s')-pd.Timedelta(hours=1))
            except OSError:continue
            if status=='rebuild':
                self._launch(account,run_time,values,reason,'prepare',market)
                if self.jobs:return

    def _launch(self,account,run_time,values,reason,mode,market):
        import os
        import subprocess
        import sys
        from core.utils.log_kit import logger
        from .resources import supervisor_command
        stamp=int(run_time.timestamp())
        if self.jobs:
            logger.info(f'{account.stem}: 等待初始化槽位；当前账户：{[p.stem for p in self.jobs]}')
            return
        token=(stamp,reason,repr(values))
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

    def command(self,account,run_time,market):
        import json
        import os
        import subprocess
        import sys
        import time
        import pyarrow as pa
        from .initialization_config import DATA_READY_WAIT_SECONDS
        if not enabled(account):return [sys.executable,str(self.root/'core/account_exec.py'),str(int(run_time.timestamp()))],None
        from core.utils.log_kit import logger
        account=Path(account).resolve();values,options=settings(account)
        minute=options['minute'];stamp=int(run_time.timestamp())
        if values['account']['hour_offset']!=f'{minute}m' or run_time.minute!=minute:
            raise ValueError('Account and runtime offset differ')
        self._reap()
        if account in self.jobs:
            logger.info(f'{account.stem}: 初始化仍在后台运行；日志：{self.jobs[account][1].name}')
            return None,None
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
        if waiting:logger.info(f'{account.stem}: 同轮四个ready已齐全，继续检查snapshot')
        destination=location(self.root,account,values)
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
                payload=dict(account=str(account),root=str(self.root),origin=meta['origin'],initializing=False,stamp=stamp)
                env=dict(os.environ,ROBUST40_SERVER_WORKER=json.dumps(payload),PYTHONIOENCODING='utf-8')
                return [sys.executable,str(self.root/'tools/robust40/cli/server_robust40.py'),'trade','--account',str(account),'--stamp',str(stamp)],env
            except (OSError,ValueError,pa.ArrowException):reason='Arrow unavailable or incomplete'
        self._launch(account,run_time,values,reason,'initialize',market)
        return None,None
