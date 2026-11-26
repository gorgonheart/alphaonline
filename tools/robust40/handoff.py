"""Account-locked common recovery. No exchange calls and no historical orders."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
import shutil
import tempfile
import time as _time
import numpy as np
import pandas as pd
from .warmup import cutoff
from .initialization_config import REVISION_HISTORY_HOURS
from . import telemetry

# Raw values feeding price/volume factors and the S3 eligibility mask. Derived
# Arrow factors and external sources still have their existing strict validators.
RAW_COLUMNS = ('open','high','low','close','volume','quote_volume','trade_num',
               'taker_buy_base_asset_volume','taker_buy_quote_asset_volume',
               'avg_price_1m','funding_rate','是否交易','symbol_spot','symbol_swap',
               'is_spot','first_candle_time','funding_fee')

# Per-market circuit breaker for simultaneous raw series disappearance:
# tolerates 10-20 project delist announcements (even dual spot+swap) while
# aborting genuine upstream feed outages without corrupting snapshots or
# triggering 6-account full rebuilds.
REMOVED_RAW_MIN_LIMIT = 30
REMOVED_RAW_RATIO_LIMIT = 0.08


class UpstreamUniverseDropError(RuntimeError):
    """Excessive simultaneous raw series disappearance; abort round without rebuild or orders."""


def _extract_held_symbols(bundle):
    """Return {(kind, canonical_symbol)} with non-zero lots or target_lots across main & virtual accounts."""
    from .offline import canonical

    cols = bundle.get('context', {}).get('columns', [])
    by_kind = {
        kind: [canonical(c.split(':', 1)[1]) for c in cols if isinstance(c, str) and c.startswith(f'{kind}:')]
        for kind in ('spot', 'swap')
    }
    result = bundle.get('result', {})
    accounts = list(result.get('virtual_accounts', []))
    if result.get('account') is not None:
        accounts.append(result['account'])
    held = set()
    for acc in accounts:
        sim_state = getattr(acc, 'attrs', {}).get('simulation_state')
        if not sim_state or len(sim_state) < 2:
            continue
        for idx, kind in enumerate(('spot', 'swap')):
            syms = by_kind[kind]
            state_tuple = sim_state[idx]
            if not state_tuple or len(state_tuple) < 3:
                continue
            lots = np.asarray(state_tuple[1])
            target_lots = np.asarray(state_tuple[2])
            n = min(len(syms), len(lots), len(target_lots))
            for i in range(n):
                if lots[i] != 0 or target_lots[i] != 0:
                    held.add((kind, syms[i]))
    return held


def utc_times(values):
    index=pd.DatetimeIndex(values)
    return index.tz_convert('UTC').tz_localize(None) if index.tz is not None else index


def input_frames(raw, pivots, end, start=None):
    end = cutoff(end)
    start = cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out = {}
    for kind, sources in raw.items():
        for symbol, frame in sources.items():
            idx = utc_times(frame.candle_begin_time)
            left,right=idx.searchsorted(start),idx.searchsorted(end,side='right')
            columns = [c for c in RAW_COLUMNS if c in frame]
            value = frame.iloc[left:right][columns].copy();value.index = idx[left:right]
            if 'funding_fee' not in value:
                value['funding_fee'] = np.nan
            if 'first_candle_time' in value:
                value['first_candle_time']=utc_times(value.first_candle_time).to_numpy()
            if not value.index.is_unique:raise ValueError('Duplicate revision input time')
            if len(value):out[f'raw/{kind}/{symbol}'] = value.sort_index()
    for kind, tables in pivots.items():
        for field in ('open','close','vwap1m','funding_rate'):
            if field not in tables:continue
            value = tables[field]
            idx = utc_times(value.index)
            value = value.loc[(idx>=start)&(idx<=end)].copy()
            value.index = idx[(idx>=start)&(idx<=end)]
            out[f'pivot/{kind}/{field}'] = value
    return out


def first_revision(
    old,
    new,
    *,
    held_symbols=None,
    removed_raw_min_limit=REMOVED_RAW_MIN_LIMIT,
    removed_raw_ratio_limit=REMOVED_RAW_RATIO_LIMIT,
):
    """Exact values, NaN equals NaN. Return earliest changed closed input bar.

    Universe removals on ``raw/{spot|swap}/<symbol>`` are governed in two tiers:
    1. Per-market surge circuit breaker (``> max(30, int(0.08 * old_market_count))``):
       raises ``UpstreamUniverseDropError`` (retryable, non-rebuild) to abort on feed outage.
    2. Within-threshold removals (e.g. bStocks halt or 10-20 coin delist announcement):
       do not rewrite closed history before T (avoiding look-ahead/survivorship bias).
       Unheld symbols stay incremental; held symbols require intact historical pivot
       columns and emit ``removed_held_symbols`` telemetry alerts (otherwise fall back
       to conservative window-start revision).
    Missing entire ``pivot/*`` tables and non-pivot ``removed_columns`` stay conservative;
    ``removed_columns`` on shared ``pivot/{spot|swap}/*`` follow the same per-market
    surge circuit breaker and zero-holding relaxation (canonicalized via ``canonical``).
    """
    from .offline import canonical

    if not old:raise ValueError('Missing revision input coverage')
    end = max(f.index.max() for f in old.values())

    # 1. Per-market circuit breaker BEFORE inspecting state or comparing cells.
    removed_raw_spot = sorted(k for k in old if k.startswith('raw/spot/') and k not in new)
    removed_raw_swap = sorted(k for k in old if k.startswith('raw/swap/') and k not in new)
    if removed_raw_spot or removed_raw_swap:
        old_spot_count = sum(1 for k in old if k.startswith('raw/spot/'))
        old_swap_count = sum(1 for k in old if k.startswith('raw/swap/'))
        spot_limit = max(int(removed_raw_min_limit), int(removed_raw_ratio_limit * old_spot_count))
        swap_limit = max(int(removed_raw_min_limit), int(removed_raw_ratio_limit * old_swap_count))
        if len(removed_raw_spot) > spot_limit or len(removed_raw_swap) > swap_limit:
            all_removed = removed_raw_spot + removed_raw_swap
            telemetry.add(removed_series=len(all_removed))
            telemetry.fields(all_removed, key='removed_series_keys')
            telemetry.record(
                circuit_breaker='removed_series_surge',
                removed_raw_spot=len(removed_raw_spot),
                removed_raw_swap=len(removed_raw_swap),
                removed_raw_spot_limit=spot_limit,
                removed_raw_swap_limit=swap_limit,
            )
            raise UpstreamUniverseDropError(
                f'Upstream raw universe drop exceeds limit: '
                f'spot={len(removed_raw_spot)}/{spot_limit} (old={old_spot_count}), '
                f'swap={len(removed_raw_swap)}/{swap_limit} (old={old_swap_count})'
            )

    # 2. Per-market circuit breaker for removed_columns across shared pivot/{spot|swap}/* tables.
    removed_pivot_spot = set()
    removed_pivot_swap = set()
    for key in old.keys() & new.keys():
        if key.startswith('pivot/spot/'):
            diff = old[key].columns.difference(new[key].columns, sort=False)
            if len(diff):
                removed_pivot_spot.update(canonical(c) for c in diff)
        elif key.startswith('pivot/swap/'):
            diff = old[key].columns.difference(new[key].columns, sort=False)
            if len(diff):
                removed_pivot_swap.update(canonical(c) for c in diff)
    if removed_pivot_spot or removed_pivot_swap:
        def _pivot_old_count(kind):
            close_key = f'pivot/{kind}/close'
            if close_key in old:
                return len(old[close_key].columns)
            return max((len(old[k].columns) for k in old if k.startswith(f'pivot/{kind}/')), default=0)

        old_pivot_spot_count = _pivot_old_count('spot')
        old_pivot_swap_count = _pivot_old_count('swap')
        pivot_spot_limit = max(int(removed_raw_min_limit), int(removed_raw_ratio_limit * old_pivot_spot_count))
        pivot_swap_limit = max(int(removed_raw_min_limit), int(removed_raw_ratio_limit * old_pivot_swap_count))
        if len(removed_pivot_spot) > pivot_spot_limit or len(removed_pivot_swap) > pivot_swap_limit:
            all_removed_piv = sorted(
                [f'spot:{s}' for s in removed_pivot_spot] + [f'swap:{s}' for s in removed_pivot_swap]
            )
            telemetry.add(removed_columns=len(all_removed_piv))
            telemetry.fields(all_removed_piv, key='removed_column_names')
            telemetry.record(
                circuit_breaker='removed_pivot_columns_surge',
                removed_pivot_spot=len(removed_pivot_spot),
                removed_pivot_swap=len(removed_pivot_swap),
                removed_pivot_spot_limit=pivot_spot_limit,
                removed_pivot_swap_limit=pivot_swap_limit,
            )
            raise UpstreamUniverseDropError(
                f'Upstream pivot universe drop exceeds limit: '
                f'spot={len(removed_pivot_spot)}/{pivot_spot_limit} (old={old_pivot_spot_count}), '
                f'swap={len(removed_pivot_swap)}/{pivot_swap_limit} (old={old_pivot_swap_count})'
            )

    resolved_held = None
    if (removed_raw_spot or removed_raw_swap or removed_pivot_spot or removed_pivot_swap) and held_symbols is not None:
        raw_held = held_symbols() if callable(held_symbols) else held_symbols
        if raw_held is not None:
            resolved_held = {(kind, canonical(sym)) for kind, sym in raw_held}

    earliest = []
    for key in old.keys() | new.keys():
        if key not in old:
            times = new[key].index[new[key].index<=end]
            if len(times):
                telemetry.add(added_series=1)
                telemetry.fields([key], key='added_series_keys')
            continue
        a = old[key]
        if key not in new:
            telemetry.add(removed_series=1)
            telemetry.fields([key], key='removed_series_keys')
            if key.startswith(('raw/spot/', 'raw/swap/')):
                _, kind, sym = key.split('/', 2)
                canon_sym = canonical(sym)
                is_held = resolved_held is not None and (kind, canon_sym) in resolved_held
                if is_held:
                    telemetry.fields([f'{kind}:{canon_sym}'], key='removed_held_symbols')
                    pivot_close = new.get(f'pivot/{kind}/close')
                    if pivot_close is None or sym not in pivot_close.columns:
                        earliest.append(a.index.min())
                continue
            earliest.append(a.index.min())
            continue
        b = new[key]
        # Earlier revision snapshots never projected raw funding_fee. Compare
        # the fields actually recorded, then keep the full new frame as baseline.
        if key.startswith('raw/') and 'funding_fee' not in a and 'funding_fee' in b:
            b = b.drop(columns='funding_fee')
        if not a.index.equals(b.index):
            positions=b.index.get_indexer(a.index)
            if (positions<0).any():raise ValueError('Revision input coverage is incomplete')
            b=b.take(positions)
        if a.equals(b):continue
        added_columns = b.columns.difference(a.columns, sort=False)
        removed_columns = a.columns.difference(b.columns, sort=False)
        if len(added_columns):
            telemetry.add(added_columns=len(added_columns))
            telemetry.fields(added_columns, key='added_column_names')
        if len(removed_columns):
            telemetry.add(removed_columns=len(removed_columns))
            telemetry.fields(removed_columns, key='removed_column_names')
            if key.startswith(('pivot/spot/', 'pivot/swap/')):
                kind = key.split('/', 2)[1]
                if resolved_held is None:
                    earliest.append(a.index.min())
                else:
                    held_hits = [
                        f'{kind}:{canonical(c)}'
                        for c in removed_columns
                        if (kind, canonical(c)) in resolved_held
                    ]
                    if held_hits:
                        telemetry.fields(held_hits, key='removed_held_symbols')
                        earliest.append(a.index.min())
            else:
                earliest.append(a.index.min())
        columns = a.columns.intersection(b.columns)
        if not len(columns):
            continue
        a = a.reindex(columns=columns);b = b.reindex(index=a.index,columns=columns)
        if a.equals(b):continue
        # Dictionary encodings are not market revisions; compare their values.
        categorical=[c for c in columns if isinstance(a[c].dtype,pd.CategoricalDtype)
                     or isinstance(b[c].dtype,pd.CategoricalDtype)]
        if categorical:
            a=a.astype({c:object for c in categorical});b=b.astype({c:object for c in categorical})
        different = ~(a.eq(b).fillna(False) | (a.isna() & b.isna()))
        changed = different.any(axis=1)
        if changed.any():
            earliest.append(a.index[changed].min())
            if telemetry.active():
                telemetry.add(changed_cells=int(different.to_numpy().sum()),changed_series=1)
                telemetry.fields(different.columns[different.any(axis=0)])
    return min(earliest) if earliest else None


_TS_UNIT_TO_NS = {'s': 10**9, 'ms': 10**6, 'us': 10**3, 'ns': 1}


def _timed_call(fn, *args, **kwargs):
    started = _time.perf_counter()
    try:
        return fn(*args, **kwargs), round(_time.perf_counter() - started, 6), None
    except BaseException as exc:
        return None, round(_time.perf_counter() - started, 6), exc


def read_inputs(run_time, minute, end, start=None, initializing=False, raw_swap=None, pivots=None):
    from config import bmac_data_path
    from core.utils.datatools import read_bmac_market_pivot
    from .offline import canonical
    from core.utils.arrow_reader import arrow_snapshot_path
    if raw_swap is None and (not initializing or all(
            arrow_snapshot_path(bmac_data_path,k,run_time).exists() for k in ('spot','swap'))):
        # Arrow already projects the required rows/columns: read spot/swap/pivot
        # concurrently and aggregate telemetry on the main thread (ContextVar-safe).
        with ThreadPoolExecutor(max_workers=3) as pool:
            spot_fut = pool.submit(_timed_call, read_arrow_tail, bmac_data_path, 'spot', run_time, end, start, indexed=True)
            swap_fut = pool.submit(_timed_call, read_arrow_tail, bmac_data_path, 'swap', run_time, end, start, indexed=True)
            pivot_fut = (
                pool.submit(_timed_call, input_frames, {}, pivots, end, start)
                if pivots is not None
                else pool.submit(_timed_call, read_pivot_inputs, bmac_data_path, minute, end, start)
            )
            results = (
                ('spot', 'revision_arrow_spot_s', spot_fut.result()),
                ('swap', 'revision_arrow_swap_s', swap_fut.result()),
                ('pivot', 'revision_pivot_s', pivot_fut.result()),
            )
        out = {}
        active = telemetry.active()
        for kind, phase_name, (value, elapsed_s, exc) in results:
            if active:
                telemetry.add(**{phase_name: elapsed_s})
            if exc is not None:
                if active:
                    telemetry._current.get().setdefault('failed_phase', phase_name)
                raise exc
            if kind in ('spot', 'swap'):
                if not value:
                    raise ValueError(f'Missing same-round {kind} Arrow for revision checks')
                out.update({f'raw/{kind}/{canonical(s)}': f for s, f in value.items()})
            else:
                out.update(value)
        return out
    raw = {}
    for kind in ('spot','swap'):
        if kind=='swap' and raw_swap is not None:
            raw[kind]=raw_swap;continue
        if initializing:
            sources = (pd.read_pickle(p) for p in sorted((Path(bmac_data_path)/f'{minute}m').rglob(f'{kind}_dict_batch*.pkl')))
            raw[kind]={s:f for source in sources for s,f in source.items()}
        else:
            raw[kind]=read_arrow_tail(bmac_data_path,kind,run_time,end,start)
            if not raw[kind]:raise ValueError(f'Missing same-round {kind} Arrow for revision checks')
    if pivots is None:pivots={k:read_bmac_market_pivot(f'{minute}m',k) for k in ('spot','swap')}
    # Normalize the symbol spelling exactly as MODEL does.
    raw={k:{canonical(s):f for s,f in source.items()} for k,source in raw.items()}
    return input_frames(raw,pivots,end,start)


def read_arrow_tail(root, kind, run_time, end, start=None, indexed=False, columns=None, tail=None):
    """Select columns/rows before pandas conversion; do not load 4050 factor rows."""
    import json
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path
    end=cutoff(end)
    start=cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out={}
    with pa.memory_map(str(arrow_snapshot_path(root,kind,run_time)),'r') as source:
        reader=pa.ipc.open_file(source)
        names=json.loads(reader.schema.metadata[b'symbols'])
        if len(names) != reader.num_record_batches:
            raise ValueError('Arrow symbol metadata differs from record batch count')
        missing_revision_fee = indexed and columns is None and 'funding_fee' not in reader.schema.names
        columns=['candle_begin_time']+[c for c in (RAW_COLUMNS if columns is None else columns)
                                      if c != 'candle_begin_time' and c in reader.schema.names]
        data_columns=[c for c in columns if not indexed or c!='candle_begin_time']
        ts_type = reader.schema.field('candle_begin_time').type
        ts_unit = ts_type.unit if pa.types.is_timestamp(ts_type) and ts_type.unit in _TS_UNIT_TO_NS else None
        if ts_unit is not None:
            scale = _TS_UNIT_TO_NS[ts_unit]
            end_key = end.as_unit('ns').value // scale
            start_key = (start.as_unit('ns').value + scale - 1) // scale
            index_dtype = f'datetime64[{ts_unit}]'
        for i,symbol in enumerate(names):
            batch=reader.get_batch(i)
            ts_col=batch.column('candle_begin_time')
            if ts_unit is not None and ts_col.null_count == 0:
                raw=ts_col.cast(pa.int64()).to_numpy(zero_copy_only=False)
                right=int(np.searchsorted(raw,end_key,side='right'))
                # MODEL features retain the original last-N-rows convention even
                # for sparse histories. Revision checks keep their time window.
                left=max(0,right-tail) if tail is not None else int(np.searchsorted(raw,start_key,side='left'))
                if right<=left:continue
                frame=batch.select(data_columns).slice(left,right-left).to_pandas(use_threads=False)
                if indexed:
                    if missing_revision_fee:
                        frame['funding_fee'] = np.nan
                    # Preserve Arrow native timestamp unit while detaching from mmap buffer.
                    frame.index=pd.DatetimeIndex(raw[left:right].copy(),dtype=index_dtype,name='candle_begin_time')
            else:
                idx=utc_times(ts_col.to_numpy(zero_copy_only=False))
                right=idx.searchsorted(end,side='right')
                left=max(0,right-tail) if tail is not None else idx.searchsorted(start)
                if right<=left:continue
                frame=batch.select(data_columns).slice(int(left),int(right-left)).to_pandas(use_threads=False)
                if indexed:
                    if missing_revision_fee:
                        frame['funding_fee'] = np.nan
                    frame.index=idx[left:right].copy(deep=True).rename('candle_begin_time')
            if indexed:
                if not frame.index.is_unique:raise ValueError('Duplicate revision input time')
                if 'first_candle_time' in frame:
                    frame['first_candle_time']=utc_times(frame.first_candle_time).to_numpy()
                if not frame.index.is_monotonic_increasing:frame=frame.sort_index()
            out[symbol]=frame
    return out


def read_pivot_inputs(root, minute, end, start=None, *, kinds=('spot', 'swap')):
    """Read each existing PKL once, but concatenate only the monitored interval."""
    end=cutoff(end)
    start=cutoff(start) if start is not None else end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    parts={}
    for kind in kinds:
        for path in (Path(root)/f'{minute}m').rglob(f'market_pivot_{kind}_*.pkl'):
            for field,frame in pd.read_pickle(path).items():
                if field not in ('open','close','vwap1m','funding_rate'):continue
                idx=utc_times(frame.index)
                value=frame.loc[(idx>=start)&(idx<=end)].copy()
                value.index=idx[(idx>=start)&(idx<=end)]
                parts.setdefault(f'pivot/{kind}/{field}',[]).append(value)
    return {key:pd.concat(frames,copy=False).sort_index() for key,frames in parts.items()}


def _validate_arrow_and_trim(root, kind, run_time, minute, stamp, required_columns):
    import pyarrow as pa
    from core.utils.arrow_reader import arrow_snapshot_path, read_arrow_history_trim_date
    path = arrow_snapshot_path(root, kind, run_time)
    if run_time.minute != int(minute):
        path = Path(root) / f'{int(minute)}m' / f'{kind}_dict_{int(stamp)}.arrow'
    with pa.memory_map(str(path), 'r') as source:
        reader = pa.ipc.open_file(source)
        if required_columns and not set(required_columns).issubset(reader.schema.names):
            raise ValueError(f'{kind} Arrow missing required columns')
    return read_arrow_history_trim_date(path)


def wait_and_read_inputs_streaming(
    bmac_data_path,
    run_time,
    minute,
    stamp,
    end,
    start,
    required_columns,
    *,
    deadline,
    poll_interval: float = 0.05,
):
    """Wait for spot/swap .ready flags without lock; stream spot reads as soon as spot is ready."""
    from .offline import canonical

    folder = Path(bmac_data_path) / f'{int(minute)}m'
    spot_arrow_ready = folder / f'spot_dict_{int(stamp)}.ready'
    spot_pivot_ready = folder / f'market_pivot_spot_{int(stamp)}.ready'
    swap_arrow_ready = folder / f'swap_dict_{int(stamp)}.ready'
    swap_pivot_ready = folder / f'market_pivot_swap_{int(stamp)}.ready'

    wait_start = _time.perf_counter()
    spot_started_at = None
    swap_ready_at = None
    spot_trim_fut = spot_arrow_fut = spot_pivot_fut = None

    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            if spot_started_at is None and spot_arrow_ready.exists() and spot_pivot_ready.exists():
                spot_started_at = _time.perf_counter()
                spot_trim_fut = pool.submit(_timed_call, _validate_arrow_and_trim, bmac_data_path, 'spot', run_time, minute, stamp, required_columns)
                spot_arrow_fut = pool.submit(_timed_call, read_arrow_tail, bmac_data_path, 'spot', run_time, end, start, indexed=True)
                spot_pivot_fut = pool.submit(_timed_call, read_pivot_inputs, bmac_data_path, minute, end, start, kinds=('spot',))

            if swap_arrow_ready.exists() and swap_pivot_ready.exists() and spot_arrow_ready.exists() and spot_pivot_ready.exists():
                swap_ready_at = _time.perf_counter()
                if spot_started_at is None:
                    spot_started_at = swap_ready_at
                    spot_trim_fut = pool.submit(_timed_call, _validate_arrow_and_trim, bmac_data_path, 'spot', run_time, minute, stamp, required_columns)
                    spot_arrow_fut = pool.submit(_timed_call, read_arrow_tail, bmac_data_path, 'spot', run_time, end, start, indexed=True)
                    spot_pivot_fut = pool.submit(_timed_call, read_pivot_inputs, bmac_data_path, minute, end, start, kinds=('spot',))
                break

            now = _time.time()
            if now >= deadline:
                for fut in (spot_trim_fut, spot_arrow_fut, spot_pivot_fut):
                    if fut is not None:
                        try:
                            fut.result()
                        except Exception:
                            pass
                return 'timeout', None, (None, None), {'wait_ready_s': round(_time.perf_counter() - wait_start, 6)}

            _time.sleep(min(poll_interval, max(0.0, deadline - now)))

        swap_trim_fut = pool.submit(_timed_call, _validate_arrow_and_trim, bmac_data_path, 'swap', run_time, minute, stamp, required_columns)
        swap_arrow_fut = pool.submit(_timed_call, read_arrow_tail, bmac_data_path, 'swap', run_time, end, start, indexed=True)
        swap_pivot_fut = pool.submit(_timed_call, read_pivot_inputs, bmac_data_path, minute, end, start, kinds=('swap',))

        spot_trim, _, spot_trim_exc = spot_trim_fut.result()
        swap_trim, _, swap_trim_exc = swap_trim_fut.result()
        spot_arrow_val, spot_arrow_s, spot_arrow_exc = spot_arrow_fut.result()
        swap_arrow_val, swap_arrow_s, swap_arrow_exc = swap_arrow_fut.result()
        spot_pivot_val, spot_pivot_s, spot_pivot_exc = spot_pivot_fut.result()
        swap_pivot_val, swap_pivot_s, swap_pivot_exc = swap_pivot_fut.result()

    wait_ready_s = round(swap_ready_at - wait_start, 6)
    spot_early_lead_s = round(max(0.0, swap_ready_at - spot_started_at), 6)
    stats = {
        '_ready_perf': swap_ready_at,
        'wait_ready_s': wait_ready_s,
        'spot_early_lead_s': spot_early_lead_s,
        'revision_arrow_spot_s': spot_arrow_s,
        'revision_arrow_swap_s': swap_arrow_s,
        'revision_pivot_s': round(spot_pivot_s + swap_pivot_s, 6),
    }

    for exc in (spot_trim_exc, swap_trim_exc, spot_arrow_exc, swap_arrow_exc):
        if exc is not None:
            stats['input_read_s'] = round(_time.perf_counter() - swap_ready_at, 6)
            return 'missing_arrow', None, (spot_trim, swap_trim), stats
    if not spot_arrow_val or not swap_arrow_val:
        stats['input_read_s'] = round(_time.perf_counter() - swap_ready_at, 6)
        return 'missing_arrow', None, (spot_trim, swap_trim), stats
    for exc in (spot_pivot_exc, swap_pivot_exc):
        if exc is not None:
            raise exc

    out = {}
    out.update({f'raw/spot/{canonical(s)}': f for s, f in spot_arrow_val.items()})
    out.update({f'raw/swap/{canonical(s)}': f for s, f in swap_arrow_val.items()})
    out.update(spot_pivot_val)
    out.update(swap_pivot_val)
    stats['input_read_s'] = round(_time.perf_counter() - swap_ready_at, 6)
    return 'ok', out, (spot_trim, swap_trim), stats


def trim_inputs(inputs, end):
    end=cutoff(end);start=end-pd.Timedelta(hours=REVISION_HISTORY_HOURS-1)
    out={}
    for key,value in inputs.items():
        tail=value.loc[start:end]
        if len(tail):out[key]=tail.copy()
    return out


def restore_child(child, time):
    """Restore both untimed/timed accounts and truncate their common selection."""
    child=Path(child);time=cutoff(time)
    for path in child.glob('*.pkl'):
        value=pd.read_pickle(path)
        if path.name.startswith('lots_'):
            point=next(p for p in value['snapshot_queue'] if cutoff(p['time'])==time)
            for kind in ('spot','swap'):value[kind]['sim'].restore(point[kind])
            value['simulate_to']=point['time']
            value['snapshot_queue']=[p for p in value['snapshot_queue'] if cutoff(p['time'])<=time]
            pd.to_pickle(value,path)
        else:
            if not isinstance(value,pd.DataFrame):raise ValueError(f'Unsupported child state: {path.name}')
            idx=value.candle_begin_time if 'candle_begin_time' in value else value.index
            keep=pd.to_datetime(idx,utc=True)<=time.tz_localize('UTC')
            value.loc[np.asarray(keep)].to_pickle(path)


def restore_snapshot(snapshot, revised_from, values):
    """Only mutate an isolated copy. Validate a common time BEFORE writing."""
    from positions.ModelRobust40Strategy import restore_model
    from tools.robust40.replay import common_before
    from positions.ModelRobust40Strategy import slice_context
    from .warmup import validate_snapshot
    snapshot=Path(snapshot)
    bundle=pd.read_pickle(snapshot/'model/state.pkl')
    meta=pd.read_pickle(snapshot/'warmup.pkl')
    if bundle.get('account_identity')!=meta['account'] or bundle['minute']!=cutoff(meta['cutoff']).minute:
        raise ValueError('Checkpoint account/offset differs')
    sections={p:pd.read_pickle(p) for p in (snapshot/'sections').glob('*.pkl')}
    lots={p:pd.read_pickle(p) for p in snapshot.glob('S*/lots_state*.pkl')}
    expected=sum(2 if s.get('re_timing') else 1 for s in values['strategy_pool'])
    if len(lots)!=expected or not sections:raise ValueError('Incomplete common checkpoint modules')
    groups=[{cutoff(p['time']) for p in s.get('snapshot_queue',[])} for s in lots.values()]
    groups += [{cutoff(t) for t in s.get('checkpoints',{})} for s in sections.values()]
    groups += [a.attrs.get('checkpoints',{}) for a in bundle['result']['virtual_accounts']+[bundle['result']['account']]]
    time=common_before(groups,cutoff(revised_from))
    if time.minute!=bundle['minute']:raise ValueError('Common checkpoint offset differs')
    restored,_=restore_model(bundle['context'],bundle['result'],revised_from,time)
    children=sorted({p.parent for p in lots})
    if len(children)<=1:
        for child in children:
            restore_child(child,time)
    else:
        with ThreadPoolExecutor(max_workers=min(4,len(children))) as pool:
            futures=[pool.submit(restore_child,child,time) for child in children]
            for fut in futures:
                fut.result()
    for path,state in sections.items():
        points=state['checkpoints'];key=next(t for t in points if cutoff(t)==time)
        output=state['output'];keep=pd.to_datetime(output.candle_begin_time,utc=True)<=time.tz_localize('UTC')
        pd.to_pickle(dict(points[key], output=output.loc[keep].copy(),
                          checkpoints={t:s for t,s in points.items() if cutoff(t)<=time}),path)
    context=slice_context(bundle['context'],bundle['context']['index'][bundle['context']['index']<=time])
    bundle=dict(bundle,context=context,result=restored)
    pd.to_pickle(bundle,snapshot/'model/state.pkl')
    (snapshot/'snapshot_time.txt').write_text(str(time.tz_localize('UTC')))
    validate_snapshot(snapshot,bundle)
    return time


