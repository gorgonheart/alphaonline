"""S5 contribution overlay; no account access and no global runtime files."""
import numpy as np
import pandas as pd


def apply_s5_delta(net, delta):
    if not net.index.equals(delta.index):
        raise ValueError('Delta time index must exactly match target time index')
    if not net.columns.is_unique or not delta.columns.is_unique:
        raise ValueError('Duplicate target symbols')
    if not np.isfinite(delta.to_numpy()).all() or (delta.to_numpy() > 0).any():
        raise ValueError('S5 delta must be finite and nonpositive')
    extra = delta.columns.difference(net.columns)
    if len(extra) and delta[extra].ne(0).any().any():
        raise ValueError('Nonzero S5 delta has no corresponding target symbol')
    aligned = delta.reindex(columns=net.columns, fill_value=0)
    # The reference applies negative deltas only to a positive net target.
    return net.where(net <= 0, (net+aligned).clip(lower=0))
