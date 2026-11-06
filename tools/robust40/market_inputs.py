"""Shared market publication freezing and public-universe preparation.

No strategy weights or parameters; legacy warmup and current initialization use
the same operations. Importing this module performs no I/O or exchange calls.
"""
import errno
import os
from pathlib import Path
import shutil
import time


def latest_ready(folder, minute, now=None):
    """All four producer publications must describe the same completed hour."""
    now = time.time() if now is None else now
    prefixes = ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_')
    common = None
    for prefix in prefixes:
        stamps = {int(p.stem[len(prefix):]) for p in folder.glob(prefix+'*.ready')
                  if p.stem[len(prefix):].isdigit()}
        common = stamps if common is None else common & stamps
    import pandas as pd
    valid = [t for t in common if t <= now and pd.Timestamp(t, unit='s').minute == minute
             and pd.Timestamp(t, unit='s').second == 0]
    if not valid:raise ValueError(f'{minute}m has no complete closed-hour publication')
    stamp = max(valid)
    # A new partial publication must not be mixed with the previous ready flags.
    return stamp, max((folder/f'{p}{stamp}.ready').stat().st_mtime_ns for p in prefixes)



class PublicationUnavailable(ValueError):
    """Publication is incomplete or was replaced while binding its immutable files."""


def full_publication(source, minute, required=(), cache=None):
    """Only ready flags/stat/header reads. Cache headers until the files change."""
    import pyarrow as pa
    folder=Path(source)/f'{minute}m'
    try:
        stamp,ready=latest_ready(folder,minute)
        # Do not bind old pivots while a newer publication is only partly ready.
        for prefix in ('spot_dict_','swap_dict_','market_pivot_spot_','market_pivot_swap_'):
            if any(p.stem[len(prefix):].isdigit() and int(p.stem[len(prefix):])>stamp
                   for p in folder.glob(prefix+'*.ready')):
                return None,'waiting for newer partial publication'
        paths=[folder/f'{kind}_dict_{stamp}.arrow' for kind in ('spot','swap')]
        version=(stamp,ready,tuple(file_version(p) for p in paths),frozenset(required))
    except (FileNotFoundError,ValueError):
        return None,'waiting for same-round complete publication'
    if cache is not None and cache.get('version')==version:return cache['result']
    result=(stamp,'full publication ready')
    try:
        for path in paths:
            with pa.memory_map(str(path),'r') as src:
                schema=pa.ipc.open_file(src).schema
                if (schema.metadata or {}).get(b'full_history')!=b'1':
                    result=(None,'waiting for full-history Arrow');break
                if not set(required).issubset(schema.names):
                    result=(None,'waiting for required Arrow columns');break
    except (FileNotFoundError,pa.ArrowInvalid):
        return None,'publication changed or incomplete Arrow header'
    if cache is not None:cache.update(version=version,result=result)
    return result

def freeze_file(source, destination):
    """Only for read-only CandleFileManager outputs (producer unlinks/recreates).

    Same-volume links pin the old file when alpha publishes or cleans its path.
    Never overwrite a destination: it may itself still link to a public input.
    """
    try:
        os.link(source, destination)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.ENOTSUP, errno.ENOSYS):
            raise
        with open(source, 'rb') as src, open(destination, 'xb') as dst:
            shutil.copyfileobj(src, dst, 1024*1024)
        shutil.copystat(source, destination)