@contextmanager
def transaction(destination, stage):
    """Caller holds account_lock. complete=True is the computation commit point.

    Keep one previous complete directory. On failure retain the failed workspace
    for diagnosis and put the old snapshot back. Exchange failure after commit
    must not rewind a calculation whose orders may already have been submitted.
    """
    destination,stage=Path(destination).resolve(),Path(stage).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    if stage==destination or stage.is_relative_to(destination):raise ValueError('Invalid replay stage')
    if backup.exists():
        if backup.parent!=destination.parent:raise ValueError('Invalid replay backup')
        shutil.rmtree(backup)
    destination.replace(backup)
    try:stage.replace(destination)
    except BaseException:
        backup.replace(destination);raise
    try:
        yield
    finally:
        try:committed=pd.read_pickle(destination/'warmup.pkl').get('complete') is True
        except Exception:committed=False
        if not committed:
            destination.replace(stage)
            backup.replace(destination)
            from .lifecycle import retire_replay
            retire_replay(stage,destination)
        else:
            retire_backup(destination)
            from .lifecycle import retire_replay
            retire_replay(stage,destination)


def retire_backup(destination):
    destination=Path(destination).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    previous=destination.with_name(destination.name+'.previous')
    if previous.exists():shutil.rmtree(previous)
    backup.replace(previous)


