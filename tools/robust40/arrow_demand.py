"""Bounded publication demand per installation/account; no market data or secrets."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path


def live_rows(values):
    """Match the existing reader's mapping warmup, before its final LIVE_BARS crop."""
    from .initialization_config import LIVE_BARS
    import config
    minimum = values.get('min_kline_num')
    if minimum is None:
        minimum = config.min_kline_num
    return max(5000, LIVE_BARS + int(minimum) + 1)


@contextmanager
def _request_lock(path):
    # All publishers of this request use the same OS lock, including the
    # controller and initialization subprocess. Process death releases it.
    with path.with_suffix('.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def set_demand(market, root, account, minute, full, *, stamp=0, rows=5000):
    from .initialization_config import ARROW_DEMAND_ENABLED, ARROW_CONSUMER_NAME
    if not ARROW_DEMAND_ENABLED:
        return
    if type(stamp) is not int or stamp < 0 or type(rows) is not int or rows < 5000:
        raise ValueError('Invalid Arrow demand stamp/depth')
    origin = str(Path(root).resolve())
    owner = base64.urlsafe_b64encode((ARROW_CONSUMER_NAME or Path(root).name).encode()).decode().rstrip('=')
    name = base64.urlsafe_b64encode(Path(account).stem.encode()).decode().rstrip('=')
    folder = Path(market)/f'{minute}m'/'.arrow-demand'/owner
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(name+'.json')
    with _request_lock(path):
        old = json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
        if old and old.get('owner') != origin:
            raise ValueError('Arrow consumer name collision; configure ARROW_CONSUMER_NAME')
        # Remove only our interrupted atomic write, even on an idempotent retry.
        path.with_suffix('.tmp').unlink(missing_ok=True)
        if old.get('stamp', 0) > stamp:
            return  # An older completion must never release a newer rebuild.
        value = dict(version=1, rows=0 if full else rows, owner=origin, stamp=stamp)
        if old == value:
            return
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(value, sort_keys=True), encoding='utf8')
        temp.replace(path)


def release_demand(market, root, account, minute, *, stamp=0, rows=None, values=None):
    """Only after installation; notification failure cannot invalidate a snapshot."""
    from .initialization_config import ARROW_DEMAND_ENABLED
    if not ARROW_DEMAND_ENABLED:
        return
    try:
        if rows is None:
            rows = live_rows(values or {})
        set_demand(market, root, account, minute, False, stamp=stamp, rows=rows)
    except (OSError, ValueError) as exc:
        from .lifecycle import emit
        emit(f'ARROW_DEMAND release deferred: {Path(account).stem}: {exc}')
