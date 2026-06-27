# 消费者 mmap-Arrow 读取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让消费者 `load_bmac_data` 在 `*.arrow` 存在时走 mmap 零拷贝快路径重建 `{symbol: DataFrame}`，否则自动 fallback 到既有 pkl 路径，结果与旧 pkl 路径逐 symbol/列/行一致。

**Architecture:** 新增 `core/utils/arrow_reader.py`（mmap 读 Arrow IPC + manifest 重建 dict + run_time_ts 校验），在 `core/utils/datatools.py::load_bmac_data` 前置 Arrow 快路径分支、缺失则落到现有 pkl glob。`read_and_merge_bmac_data` 完全不改——Arrow 只替换"怎么拿到 dict"，不改"拿到 dict 之后做什么"。`all_candle_df_list.pkl` 跨进程往返**保持不变**（它是 `mem_save_exe` 子进程边界，非本设计范围；spec §3.2 的消除项降级为可选后续）。

**Tech Stack:** Python 3.11 (conda env `alpha311`), pyarrow 17.0.0, pandas 2.2.3, pytest。

## Global Constraints

- conda env **必须** `alpha311`（`alpha` 环境读新 pkl 报 `No module named 'numpy._core'`）。所有 pytest/python 命令前缀 `conda run -n alpha311`。
- 注释、日志、docstring 一律**中文**（匹配仓库约定）。
- **加法 + fallback**：Arrow 缺失/损坏/ts 不符 → 自动回退 pkl 路径，最坏退回今天性能，不影响正确性。
- 真实对账数据：`d:/alpha/data/preprocess_1h_resample/30m/`，ts=`1782563400`（run_time=2026-06-27 20:30:00，minute=30）。`{spot,swap}_dict_1782563400.arrow` 与 `{spot,swap}_dict_batch*.pkl` 并存。
- 已确认：`key_0` 列在 pkl dict 与 Arrow 中**都存在**（是生产者 dict 的一部分，非 Arrow 产物），消费者**不得删除**任何列，原样重建。
- 生产者已提供参考实现 `d:/alpha/core/arrow_publish.py::read_arrow_snapshot`，消费者重建逻辑与之对齐（mmap → `ipc.open_file` → `schema.metadata[b'symbols']` JSON → 逐 batch `to_pandas()`）。
- 文件命名契约：`{symbol_type}_dict_{ts}.arrow`，`ts = int(run_time.timestamp())`，位于 `{run_time.minute}m/` 子目录。

---

### Task 1: Arrow 读取器 `read_arrow_market_dict`

**Files:**
- Create: `core/utils/arrow_reader.py`
- Test: `tests/test_arrow_reader.py`

**Interfaces:**
- Consumes: 磁盘上的 `*.arrow` 文件（生产者 `arrow_publish.publish_arrow_snapshot` 产出）。
- Produces:
  - `read_arrow_market_dict(arrow_path) -> dict[str, pandas.DataFrame]`：mmap 零拷贝重建 `{symbol: DataFrame}`，列原样保留（含 `key_0`），不做 concat/groupby。
  - 抛 `ArrowReadError`（本模块定义的异常）当文件不可读/manifest 缺失/损坏，供上层 catch 后 fallback。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arrow_reader.py
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from core.utils.arrow_reader import read_arrow_market_dict, ArrowReadError

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.utils.arrow_reader'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/utils/arrow_reader.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_reader.py -v`
Expected: PASS（3 项；真实数据项若文件在则 PASS，否则 skip）

- [ ] **Step 5: Commit**

```bash
git add core/utils/arrow_reader.py tests/test_arrow_reader.py
git commit -m "feat: 新增消费者 Arrow 快照 mmap 零拷贝读取器"
```

---

### Task 2: run_time_ts 校验 helper

**Files:**
- Modify: `core/utils/arrow_reader.py`
- Test: `tests/test_arrow_reader.py`

**Interfaces:**
- Consumes: Task 1 的 `read_arrow_market_dict`。
- Produces:
  - `arrow_snapshot_path(bmac_data_path, symbol_type, run_time) -> pathlib.Path`：拼出 `{minute}m/{symbol_type}_dict_{ts}.arrow`，`ts=int(run_time.timestamp())`。
  - `read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type, run_time) -> dict | None`：文件不存在或读取失败/ts 不符 → 返回 `None`（让上层走 fallback），成功 → 返回 dict。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_arrow_reader.py
