import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from datetime import datetime

from core.utils.arrow_reader import (
    read_arrow_market_dict, ArrowReadError,
    arrow_snapshot_path, read_arrow_market_dict_for_run_time,
)

ARROW_30M = Path(r'd:/alpha/data/preprocess_1h_resample/30m')
TS = 1782563400


def _write_arrow(tmp_path, market_dict):
    """用与生产者一致的布局写一个最小 arrow 文件用于隔离测试。"""
    symbols = list(market_dict.keys())
    ref_schema = None
    batches = []
    for sym in symbols:
        df = market_dict[sym].reset_index(drop=True)
        if ref_schema is None:
            b = pa.RecordBatch.from_pandas(df, preserve_index=False)
            ref_schema = b.schema
        else:
            b = pa.RecordBatch.from_pandas(df, schema=ref_schema, preserve_index=False)
        batches.append(b)
    md = dict(ref_schema.metadata or {})
    md[b'symbols'] = json.dumps(symbols).encode()
    schema = ref_schema.with_metadata(md)
    out = tmp_path / 'swap_dict_1.arrow'
    with pa.ipc.new_file(str(out), schema) as w:
        for b in batches:
            w.write_batch(b)
    return out


def test_reconstructs_symbol_dict_from_manifest(tmp_path):
    md = {
        'AAAUSDT': pd.DataFrame({'candle_begin_time': pd.to_datetime(['2026-01-01', '2026-01-02']),
                                 'symbol': ['AAAUSDT', 'AAAUSDT'], 'close': [1.0, 2.0]}),
        'BBBUSDT': pd.DataFrame({'candle_begin_time': pd.to_datetime(['2026-01-01', '2026-01-02']),
                                 'symbol': ['BBBUSDT', 'BBBUSDT'], 'close': [3.0, 4.0]}),
    }
    out = _write_arrow(tmp_path, md)
    result = read_arrow_market_dict(out)
    assert set(result.keys()) == {'AAAUSDT', 'BBBUSDT'}
    pd.testing.assert_frame_equal(result['BBBUSDT'], md['BBBUSDT'].reset_index(drop=True))


def test_missing_manifest_raises(tmp_path):
    df = pd.DataFrame({'symbol': ['X'], 'close': [1.0]})
    schema = pa.RecordBatch.from_pandas(df, preserve_index=False).schema  # 无 b'symbols'
    out = tmp_path / 'swap_dict_1.arrow'
    with pa.ipc.new_file(str(out), schema) as w:
        w.write_batch(pa.RecordBatch.from_pandas(df, preserve_index=False))
    with pytest.raises(ArrowReadError):
        read_arrow_market_dict(out)


@pytest.mark.skipif(not (ARROW_30M / f'swap_dict_{TS}.arrow').exists(),
                    reason='需要真实 30m arrow 数据')
def test_real_arrow_preserves_all_columns_including_key_0():
    result = read_arrow_market_dict(ARROW_30M / f'swap_dict_{TS}.arrow')
    assert len(result) > 100
    any_df = next(iter(result.values()))
    assert 'key_0' in any_df.columns
    assert 'candle_begin_time' in any_df.columns
    assert len(any_df) == 5000  # publish_kline_num


def test_arrow_snapshot_path_uses_minute_and_ts():
    rt = datetime.fromtimestamp(TS)
    p = arrow_snapshot_path(Path('/data'), 'swap', rt)
    assert p.name == f'swap_dict_{TS}.arrow'
    assert p.parent.name == f'{rt.minute}m'


def test_for_run_time_returns_none_when_missing(tmp_path):
    rt = datetime.fromtimestamp(TS)
    assert read_arrow_market_dict_for_run_time(tmp_path, 'swap', rt) is None


@pytest.mark.skipif(not (ARROW_30M / f'swap_dict_{TS}.arrow').exists(),
                    reason='需要真实 30m arrow 数据')
def test_for_run_time_reads_real_snapshot():
    rt = datetime.fromtimestamp(TS)
    bmac_root = ARROW_30M.parent  # .../preprocess_1h_resample
    d = read_arrow_market_dict_for_run_time(bmac_root, 'swap', rt)
    assert d is not None and len(d) > 100
