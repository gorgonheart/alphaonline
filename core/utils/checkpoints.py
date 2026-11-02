"""Bounded simulator checkpoints. Capturing does not imply automatic rewind."""
import pandas as pd


def merge_points(old, new, count):
    points = {pd.Timestamp(p['time']): p for p in old}
    points.update({pd.Timestamp(p['time']): p for p in new})
    return [points[t] for t in sorted(points)[-count:]]
