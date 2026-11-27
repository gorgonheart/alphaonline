"""Locate the source of target_lots scale gap: implied notional per symbol.

notional = target_lots * price. For symbols held by both sides with the same
direction, compute test/prod notional ratio, overall and grouped by strategy.
"""
import json
import statistics
from collections import defaultdict

TAGS = ["2026-11-26_17", "2026-11-26_18"]
t = json.load(open("16-test-products.json", encoding="utf-8"))
p = json.load(open("17-prod-products.json", encoding="utf-8"))
out = []


def w(s=""):
    out.append(s)
    print(s)


for tag in TAGS:
    tl = t["position_results"][tag]["lots"]
    pl = p["position_results"][tag]["lots"]
    tp = t["position_results"][tag]["price"]
    pp = p["position_results"][tag]["price"]
    rows = []
    for s in tl:
        a, b = tl.get(s, 0.0), pl.get(s, 0.0)
        if a == 0 or b == 0 or (a > 0) != (b > 0):
            continue
        pt, ppr = tp.get(s, 0), pp.get(s, 0)
        if not pt or not ppr:
            continue
        at, ap = abs(a) * pt, abs(b) * ppr
        if ap <= 0:
            continue
        rows.append((s, at, ap, at / ap))
    ratios = [r[3] for r in rows]
    w("\n--- %s notional ratio (test/prod), same-direction symbols=%d ---" % (tag, len(rows)))
    w("median=%.4f  mean=%.4f  p10=%.4f  p90=%.4f  min=%.4f  max=%.4f" % (
        statistics.median(ratios), statistics.mean(ratios),
        sorted(ratios)[int(len(ratios) * 0.1)], sorted(ratios)[int(len(ratios) * 0.9)],
        min(ratios), max(ratios)))
    w("sum notional test=%.2f prod=%.2f ratio=%.4f" % (
        sum(r[1] for r in rows), sum(r[2] for r in rows),
        sum(r[1] for r in rows) / sum(r[2] for r in rows)))
    buckets = defaultdict(int)
    for r in ratios:
        buckets[round(r, 1)] += 1
    w("ratio histogram (rounded 0.1): %s" % dict(sorted(buckets.items())))

    # group by strategy using latest select_coin rows if tag matches latest
    if tag == TAGS[-1]:
        st = {}
        for row in t["latest_rows"]["select_coin"]:
            st.setdefault(row["symbol"], row["strategy"])
        by = defaultdict(list)
        for s, at, ap, r in rows:
            by[st.get(s, "?")].append(r)
        w("by strategy (median ratio, n):")
        for k in sorted(by):
            v = by[k]
            w("  %-34s median=%.4f n=%d" % (k, statistics.median(v), len(v)))

open("22-amount-ratio.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
