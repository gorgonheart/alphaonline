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
    # 可选声明只用于计算输入；原LazyFrame及其他因子列保持不变。
    columns = getattr(factor, 'get_state_input_columns', None)
    source = frame
    if polars:
        if columns:
            needed = list(columns(params))
            if column not in needed and column in frame.collect_schema().names():needed.append(column)
            source = frame.select(needed)
        source = source.collect().to_pandas()
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


def run_section_blocks(conf, factor, store, columns, params, column):
    """Stage all section outputs; publish state only after every block succeeds."""
    persisted = getattr(conf, 'section_state', False) is True and hasattr(factor, 'signal_with_state')
    path = None
    previous = None
    hours = getattr(conf, 'checkpoint_hours', 0) if persisted else 0
    if persisted:
        if getattr(conf, 'incremental_lookback_hours', 0):
            raise ValueError('Section continuation does not support simulator rewind')
        if any(c in column for c in '/\\\x00') or '..' in column:
            raise ValueError('Invalid section state column')
        path = Path(conf.get_snapshot_folder())/'sections'/(column.replace(':', '_')+'.pkl')
        previous = pd.read_pickle(path) if path.exists() else None
    keys = store.block_keys()
    options = {}
    if hours:
        # The existing protocol counts observed boundaries, not elapsed hours.
        # Read narrow timestamps from the tail only; no whole-history panel.
        times = set()
        for key in reversed(keys):
            times.update(store.read_block(key, ['candle_begin_time'])['candle_begin_time'].unique().to_list())
            if len(times) >= hours:
                break
        checkpoint_start = sorted(times)[-min(hours, len(times))]
        options = dict(checkpoint_start=checkpoint_start, checkpoint_hours=hours)
    state, parts = previous, {}
    import config
    gate = getattr(config, 'compute_gate', None)
    needed = list(dict.fromkeys(['_row', 'candle_begin_time', *columns]))
    for key in keys:
        if gate:
            gate()
        frame = store.read_block(key, needed)
        result, state = factor.signal_block(frame, params, column, state, **options)
        if not result['_row'].equals(frame['_row']):
            raise ValueError('Section block row order differs')
        parts[key] = result.select('_row', column)
        del frame, result
    finalize = getattr(factor, 'finalize_block_state', None)
    if persisted and previous is None and finalize is not None:
        state = finalize(state, **options)
    store.add_section(column, parts)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        pd.to_pickle(state, temporary)
        temporary.replace(path)
    return state
