"""Compare 2026-11-27 _09/_10 products between test and prod.

Reads 16-prod-products.json / 17-test-products.json.
Writes 18-product-diff.txt, 20-lots-diff.txt, 22-amount-ratio.txt.
"""
import json
import statistics
from collections import defaultdict

TAG = ["2026-11-27_09", "2026-11-27_10"]
KEY = ["symbol", "strategy", "方向", "is_spot", "offset"]

t = json.load(open("17-test-products.json", encoding="utf-8"))
p = json.load(open("16-prod-products.json", encoding="utf-8"))


def run(name):
    out = []

    def w(s=""):
        out.append(s)
        print(s)

    if name == "product":
        w("=== 2026-11-27 product comparison (test vs prod, same account 25m-eart1106) ===")
        for tag in TAG:
            ts, ps = t["select_coin"][tag], p["select_coin"][tag]
            w("\n--- %s ---" % tag)
            w("select_coin shape      test=%s prod=%s" % (ts["shape"], ps["shape"]))
            w("group_count            test=%d prod=%d" % (ts["group_count"], ps["group_count"]))
            w("cap_weight_total       test=%.8f prod=%.8f diff=%+.8f" % (
                ts["cap_weight_total"], ps["cap_weight_total"],
                ts["cap_weight_total"] - ps["cap_weight_total"]))
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
                     if k not in ppar or tpar[k][0] != ppar[k][0]
                     or abs(tpar[k][1] - ppar[k][1]) > 1e-9]
            w("parent keys test=%d prod=%d differing=%d" % (len(tpar), len(ppar), len(pdiff)))
            for k, a, b in pdiff[:10]:
                w("  PARENT_DIFF %s test=%s prod=%s" % (k, a, b))
            tr, pr = t["position_results"][tag], p["position_results"][tag]
            w("position_results shape test=%s prod=%s" % (tr["shape"], pr["shape"]))
            w("nonzero/long/short/flat test=%d/%d/%d/%d prod=%d/%d/%d/%d" % (
                tr["nonzero"], tr["long"], tr["short"], tr["flat"],
                pr["nonzero"], pr["long"], pr["short"], pr["flat"]))
            w("abs_target_lots_sum   test=%.6f prod=%.6f diff=%+.6f (%.2f%%)" % (
                tr["abs_target_lots_sum"], pr["abs_target_lots_sum"],
                tr["abs_target_lots_sum"] - pr["abs_target_lots_sum"],
                (tr["abs_target_lots_sum"] - pr["abs_target_lots_sum"])
                / pr["abs_target_lots_sum"] * 100 if pr["abs_target_lots_sum"] else 0))
            td, pdd = tr["direction"], pr["direction"]
            mism = [(s, td.get(s), pdd.get(s)) for s in sorted(set(td) | set(pdd))
                    if td.get(s) != pdd.get(s)]
            w("direction mismatches  %d / %d symbols" % (len(mism), len(set(td) | set(pdd))))
            for s, a, b in mism[:20]:
                w("  DIR %s test=%s prod=%s" % (s, a, b))
            tprice, pprice = tr["price"], pr["price"]
            pmm = [(s, tprice[s], pprice[s]) for s in tprice
                   if s in pprice and abs(tprice[s] - pprice[s]) > 1e-12]
            w("price mismatches      %d / %d (tol 1e-12)" % (len(pmm), len(tprice)))
            for s, a, b in pmm[:10]:
                w("  PRICE %s test=%.12f prod=%.12f diff=%+.3e" % (s, a, b, a - b))
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
            w("  ROW_ONLY_TEST %s cap=%s dir=%s trade=%s" % (
                k, tm[k]["cap_weight"], tm[k]["方向"], tm[k]["是否交易"]))
        for k in only_p[:10]:
            w("  ROW_ONLY_PROD %s cap=%s dir=%s trade=%s" % (
                k, pm[k]["cap_weight"], pm[k]["方向"], pm[k]["是否交易"]))
        common = set(tm) & set(pm)
        maxdiff = {"close": (0, ""), "next_close": (0, ""),
                   "target_alloc_ratio": (0, ""), "cap_weight": (0, "")}
        for k in common:
            for f in maxdiff:
                a = float(tm[k].get(f) or 0)
                b = float(pm[k].get(f) or 0)
                if abs(a - b) > maxdiff[f][0]:
                    maxdiff[f] = (abs(a - b), k)
        for f, (d, k) in maxdiff.items():
            w("max |delta| %-20s = %.3e at %s" % (f, d, k))
        sig = [k for k in common if any(
            abs(float(tm[k].get(f) or 0) - float(pm[k].get(f) or 0)) > 1e-9
            for f in ("close", "next_close", "cap_weight", "target_alloc_ratio"))]
        w("rows with |delta|>1e-9 in close/next_close/cap_weight/target_alloc_ratio: %d / %d"
          % (len(sig), len(common)))
        for k in sig[:10]:
            w("  SIG %s test(cap=%s alloc=%s close=%s) prod(cap=%s alloc=%s close=%s)" % (
                k, tm[k]["cap_weight"], tm[k]["target_alloc_ratio"], tm[k]["close"],
                pm[k]["cap_weight"], pm[k]["target_alloc_ratio"], pm[k]["close"]))
        return out, "18-product-diff.txt"

    if name == "lots":
        for tag in TAG:
            tl = t["position_results"][tag]["lots"]
            pl = p["position_results"][tag]["lots"]
            tp = t["position_results"][tag]["price"]
            pp = p["position_results"][tag]["price"]
            syms = sorted(set(tl) | set(pl))
            both_zero = dir_mis = same_dir = 0
            big = []
            max_abs = (0, "")
            max_rel = (0, "")
            sum_t = sum_p = 0.0
            for s in syms:
                a, b = tl.get(s, 0.0), pl.get(s, 0.0)
                sum_t += abs(a)
                sum_p += abs(b)
                if a == 0 and b == 0:
                    both_zero += 1
                    continue
                if (a > 0) != (b > 0) and a != 0 and b != 0:
                    dir_mis += 1
                elif a == 0 or b == 0:
                    dir_mis += 1
                else:
                    same_dir += 1
                d = a - b
                if abs(d) > max_abs[0]:
                    max_abs = (abs(d), s)
                base = max(abs(a), abs(b))
                if base > 0 and abs(d) / base > max_rel[0]:
                    max_rel = (abs(d) / base, s)
                if base > 0 and abs(d) / base > 0.01:
                    big.append((s, a, b, d, d / base if base else 0, tp.get(s), pp.get(s)))
            big.sort(key=lambda x: -abs(x[3]))
            w("\n--- %s ---" % tag)
            w("symbols=%d both_flat=%d same_dir=%d dir_or_zero_mismatch=%d"
              % (len(syms), both_zero, same_dir, dir_mis))
            w("sum|target_lots| test=%.0f prod=%.0f diff=%+.0f (%.2f%%)" % (
                sum_t, sum_p, sum_t - sum_p,
                (sum_t - sum_p) / sum_p * 100 if sum_p else 0))
            w("max |lots diff| = %.6f at %s" % (max_abs[0], max_abs[1]))
            w("max relative  = %.4f%% at %s" % (max_rel[0] * 100, max_rel[1]))
            w("symbols with relative lots diff >1%%: %d" % len(big))
            for s, a, b, d, r, pa, pb in big[:15]:
                w("  %-14s test=%14.6f prod=%14.6f diff=%+14.6f rel=%+7.2f%% price %.10g/%.10g"
                  % (s, a, b, d, r * 100, pa or 0, pb or 0))
        return out, "20-lots-diff.txt"

    if name == "amount":
        for tag in TAG:
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
            if not rows:
                w("\n--- %s no same-direction rows ---" % tag)
                continue
            w("\n--- %s notional ratio (test/prod), same-direction symbols=%d ---"
              % (tag, len(rows)))
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
            if tag == TAG[-1]:
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
        return out, "22-amount-ratio.txt"
    return [], None


for mode in ("product", "lots", "amount"):
    lines, fname = run(mode)
    open(fname, "w", encoding="utf-8").write("\n".join(lines) + "\n")
