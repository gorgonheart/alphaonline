"""Parse 2026-11-26 MODEL_REPLAY 'end' lines into a round-level CSV.

Reads 01-test-replay-20261126.txt and 02-prod-25m-replay-20261126.txt,
keeps only event=end, and writes 04-replay-rounds.csv plus a console summary.
"""
import csv
import json
import statistics

SRC = [("test", "01-test-replay-20261126.txt"), ("prod", "02-prod-25m-replay-20261126.txt")]
FIELDS = ["host", "account", "cutoff", "mode", "status", "committed", "offset",
          "input_frames", "input_read_s", "compare_s", "prepare_s",
          "account_calculation_s", "total_s", "changed_series", "changed_cells",
          "revised_from", "restore_s", "copy_s"]

rows = []
for host, path in SRC:
    for line in open(path, encoding="utf-8", errors="replace"):
        i = line.find("{")
        if i < 0:
            continue
        try:
            d = json.loads(line[i:])
        except Exception:
            continue
        if d.get("event") != "end":
            continue
        rows.append({k: d.get(k, "") for k in FIELDS} | {"host": host})

rows.sort(key=lambda r: (r["cutoff"], r["host"]))
with open("04-replay-rounds.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)

print("total end rounds:", len(rows))
for host in ("test", "prod"):
    sub = [r for r in rows if r["host"] == host]
    print("\n===", host, "rounds:", len(sub))
    print("cutoffs:", ", ".join(sorted(r["cutoff"] for r in sub)))
    print("modes:", {m: sum(1 for r in sub if r["mode"] == m) for m in {r["mode"] for r in sub}})
    print("status:", {m: sum(1 for r in sub if r["status"] == m) for m in {r["status"] for r in sub}})
    print("changed_series:", [r["changed_series"] for r in sub])
    for key in ("total_s", "prepare_s", "account_calculation_s", "input_read_s"):
        vals = [float(r[key]) for r in sub if r[key] != ""]
        if vals:
            print("%-24s n=%d median=%.3f min=%.3f max=%.3f" % (key, len(vals), statistics.median(vals), min(vals), max(vals)))
print("\n=== per-round table ===")
for r in rows:
    print("%-5s %-38s %-12s %-10s cs=%-5s total=%-10s prep=%-9s calc=%-10s read=%-8s status=%s" % (
        r["host"], r["cutoff"], r["mode"], r["committed"], r["changed_series"],
        round(float(r["total_s"]), 3) if r["total_s"] != "" else "-",
        round(float(r["prepare_s"]), 3) if r["prepare_s"] != "" else "-",
        round(float(r["account_calculation_s"]), 3) if r["account_calculation_s"] != "" else "-",
        round(float(r["input_read_s"]), 3) if r["input_read_s"] != "" else "-", r["status"]))
