"""Compare 2026-11-26 17/18 products between test and prod.

Reads 16-test-products.json / 17-prod-products.json, writes 18-product-diff.txt.
Compares: shape, group sets and cap_weight sums, parent aggregates,
position direction per symbol, price per symbol, and latest-tag row-level values.
"""
import json

TAG = ["2026-11-26_17", "2026-11-26_18"]
KEY = ["symbol", "strategy", "方向", "is_spot", "offset"]

t = json.load(open("16-test-products.json", encoding="utf-8"))
p = json.load(open("17-prod-products.json", encoding="utf-8"))
out = []


def w(s=""):
    out.append(s)
    print(s)


w("=== 2026-11-26 product comparison (test vs prod, same account 25m-eart1106) ===")
for tag in TAG:
    ts, ps = t["select_coin"][tag], p["select_coin"][tag]
    w("\n--- %s ---" % tag)
    w("select_coin shape      test=%s prod=%s" % (ts["shape"], ps["shape"]))
    w("group_count            test=%d prod=%d" % (ts["group_count"], ps["group_count"]))
    w("cap_weight_total       test=%.8f prod=%.8f diff=%+.8f" % (
        ts["cap_weight_total"], ps["cap_weight_total"], ts["cap_weight_total"] - ps["cap_weight_total"]))
    w("long/short/trade rows  test=%d/%d/%d prod=%d/%d/%d" % (
        ts["long_rows"], ts["short_rows"], ts["trade_rows"],
        ps["long_rows"], ps["short_rows"], ps["trade_rows"]))

    tg, pg = ts["groups"], ps["groups"]
    only_t, only_p = sorted(set(tg) - set(pg)), sorted(set(pg) - set(tg))
    w("groups only_test=%d only_prod=%d" % (len(only_t), len(only_p)))
    for k in only_t[:10]:
        w("  ONLY_TEST %s n=%d w=%.8f" % (k, tg[k][0], tg[k][1]))
    for k in only_p[:10]:
        w("  ONLY_PROD %s n=%d w=%.8f" % (k, pg[k][0], pg[k][1]))
    gdiff = [(k, tg[k], pg[k]) for k in set(tg) & set(pg)
             if tg[k][0] != pg[k][0] or abs(tg[k][1] - pg[k][1]) > 1e-9]
    w("groups differing rows/weight=%d" % len(gdiff))
    for k, a, b in gdiff[:10]:
        w("  DIFF %s test=%s prod=%s" % (k, a, b))

    tpar, ppar = ts["parent"], ps["parent"]
    pdiff = [(k, tpar[k], ppar.get(k)) for k in tpar
             if k not in ppar or tpar[k][0] != ppar[k][0] or abs(tpar[k][1] - ppar[k][1]) > 1e-9]
    w("parent keys test=%d prod=%d differing=%d" % (len(tpar), len(ppar), len(pdiff)))
    for k, a, b in pdiff[:10]:
        w("  PARENT_DIFF %s test=%s prod=%s" % (k, a, b))

    tr, pr = t["position_results"][tag], p["position_results"][tag]
    w("position_results shape test=%s prod=%s" % (tr["shape"], pr["shape"]))
    w("nonzero/long/short/flat test=%d/%d/%d/%d prod=%d/%d/%d/%d" % (
        tr["nonzero"], tr["long"], tr["short"], tr["flat"],
        pr["nonzero"], pr["long"], pr["short"], pr["flat"]))
    w("abs_target_lots_sum   test=%.6f prod=%.6f diff=%+.6f" % (
        tr["abs_target_lots_sum"], pr["abs_target_lots_sum"],
        tr["abs_target_lots_sum"] - pr["abs_target_lots_sum"]))
    td, pdd = tr["direction"], pr["direction"]
    mism = [(s, td.get(s), pdd.get(s)) for s in sorted(set(td) | set(pdd)) if td.get(s) != pdd.get(s)]
    w("direction mismatches  %d / %d symbols" % (len(mism), len(set(td) | set(pdd))))
    for s, a, b in mism[:20]:
        w("  DIR %s test=%s prod=%s" % (s, a, b))
    tprice, pprice = tr["price"], pr["price"]
    pmm = [(s, tprice[s], pprice[s]) for s in tprice if s in pprice and abs(tprice[s] - pprice[s]) > 1e-12]
    w("price mismatches      %d / %d (tol 1e-12)" % (len(pmm), len(tprice)))
    for s, a, b in pmm[:10]:
        w("  PRICE %s test=%.12f prod=%.12f diff=%+.3e" % (s, a, b, a - b))

# latest tag row-level
tag = TAG[-1]
tr_rows = t["latest_rows"]["select_coin"]
pr_rows = p["latest_rows"]["select_coin"]


def key_of(r):
    return "|".join(str(r[k]) for k in KEY)


tm = {key_of(r): r for r in tr_rows}
pm = {key_of(r): r for r in pr_rows}
w("\n--- %s row-level (latest tag) ---" % tag)
w("rows test=%d prod=%d" % (len(tr_rows), len(pr_rows)))
only_t = sorted(set(tm) - set(pm))
only_p = sorted(set(pm) - set(tm))
w("row keys only_test=%d only_prod=%d" % (len(only_t), len(only_p)))
for k in only_t[:10]:
    w("  ROW_ONLY_TEST %s cap=%s dir=%s trade=%s" % (k, tm[k]["cap_weight"], tm[k]["方向"], tm[k]["是否交易"]))
for k in only_p[:10]:
    w("  ROW_ONLY_PROD %s cap=%s dir=%s trade=%s" % (k, pm[k]["cap_weight"], pm[k]["方向"], pm[k]["是否交易"]))
common = set(tm) & set(pm)
maxdiff = {"close": (0, ""), "next_close": (0, ""), "target_alloc_ratio": (0, ""), "cap_weight": (0, "")}
for k in common:
    for f in maxdiff:
        a = float(tm[k].get(f) or 0)
        b = float(pm[k].get(f) or 0)
        if abs(a - b) > maxdiff[f][0]:
            maxdiff[f] = (abs(a - b), k)
for f, (d, k) in maxdiff.items():
    w("max |delta| %-20s = %.3e at %s" % (f, d, k))
sig_diff = [k for k in common if any(abs(float(tm[k].get(f) or 0) - float(pm[k].get(f) or 0)) > 1e-9
                                     for f in ("close", "next_close", "cap_weight", "target_alloc_ratio"))]
w("rows with |delta|>1e-9 in close/next_close/cap_weight/target_alloc_ratio: %d / %d" % (len(sig_diff), len(common)))
for k in sig_diff[:10]:
    w("  SIG %s test(cap=%s alloc=%s close=%s) prod(cap=%s alloc=%s close=%s)" % (
        k, tm[k]["cap_weight"], tm[k]["target_alloc_ratio"], tm[k]["close"],
        pm[k]["cap_weight"], pm[k]["target_alloc_ratio"], pm[k]["close"]))

open("18-product-diff.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
