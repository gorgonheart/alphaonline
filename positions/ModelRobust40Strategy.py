"""Independent-offset MODEL_ROBUST40, using explicit framework hooks."""
from tools.robust40 import live


def prepare_live(me_conf, account, run_time):
    live.prepare(me_conf, account, run_time)


def calc_ratio(equity_dfs, stg_conf):
    return live.parent_ratios(equity_dfs, stg_conf)


def transform_targets(spot, swap, stg_conf):
    return live.transform_targets(spot, swap, stg_conf)
