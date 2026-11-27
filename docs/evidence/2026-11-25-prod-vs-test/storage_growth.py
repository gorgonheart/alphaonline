"""Read-only: aggregate files modified today / after this morning's release (10:57 CST).

Usage: python - <data_root> [<data_root> ...]
Prints, per L2 sub-directory under each root:
  file count and summed apparent size for files with mtime >= 2026-11-25 00:00
  file count and summed apparent size for files with mtime >= 2026-11-25 10:57
Nothing is modified.
"""
import collections
import datetime
import os
import sys

DAY = datetime.datetime.strptime("2026-11-25 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
RELEASE = datetime.datetime.strptime("2026-11-25 10:57:00", "%Y-%m-%d %H:%M:%S").timestamp()

for root in sys.argv[1:]:
    print("=== ROOT", root)
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])
    total = [0, 0, 0, 0]
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            parts = os.path.relpath(p, root).split(os.sep)
            key = os.sep.join(parts[:2]) if len(parts) > 1 else parts[0]
            a = agg[key]
            if st.st_mtime >= DAY:
                a[0] += 1
                a[1] += st.st_size
                total[0] += 1
                total[1] += st.st_size
            if st.st_mtime >= RELEASE:
                a[2] += 1
                a[3] += st.st_size
                total[2] += 1
                total[3] += st.st_size
    print("%-58s %10s %14s %10s %14s" % ("L2_DIR", "DAY_FILES", "DAY_BYTES", "POST_FILES", "POST_BYTES"))
    for k in sorted(agg, key=lambda x: -agg[x][3])[:35]:
        a = agg[k]
        print("%-58s %10d %14d %10d %14d" % (k, a[0], a[1], a[2], a[3]))
    print("%-58s %10d %14d %10d %14d" % ("__TOTAL__", total[0], total[1], total[2], total[3]))
