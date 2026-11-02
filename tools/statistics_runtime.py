"""Small statistics-only IO/concurrency helpers; no trading dependencies."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


@contextmanager
def temporary_path(directory, suffix):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='statistics_', suffix=suffix, dir=directory)
    os.close(fd)
    try:
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_save(path, writer):
    path = Path(path)
    with temporary_path(path.parent, path.suffix) as temporary:
        writer(temporary)
        os.replace(temporary, path)


@contextmanager
def account_lock(directory):
    """Nonblocking OS lock; process crashes release it. Do not unlink lock inode."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'statistics.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


class StatisticsTasks:
    """No queue: skip busy accounts/capacity; always observe worker failures."""
    def __init__(self, workers=2, log=print):
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.workers, self.log = workers, log
        self.active = set()
        self.lock = Lock()

    def submit(self, account, func, *args):
        with self.lock:
            if account in self.active or len(self.active) >= self.workers:
                self.log(f'{account}: statistics busy; skip this round (no backlog)')
                return False
            self.active.add(account)
        try:
            future = self.pool.submit(func, *args)
        except BaseException:
            with self.lock:
                self.active.remove(account)
            raise
        def finished(result):
            try:
                result.result()
            except Exception as error:
                self.log(f'{account}: statistics failed: {error}')
            finally:
                with self.lock:
                    self.active.remove(account)
        future.add_done_callback(finished)
        return True
