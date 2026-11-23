"""
邢不行｜策略分享会
仓位管理实盘框架

消费者侧 Arrow IPC 零拷贝读取：mmap 打开生产者发布的 *.arrow 快照，按 schema
metadata 中的 manifest（b'symbols'，JSON 数组）把每个 record batch 还原成
market_dict[symbols[i]]。列原样保留，不做 concat/groupby，不删任何列。

与生产者 d:/alpha/core/arrow_publish.py::publish_arrow_snapshot 的布局严格对齐。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa

_MANIFEST_KEY = b'symbols'


class ArrowReadError(Exception):
    """Arrow 快照不可读 / manifest 缺失 / 损坏。上层据此 fallback 到 pkl 路径。"""


def read_arrow_market_dict(arrow_path, max_rows=None) -> dict[str, pd.DataFrame]:
    """Read all symbols, optionally slicing Arrow BEFORE allocating Pandas data."""
    return dict(iter_arrow_market(arrow_path, max_rows=max_rows))


def iter_arrow_market(arrow_path, max_rows=None, *, require_full=False, required_columns=()):
    """逐币转换；mmap读取Arrow，转Pandas时仍可能分配内存。

    :param arrow_path: 生产者发布的 *.arrow 文件路径。
    :return: 迭代(symbol, DataFrame)，列布局与生产者一致。
    :raises ArrowReadError: 文件无法以 IPC FILE 打开，或缺少 b'symbols' manifest。
    """
    arrow_path = Path(arrow_path)
    try:
        with pa.memory_map(str(arrow_path), 'r') as source:
            reader = pa.ipc.open_file(source)
            if require_full and (reader.schema.metadata or {}).get(b'full_history') != b'1':
                raise ArrowReadError(f'{arrow_path.name}: full history Arrow required; producer publish_kline_num=0')
            missing = set(required_columns) - set(reader.schema.names)
            if missing:
                raise ArrowReadError(f'{arrow_path.name}: missing precomputed columns {sorted(missing)}')
            manifest = (reader.schema.metadata or {}).get(_MANIFEST_KEY)
            if manifest is None:
                raise ArrowReadError(f'{arrow_path.name} 缺少 manifest（b"symbols"）')
            symbols = json.loads(manifest.decode('utf-8'))
            if len(symbols) != reader.num_record_batches:
                raise ArrowReadError(
                    f'{arrow_path.name} manifest 长度 {len(symbols)} != '
                    f'record batch 数 {reader.num_record_batches}')
            for i in range(reader.num_record_batches):
                batch = reader.get_batch(i)
                if max_rows is not None:
                    if max_rows <= 0:
                        raise ValueError('max_rows must be positive or None')
                    batch = batch.slice(max(0, batch.num_rows-max_rows))
                yield symbols[i], batch.to_pandas()
    except ArrowReadError:
        raise
    except Exception as e:  # noqa: BLE001 — 任何底层异常都转成可 fallback 的信号
        raise ArrowReadError(f'读取 {arrow_path} 失败: {e!r}') from e


def arrow_snapshot_path(bmac_data_path, symbol_type: str, run_time: datetime) -> Path:
    """拼出 Arrow 快照路径：{bmac_data_path}/{minute}m/{symbol_type}_dict_{ts}.arrow。"""
    ts = int(run_time.timestamp())
    return Path(bmac_data_path) / f'{run_time.minute}m' / f'{symbol_type}_dict_{ts}.arrow'


def read_arrow_history_trim_date(arrow_path: str | Path) -> str | None:
    """只读 Arrow schema metadata 中的 b'history_trim_date'；缺失或空值返回 None。"""
    arrow_path = Path(arrow_path)
    with pa.memory_map(str(arrow_path), 'r') as source:
        reader = pa.ipc.open_file(source)
        raw = (reader.schema.metadata or {}).get(b'history_trim_date')
    if not raw:
        return None
    text = raw.decode('utf-8').strip()
    return text or None


def read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type: str, run_time: datetime, max_rows=5000):
    """按 run_time 读 Arrow 快照；文件缺失或读取失败 → 返回 None（上层 fallback）。

    文件名内嵌 ts（= int(run_time.timestamp())），命中即天然保证 run_time 一致，
    避免读到上一轮残留文件。
    """
    path = arrow_snapshot_path(bmac_data_path, symbol_type, run_time)
    if not path.exists():
        return None
    try:
        # Preserve the previous producer's 5000-row window for ordinary callers.
        # MODEL then applies its existing 4050 cut after mapping/warmup handling.
        return read_arrow_market_dict(path, max_rows=max_rows)
    except ArrowReadError:
        return None
