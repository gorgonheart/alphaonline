"""Bounded simulator checkpoints. Capturing does not imply automatic rewind."""
import numpy as np
import pandas as pd


def align_expert_points(points, kind, previous, units, market):
    """Carry historical simulator arrays into an expanded selection by symbol."""
    if not points:
        return points
    old = pd.Index(previous['columns'])
    new = units.index
    if not old.is_unique or not new.is_unique:
        return []
    positions = new.get_indexer(old)
    if (positions < 0).any() or not np.array_equal(previous['sim'].lot_sizes,
                                                  units.to_numpy()[positions]):
        # A removed instrument or changed lot unit is not an array reordering.
        return []
    if old.equals(new):
        return points
    added = new.difference(old, sort=False)
    when = pd.DatetimeIndex([p['time'] for p in points])
    # A newly selected symbol previously held zero lots. Capture the last price
    # known at each old boundary, matching Simulator's open/close update order.
    close = market['close'].loc[:when.max(), added]
    opening = market['open'].loc[:when.max(), added]
    prices = close.combine_first(opening).ffill().reindex(when, method='ffill').fillna(0.)
    added_positions = new.get_indexer(added)
    aligned = []
    for i, point in enumerate(points):
        snap = dict(point[kind])
        for field in ('lots', 'target_lots', 'last_prices'):
            source = np.asarray(snap[field])
            if source.shape != (len(old),):
                return []
            dest = np.zeros(len(new), dtype=source.dtype)
            dest[positions] = source
            if field == 'last_prices':
                dest[added_positions] = prices.iloc[i].to_numpy()
            snap[field] = dest
        aligned.append({**point, kind: snap})
    return aligned


def merge_points(old, new, count):
    points = {pd.Timestamp(p['time']): p for p in old}
    points.update({pd.Timestamp(p['time']): p for p in new})
    return [points[t] for t in sorted(points)[-count:]]
