"""Exact-byte 25m-scope comparison from 48/49 raw [T] sections.

Outputs 50-25m-storage-diff.csv and a plain-text conclusion (51).
Only 25m-specific directories plus the shared 5m K-line / WS-cache base are
included; other offsets, other accounts and the 2026-11-04 research snapshots
are deliberately excluded.
"""
import csv

EXCLUSIVE = [
    "preprocess_1h_resample/25m",
    "binance_spot_1h_resample/25m",
    "data_api_spot_1h_resample/25m",
    "binance_swap_1h_resample/25m",
    "data_api_swap_1h_resample/25m",
    "25m-eart1106",
    "snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m",
    "snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m.previous",
    "子策略回测结果/25m-eart1106_MODEL_ROBUST40_V1_25m",
    "server_initialization/25m-eart1106",
]
SHARED = ["ws_cache", "binance_spot_5m", "binance_swap_5m"]


def parse(path):
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        parts = line.rstrip("\n").split(" ", 3)
        if len(parts) != 4:
            continue
        size, n, r, p = parts
        if not size.isdigit():
            continue
        key = p.replace("/home/ubuntu/alpha2/", "").replace("/home/ubuntu/alpha/", "")
        key = key.replace("/home/ubuntu/alphatrade/", "")
        key = key.replace("data/", "", 1)
        out[key] = (int(size), int(n), int(r))
    return out


t, p = parse("48-test-25m-storage.txt"), parse("49-prod-25m-storage.txt")
rows, lines = [], []


def fmt(b):
    return "%.1f" % (b / 1024**2)


for group, keys in [("25m专属", EXCLUSIVE), ("共享底座", SHARED)]:
    ta = pa = 0
    for k in keys:
        a = t.get(k, (0, 0, 0))
        b = p.get(k, (0, 0, 0))
        ta += a[0]
        pa += b[0]
        d = a[0] - b[0]
        pct = (d / b[0] * 100) if b[0] else ""
        rows.append([group, k, a[0], b[0], d, pct, fmt(a[0]), fmt(b[0]),
                     a[1], b[1], a[2], b[2]])
        lines.append("%-14s %-62s test=%9.1f MiB prod=%9.1f MiB delta=%+9.1f MiB (%s%%) files %d/%d ready %d/%d"
                     % (group, k, a[0] / 1024**2, b[0] / 1024**2, d / 1024**2,
                        ("%+.2f" % pct) if pct != "" else "n/a", a[1], b[1], a[2], b[2]))
    rows.append([group, "__SUBTOTAL__", ta, pa, ta - pa, (ta - pa) / pa * 100 if pa else "",
                 fmt(ta), fmt(pa), "", "", "", ""])
    lines.append("%-14s %-62s test=%9.1f MiB prod=%9.1f MiB delta=%+9.1f MiB (%.2f%%)"
                 % (group, "__SUBTOTAL__", ta / 1024**2, pa / 1024**2,
                    (ta - pa) / 1024**2, (ta - pa) / pa * 100 if pa else 0))
    globals()["sum_" + ("a" if group == "25m专属" else "b")] = (ta, pa)

total_t = sum_a[0] + sum_b[0]
total_p = sum_a[1] + sum_b[1]
rows.append(["全部", "__TOTAL__", total_t, total_p, total_t - total_p,
             (total_t - total_p) / total_p * 100, fmt(total_t), fmt(total_p), "", "", "", ""])
lines.append("%-14s %-62s test=%9.1f MiB prod=%9.1f MiB delta=%+9.1f MiB (%.2f%%)"
             % ("全部", "__TOTAL__", total_t / 1024**2, total_p / 1024**2,
                (total_t - total_p) / 1024**2, (total_t - total_p) / total_p * 100))
lines.append("")
lines.append("增大的目录（25m 口径内）:")
for r in rows:
    if r[1] not in ("__SUBTOTAL__", "__TOTAL__") and r[4] > 0:
        pct = r[5] if isinstance(r[5], (int, float)) else None
        pct_txt = ("%+.2f%%" % pct) if pct is not None else "正式机无此目录"
        lines.append("  %-62s +%d bytes (%s) %s" % (r[1], r[4], pct_txt, r[0]))

with open("50-25m-storage-diff.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["group", "dir", "test_bytes", "prod_bytes", "delta_bytes", "delta_pct",
                "test_MiB", "prod_MiB", "test_files", "prod_files", "test_ready", "prod_ready"])
    w.writerows(rows)
with open("51-25m-storage-conclusion.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
