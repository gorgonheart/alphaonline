"""Functional resource/input settings only; all strategy parameters stay in accounts."""
# 仅控制MODEL运行资源与历史覆盖；收益/权重/仓管策略参数仍在各账户文件。
INITIALIZATION = dict(minimum_history_bars=14000)
# 唯一MODEL计算并发：完整初始化、正常续算和手工预热共同使用。
# Windows spawn会为每个进程加载框架并接收行情副本。
# 不沿用全局CPU数，避免20个因子进程同时启动的内存峰值；传统策略仍用config.job_num。
MODEL_JOB_NUM = 4
LIVE_BARS = 4050
# Bounds waiting for missing input only; valid calculation has no time deadline.
DATA_READY_WAIT_SECONDS = 240
# Includes the current bar: 25 points allow restoring before a 24-bar revision.
CHECKPOINT_POINTS = 25
REVISION_HISTORY_HOURS = 168