from datetime import datetime
from core.utils.arrow_reader import arrow_snapshot_path, read_arrow_market_dict_for_run_time


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_reader.py -k "run_time or snapshot_path" -v`
Expected: FAIL — `ImportError: cannot import name 'arrow_snapshot_path'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to core/utils/arrow_reader.py
from datetime import datetime


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_reader.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add core/utils/arrow_reader.py tests/test_arrow_reader.py
git commit -m "feat: 新增 Arrow 快照按 run_time 读取与路径拼接 helper"
```

---

### Task 3: `load_bmac_data` 接入 Arrow 快路径 + fallback

**Files:**
- Modify: `core/utils/datatools.py:191-220`（`load_bmac_data`）
- Test: `tests/test_load_bmac_data_fallback.py`

**Interfaces:**
- Consumes: Task 2 的 `read_arrow_market_dict_for_run_time`；既有 `read_and_merge_bmac_data`（**不改**）；既有 `BinanceClient().get_market_info`。
- Produces: `load_bmac_data(symbol_type, run_time, conf)` 行为不变的返回值 `{symbol: DataFrame}`，但优先走 Arrow。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_load_bmac_data_fallback.py
"""验证 Arrow 缺失时 load_bmac_data 仍走 pkl（fallback 不回归）。
真实 30m 对账见 test_arrow_pkl_parity.py。"""
from datetime import datetime
from unittest import mock

import pandas as pd

import core.utils.datatools as dt


def _fake_conf():
    conf = mock.Mock()
    conf.black_list = []
    conf.white_list = []
    conf.min_kline_num = 1
    conf.get_kline_num.return_value = 4050
    return conf


def test_falls_back_to_pkl_when_no_arrow(tmp_path, monkeypatch):
    # bmac_data_path 指向空目录 → 无 arrow、无 pkl
    monkeypatch.setattr(dt, 'bmac_data_path', tmp_path)
    rt = datetime(2026, 6, 27, 20, 30)
    (tmp_path / '30m').mkdir()
    with mock.patch.object(dt, 'BinanceClient') as mc:
        mc.return_value.get_market_info.return_value = {'symbol_list': []}
        # arrow 读取应被调用并返回 None，然后落到 pkl glob（空）
        with mock.patch.object(dt, 'read_arrow_market_dict_for_run_time', return_value=None) as m_arrow:
            result = dt.load_bmac_data('swap', rt, _fake_conf())
    m_arrow.assert_called_once()
    assert result == {}


def test_uses_arrow_when_present(monkeypatch):
    rt = datetime(2026, 6, 27, 20, 30)
    fake_df = pd.DataFrame({
        'candle_begin_time': pd.to_datetime(['2026-01-01']),
        'first_candle_time': pd.to_datetime(['2026-01-01']),
        'last_candle_time': pd.to_datetime(['2026-01-01']),
        'symbol': ['ZZZUSDT'], 'symbol_swap': ['ZZZUSDT'], 'symbol_spot': [''],
    })
    with mock.patch.object(dt, 'read_arrow_market_dict_for_run_time',
                           return_value={'ZZZUSDT': fake_df}) as m_arrow:
        with mock.patch.object(dt, 'BinanceClient') as mc:
            mc.return_value.get_market_info.return_value = {'symbol_list': ['ZZZUSDT']}
            result = dt.load_bmac_data('swap', rt, _fake_conf())
    m_arrow.assert_called_once()
    assert 'ZZZUSDT' in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n alpha311 python -m pytest tests/test_load_bmac_data_fallback.py -v`
Expected: FAIL — `AttributeError: <module 'core.utils.datatools'> does not have the attribute 'read_arrow_market_dict_for_run_time'`

- [ ] **Step 3: Write minimal implementation**

在 `core/utils/datatools.py` 顶部 import 区加：

```python
from core.utils.arrow_reader import read_arrow_market_dict_for_run_time
```

把 `load_bmac_data`（191-220）改为：

