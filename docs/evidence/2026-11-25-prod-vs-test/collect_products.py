"""Read-only product snapshot collector.

Usage (executed on each host via stdin):
    <python> - <alphatrade_root> <hour1> <hour2> ...
Outputs a single JSON document on stdout; no files are written on the host.
"""
import json
import os
import sys

import pandas as pd

ROOT = sys.argv[1]
HOURS = sys.argv[2:]
DATE = "2026-11-25"
BASE = os.path.join(ROOT, "data", "25m-eart1106")
GROUP_KEYS = ["symbol", "strategy", "方向", "是否交易", "is_spot"]
ROW_COLS = [
    "symbol", "strategy", "方向", "是否交易", "is_spot", "offset",
    "order_first", "cap_weight", "parent_name", "close", "next_close",
    "target_alloc_ratio",
]

out = {"root": ROOT, "date": DATE, "select_coin": {}, "position_results": {}, "latest_rows": {}}

for hour in HOURS:
    tag = "%s_%s" % (DATE, hour)
    sc = pd.read_pickle(os.path.join(BASE, "select_coin", tag + ".pkl"))
    groups = {}
    for key, sub in sc.groupby(GROUP_KEYS, dropna=False):
        groups["|".join(str(x) for x in key)] = [
            int(len(sub)), round(float(sub["cap_weight"].sum()), 8)
        ]
    parent = sc.groupby("parent_name", dropna=False)["cap_weight"].agg(["size", "sum"])
    sc_summary = {
        "shape": [int(sc.shape[0]), int(sc.shape[1])],
        "columns": list(sc.columns),
        "group_count": len(groups),
        "cap_weight_total": round(float(sc["cap_weight"].sum()), 8),
        "long_rows": int((sc["方向"] > 0).sum()),
        "short_rows": int((sc["方向"] < 0).sum()),
        "trade_rows": int((sc["是否交易"] == 1).sum()),
        "parent": {
            str(k): [int(v["size"]), round(float(v["sum"]), 8)]
            for k, v in parent.iterrows()
        },
        "groups": groups,
    }
    out["select_coin"][tag] = sc_summary

    pr = pd.read_pickle(os.path.join(BASE, "position_results", tag + ".pkl"))
    lots = pd.to_numeric(pr["target_lots"], errors="coerce").fillna(0)
    out["position_results"][tag] = {
        "shape": [int(pr.shape[0]), int(pr.shape[1])],
        "nonzero": int((lots != 0).sum()),
        "long": int((lots > 0).sum()),
        "short": int((lots < 0).sum()),
        "flat": int((lots == 0).sum()),
        "abs_target_lots_sum": round(float(lots.abs().sum()), 6),
        "finite": bool(lots.replace([float("inf"), float("-inf")], float("nan")).notna().all()),
        "direction": {
            str(r["symbol"]): int(1 if r["target_lots"] > 0 else (-1 if r["target_lots"] < 0 else 0))
            for _, r in pr.iterrows()
        },
        "price": {str(r["symbol"]): float(r["price"]) for _, r in pr.iterrows()},
    }

    if hour == HOURS[-1]:
        out["latest_rows"]["select_coin"] = json.loads(
            sc[ROW_COLS].to_json(orient="records", double_precision=10)
        )
        out["latest_rows"]["position_results"] = json.loads(
            pr.to_json(orient="records", double_precision=10)
        )

print(json.dumps(out, ensure_ascii=False))
