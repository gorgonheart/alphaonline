"""Small structured records in the existing worker log; no market/state I/O."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import time

_current = ContextVar('robust40_telemetry', default=None)


def _write(message):
    from core.utils.log_kit import logger
    logger.info(message)


def active():
    return _current.get() is not None


def record(**values):
    state=_current.get()
    if state is not None:state.update(values)


def reset_timer(*, start_perf=None, **values):
    state = _current.get()
    if state is not None:
        state['_start'] = time.perf_counter() if start_perf is None else start_perf
        if values:
            state.update(values)


def add(**values):
    state=_current.get()
    if state is not None:
        for key,value in values.items():state[key]=state.get(key,0)+value


def fields(names, key='changed_fields'):
    state=_current.get()
    if state is not None:
        state[key]=sorted(set(state.get(key,[])) | set(map(str,names)))


def emit(event):
    state=_current.get()
    if state is not None:
        payload={k:v for k,v in state.items() if not k.startswith('_')}
        payload.update(event=event,elapsed_s=round(time.perf_counter()-state['_start'],6))
        # Diagnostics must never turn a successful state commit into a failure.
        try:_write('MODEL_REPLAY '+json.dumps(payload,ensure_ascii=False,default=str))
        except Exception:pass


@contextmanager
def phase(name):
    if not active():
        yield
        return
    start=time.perf_counter()
    _current.get()['_'+name+'_start']=start
    try:yield
    except BaseException:
        state=_current.get()
        state.setdefault('failed_phase',name)
        raise
    finally:add(**{name:round(time.perf_counter()-start,6)})


def commit():
    state=_current.get()
    if state is not None:
        record(committed=True,calculation_s=round(time.perf_counter()-state['_start'],6))
        if '_account_flow_s_start' in state:
            record(account_calculation_s=round(time.perf_counter()-state['_account_flow_s_start'],6))
        emit('calculation_committed')


@contextmanager
def round_log(account, stamp, kind='trade'):
    token=_current.set(dict(account=account,stamp=int(stamp),kind=kind,
        attempt=f'{os.getpid()}-{time.time_ns()}',_start=time.perf_counter(),
        mode='initialization' if kind in ('initialize','compute') else 'preflight',committed=False,
        trained_quarters=0,reused_quarters=0,scoring_calls=0,status='running'))
    emit('start')
    try:
        yield
        if _current.get()['status']=='running':record(status='ok')
    except BaseException as exc:
        # Do not duplicate exception text: exchange exceptions may contain URLs/signatures.
        record(status='error',error_type=type(exc).__name__)
        raise
    finally:
        record(total_s=round(time.perf_counter()-_current.get()['_start'],6))
        emit('end')
        _current.reset(token)