```python
def load_bmac_data(symbol_type, run_time, conf: BacktestConfig):
    """
    加载数据：优先 Arrow mmap 零拷贝快路径，缺失/损坏自动 fallback 到 pkl glob。
    :param symbol_type: 数据类型
    :param run_time:  实盘的运行时间
    :param conf:  账户配置
    :return:
    """
    # 获取当前可交易的币种列表，用于过滤已下架或不可交易或传统资产的币种
    symbol_list = BinanceClient().get_market_info(symbol_type).get('symbol_list', [])

    # 快路径：Arrow 快照（文件名内嵌 ts，天然校验 run_time）
    arrow_dict = read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type, run_time)
    if arrow_dict is not None:
        results = dict()
        for _df in tqdm(arrow_dict.values(), desc=f'💿 {symbol_type} Arrow 快路径'):
            if symbol_list and _df is not None and not _df.empty:
                symbol = _df['symbol'].iloc[-1]
                if symbol not in symbol_list:
                    continue
            symbol, df_candle = read_and_merge_bmac_data(conf, symbol_type, _df, run_time)
            if df_candle is not None:
                results[symbol] = df_candle
        return results

    # fallback：既有 pkl glob 路径（行为完全不变）
    hour_offset = f'{run_time.minute}m'
    file_list = get_file_path(bmac_data_path, hour_offset, as_path_type=True).rglob(f'{symbol_type}_dict_batch*.pkl')

    results = dict()
    for file_path in file_list:
        data_dict = pd.read_pickle(file_path)
        for _df in tqdm(data_dict.values(), desc=f'💿 {file_path.stem}数据'):
            if symbol_list and _df is not None and not _df.empty:
                symbol = _df['symbol'].iloc[-1]
                if symbol not in symbol_list:
                    continue
            symbol, df_candle = read_and_merge_bmac_data(conf, symbol_type, _df, run_time)
            if df_candle is not None:
                results[symbol] = df_candle
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 python -m pytest tests/test_load_bmac_data_fallback.py -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add core/utils/datatools.py tests/test_load_bmac_data_fallback.py
git commit -m "feat: load_bmac_data 接入 Arrow 快路径并保留 pkl fallback"
```

---

### Task 4: 真实 30m 数据逐 symbol/列/行对账（核心验收）

**Files:**
- Test: `tests/test_arrow_pkl_parity.py`

**Interfaces:**
- Consumes: Task 1 `read_arrow_market_dict`；既有 `read_and_merge_bmac_data`；真实 `d:/alpha/data/preprocess_1h_resample/30m/` ts=1782563400 数据。
- Produces: 无（纯验收测试）。

**说明（为何在 merge 输出层对账）：** Arrow 每 batch 5000 行（publish tail），pkl 全量 ~6789+ 行。两路都经 `read_and_merge_bmac_data` 的 `candle_begin_time + utc_offset < run_time` 过滤后 `tail(get_kline_num=4050)`。最近 4050 行两路都覆盖，故**输出层**应逐 symbol/列/行一致。直接比 raw dict 会因行数不同而误判，必须比 merge 输出。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arrow_pkl_parity.py
"""真实 30m 数据：Arrow 快路径 vs pkl 路径，经 read_and_merge_bmac_data 后逐 symbol/列/行对账。"""
import glob
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from core.utils.arrow_reader import read_arrow_market_dict
from core.utils.datatools import read_and_merge_bmac_data

ARROW_30M = Path(r'd:/alpha/data/preprocess_1h_resample/30m')
TS = 1782563400
RUN_TIME = datetime.fromtimestamp(TS)

pytestmark = pytest.mark.skipif(
    not (ARROW_30M / f'swap_dict_{TS}.arrow').exists(),
    reason='需要真实 30m arrow 数据')


def _conf():
    conf = mock.Mock()
    conf.black_list = []
    conf.white_list = []
    conf.min_kline_num = 1
    conf.get_kline_num.return_value = 4050
    return conf


def _pkl_dict(symbol_type):
    merged = {}
    for p in glob.glob(str(ARROW_30M / f'{symbol_type}_dict_batch*.pkl')):
        merged.update(pd.read_pickle(p))
    return merged


@pytest.mark.parametrize('symbol_type', ['swap', 'spot'])
def test_arrow_pkl_merge_parity(symbol_type):
    conf = _conf()
    arrow_dict = read_arrow_market_dict(ARROW_30M / f'{symbol_type}_dict_{TS}.arrow')
    pkl_dict = _pkl_dict(symbol_type)

    # merge 两路（各自独立拷贝，read_and_merge 会原地改 df）
    def merge_all(d):
        out = {}
        for _df in d.values():
            if _df is None or _df.empty:
                continue
            sym, m = read_and_merge_bmac_data(conf, symbol_type, _df.copy(), RUN_TIME)
            if m is not None:
                out[sym] = m
        return out

    arrow_out = merge_all(arrow_dict)
    pkl_out = merge_all(pkl_dict)

    assert set(arrow_out.keys()) == set(pkl_out.keys()), \
        f'symbol 集合不一致: arrow-pkl={set(arrow_out)-set(pkl_out)}, pkl-arrow={set(pkl_out)-set(arrow_out)}'

    for sym in pkl_out:
        a, p = arrow_out[sym], pkl_out[sym]
        assert list(a.columns) == list(p.columns), f'{sym} 列顺序不一致'
        pd.testing.assert_frame_equal(a, p, check_like=False,
                                      obj=f'symbol={sym}')
