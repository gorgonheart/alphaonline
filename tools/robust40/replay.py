"""Bounded checkpoint primitives shared by offline verification and server handoff.

Callers provide the earliest affected time and corrected, warm input history.
Disk transactions are owned by handoff.py; this module never submits orders.
"""
import importlib
import pandas as pd


def before(points, revised_from):
    candidates = [t for t in points if t < pd.Timestamp(revised_from)]
    if not candidates:
        raise ValueError('No checkpoint precedes the revision; older state/history is required')
    return max(candidates)


def common_before(groups, revised_from):
    """Choose a time present in every module of ONE account/offset."""
    groups = list(groups)
    if not groups:raise ValueError('No checkpoint groups')
    common = set(groups[0])
    for points in groups[1:]:common &= set(points)
    return before(common, revised_from)


def section_run(frame, params, column, state=None, hours=24):
    section = importlib.import_module('sections.VolumeMaxBias_进出场阈值分离官方')
    return section.signal_with_checkpoints(frame, params, column, state, hours)


def section_replay(frame, params, column, state, revised_from, hours=24, checkpoint_time=None):
    points = state.get('checkpoints', {})
    time = before(points, revised_from) if checkpoint_time is None else pd.Timestamp(checkpoint_time)
    if time not in points or time >= pd.Timestamp(revised_from):raise ValueError('Invalid common checkpoint')
    restored = dict(points[time], output=state['output'][state['output'].candle_begin_time <= time].copy(),
                    checkpoints={t:s for t,s in points.items() if t <= time})
    return section_run(frame, params, column, restored, hours)
