"""Bound in-flight arguments/results without changing executor or task semantics."""
from concurrent.futures import FIRST_COMPLETED, wait
from itertools import islice


def iter_results(executor, function, arguments, max_pending, before_submit=None):
    """Yield completed results; release futures before reading the next input batch."""
    source = iter(arguments)
    if before_submit:
        before_submit()
    pending = {executor.submit(function, *args) for args in islice(source, max_pending)}
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        count = len(done)
        while done:
            future = done.pop()
            result = future.result()
            del future
            yield result
            del result
        batch = list(islice(source, count))
        if batch and before_submit:
            before_submit()
        pending.update(executor.submit(function, *args) for args in batch)