```

- [ ] **Step 2: Run test to verify it fails (or surfaces real diffs)**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_pkl_parity.py -v`
Expected: 初次运行预期 PASS（逻辑相同）。若 FAIL，记录首个不一致 symbol 的列/行差异——这是真实 parity bug，必须在 Task 1/3 实现侧修复（不得改测试放宽断言）。常见可能：dtype（`to_pandas` 对 int64 列）、NaN 处理、`tail` 边界行。

- [ ] **Step 3: 若失败，定位并修复实现**

诊断顺序：
1. 单 symbol 复现：取首个 diff symbol，分别打印 arrow/pkl 的 `dtypes`、`head`、`tail`、`shape`。
2. dtype 差异 → 在 `read_arrow_market_dict` 的 `to_pandas()` 后按需对齐（仅当确证 pkl 侧 dtype 不同；优先用 `to_pandas(types_mapper=...)` 或显式 astype，**只动 Arrow 读取器，不动 merge**）。
3. 行数/边界差异 → 确认 publish_kline_num(5000) ≥ 过滤后 4050；若某 symbol pkl 历史 < 5000 而 arrow 截断，复核生产者 tail 语义。
4. 修复后重跑 Step 2。

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n alpha311 python -m pytest tests/test_arrow_pkl_parity.py -v`
Expected: PASS（swap + spot 两参数化项，逐 symbol/列/行一致）

- [ ] **Step 5: Commit**

```bash
git add tests/test_arrow_pkl_parity.py
git commit -m "test: 真实 30m 数据 Arrow vs pkl 经 merge 后逐 symbol/列/行对账"
```

---

### Task 5: 全量回归 + 文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-06-27-consumer-mmap-arrow-read-design.md`（§3.2 状态注记）

**Interfaces:**
- Consumes: Task 1-4 全部测试。
- Produces: 无新接口。

- [ ] **Step 1: 跑全部新增测试**

Run: `conda run -n alpha311 python -m pytest tests/ -v`
Expected: 全 PASS（真实数据项命中则跑，否则 skip）

- [ ] **Step 2: 标注 spec §3.2 范围调整**

在 spec §3.2 末尾追加一行说明 `all_candle_df_list.pkl` 往返保持不变的原因：

```markdown
> **实现期调整（2026-06-27）**：经核对，`all_candle_df_list.pkl` 是 `mem_save_exe`
> 的 `max_workers=1` 子进程边界（`load_realtime_data` 在子进程写、`calc_factors` 在
> 父进程读），并非纯 in-process 冗余序列化。消除它需改因子计算 worker 边界，风险高于
> 收益，本期**保持不变**，降级为可选后续。本期收益来自 Arrow 替换 pkl 反序列化本身。
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-27-consumer-mmap-arrow-read-design.md
git commit -m "docs: 注记 all_candle_df_list.pkl 往返本期保持不变的原因"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 Arrow 快路径 → Task 1 + Task 3 ✅
- §3.2 消除 all_candle_df_list.pkl → 经核对降级为可选后续，Task 5 在 spec 注明原因 ✅（范围调整，非遗漏）
- §3.3 run_time 校验 → Task 2（文件名内嵌 ts，命中即一致）✅
- §6/§7 fallback、对账验收 → Task 3（fallback 测试）+ Task 4（逐 symbol/列/行对账）✅
- §2 `read_and_merge_bmac_data` 不改 → 全程复用，未列入修改 ✅

**2. Placeholder scan:** 无 TBD/TODO；所有 step 含完整代码或精确命令。Task 4 Step 3 是条件分支（仅当 parity 失败），给了具体诊断顺序而非占位。

**3. Type consistency:** `read_arrow_market_dict(path) -> dict[str, DataFrame]`、`read_arrow_market_dict_for_run_time(bmac_data_path, symbol_type, run_time) -> dict | None`、`arrow_snapshot_path(...) -> Path`、`ArrowReadError` 在 Task 1-3 引用一致；`load_bmac_data` 签名不变。
