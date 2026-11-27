"""Supplementary local analysis: anomaly round, row-level numeric deltas, per-offset ready medians."""
import csv
import json
import os
import statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
E = lambda n: os.path.join(HERE, n)
lines = []


def say(s):
    lines.append(s)
    print(s)


# 1) anomalous test round 02:25
say("=== test 25m anomalous round detail (02:25 UTC) ===")
for line in open(E("12-test-replay-end-raw.txt"), encoding="utf-8", errors="replace"):
    i = line.find("{")
    if i < 0:
        continue
    d = json.loads(line[i:])
    if d.get("cutoff") == "2026-11-25 02:25:00" and d.get("event") == "end":
        for k in ["attempt", "mode", "status", "committed", "input_frames", "changed_cells",
                  "changed_series", "revised_from", "replay_hours", "prepare_s", "copy_s",
                  "restore_s", "input_read_s", "account_calculation_s", "account_flow_s",
                  "calculation_s", "total_s", "elapsed_s"]:
            say("  %s = %s" % (k, d.get(k)))

# 2) row-level numeric deltas for the latest cutpoint
t = json.load(open(E("18-test-products.json"), encoding="utf-8"))["latest_rows"]["select_coin"]
p = json.load(open(E("19-prod-products.json"), encoding="utf-8"))["latest_rows"]["select_coin"]
KEY = ["symbol", "strategy", "方向", "是否交易", "is_spot", "offset", "order_first", "parent_name", "cap_weight"]


def bucket(rows):
    out = defaultdict(list)
    for r in rows:
        out[tuple(r[k] for k in KEY)].append(r)
    return out


bt, bp = bucket(t), bucket(p)
say("=== latest cutpoint row-level numeric deltas (matched keys only) ===")
matched = 0
deltas = defaultdict(list)
for key in set(bt) & set(bp):
    for a, b in zip(sorted(bt[key], key=lambda x: str(x["close"])),
                    sorted(bp[key], key=lambda x: str(x["close"]))):
        matched += 1
        for col in ["close", "next_close", "target_alloc_ratio"]:
            if a[col] is not None and b[col] is not None:
                deltas[col].append(abs(float(a[col]) - float(b[col])))
say("matched rows = %d (test rows=%d, prod rows=%d)" % (matched, len(t), len(p)))
for col, vals in deltas.items():
    nz = [v for v in vals if v > 1e-12]
    say("  %s: n=%d nonzero=%d (%.2f%%) max_abs=%.8g median_nonzero=%.8g"
        % (col, len(vals), len(nz), 100.0 * len(nz) / max(1, len(vals)),
           max(vals) if vals else 0, statistics.median(nz) if nz else 0))

# 3) ready delay by offset, after the 10:57 release
rows = list(csv.DictReader(open(E("21-upstream-rounds.csv"), encoding="utf-8")))
say("=== swap_dict.ready delay after 11:00, grouped by cycle offset (seconds) ===")
for host in ["test", "prod"]:
    by = defaultdict(list)
    byspot = defaultdict(list)
    for r in rows:
        if r["host"] != host or r["run_time"] < "2026-11-25 11:00:00":
            continue
        off = r["run_time"][14:16]
        if r["swap_ready_s"]:
            by[off].append(float(r["swap_ready_s"]))
        if r["spot_ready_s"]:
            byspot[off].append(float(r["spot_ready_s"]))
    for off in sorted(by):
        say("  %s %sm: spot n=%d median=%.1f values=%s | swap n=%d median=%.1f values=%s"
            % (host, off, len(byspot[off]), statistics.median(byspot[off]) if byspot[off] else -1,
               [int(x) for x in byspot[off]], len(by[off]),
               statistics.median(by[off]) if by[off] else -1, [int(x) for x in by[off]]))

# 4) changed_series with incremental rounds counted as zero
say("=== changed_series by host, incremental counted as 0 ===")
for name in ["12-test-replay-end-raw.txt", "13-prod-replay-end-raw.txt"]:
    vals = []
    modes = defaultdict(int)
    for line in open(E(name), encoding="utf-8", errors="replace"):
        i = line.find("{")
        if i < 0:
            continue
        d = json.loads(line[i:])
        if d.get("event") != "end":
            continue
        modes[d.get("mode")] += 1
        vals.append(d.get("changed_series") or 0)
    say("  %s: n=%d modes=%s changed_series values=%s median=%.1f"
        % (name, len(vals), dict(modes), [int(v) for v in vals], statistics.median(vals)))

open(E("29-extra-analysis.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
