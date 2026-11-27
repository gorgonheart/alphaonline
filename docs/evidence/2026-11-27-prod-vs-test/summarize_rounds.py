"""Summarize today's rounds on both hosts (2026-11-27)."""
import csv
import statistics
from collections import defaultdict


def load(path, has_ts=True):
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        r = csv.DictReader(fh)
        for d in r:
            if not d.get("account"):
                continue
            for k in ("read_s", "prep_s", "calc_s", "acctcalc_s", "flow_s", "total_s", "wait_s"):
                try:
                    d[k] = float(d[k])
                except (TypeError, ValueError):
                    d[k] = 0.0
            rows.append(d)
    return rows


prod = load("02-prod-rounds-1127.csv")
test = load("04-test-rounds-1127.csv")

print("=== 轮次计数 ===")
print("prod rounds=%d accounts=%d" % (len(prod), len({r["account"] for r in prod})))
print("test rounds=%d accounts=%d" % (len(test), len({r["account"] for r in test})))

print("\n=== prod 按账户/模式 (median total_s, n) ===")
g = defaultdict(list)
for r in prod:
    g[(r["account"], r["mode"])].append(r["total_s"])
for k in sorted(g):
    v = g[k]
    print("  %-22s %-12s n=%2d median=%6.2f min=%6.2f max=%6.2f" % (
        k[0], k[1], len(v), statistics.median(v), min(v), max(v)))

print("\n=== prod 增量 vs 回放 (全部账户) ===")
for mode in ("incremental", "replay"):
    v = [r["total_s"] for r in prod if r["mode"] == mode]
    if not v:
        continue
    print("  %-12s n=%2d median=%6.2f  mean=%6.2f  prep_med=%.2f read_med=%.2f calc_med=%.2f" % (
        mode, len(v), statistics.median(v), statistics.mean(v),
        statistics.median([r["prep_s"] for r in prod if r["mode"] == mode]),
        statistics.median([r["read_s"] for r in prod if r["mode"] == mode]),
        statistics.median([r["calc_s"] for r in prod if r["mode"] == mode])))

print("\n=== prod 09:30 重启前/后 (同 6 账户) ===")
before = [r for r in prod if r["ts"] < "2026-11-27 09:30"]
after = [r for r in prod if r["ts"] >= "2026-11-27 09:30"]
for label, sel in (("重启前 00:08-09:28", before), ("重启后 09:36-now", after)):
    if not sel:
        continue
    print("  %-22s n=%2d median=%6.2f calc_med=%.2f prep_med=%.2f read_med=%.2f" % (
        label, len(sel), statistics.median([r["total_s"] for r in sel]),
        statistics.median([r["calc_s"] for r in sel]),
        statistics.median([r["prep_s"] for r in sel]),
        statistics.median([r["read_s"] for r in sel])))

print("\n=== test 25m 全部轮次 ===")
v = [r["total_s"] for r in test]
print("  n=%d median=%.2f min=%.2f max=%.2f calc_med=%.2f prep_med=%.2f" % (
    len(v), statistics.median(v), min(v), max(v),
    statistics.median([r["calc_s"] for r in test]),
    statistics.median([r["prep_s"] for r in test])))

print("\n=== 同切点 25m 对比 (cutoff 相同) ===")
by_cut_p = {}
by_cut_t = {}
for r in prod:
    if r["account"] == "25m-eart1106":
        by_cut_p[r["cutoff"]] = r
for r in test:
    by_cut_t[r["cutoff"]] = r
print("  %-20s %-12s %8s %8s %8s %8s" % ("cutoff", "mode(prod)", "t_total", "p_total", "t_calc", "p_calc"))
for cut in sorted(set(by_cut_p) & set(by_cut_t))[-8:]:
    a, b = by_cut_t[cut], by_cut_p[cut]
    print("  %-20s %-12s %8.2f %8.2f %8.2f %8.2f" % (
        cut, b["mode"], a["total_s"], b["total_s"], a["calc_s"], b["calc_s"]))

print("\n=== 每小时有轮次覆盖的墙钟秒数 (prod, 估算 duty cycle) ===")
hour_wall = defaultdict(float)
for r in prod:
    # 轮次本体 (total_s) + 前后等待不计入
    hour_wall[r["ts"][11:13]] += r["total_s"]
for h in sorted(hour_wall):
    print("  %s时: %6.1f s / 3600 s = %5.1f%%" % (h, hour_wall[h], hour_wall[h] / 3600 * 100))
