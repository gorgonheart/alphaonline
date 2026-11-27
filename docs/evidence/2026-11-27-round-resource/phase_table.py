#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-phase resource table for a round CSV.

Usage: profile_round.py <csv> "label,t0,t1" "label,t0,t1" ...
t0/t1 are HH:MM:SS inclusive bounds.
"""
import csv
import sys

COMP = ["alpha2", "alpha_legacy", "model", "trade_main", "trade_spawn", "spawn", "legacy", "other"]


def load(path):
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            rows.append(line.split(","))
    head = None
    out = []
    for p in rows:
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


def slice_rows(rows, t0, t1):
    return [r for r in rows if t0 <= r["ts"] <= t1]


def table(rows, specs):
    print("%-16s %4s %7s %8s %8s %8s %9s %9s %9s %9s" % (
        "阶段", "n", "busy%", "均核", "峰核", "memavail", "swap峰", "读MB/s", "写MB/s", "util%"))
    for label, t0, t1 in specs:
        w = slice_rows(rows, t0, t1)
        if not w:
            print("%-16s  (no data)" % label)
            continue
        n = len(w)
        cores = [sum(r.get(c + "_cpu", 0) for c in COMP) for r in w]
        rd = sum(r["io_rmbs"] for r in w) / n
        wr = sum(r["io_wmbs"] for r in w) / n
        print("%-16s %4d %7.1f %8.2f %8.2f %8.0f %9.0f %9.2f %9.2f %9.1f" % (
            label, n, sum(r["busy"] for r in w) / n, sum(cores) / n, max(cores),
            min(r["memavail"] for r in w), max(r["swapused"] for r in w),
            rd, wr, sum(r["io_util"] for r in w) / n))
        # component split
        parts = []
        for c in COMP:
            s = sum(r.get(c + "_cpu", 0) for r in w) / n
            if s >= 0.05:
                parts.append("%s=%.2f" % (c, s))
        print("      均核拆分: " + ("  ".join(parts) if parts else "-"))
        print("      读合计 %.0fMB 写合计 %.0fMB | swap in/out %.0f/%.0fMB | 主缺页均 %.0f/s | PSI mem/io 峰 %.1f/%.1f" % (
            sum(r["io_rmbs"] for r in w), sum(r["io_wmbs"] for r in w),
            sum(r["pswpin_ps"] for r in w) * 4 / 1024, sum(r["pswpout_ps"] for r in w) * 4 / 1024,
            sum(r["majflt_ps"] for r in w) / n,
            max(r["psi_mem"] for r in w), max(r["psi_io"] for r in w)))


def main():
    rows = load(sys.argv[1])
    print("%s  samples=%d  %s -> %s\n" % (sys.argv[1], len(rows), rows[0]["ts"], rows[-1]["ts"]))
    specs = []
    for a in sys.argv[2:]:
        label, t0, t1 = a.split(",")
        specs.append((label, t0, t1))
    table(rows, specs)


if __name__ == "__main__":
    main()
