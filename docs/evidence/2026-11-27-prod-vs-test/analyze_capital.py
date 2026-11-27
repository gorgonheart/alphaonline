"""Derive implied capital base per sub-strategy from target_lots / target_alloc_ratio.

implied_capital = |target_lots| * price / |target_alloc_ratio|
"""
import json
import statistics
from collections import defaultdict

TAG = "2026-11-27_10"
t = json.load(open("17-test-products.json", encoding="utf-8"))
p = json.load(open("16-prod-products.json", encoding="utf-8"))


def implied(doc):
    alloc = {}
    strat = {}
    for r in doc["latest_rows"]["select_coin"]:
        k = r["symbol"]
        a = abs(float(r.get("target_alloc_ratio") or 0))
        if a > 0:
            alloc[k] = a
            strat[k] = (r["strategy"], r["parent_name"], r["cap_weight"])
    lots = doc["position_results"][TAG]["lots"]
    price = doc["position_results"][TAG]["price"]
    out = {}
    for s, l in lots.items():
        if abs(l) == 0 or s not in alloc or not price.get(s):
            continue
        out[s] = abs(l) * price[s] / alloc[s]
    return out, strat


it, strat = implied(t)
ip, _ = implied(p)
common = sorted(set(it) & set(ip))
print("common symbols with derivable capital: %d" % len(common))

by = defaultdict(lambda: ([], []))
for s in common:
    st, parent, cw = strat[s]
    by[st][0].append(it[s])
    by[st][1].append(ip[s])

print("%-36s %6s %12s %12s %8s" % ("strategy", "n", "test_cap", "prod_cap", "t/p"))
rows = []
for st in sorted(by):
    a, b = by[st]
    ma, mb = statistics.median(a), statistics.median(b)
    rows.append((st, len(a), ma, mb, ma / mb if mb else 0))
for st, n, ma, mb, r in sorted(rows, key=lambda x: x[4]):
    print("%-36s %6d %12.1f %12.1f %8.3f" % (st, n, ma, mb, r))

allt = [it[s] for s in common]
allp = [ip[s] for s in common]
print("\noverall median  test=%.1f prod=%.1f ratio=%.3f" % (
    statistics.median(allt), statistics.median(allp),
    statistics.median(allt) / statistics.median(allp)))
print("overall sum     test=%.0f prod=%.0f ratio=%.3f" % (
    sum(allt), sum(allp), sum(allt) / sum(allp)))
