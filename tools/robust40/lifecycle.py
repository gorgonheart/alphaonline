"""Bounded scratch ownership. Never scan market contents to decide retention."""
from pathlib import Path
import json
import os
import shutil
import time
from functools import wraps


def emit(message):
    try:print(message,flush=True)
    except (OSError,ValueError):pass


def nonfatal(function):
    @wraps(function)
    def call(*args,**kwargs):
        try:return function(*args,**kwargs)
        except Exception as exc:
            emit(f'SCRATCH_CLEANUP deferred: {exc}')
            return False
    return call


@nonfatal
def discard(path, boundary):
    path,boundary=Path(path),Path(boundary).resolve()
    if path.is_symlink() or path.absolute()!=path.resolve() or path.resolve()==boundary or not path.resolve().is_relative_to(boundary):return False
    started=time.perf_counter()
    try:
        if path.exists():shutil.rmtree(path)
        return True
    except OSError as exc:
        emit(f'Scratch cleanup deferred: {path}: {exc}')
        return False
    finally:
        emit('SCRATCH_CLEANUP '+json.dumps(dict(path=str(path),elapsed_s=time.perf_counter()-started)))


def active_roots():
    # Only used for abandoned scratch, never on an ordinary trading round.
    import psutil
    roots=set()
    for proc in psutil.process_iter(['pid','name','username']):
        if proc.pid==os.getpid() or 'python' not in (proc.info['name'] or '').lower():continue
        try:
            if hasattr(os,'getuid') and proc.uids().real!=os.getuid():continue
            value=proc.environ().get('ROBUST40_SERVER_WORKER')
            if value:roots.add(Path(json.loads(value)['root']).resolve())
        except (psutil.NoSuchProcess,psutil.ZombieProcess):continue
        except (psutil.AccessDenied,OSError,ValueError,KeyError):return None
    return roots


@nonfatal
def prune_logs(folder,keep,protect=None):
    # At most one flat account directory, no recursive sizes or stat per candle.
    files=list(Path(folder).glob('*.log'))
    if len(files)<=keep:return
    candidates=[p for p in files if p.stem!=str(protect)]
    for path in sorted(candidates,key=lambda p:int(p.stem) if p.stem.isdigit() else -1)[:len(files)-keep]:
        try:path.unlink()
        except OSError as exc:emit(f'Log cleanup deferred: {path}: {exc}')


@nonfatal
def sweep_initialization(root,account,current):
    from .initialization_config import DIAGNOSTIC_LOG_COUNT
    folder=Path(root).resolve()/'data/server_initialization'/Path(account).stem
    if not folder.exists():return
    stale=[p for p in folder.iterdir() if p.name.isdigit() and p.name!=str(current) and p.is_dir()]
    if stale:
        active=active_roots()
        if active is not None:
            for path in stale:
                if path.resolve() not in active:discard(path,folder)
    prune_logs(folder,DIAGNOSTIC_LOG_COUNT,protect=current)


@nonfatal
def finish_initialization(workspace,root,account,stamp):
    path=Path(workspace)
    expected=Path(root).resolve()/'data/server_initialization'/Path(account).stem/str(stamp)
    if path.absolute()!=expected:return
    if not path.exists():return
    active=active_roots()
    if active is not None and path.resolve() not in active:discard(path,path.parent)


@nonfatal
def retire_replay(stage,destination):
    stage,destination=Path(stage),Path(destination)
    parent=destination.parent.parent/'model_replay'/destination.name
    if stage.parent.parent==parent and stage.name=='snapshot':discard(stage.parent,parent)


@nonfatal
def sweep_replay(destination):
    destination=Path(destination)
    folder=destination.parent.parent/'model_replay'/destination.name
    if not folder.exists():return
    candidates=[p for p in folder.iterdir() if p.is_dir() and p.name.startswith(('work-','interrupted-','unreadable-'))]
    if not candidates:return
    active=active_roots()
    # Orphan trade workers inherit project root, unlike initializer workers.
    project=destination.parent.parent.parent.resolve()
    if active is None or project in active:return
    for p in candidates:discard(p,folder)


@nonfatal
def discard_interrupted(path,destination):
    # Keep at most one tiny metadata sample per account, never model/market data.
    path,destination=Path(path),Path(destination)
    diagnostic=destination.parent.parent/'model_replay'/destination.name/'last-failure'
    try:
        source=path/'warmup.pkl'
        if source.is_file() and source.stat().st_size<=65536:
            diagnostic.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,diagnostic/'warmup.pkl')
    except OSError as exc:
        emit(f'Diagnostic copy skipped: {exc}')
    return discard(path,path.parent)
