"""Local, read-only analysis of the raw evidence captured from both hosts.

Produces:
  20-replay-rounds.csv     every MODEL_REPLAY end line, one row per round
  21-upstream-rounds.csv   every upstream cycle, one row per run_time
  22-product-diff.txt      content-level product comparison
  23-summary.txt           aligned 25m comparison + medians used in the report
"""
import csv
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
E = lambda name: os.path.join(HERE, name)

REPLAY_FIELDS = [
    "cutoff", "account", "mode", "status", "committed", "input_frames",
    "changed_cells", "changed_series", "old_read_s", "input_read_s",
    "revision_arrow_spot_s", "revision_arrow_swap_s", "revision_pivot_s",
    "compare_s", "copy_s", "restore_s", "prepare_s",
    "account_calculation_s", "account_flow_s", "total_s", "elapsed_s",
]


def load_replay(name, host):
    rows = []
    for line in open(E(name), encoding="utf-8", errors="replace"):
        i = line.find("{")
        if i < 0:
            continue
        try:
            d = json.loads(line[i:])
        except json.JSONDecodeError:
            continue
        if d.get("event") != "end":
            continue
        row = {"host": host}
        row.update({k: d.get(k) for k in REPLAY_FIELDS})
        rows.append(row)
    return rows


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return "%.3f" % v
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


