"""Per-symbol target_lots / price comparison for 2026-11-26 17 & 18.

Writes 20-lots-diff.txt. Classifies each symbol as: both flat, direction
mismatch, same direction but size differs beyond thresholds.
"""
import json

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
    w("symbols=%d both_flat=%d same_dir=%d dir_or_zero_mismatch=%d" % (len(syms), both_zero, same_dir, dir_mis))
    w("sum|target_lots| test=%.0f prod=%.0f diff=%+.0f (%.2f%%)" % (
        sum_t, sum_p, sum_t - sum_p, (sum_t - sum_p) / sum_p * 100 if sum_p else 0))
    w("max |lots diff| = %.6f at %s" % (max_abs[0], max_abs[1]))
    w("max relative  = %.4f%% at %s" % (max_rel[0] * 100, max_rel[1]))
    w("symbols with relative lots diff >1%%: %d" % len(big))
    for s, a, b, d, r, pa, pb in big[:15]:
        w("  %-14s test=%14.6f prod=%14.6f diff=%+14.6f rel=%+7.2f%% price %.10g/%.10g" % (
            s, a, b, d, r * 100, pa or 0, pb or 0))

open("20-lots-diff.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