def recover_interrupted(destination, locked=False):
    """A process killed during the directory transaction leaves this unique backup."""
    destination=Path(destination).resolve()
    backup=destination.with_name(destination.name+'.replay-previous')
    if not backup.exists():
        folder=destination.parent.parent/'model_replay'/destination.name
        if folder.exists():
            from .server import account_lock
            from .lifecycle import sweep_replay
            with nullcontext() if locked else account_lock(destination):sweep_replay(destination)
        return
    from .server import account_lock
    with nullcontext() if locked else account_lock(destination):
        from .lifecycle import active_roots
        active=active_roots()
        if active is None or destination.parent.parent.parent.resolve() in active:
            raise BlockingIOError('Replay workers still active or ownership unreadable; recovery deferred')
        try:committed=pd.read_pickle(destination/'warmup.pkl').get('complete') is True
        except Exception:committed=False
        try:valid_backup=pd.read_pickle(backup/'warmup.pkl').get('complete') is True
        except Exception:valid_backup=False
        if not valid_backup:
            from .lifecycle import discard_interrupted
            discard_interrupted(backup,destination)
            return  # inspect() can now request full initialization as usual.
        if committed:
            retire_backup(destination);return
        failed=None
        if destination.exists():
            parent=destination.parent.parent/'model_replay'/destination.name;parent.mkdir(parents=True,exist_ok=True)
            failed=Path(tempfile.mkdtemp(prefix='interrupted-',dir=parent))
            destination.replace(failed/'snapshot')
        backup.replace(destination)
        if failed is not None:
            from .lifecycle import discard_interrupted, discard
            discard_interrupted(failed/'snapshot',destination)
            discard(failed,failed.parent)


