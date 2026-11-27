#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Profile a round from round_sample.py CSV output."""
import csv
import sys

COMP = ["alpha2", "alpha_legacy", "model", "trade_main", "trade_spawn", "spawn", "legacy", "other"]


def load(path):
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("#") or not line:
                continue
            rows.append(line)
    head = None
    out = []
    for r in rows:
        p = r.split(",")
        if head is None:
            if p[0] == "ts":
                head = p
            continue
        d = {}
        for i, k in enumerate(head):
            v = p[i] if i < len(p) else ""
            try:
                d[k] = float(v)
            except (ValueError, TypeError):
                d[k] = v
        if isinstance(d.get("busy"), float):
            out.append(d)
    return out


def sec2hms(t):
    return t


def summarize(rows, label):
    if not rows:
        return
    n = len(rows)
    dur = n
    tot = sum(r["busy"] for r in rows) / n
    print("\n--- %s  n=%ds ---" % (label, dur))
    print("  系统 busy 均值 %.1f%% (%.2f 核)  峰值 %.1f%%  PSI cpu/mem/io 峰值 %.2f/%.2f/%.2f" % (
        tot, tot / 100 * 4, max(r["busy"] for r in rows),
        max(r["psi_cpu"] for r in rows), max(r["psi_mem"] for r in rows), max(r["psi_io"] for r in rows)))
    print("  MemAvailable min %.0fMB 均值 %.0fMB | swap used max %.0fMB 均值 %.0fMB | dirty max %.0fMB" % (
        min(r["memavail"] for r in rows), sum(r["memavail"] for r in rows) / n,
        max(r["swapused"] for r in rows), sum(r["swapused"] for r in rows) / n,
        max(r["dirty"] for r in rows)))
    print("  swap in/out 合计 %.1f/%.1f MB (%.2f/%.2f MB/s) | 主缺页 %.0f/s" % (
        sum(r["pswpin_ps"] for r in rows) * 4 / 1024, sum(r["pswpout_ps"] for r in rows) * 4 / 1024,
        sum(r["pswpin_ps"] for r in rows) * 4 / 1024 / dur / 1.0 if dur else 0,
        sum(r["pswpout_ps"] for r in rows) * 4 / 1024 / dur,
        sum(r["majflt_ps"] for r in rows) / n))
    print("  磁盘 读 %.2f MB/s 写 %.2f MB/s | IOPS %.0f/%.0f | util 均值 %.1f%% 峰 %.1f%% | await 读 %.2fms 写 %.2fms" % (
        sum(r["io_rmbs"] for r in rows) / n, sum(r["io_wmbs"] for r in rows) / n,
        sum(r["io_riops"] for r in rows) / n, sum(r["io_wiops"] for r in rows) / n,
        sum(r["io_util"] for r in rows) / n, max(r["io_util"] for r in rows),
        sum(r["io_ar"] for r in rows) / n, sum(r["io_aw"] for r in rows) / n))
    print("  %-12s %8s %8s %10s %10s %10s" % ("组件", "均核", "峰值核", "核秒", "RSS峰MB", "读MB/s"))
    tot_cores = sum(sum(r.get(c + "_cpu", 0) for r in rows) for c in COMP)
    for c in COMP:
        cs = [r.get(c + "_cpu", 0) for r in rows]
        if not any(cs):
            continue
        s = sum(cs)
        print("  %-12s %8.2f %8.2f %10.0f %10.0f %10.2f  (占 %.1f%%)" % (
            c, s / n, max(cs), s, max(r.get(c + "_rss", 0) for r in rows),
            sum(r.get(c + "_rd", 0) for r in rows) / n,
            100 * s / tot_cores if tot_cores else 0))
    tot_wr = sum(sum(r.get(c + "_wr", 0) for r in rows) for c in COMP)
    print("  组件写盘合计 %.0f MB" % tot_wr)
    for c in COMP:
        w = sum(r.get(c + "_wr", 0) for r in rows)
        if w > 50:
            print("    %-12s 写 %7.0f MB" % (c, w))


def main():
    path = sys.argv[1]
    rows = load(path)
    print("samples=%d  %s -> %s" % (len(rows), rows[0]["ts"], rows[-1]["ts"]))
    # active segments: busy>=30 or comp cpu sum >0.8
    act = []
    for r in rows:
        cc = sum(r.get(c + "_cpu", 0) for c in COMP)
        act.append(r["busy"] >= 30 or cc >= 0.8)
    i = 0
    segs = []
    while i < len(rows):
        if not act[i]:
            i += 1
            continue
        j = i
        while j < len(rows) and act[j]:
            j += 1
        segs.append((i, j))
        i = j
    print("active segments: %d" % len(segs))
    for a, b in segs:
        print("  %s -> %s  %ds  busy_mean=%.1f%%" % (
            rows[a]["ts"], rows[b - 1]["ts"], b - a, sum(r["busy"] for r in rows[a:b]) / (b - a)))
    for a, b in segs:
        if b - a >= 20:
            summarize(rows[a:b], "段 %s -> %s" % (rows[a]["ts"], rows[b - 1]["ts"]))
    idle = [r for r in rows if not (r["busy"] >= 30 or sum(r.get(c + "_cpu", 0) for c in COMP) >= 0.8)]
    summarize(idle, "空闲/低载")
    # per-second compact
    print("\n逐秒: ts busy avail swap | a2 model tmain tspawn spawn legacy | io_r io_w util pswp_in pswp_out")
    for r in rows:
        print("%s %5.1f %5.0f %5.0f | %4.1f %4.1f %4.1f %4.1f %4.1f %4.1f | %5.1f %5.1f %5.1f %5.1f %5.1f" % (
            r["ts"], r["busy"], r["memavail"], r["swapused"],
            r.get("alpha2_cpu", 0), r.get("model_cpu", 0), r.get("trade_main_cpu", 0),
            r.get("trade_spawn_cpu", 0), r.get("spawn_cpu", 0), r.get("legacy_cpu", 0),
            r["io_rmbs"], r["io_wmbs"], r["io_util"], r["pswpin_ps"], r["pswpout_ps"]))


if __name__ == "__main__":
    main()
