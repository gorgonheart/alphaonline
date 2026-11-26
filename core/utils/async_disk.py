"""Scoped single-thread background disk writer for overlapping non-blocking writes."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock


class AsyncDiskWriter:
    """Queue disk writes on one dedicated background thread and flush with error propagation."""

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='async_disk')
        self._futures: list[Future] = []
        self._lock = Lock()

    def submit(self, fn, *args, **kwargs) -> Future:
        fut = self._pool.submit(fn, *args, **kwargs)
        with self._lock:
            self._futures.append(fut)
        return fut

    def flush(self) -> None:
        with self._lock:
            pending = list(self._futures)
            self._futures.clear()
        first_exc = None
        for fut in pending:
            try:
                fut.result()
            except BaseException as exc:
                if first_exc is None:
                    first_exc = exc
        if first_exc is not None:
            raise first_exc

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self._pool.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            with self._lock:
                pending = list(self._futures)
                self._futures.clear()
            for fut in pending:
                try:
                    fut.result()
                except BaseException:
                    pass
            self._pool.shutdown(wait=True)