def prepare(destination, values, run_time, minute, end, *, preloaded_old=None, preloaded_current=None):
    """Check bounded inputs before any child state or exchange action is changed."""
    with telemetry.phase('prepare_s'):
        return _prepare(
            destination, values, run_time, minute, end,
            preloaded_old=preloaded_old, preloaded_current=preloaded_current,
        )


def _prepare(destination, values, run_time, minute, end, *, preloaded_old=None, preloaded_current=None):
    destination=Path(destination)
    telemetry.record(offset=minute,cutoff=end)
    if preloaded_old is not None:
        old = preloaded_old
    else:
        with telemetry.phase('old_read_s'):
            old=pd.read_pickle(destination/'model/revision_inputs.pkl')
    start=min(f.index.min() for f in old.values())
    telemetry.record(input_start=start,input_frames=len(old))
    if preloaded_current is not None:
        current = preloaded_current
    else:
        with telemetry.phase('input_read_s'):
            current=read_inputs(run_time,minute,end,start=start)
    def _held_loader():
        state_path = destination / 'model/state.pkl'
        if not state_path.exists():
            return None
        return _extract_held_symbols(pd.read_pickle(state_path))

    with telemetry.phase('compare_s'):
        revised=first_revision(old,current,held_symbols=_held_loader)
    if revised is None:
        telemetry.record(mode='incremental')
        return None,trim_inputs(current,end)
    telemetry.record(mode='replay',revised_from=revised)
    telemetry.emit('revision_detected')
    parent=destination.parent.parent/'model_replay'/destination.name
    parent.mkdir(parents=True,exist_ok=True)
    from .lifecycle import sweep_replay
    sweep_replay(destination)
    workspace=Path(tempfile.mkdtemp(prefix='work-',dir=parent))
    stage=workspace/'snapshot'
    try:
        with telemetry.phase('copy_s'):
            shutil.copytree(destination,stage)
        with telemetry.phase('restore_s'):
            restored=restore_snapshot(stage,revised,values)
        telemetry.record(restored_at=restored,replay_hours=float((cutoff(end)-restored)/pd.Timedelta(hours=1)))
        telemetry.emit('restored')
        from .server import save_meta
        meta=pd.read_pickle(stage/'warmup.pkl')
        save_meta(stage,dict(meta,complete=False,cutoff=restored))
        print(f'MODEL历史修订 {revised}；共同恢复至 {restored}，子策略/S3/MODEL重新计算',flush=True)
    except BaseException:
        from .lifecycle import retire_replay
        retire_replay(stage,destination)
        raise
    return stage,trim_inputs(current,end)
