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


def read_arrow_market_dict(arrow_path) -> dict[str, pd.DataFrame]:
    """mmap 零拷贝重建 {symbol: DataFrame}。

    :param arrow_path: 生产者发布的 *.arrow 文件路径。
    :return: {symbol: DataFrame}，列布局与生产者发布时一致（含 key_0）。
    :raises ArrowReadError: 文件无法以 IPC FILE 打开，或缺少 b'symbols' manifest。
    """
    arrow_path = Path(arrow_path)
    try:
        with pa.memory_map(str(arrow_path), 'r') as source:
            reader = pa.ipc.open_file(source)
            manifest = (reader.schema.metadata or {}).get(_MANIFEST_KEY)
            if manifest is None:
                raise ArrowReadError(f'{arrow_path.name} 缺少 manifest（b"symbols"）')
            symbols = json.loads(manifest.decode('utf-8'))
            if len(symbols) != reader.num_record_batches:
                raise ArrowReadError(
                    f'{arrow_path.name} manifest 长度 {len(symbols)} != '
                    f'record batch 数 {reader.num_record_batches}')
            result: dict[str, pd.DataFrame] = {}
            for i in range(reader.num_record_batches):
                result[symbols[i]] = reader.get_batch(i).to_pandas()
        return result
    except ArrowReadError:
        raise
    except Exception as e:  # noqa: BLE001 — 任何底层异常都转成可 fallback 的信号
        raise ArrowReadError(f'读取 {arrow_path} 失败: {e!r}') from e


def arrow_snapshot_path(bmac_data_path, symbol_type: str, run_time: datetime) -> Path:
    """拼出 Arrow 快照路径：{bmac_data_path}/{minute}m/{symbol_type}_dict_{ts}.arrow。"""
    ts = int(run_time.timestamp())
    return Path(bmac_data_path) / f'{run_time.minute}m' / f'{symbol_type}_dict_{ts}.arrow'


def read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type: str, run_time: datetime):
    """按 run_time 读 Arrow 快照；文件缺失或读取失败 → 返回 None（上层 fallback）。

    文件名内嵌 ts（= int(run_time.timestamp())），命中即天然保证 run_time 一致，
    避免读到上一轮残留文件。
    """
    path = arrow_snapshot_path(bmac_data_path, symbol_type, run_time)
    if not path.exists():
        return None
    try:
        return read_arrow_market_dict(path)
    except ArrowReadError:
        return None