def file_version(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def freeze_extra(source, destination, loader, minute):
    """Freeze reader dependencies; unknown/custom writers retain ordinary copies."""
    def inputs():
        if loader == 'load_coin_btc':
            return [p for p in (source/'BTC-USDT.csv', source/f'{minute}m/BTCUSDT.pkl',
                                source/f'{minute}m/BTC-USDT.pkl') if p.is_file()]
        if loader == 'load_coin_cap':
            return list(source.glob('*.pkl'))
        return [p for p in source.rglob('*') if p.is_file()]
    files = inputs()
    before = {p: file_version(p) for p in files}
    destination.mkdir(parents=True, exist_ok=False)
    for path in files:
        target = destination/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        # CSV and custom loaders have no verified unlink/recreate contract.
        if loader in ('load_coin_btc', 'load_coin_cap') and path.suffix == '.pkl':
            freeze_file(path, target)
        else:
            shutil.copy2(path, target)
    if before != {p: file_version(p) for p in inputs()}:
        raise PublicationUnavailable(f'Factor input changed during freeze: {source}')


def freeze_inputs(source, exginfo, destination, minute, stamp, ready_time, *, full_arrow=False):
    try:
        return _freeze_inputs(source,exginfo,destination,minute,stamp,ready_time,full_arrow=full_arrow)
    except FileNotFoundError as exc:
        raise PublicationUnavailable('Publication file disappeared during freeze') from exc


def _freeze_inputs(source, exginfo, destination, minute, stamp, ready_time, *, full_arrow=False):
    folder = source/f'{minute}m'
    target = destination/f'{minute}m'
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for kind in ('spot', 'swap'):
        if full_arrow:
            import pyarrow as pa
            path = folder/f'{kind}_dict_{stamp}.arrow'
            if not path.is_file():
                raise PublicationUnavailable(f'Missing full-history Arrow: {path}; alpha publish_kline_num=0 required')
            # Header-only check: no historical conversion or factor computation.
            with pa.memory_map(str(path), 'r') as src:
                metadata = pa.ipc.open_file(src).schema.metadata or {}
                if metadata.get(b'full_history') != b'1':
                    raise PublicationUnavailable(f'{path.name}: full-history Arrow required; alpha publish_kline_num=0')
            files.append(path)
        # Full Arrow supplies factors, MODEL raw features and revision tails.
        # Keep PKL batches only for the explicitly selected legacy PKL input.
        patterns=[f'market_pivot_{kind}_*.pkl']
        if not full_arrow:patterns.insert(0,f'{kind}_dict_batch*.pkl')
        for pattern in patterns:
            found = sorted(folder.glob(pattern))
            if not found:raise PublicationUnavailable(f'Missing input: {pattern}')
            files.extend(found)
    before = {p: file_version(p) for p in files}
    if any(info[1] > ready_time for info in before.values()):
        raise PublicationUnavailable('Producer is publishing newer inputs; retry after it is ready')
    for path in files:freeze_file(path, target/path.name)
    after = {p: file_version(p) for p in files}
    if before != after:raise PublicationUnavailable('Producer inputs changed during freeze; retry in a new workspace')
    for prefix in ('spot_dict_', 'swap_dict_', 'market_pivot_spot_', 'market_pivot_swap_'):
        shutil.copy2(folder/f'{prefix}{stamp}.ready', target/f'{prefix}{stamp}.ready')
    (destination/'exginfo').mkdir()
    for kind in ('spot', 'swap'):
        path = exginfo/f'exginfo_{kind}.pkl'
        before = file_version(path)
        freeze_file(path, destination/'exginfo'/path.name)
        if before != file_version(path):
            raise ValueError('Market lot-size input changed during freeze')


def public_market_client():
    """A fork must not inherit the account client's restricted class-level cache."""
    from core.utils.exchange_hub import get_base_client_class
    client = get_base_client_class().get_dummy_client()
    client.market_info = {}
    return client


def prepare_public_market():
    """Preserve account bStocks policy; selection uses the public market client."""
    import config
    from core.model.account_config import AccountConfig
    account = AccountConfig(config.account_name, **config.account_config)
    account.init_exchange(dict(config.exchange_basic_config))
    refresh = getattr(account.bn, 'fetch_bstocks_coins', None)
    if refresh is not None:
        refresh()
        # A successful empty result has a timestamp; a cold failure does not.
        if account.bn.filter_bstocks and not account.bn._bstocks_last_update:
            raise ValueError('bStocks automatic identification failed without a successful cache; retry initialization')
    public = public_market_client()
    public.fetch_market_info('spot')
    public.fetch_market_info('swap')
    return public