test_replay = load_replay("12-test-replay-end-raw.txt", "test")
prod_replay = load_replay("13-prod-replay-end-raw.txt", "prod")
with open(E("20-replay-rounds.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["host"] + REPLAY_FIELDS)
    w.writeheader()
    for r in test_replay + prod_replay:
        w.writerow({k: fmt(v) for k, v in r.items()})

# ---- upstream cycles -------------------------------------------------
TS = re.compile(r"(?:当前时间=|run_time=)(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def round_base(ts):
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    minute = dt.minute // 10 * 10 + 5
    base = dt.replace(minute=minute, second=0, microsecond=0)
    if base > dt:
        base = dt.replace(minute=minute - 10 if minute >= 10 else 55, second=0, microsecond=0)
        if base > dt:
            base = base.replace(hour=(base.hour - 1) % 24, minute=55)
    return base, (dt - base).total_seconds()


def parse_upstream(name, host):
    cycles = defaultdict(dict)
    pending = []
    for line in open(E(name), encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("=== ERROR COUNT ==="):
            break
        m = re.search(r"barrier\[(spot|swap)\] 完成: success=\d+/\d+ skipped=(\d+) 耗时=([\d.]+)s", line)
        if m:
            pending.append((m.group(1), float(m.group(3)), int(m.group(2))))
            continue
        m = re.search(r"REST 兜底 grace\([\d.]+s\) 后计算 missed: spot=(\d+) swap=(\d+) \(run_time=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            key = m.group(3)
            c = cycles[key]
            c["host"] = host
            c["run_time"] = key
            c["missed_spot"] = int(m.group(1))
            c["missed_swap"] = int(m.group(2))
            for side, secs, skipped in pending[-2:]:
                c["barrier_" + side + "_s"] = secs
                c["barrier_" + side + "_skipped"] = skipped
            pending = []
            continue
        m = TS.search(line)
        if not m:
            continue
        ts = m.group(1)
        base, delay = round_base(ts)
        key = base.strftime("%Y-%m-%d %H:%M:%S")
        c = cycles[key]
        c["host"] = host
        c["run_time"] = key
        if "预处理 Pivot Table spot 完成" in line:
            c["spot_ready_s"] = delay
        elif "预处理 Pivot Table swap 完成" in line:
            c["swap_ready_s"] = delay
        elif "预处理 Market Dict (含因子) spot 完成" in line:
            c["market_dict_spot_s"] = float(re.search(r"耗时 ([\d.]+) 秒", line).group(1))
        elif "预处理 Market Dict (含因子) swap 完成" in line:
            c["market_dict_swap_s"] = float(re.search(r"耗时 ([\d.]+) 秒", line).group(1))
        elif "Binance spot API" in line:
            c["api_spot_s"] = float(re.search(r"耗时=([\d.]+)秒", line).group(1))
        elif "Binance swap API" in line:
            c["api_swap_s"] = float(re.search(r"耗时=([\d.]+)秒", line).group(1))
        elif "[io-stat] phase spot" in line:
            c["publish_spot_s"] = float(re.search(r"publish=([\d.]+)s", line).group(1))
            c["pivot_spot_s"] = float(re.search(r"pivot=([\d.]+)s", line).group(1))
        elif "[io-stat] phase swap" in line:
            c["publish_swap_s"] = float(re.search(r"publish=([\d.]+)s", line).group(1))
            c["pivot_swap_s"] = float(re.search(r"pivot=([\d.]+)s", line).group(1))
        elif "Resample spot 内存合成就绪" in line:
            c["resample_spot_ready_s"] = delay
        elif "Resample swap 内存合成就绪" in line:
            c["resample_swap_ready_s"] = delay
    return cycles


test_up = parse_upstream("14-test-upstream-raw.txt", "test")
prod_up = parse_upstream("15-prod-upstream-raw.txt", "prod")
UP_FIELDS = [
    "run_time", "host", "barrier_spot_s", "barrier_swap_s", "barrier_spot_skipped",
    "barrier_swap_skipped", "missed_spot", "missed_swap", "resample_spot_ready_s",
    "resample_swap_ready_s", "api_spot_s", "api_swap_s", "market_dict_spot_s",
    "market_dict_swap_s", "publish_spot_s", "publish_swap_s", "pivot_spot_s",
    "pivot_swap_s", "spot_ready_s", "swap_ready_s",
]
with open(E("21-upstream-rounds.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=UP_FIELDS)
    w.writeheader()
    for c in sorted(list(test_up.values()) + list(prod_up.values()), key=lambda x: (x["run_time"], x["host"])):
        w.writerow({k: fmt(c.get(k)) for k in UP_FIELDS})

# ---- products ---------------------------------------------------------
t = json.load(open(E("18-test-products.json"), encoding="utf-8"))
p = json.load(open(E("19-prod-products.json"), encoding="utf-8"))
diff = []


def say(s):
    diff.append(s)
    print(s)


for tag in ["2026-11-25_11", "2026-11-25_12", "2026-11-25_13"]:
    say("=== %s ===" % tag)
    st, sp = t["select_coin"][tag], p["select_coin"][tag]
    say("select_coin shape test=%s prod=%s | groups test=%d prod=%d | cap_weight_total test=%s prod=%s"
        % (st["shape"], sp["shape"], st["group_count"], sp["group_count"],
           st["cap_weight_total"], sp["cap_weight_total"]))
    gt, gp = st["groups"], sp["groups"]
    only_t, only_p = sorted(set(gt) - set(gp)), sorted(set(gp) - set(gt))
    common = sorted(set(gt) & set(gp))
    differing = [(k, gt[k], gp[k]) for k in common if gt[k] != gp[k]]
    say("  groups: only_test=%d only_prod=%d common=%d value_diff=%d"
        % (len(only_t), len(only_p), len(common), len(differing)))
    for k in only_t[:12]:
        say("   ONLY_TEST %s -> %s" % (k, gt[k]))
    for k in only_p[:12]:
        say("   ONLY_PROD %s -> %s" % (k, gp[k]))
    for k, a, b in differing[:12]:
        say("   DIFF %s test=%s prod=%s" % (k, a, b))
    pt, pp = st["parent"], sp["parent"]
    pdiff = [(k, pt.get(k), pp.get(k)) for k in sorted(set(pt) | set(pp)) if pt.get(k) != pp.get(k)]
    say("  parent strategies: test=%d prod=%d diff=%d" % (len(pt), len(pp), len(pdiff)))
    for k, a, b in pdiff[:12]:
        say("   PARENT_DIFF %s test=%s prod=%s" % (k, a, b))
    rt, rp = t["position_results"][tag], p["position_results"][tag]
    dt_dir, dp_dir = rt["direction"], rp["direction"]
    ddiff = [(k, dt_dir.get(k), dp_dir.get(k)) for k in sorted(set(dt_dir) | set(dp_dir))
             if dt_dir.get(k) != dp_dir.get(k)]
    say("position_results shape test=%s prod=%s | nonzero %d/%d | long %d/%d | short %d/%d | flat %d/%d"
        % (rt["shape"], rp["shape"], rt["nonzero"], rp["nonzero"], rt["long"], rp["long"],
           rt["short"], rp["short"], rt["flat"], rp["flat"]))
    say("  abs(target_lots) test=%s prod=%s | direction_diff_symbols=%d"
        % (rt["abs_target_lots_sum"], rp["abs_target_lots_sum"], len(ddiff)))
    for k, a, b in ddiff[:12]:
        say("   DIR_DIFF %s test=%s prod=%s" % (k, a, b))

say("=== latest cutpoint row-level (13) ===")
rt = t["latest_rows"]["select_coin"]
rp = p["latest_rows"]["select_coin"]


def row_key(r):
    return (r["symbol"], r["strategy"], r["方向"], r["是否交易"], r["is_spot"],
            r["offset"], r["order_first"], r["parent_name"], round(float(r["cap_weight"]), 8))


kt = defaultdict(int)
kp = defaultdict(int)
for r in rt:
    kt[row_key(r)] += 1
for r in rp:
    kp[row_key(r)] += 1
only_t = [(k, v) for k, v in kt.items() if k not in kp]
only_p = [(k, v) for k, v in kp.items() if k not in kt]
say("row keys test=%d prod=%d | only_test=%d only_prod=%d | count_mismatch=%d"
    % (len(kt), len(kp), len(only_t), len(only_p),
       sum(1 for k, v in kt.items() if k in kp and kp[k] != v)))
for k, v in only_t[:10]:
    say("  ONLY_TEST_ROW %s x%d" % (k, v))
for k, v in only_p[:10]:
    say("  ONLY_PROD_ROW %s x%d" % (k, v))
sig = ["方向", "是否交易", "is_spot", "order_first"]
say("signal columns compared (group-level): %s" % ", ".join(sig))
for col, tol in [("close", None), ("next_close", None), ("target_alloc_ratio", None)]:
    a = [float(r[col]) for r in rt if r[col] is not None]
    b = [float(r[col]) for r in rp if r[col] is not None]
    say("  %s: test n=%d min=%.6g max=%.6g | prod n=%d min=%.6g max=%.6g"
        % (col, len(a), min(a), max(a), len(b), min(b), max(b)))

open(E("22-product-diff.txt"), "w", encoding="utf-8").write("\n".join(diff) + "\n")

# ---- aligned 25m + medians -------------------------------------------
summary = []


def add(s):
    summary.append(s)
    print(s)


t25 = {r["cutoff"]: r for r in test_replay if r["account"] == "25m-eart1106"}
p25 = {r["cutoff"]: r for r in prod_replay if r["account"] == "25m-eart1106"}
add("=== 25m aligned cutoffs (UTC) ===")
add("cutoff | test mode/status | prod mode/status | total test/prod/diff | prepare test/prod | account_calc test/prod | input_read test/prod | changed_series test/prod")
for cut in sorted(set(t25) & set(p25)):
    a, b = t25[cut], p25[cut]
    add("%s | %s/%s | %s/%s | %.2f/%.2f/%+.2f | %.2f/%.2f | %.2f/%.2f | %.2f/%.2f | %s/%s"
        % (cut, a["mode"], a["status"], b["mode"], b["status"],
           a["total_s"], b["total_s"], a["total_s"] - b["total_s"],
           a["prepare_s"], b["prepare_s"], a["account_calculation_s"],
           b["account_calculation_s"], a["input_read_s"], b["input_read_s"],
           a["changed_series"], b["changed_series"]))


def med(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


for label, rows in [("test 25m all", test_replay), ("prod 25m all", [r for r in prod_replay if r["account"] == "25m-eart1106"]),
                    ("prod all 6 accounts", prod_replay)]:
    add("%s n=%d | total=%.2f prepare=%.2f account_calc=%.2f input_read=%.2f changed_series=%s"
        % (label, len(rows), med(rows, "total_s"), med(rows, "prepare_s"),
           med(rows, "account_calculation_s"), med(rows, "input_read_s"),
           med(rows, "changed_series")))

add("=== upstream ready delay medians (s since XX:M5:00) ===")
for label, cycles in [("test", test_up), ("prod", prod_up)]:
    allc = list(cycles.values())
    post = [c for c in allc if c["run_time"] >= "2026-11-25 11:00:00"]
    for scope, rows in [("all_today", allc), ("after_11h", post)]:
        add("%s %s n=%d | spot_ready=%.1f swap_ready=%.1f | barrier_spot=%.2f barrier_swap=%.2f | missed_spot_zero=%d/%d missed_swap_zero=%d/%d | market_dict_spot=%.1f market_dict_swap=%.1f"
            % (label, scope, len(rows),
               med(rows, "spot_ready_s") or -1, med(rows, "swap_ready_s") or -1,
               med(rows, "barrier_spot_s") or -1, med(rows, "barrier_swap_s") or -1,
               sum(1 for r in rows if r.get("missed_spot") == 0), len(rows),
               sum(1 for r in rows if r.get("missed_swap") == 0), len(rows),
               med(rows, "market_dict_spot_s") or -1, med(rows, "market_dict_swap_s") or -1))

add("=== prod 6 accounts latest rounds ===")
latest = {}
for r in prod_replay:
    if r["account"] not in latest or r["cutoff"] > latest[r["account"]]["cutoff"]:
        latest[r["account"]] = r
for acct in sorted(latest):
    r = latest[acct]
    add("%s cutoff=%s mode=%s total_s=%.2f account_calc=%.2f prepare=%.2f input_read=%.2f changed_series=%s"
        % (acct, r["cutoff"], r["mode"], r["total_s"], r["account_calculation_s"],
           r["prepare_s"], r["input_read_s"], r["changed_series"]))

open(E("23-summary.txt"), "w", encoding="utf-8").write("\n".join(summary) + "\n")
