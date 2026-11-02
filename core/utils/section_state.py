"""Optional section state protocol; factors own their calculation and state schema."""
from pathlib import Path
import pandas as pd


def run_section(conf, factor, frame, params, column, polars=False):
    callback = getattr(factor, 'signal_with_state', None)
    if getattr(conf, 'section_state', False) is not True or callback is None:
        return factor.signal_polars(frame, params, column) if polars else factor.signal(frame, params, column)
    if getattr(conf, 'incremental_lookback_hours', 0):
        raise ValueError('Section continuation does not support simulator rewind')
    # Column names already identify parameterized factor caches throughout core.
    if any(c in column for c in '/\\\x00') or '..' in column:
        raise ValueError('Invalid section state column')
    folder = Path(conf.get_snapshot_folder())/'sections'
    folder.mkdir(parents=True, exist_ok=True)
    # Windows filenames cannot contain ':' in date parameters.
    path = folder/(column.replace(':','_')+'.pkl')
    previous = pd.read_pickle(path) if path.exists() else None
    source = frame.collect().to_pandas() if polars else frame
    hours = getattr(conf, 'checkpoint_hours', 0)
    if hours:
        result, state = factor.signal_with_checkpoints(source, params, column, previous, hours)
    else:
        result, state = callback(source, params, column, previous)
    temporary = path.with_suffix('.tmp')
    pd.to_pickle(state, temporary)
    temporary.replace(path)
    if polars:
        import polars as pl
        return frame.with_columns(pl.from_pandas(result[column].rename(column)))
    return result
