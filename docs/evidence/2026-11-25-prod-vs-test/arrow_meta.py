"""Read-only: inspect Arrow IPC footer metadata for the given files.

Prints file path, os size, IPC format, record-batch count, row counts, column
count and the first column names. Uses memory_map so no column data is copied.
"""
import os
import sys

try:
    import pyarrow as pa
except Exception as exc:  # pragma: no cover
    print("PYARROW_IMPORT_ERROR", type(exc).__name__, exc)
    sys.exit(0)

for p in sys.argv[1:]:
    print("FILE", p)
    try:
        print(" os_bytes", os.path.getsize(p))
        with pa.memory_map(p, "r") as src:
            try:
                reader = pa.ipc.open_file(src)
                kind = "ipc_file"
            except Exception:
                src.seek(0)
                reader = pa.ipc.open_stream(src)
                kind = "ipc_stream"
            print(" format", kind, "ncols", len(reader.schema))
            print(" first_cols", reader.schema.names[:6])
            if kind == "ipc_file":
                n = reader.num_record_batches
                print(" n_batches", n)
                rows = 0
                for i in range(n):
                    b = reader.get_batch(i)
                    rows += b.num_rows
                    if i < 5:
                        print("  batch", i, "rows", b.num_rows)
                print(" total_rows", rows)
            else:
                n = 0
                rows = 0
                for b in reader:
                    n += 1
                    rows += b.num_rows
                    if n <= 5:
                        print("  batch", n - 1, "rows", b.num_rows)
                print(" n_batches", n, "total_rows", rows)
    except Exception as exc:
        print(" ERR", type(exc).__name__, exc)
