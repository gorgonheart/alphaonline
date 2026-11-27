# 子项目 2B：生产端 `v2.0.2` 升级、存储解耦与双端零重建滚动 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将生产端 `D:/alpha` 升级至 `coin-realtime-data_v2.0.2` WebSocket 底座并拆除全部 5 处暗雷、实现 5m/1h 存储解耦与全量本地定制平移，同时为消费端 `D:/alphaonline` 补齐 Pivot 移除分级治理、滚动 S3 初始化起点与 `history_trim_date` 计划内瘦身联动，确保 2B 上线首日 0 `动量V2` 漂移、0 重建，离线全量回归通过后具备测试机 24h 门禁交付条件。

**Architecture:** 在隔离分支上分双仓推进：`D:/alpha`（`feat/v202-migration-2b`，基于 `main=b5eb945`）迁入 `v2.0.2` 分包架构并保留顶层 `core/*.py` 薄转发层与 `realtime_data.py` / `config.py` 入口兼容，将 `base_5m` 压为 30 天滚动、`flat pkl` 压为 240 行、常驻进程压至 5 个（2 writers + 2 单进程多协程 WS runners + 1 main）、`1h`/`funding`/`batch PKL` 改为固定时间戳下限保留、预处理顺序跑 `spot→swap` 压低峰值至 `< 2.5 GiB`；`D:/alphaonline`（`feat/phase5-2b-consumer-hardening`，基于 `main=2001075`）在不改动 `accounts/*.py` 与 `positions/ModelRobust40Strategy.py` 的前提下，通过 `handoff.py`、`server.py`、`server_robust40.py` 与 `arrow_reader.py` 完成双保险与滚动起点衔接。

**Tech Stack:** Python 3.11 (`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`), `pandas 2.2.3`, `numpy 2.0.2`, `pyarrow 17.0.0`, `aiohttp 3.11.11` (test/prod `3.13.3`), `sqlite3`, `cffi` (4 existing C extensions), `pytest`.

**Spec:** [docs/superpowers/specs/2026-11-23-subproject-2b-v202-migration-design.md](../specs/2026-11-23-subproject-2b-v202-migration-design.md)

## Global Constraints

- 唯一 Python 解释器：`C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`；所有 Windows pytest 均带 `-p no:cacheprovider --basetemp=C:/tmp/pt2b`（或对应独立短路径）；离线测试禁止外网连接。
- 严禁引入任何新第三方依赖（WS 仅用现有 `aiohttp`）。
- 保留 `D:/alpha/realtime_data.py` 与 `D:/alpha/config.py` + 机器私有 `config.json` 入口兼容；保留 `D:/alpha` 未跟踪上游目录（`coin-realtime-data_v2.0.2/` 等）与 `D:/alphaonline` 已有未提交文档修改。
- 严禁修改 `D:/alphaonline/accounts/*.py`、`D:/alphaonline/positions/ModelRobust40Strategy.py` 及任何核心因子数学公式（`动量V2` / `CciV3` / `ILLQ`）。
- 默认离线开发与验证：本计划执行至 Task 11 全部为离线代码与测试；测试机 `alphatest` 部署与重启（阶段 2B-3）须待离线回归完成并获得用户单独授权后再执行。
- 7 条跨项目数据契约不可破坏：尤其原子发布顺序（C 因子 → 原子 `.arrow` → `market_pivot_*` → 最后写 `.ready`）、`batch PKL` 永不落预计算因子、`history_trim_date=None` 时上线首日对现有 `resample_1h`/`batch PKL` 0 截断且 0 隐式前置补拉。
- 严禁在代码、测试或提交记录中写入真实 API Key、UUID、Webhook 或密码。

## Review Focus

1. **Barrier 在 `rest_done.set()` 边界的竞态提前退出**：`wait_for_ready_barrier` 若在未于 `rest_done` 置位后跑完至少一轮 `.ready` 检查就提前 break，会误 skip 刚由 REST 补齐落盘的 symbol；必须在循环头采样 `rest_done_was_set = rest_done.is_set()`，仅当该轮以 `True` 跑完 `pending` 检查且 `not (pending & rest_submitted)` 时才允许提前退出（由 Task 7 测试锁定）。
2. **存量非 UTC / ms 精度时间戳混入（无缝升级现网磁盘）**：测试机/正式机存量 `flat pkl`、`base_5m` 或 `Arrow` 可能含 `tz=None` 或 `ms`/`us`/`ns` 混合时间列；`Writer._merge` 必须统一规范为 `datetime64[ns, UTC]`，`scan_arrow_earliest_bar` 必须兼容 `s`/`ms`/`us`/`ns` 与 tz-aware/naive Arrow 列（由 Task 2 与 Task 9 测试锁定）。
3. **单进程多协程 `WsRunner` 的单 chunk 故障隔离与空 symbol 列表**：超过 200 个 symbol 拆成多 chunk 后，单 chunk 连接异常重连不得拖垮或阻塞其他 chunk，且 `symbols=[]` 时 `start()` / `run()` / `stop()` 不得挂死（由 Task 3 测试锁定）。
4. **`history_trim_date` 半轮不一致与倒退防御**：`spot` 与 `swap` Arrow 的 `b'history_trim_date'` 不一致时 `_trade()` 必须报错跳过本轮且不破坏 `warmup.pkl`；上游值晚于（更早于）快照已记录值时（配置回退）不得触发无限重建循环（由 Task 10 测试锁定）。
5. **Pivot `removed_columns` 别名匹配与缺失 `pivot/{kind}/close` 分母边界**：持仓校验须按 `(kind, canonical(col))` 匹配（识别 `BTC-USDT` 与 `BTCUSDT`）；当 `old` 中缺少 `pivot/{kind}/close` 时，8% 熔断分母按实际存在的旧 Pivot 表最大列数（非 0）计算且不抛 `ZeroDivisionError`，同时若整张 Pivot 表缺失保持原保守重建行为（由 Task 8 测试锁定）。

---

## File Map

### `D:/alpha`（分支 `feat/v202-migration-2b`，基于 `main=b5eb945`）
- **保留入口与配置**：`realtime_data.py`、`config.py`、`tools/deploy_alpha.sh`
- **新增/升级子包**：
  - `core/app.py`、`core/handle.py`、`core/alert.py`、`core/version.py`
  - `core/exchange/`（全套 `v2.0.2` 抽象 + `rest/binance_fetcher.py` 本地 funding 校验/限流加固 + `symbol_match.py`）
  - `core/storage/`（`candle_manager.py`、`ts_manager.py`、`writer.py` 暗雷 1 修复）
  - `core/ws/`（`ws_kline_client.py`、`ws_local_cache.py` 暗雷 4 修复、`ws_runner.py` 暗雷 2 单进程多协程改造）
  - `core/pipeline/`（`runtime_supervisor.py` 暗雷 2、`barrier.py` 提前退出、`update_cycle.py` 非阻塞发布、`data_update.py`、`resample.py` 暗雷 3 + `int_dtypes` + 固定 `min_ts`、`backfill.py`、`publication_sources.py`）
  - `core/history_data/`（`history.py`、`candle.py` 30d 快路径与自愈、`funding.py` 并发4+450/300s、`utils.py`）
  - `core/ingest/`（`coin_cap.py`、`coin_cap_schema.py`、`quantclass.py`、`data_api_update.py`）
  - `core/strategy/`（`preprocess.py` 暗雷 5 顺序 `spot→swap` + 保列、`factor_calc.py`、`arrow_publish.py` + `history_trim_date`、`arrow_demand.py`、`candle_origins.py`、`funding_backfill.py`、`funding_hours.py`、`funding_publication.py`）
  - `core/snapshot/`（`v2.0.2` 骨架，默认关闭）、`core/utils/`（`file_system.py` 非破坏 `init_dirs` + `apply_explicit_history_trim`、`log_bridge.py` 等）
- **顶层兼容薄转发层**：`core/{arrow_demand,arrow_publish,backfill,binance_fetcher_async,binance_market_async,binance_update,candle_manager,candle_origins,coin_cap,coin_cap_schema,data_api_update,factor_calc,funding_backfill,funding_hours,funding_publication,preprocess,publication_sources,quantclass_async,resample_candle,symbol_filter,symbol_match,ts_manager}.py`（纯 `from core.<subpkg>.<mod> import *` + 必要的旧函数名别名，保证既有 30+ 测试零断裂）。

### `D:/alphaonline`（分支 `feat/phase5-2b-consumer-hardening`，基于 `main=2001075`）
- **修改**：`tools/robust40/handoff.py`、`tools/robust40/server.py`、`tools/robust40/cli/server_robust40.py`、`core/utils/arrow_reader.py`
- **新增测试**：`tests/test_2b_pivot_removed_columns.py`、`tests/test_2b_rolling_s3_origin.py`、`tests/test_2b_history_trim_handoff.py`
- **文档同步**：`docs/DEVELOPMENT.md`、`docs/README.md`、`docs/kb/roadmap-and-backlog.md`、`docs/kb/branches-and-sync.md`、`docs/2026-11-23-phase5-and-v202-migration-plan.md`

---

### Task 1: `D:/alpha` 隔离分支、`v2.0.2` 基础骨架、配置校验与 `BmacHandle` 扩展

**Files:**
- Branch: `D:/alpha` 创建 `feat/v202-migration-2b`（从 `main=b5eb945`）
- Create: `D:/alpha/core/alert.py`, `core/utils/log_bridge.py`, `core/exchange/**`, `core/storage/{__init__,candle_manager,ts_manager}.py`, `core/snapshot/**`（自只读参考 `coin-realtime-data_v2.0.2/` 拷入并清理末尾隐形水印字节）
- Modify: `D:/alpha/config.py`, `D:/alpha/core/handle.py`, `D:/alpha/core/version.py`, `D:/alpha/core/{candle_manager,ts_manager,symbol_filter,symbol_match}.py`（改为薄转发）
- Test: `D:/alpha/tests/test_2b_config_and_handle.py`

**Interfaces:**
- Consumes: `D:/alpha/coin-realtime-data_v2.0.2/core/{exchange,storage,snapshot,utils/log_bridge.py,alert.py}`（只读参考源）
- Produces:
  - `config.py` 新增默认项：`use_websocket=True`, `rest_fetch_delay_seconds=3.0`, `num_writers=2`, `ws_flat_pkl_bars=240`, `base_5m_retention_days=30`, `min_history_start_date='2025-03-25'`, `history_trim_date=None`, `kline_count_1h_spot=15000`, `kline_count_1h_swap=15000`
  - `core.handle.MarketHandle`（`BinanceHandle = MarketHandle`）
  - `core.handle.BmacHandle` 属性/方法：`spot`, `swap`, `binance_spot`, `binance_swap`, `get_market_handle(trade_type)`, `get_binance_handle(trade_type)`, `kline_count_1h_for(trade_type) -> int`, `effective_kline_count_1h(trade_type: str, run_time: datetime) -> int`, `history_trim_ts() -> Optional[pd.Timestamp]`, `base_5m_retention_bars() -> int`, `flat_pkl_max_bars() -> int`
  - `core.handle.create_bmac_handle_from_config(cfg=None) -> BmacHandle` 与 `create_binance_handle(cfg) -> BmacHandle`

- [ ] **Step 1: 在 `D:/alpha` 创建特性分支并核对工作树干净**

Run: `git -C D:/alpha checkout -b feat/v202-migration-2b main && git -C D:/alpha status --short --untracked-files=no`
Expected: 切换到新分支 `feat/v202-migration-2b`，已跟踪文件无脏改动。

- [ ] **Step 2: 编写失败测试 `D:/alpha/tests/test_2b_config_and_handle.py`**

```python
import importlib.util, json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pytest
from core.utils import path_kit


def _load_cfg(tmp_path, monkeypatch, overrides):
    monkeypatch.setattr(path_kit, "get_file_path", lambda *a, **kw: tmp_path / a[0])
    monkeypatch.setattr(path_kit, "get_folder_path", lambda *a, **kw: str(tmp_path / "data"))
    (tmp_path / "config.json").write_text(json.dumps(overrides), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cfg_2b_test", Path(__file__).resolve().parents[1] / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_2b_config_defaults_and_single_kline_count_fallback(tmp_path, monkeypatch):
    cfg = _load_cfg(tmp_path, monkeypatch, {"kline_count_1h": 14400})
    assert cfg.use_websocket is True
    assert cfg.num_writers == 2
    assert cfg.ws_flat_pkl_bars == 240
    assert cfg.base_5m_retention_days == 30
    assert cfg.min_history_start_date == "2025-03-25"
    assert cfg.history_trim_date is None
    assert cfg.kline_count_1h_spot == 14400
    assert cfg.kline_count_1h_swap == 14400


def test_handle_effective_kline_count_anchors_past_2026_12_16(tmp_path, monkeypatch):
    from core.handle import create_bmac_handle_from_config
    cfg = _load_cfg(tmp_path, monkeypatch, {
        "use_api": {"kline": False, "coin_cap": False},
        "kline_count_1h_spot": 15000, "kline_count_1h_swap": 15000,
        "min_history_start_date": "2025-03-25", "history_trim_date": None,
    })
    h = create_bmac_handle_from_config(cfg)
    assert h.binance_spot is h.spot and h.get_binance_handle("swap") is h.swap
    assert h.base_5m_retention_bars() == 30 * 24 * 12
    assert h.flat_pkl_max_bars() == 240
    assert h.history_trim_ts() is None
    late = datetime(2027, 1, 15, 0, 25, tzinfo=timezone.utc)
    # 2025-03-25 -> 2027-01-15 > 15,800h, +48h safety buffer
    assert h.effective_kline_count_1h("swap", late) > 15800


def test_history_trim_date_rejects_too_recent_and_overrides_floor(tmp_path, monkeypatch):
    from core.handle import create_bmac_handle_from_config
    too_recent = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=10000)).strftime("%Y-%m-%d")
    cfg_bad = _load_cfg(tmp_path, monkeypatch, {
        "use_api": {"kline": False, "coin_cap": False},
        "history_trim_date": too_recent,
    })
    with pytest.raises(ValueError, match="14400|600"):
        create_bmac_handle_from_config(cfg_bad)

    valid_trim = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=15000)).strftime("%Y-%m-%d")
    cfg_ok = _load_cfg(tmp_path, monkeypatch, {
        "use_api": {"kline": False, "coin_cap": False},
        "min_history_start_date": "2024-01-01",
        "history_trim_date": valid_trim,
    })
    h = create_bmac_handle_from_config(cfg_ok)
    assert h.history_trim_ts() == pd.Timestamp(valid_trim, tz="UTC")
```

- [ ] **Step 3: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_config_and_handle.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL（`config.py` 缺少 `use_websocket` 等属性、`handle.py` 缺少新方法）。

- [ ] **Step 4: 迁入 `v2.0.2` 基础模块并实现 `config.py` 与 `core/handle.py`**

按以下要求落实：
- 从 `D:/alpha/coin-realtime-data_v2.0.2/core/` 迁入 `alert.py`、`utils/log_bridge.py`、`exchange/**`、`storage/{__init__,candle_manager,ts_manager}.py`、`snapshot/**`，逐文件去除任何末尾不可见 tag 字符（`U+E0000..U+E007F`）；保持现有 `core/utils/network.py`、`core/utils/time.py`、`core/exchange/symbol_match.py`（保留 `D:/alpha/core/symbol_match.py` 的全量已验证逻辑）。
- 在 `D:/alpha/config.py` 补全全部 2B 默认配置与 `config.json` 覆盖（含若只配 `kline_count_1h` 且未单独配 `kline_count_1h_spot`/`kline_count_1h_swap` 时同步赋值）；校验 `num_writers >= 1`、`ws_flat_pkl_bars >= 1`、`base_5m_retention_days >= 7`、`rest_fetch_delay_seconds >= 0`（非 bool）。
- 在 `D:/alpha/core/handle.py` 合并 `v2.0.2` 的 `MarketHandle` + `BmacHandle` 与本地 `kline_count_1h_spot/swap`、`coin_cap_use_proxy`、`precompute_factor_*`、`arrow_demand_*` 字段；实现 `effective_kline_count_1h(trade_type, run_time)`、`history_trim_ts()`、`base_5m_retention_bars()`、`flat_pkl_max_bars()`、`binance_spot`/`binance_swap` property、`get_binance_handle`，并在 `validate_config` 校验 `history_trim_date` 距今 `>= 14400h`（否则 `raise ValueError`）以及 `history_trim_date > min_history_start_date` 时记录 `logger.warning` 并以 `history_trim_date` 作有效下限。
- 将 `core/candle_manager.py`、`core/ts_manager.py`、`core/symbol_filter.py`、`core/symbol_match.py` 改为薄转发到 `core.storage.*` / `core.exchange.*`。

- [ ] **Step 5: 运行测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_config_and_handle.py D:/alpha/tests/test_deployment_config.py D:/alpha/tests/test_symbol_match_1112.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add config.py core/ tests/test_2b_config_and_handle.py
git -C D:/alpha commit -m "feat(2b-1): add v2.0.2 base skeleton, 2B config validation, and extended BmacHandle"
```

---

### Task 2: 暗雷 1 修复（`Writer` 小窗 `flat pkl` + 全旧分区裁剪）、非破坏性 `init_dirs` 与 `apply_explicit_history_trim`

**Files:**
- Create: `D:/alpha/core/storage/writer.py`
- Modify: `D:/alpha/core/utils/file_system.py`
- Test: `D:/alpha/tests/test_2b_writer_and_storage.py`

**Interfaces:**
- Consumes: `core.storage.ts_manager.TSManager`, `core.storage.candle_manager.CandleFileManager`, `core.candle_origins.preserve_published_origins`, `core.handle.BmacHandle`
- Produces:
  - `Writer(base_candle_dirs, flat_pkl_max_bars=240, partition_retention_bars=8640, base_delta=timedelta(minutes=5), alert=None, gap_check_bars=99)`（保留关键字兼容：若调用方仅传 `num_base_candles` 未传 `partition_retention_bars`，映射到 `partition_retention_bars`）
  - `init_dirs(handle: BmacHandle) -> None`（非破坏性创建目录，绝不 `shutil.rmtree` `resample_dir`/`preprocess_dir`/`exg_mgr`/`funding_mgr`/`coin_cap_mgr`/`data_api`）
  - `apply_explicit_history_trim(handle: BmacHandle, trim_ts: Optional[pd.Timestamp]) -> dict[str, int]`（返回各目录实际裁剪文件计数；`trim_ts is None` 为纯 no-op 返回全 0）

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_writer_and_storage.py`**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest


def test_writer_caps_flat_pkl_at_240_and_trims_all_old_partitions(tmp_path):
    from core.storage.writer import Writer
    from core.storage.ts_manager import TSManager

    base_dir = tmp_path / "spot_5m"
    base_dir.mkdir()
    # Seed 3 old monthly partitions (2025-04, 2025-05, 2026-10) + recent 2026-11
    ts_mgr = TSManager(base_dir / "BTCUSDT")
    old_times = pd.to_datetime(
        ["2025-04-01 00:00", "2025-05-01 00:00", "2026-10-01 00:00", "2026-11-20 00:00"], utc=True
    )
    ts_mgr.update(pd.DataFrame({"candle_begin_time": old_times, "open": 1.0, "close": 1.0}))
    assert len(ts_mgr.list_partitions()) == 4

    writer = Writer(
        base_candle_dirs={"spot": base_dir},
        flat_pkl_max_bars=240,
        partition_retention_bars=30 * 24 * 12,
        base_delta=timedelta(minutes=5),
    )
    # Push 300 bars via WS -> flat pkl must cap at 240, partitions older than 30d ALL deleted
    run_time = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)
    times = pd.date_range(end=run_time - timedelta(minutes=5), periods=300, freq="5min", tz="UTC")
    df_new = pd.DataFrame({"candle_begin_time": times, "open": 2.0, "close": 2.0})
    writer.process_one({"symbol": "BTCUSDT", "trade_type": "spot", "df": df_new, "source": "ws", "run_time": run_time})

    flat = pd.read_pickle(base_dir / "BTCUSDT.pkl")
    assert len(flat) == 240
    assert flat["candle_begin_time"].dtype == "datetime64[ns, UTC]"
    # 2025-04 and 2025-05 must BOTH be gone (upstream bug only trimmed partitions[0])
    remaining = ts_mgr.list_partitions()
    assert "2025-04" not in remaining and "2025-05" not in remaining
    assert "2026-11" in remaining


def test_writer_normalizes_mixed_naive_and_ms_timestamps_in_legacy_flat_pkl(tmp_path):
    """Review Focus #2: legacy flat pkl with tz-naive or ms precision merges cleanly with ns UTC."""
    from core.storage.writer import Writer

    base_dir = tmp_path / "swap_5m"
    base_dir.mkdir()
    legacy = pd.DataFrame({
        "candle_begin_time": pd.to_datetime(["2026-11-22 23:50:00"]).astype("datetime64[ms]"),
        "open": [10.0], "close": [10.0],
    })
    legacy.to_pickle(base_dir / "ETHUSDT.pkl")

    writer = Writer(base_candle_dirs={"swap": base_dir}, flat_pkl_max_bars=240, partition_retention_bars=8640)
    run_time = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)
    incoming = pd.DataFrame({
        "candle_begin_time": pd.to_datetime(["2026-11-22 23:55:00"], utc=True),
        "open": [11.0], "close": [11.0],
    })
    writer.process_one({"symbol": "ETHUSDT", "trade_type": "swap", "df": incoming, "source": "ws", "run_time": run_time})
    merged = pd.read_pickle(base_dir / "ETHUSDT.pkl")
    assert len(merged) == 2
    assert merged["candle_begin_time"].dtype == "datetime64[ns, UTC]"


def test_init_dirs_preserves_existing_pivots_batches_and_arrow_demand(tmp_path):
    from core.storage.candle_manager import CandleFileManager
    from core.utils.file_system import init_dirs

    pre_dir = tmp_path / "preprocess" / "25m"
    (pre_dir / ".arrow-demand").mkdir(parents=True)
    pivot_file = pre_dir / "market_pivot_spot_2026.pkl"
    complete_file = pre_dir / "market_pivot_spot_2026.complete"
    demand_file = pre_dir / ".arrow-demand" / "acct.json"
    batch_file = pre_dir / "spot_dict_batch0.pkl"
    for f in (pivot_file, complete_file, demand_file, batch_file):
        f.write_bytes(b"keep")
    res_dir = tmp_path / "spot_1h" / "25m"
    res_dir.mkdir(parents=True)
    res_file = res_dir / "BTCUSDT.pkl"
    res_file.write_bytes(b"keep-1h")
    exg_dir = tmp_path / "exginfo"
    exg_dir.mkdir()
    exg_file = exg_dir / "exginfo_spot.pkl"
    exg_file.write_bytes(b"keep-exg")

    h = SimpleNamespace(
        spot=SimpleNamespace(base_candle_dir=tmp_path / "spot_5m", resample_dir=tmp_path / "spot_1h",
                             resample_mgr_map={"25m": CandleFileManager(res_dir, "pickle")}),
        swap=SimpleNamespace(base_candle_dir=tmp_path / "swap_5m", resample_dir=tmp_path / "swap_1h",
                             resample_mgr_map={}),
        exg_mgr=CandleFileManager(exg_dir, "pickle"),
        funding_mgr=CandleFileManager(tmp_path / "funding", "pickle"),
        data_api=None,
        preprocess_handle=SimpleNamespace(preprocess_dir=tmp_path / "preprocess",
                                          preprocess_mgr_map={"25m": CandleFileManager(pre_dir, "pickle")}),
        coin_cap_handle=None,
    )
    init_dirs(h)
    for f in (pivot_file, complete_file, demand_file, batch_file, res_file, exg_file):
        assert f.exists() and f.read_bytes().startswith(b"keep")


def test_apply_explicit_history_trim_noop_when_none_and_trims_when_set(tmp_path):
    from core.storage.candle_manager import CandleFileManager
    from core.utils.file_system import apply_explicit_history_trim

    res_dir = tmp_path / "spot_1h" / "25m"
    pre_dir = tmp_path / "preprocess" / "25m"
    fun_dir = tmp_path / "funding"
    for d in (res_dir, pre_dir, fun_dir):
        d.mkdir(parents=True)
    times = pd.to_datetime(["2024-12-31 23:25", "2025-06-01 00:25", "2026-11-01 00:25"], utc=True)
    df = pd.DataFrame({"candle_begin_time": times, "close": [1.0, 2.0, 3.0]})
    df.to_pickle(res_dir / "BTCUSDT.pkl")
    pd.to_pickle({"BTCUSDT": df.copy()}, pre_dir / "spot_dict_batch0.pkl")
    df[["candle_begin_time"]].assign(funding_rate=0.001).to_pickle(fun_dir / "BTCUSDT.pkl")
    old_pivot = pre_dir / "market_pivot_spot_2024.pkl"
    old_complete = pre_dir / "market_pivot_spot_2024.complete"
    cur_complete = pre_dir / "market_pivot_spot_2025.complete"
    for f in (old_pivot, old_complete, cur_complete):
        f.write_bytes(b"p")

    h = SimpleNamespace(
        enabled_hour_offsets=("25m",),
        spot=SimpleNamespace(resample_mgr_map={"25m": CandleFileManager(res_dir, "pickle")}),
        swap=SimpleNamespace(resample_mgr_map={}),
        funding_mgr=CandleFileManager(fun_dir, "pickle"),
        data_api=None,
        preprocess_handle=SimpleNamespace(preprocess_mgr_map={"25m": CandleFileManager(pre_dir, "pickle")}),
    )
    assert apply_explicit_history_trim(h, None) == {"resample": 0, "batch": 0, "funding": 0, "data_api": 0, "pivots_removed": 0}
    assert len(pd.read_pickle(res_dir / "BTCUSDT.pkl")) == 3

    trim_ts = pd.Timestamp("2025-06-01 00:00:00", tz="UTC")
    stats = apply_explicit_history_trim(h, trim_ts)
    assert stats["resample"] == 1 and stats["batch"] == 1 and stats["funding"] == 1 and stats["pivots_removed"] == 1
    assert len(pd.read_pickle(res_dir / "BTCUSDT.pkl")) == 2
    assert len(pd.read_pickle(pre_dir / "spot_dict_batch0.pkl")["BTCUSDT"]) == 2
    assert len(pd.read_pickle(fun_dir / "BTCUSDT.pkl")) == 2
    assert not old_pivot.exists() and not old_complete.exists() and not cur_complete.exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_writer_and_storage.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL（`core.storage.writer` 尚未创建、`init_dirs` 仍在删目录）。

- [ ] **Step 3: 实现 `core/storage/writer.py` 与 `core/utils/file_system.py`**

- 在 `core/storage/writer.py`（基于 `v2.0.2`）：
  - 拆分 `flat_pkl_max_bars` 与 `partition_retention_bars`；
  - 新增 `_normalize_candle_begin_time(df)`，将 `df["candle_begin_time"]` 统一转为 tz-aware UTC `datetime64[ns, UTC]`（兼容 tz-naive 与 `ms`/`us`/`s` 精度）后再合并；
  - `_merge` 用 `self.flat_pkl_max_bars` 裁剪；
  - `_update_tsmanager` 遍历 `ts_mgr.list_partitions()`，对**所有**早于 `required_first_begin` 的月分区调用 `trim_partition(part, required_first_begin, run_time)`（可按 `YYYY-MM` 跳过明确晚于 `required_first_begin` 的较新分区，确保包含 `required_first_begin` 所在月及更早所有分区均被裁剪并删除空分区）。
- 在 `core/utils/file_system.py`：
  - `init_dirs(handle)` 保留所有目录（`remove_exist=False`），对每个 offset 调用 `preserve_published_origins(mgr)`；兼容 `handle.spot`/`handle.swap` 及旧测试的 `handle.binance_spot`/`handle.binance_swap`；
  - 实现 `apply_explicit_history_trim(handle, trim_ts)`，使用原子 `.tmp` → `os.replace` 回写被裁剪的 `resample_1h`、`batch PKL`、`funding`、`data_api` 文件，删除 `year < trim_ts.year` 的旧年 Pivot 及 `.complete`，且只要至少裁剪了 1 份文件，清除受影响年份（`year >= trim_ts.year` 且原数据有早于 `trim_ts` 的记录）的 `.complete` 标记。

- [ ] **Step 4: 运行测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_writer_and_storage.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add core/storage/writer.py core/utils/file_system.py tests/test_2b_writer_and_storage.py
git -C D:/alpha commit -m "feat(2b-1): fix writer flat/partition bounds, non-destructive init_dirs, and explicit history trim"
```

---

### Task 3: 暗雷 2 & 暗雷 4 修复（`WsLocalCache` 48h 清理 + 单进程多协程 `WsRunner` + `RuntimeSupervisor` 5 进程瘦身）

**Files:**
- Create: `D:/alpha/core/ws/{__init__,ws_kline_client,ws_local_cache,ws_runner}.py`
- Create: `D:/alpha/core/pipeline/{__init__,runtime_supervisor}.py`
- Test: `D:/alpha/tests/test_2b_ws_supervisor.py`

**Interfaces:**
- Consumes: `core.exchange.protocol.ExchangeProtocol`, `core.storage.writer.Writer`, `core.handle.BmacHandle`
- Produces:
  - `WsLocalCache.prune_before(cutoff_ms: int) -> int`
  - `WsRunner` 支持单市场全量 `symbols` 按 `protocol.max_streams_per_conn` 切分为多个 `WsKlineClient` 协程并发运行在同一事件循环，共享单个 `WsLocalCache`（`ws_cache/{trade_type}.sqlite`），支持独立 chunk 异常隔离、启动重放后及每小时自动 `prune_before(now_ms - 48h)`
  - `RuntimeSupervisor(handle, alert_webhook_url, protocol=None, num_writers=None)`：默认从 `handle.num_writers`（默认 `2`）取 writer 数；每市场仅 spawn **1 个** `ws_runner` 进程（`bmac-{exchange}-ws-{trade_type}`），将 `flat_pkl_max_bars` 与 `partition_retention_bars` 传入 `run_writer_subprocess`

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_ws_supervisor.py`**

```python
import asyncio
import queue
from datetime import timedelta
from types import SimpleNamespace
import pandas as pd
import pytest


def test_ws_local_cache_prune_before_deletes_old_rows_only(tmp_path):
    from core.ws.ws_local_cache import WsLocalCache
    cache = WsLocalCache(tmp_path / "spot.sqlite")
    old_ms = 1_700_000_000_000
    new_ms = old_ms + 72 * 3600 * 1000
    for ms in (old_ms, new_ms):
        cache.write("BTCUSDT", "spot", {
            "begin_time_ms": ms, "open": 1, "high": 2, "low": 1, "close": 2,
            "volume": 10, "quote_volume": 20, "trade_num": 5, "taker_buy_base": 3, "taker_buy_quote": 6,
        })
    deleted = cache.prune_before(new_ms - 48 * 3600 * 1000)
    assert deleted == 1
    rows = cache.replay_after("BTCUSDT", "spot", 0)
    assert [r["begin_time_ms"] for r in rows] == [new_ms]
    cache.close()


def test_ws_runner_multichunk_single_process_isolates_chunk_failure_and_empty_symbols(tmp_path):
    """Review Focus #3: >200 symbols split into multi-chunk coroutines; chunk-1 error does not crash chunk-0; empty symbols safe."""
    from core.ws.ws_runner import WsRunner
    from core.exchange.protocol import BinanceProtocol

    created_clients = []
    q = queue.Queue()

    class FakeClient:
        def __init__(self, url, chunk_idx, on_closed):
            self.url = url
            self.chunk_idx = chunk_idx
            self.on_closed = on_closed
            self.subscribed = []
            self.stopped = False
            self.run_attempts = 0
        async def connect(self): pass
        async def subscribe(self, streams): self.subscribed = list(streams)
        async def run(self):
            self.run_attempts += 1
            if self.chunk_idx == 1 and self.run_attempts == 1:
                raise ConnectionResetError("transient chunk-1 drop")
            if self.chunk_idx == 0:
                self.on_closed({"symbol": "SYM0", "kline": {
                    "begin_time_ms": 1_700_000_000_000, "open": 1, "high": 1, "low": 1, "close": 1,
                    "volume": 1, "quote_volume": 1, "trade_num": 1, "taker_buy_base": 1, "taker_buy_quote": 1,
                }})
            await asyncio.sleep(0.05)
        async def close(self): self.stopped = True

    def factory(on_closed, url=None, chunk_idx=0):
        c = FakeClient(url, chunk_idx, on_closed)
        created_clients.append(c)
        return c

    runner = WsRunner(
        url="wss://example", trade_type="spot", queue=q,
        cache_path=tmp_path / "spot.sqlite", base_delta=timedelta(minutes=5),
        ws_client_factory=factory, protocol=BinanceProtocol(), chunk_retry_base_sec=0.01,
    )
    symbols = [f"SYM{i}" for i in range(450)]  # 3 chunks: 200 + 200 + 50
    async def exercise():
        await runner.start(symbols=symbols, replay_on_start=False)
        assert len(created_clients) == 3
        assert [len(c.subscribed) for c in created_clients] == [200, 200, 50]
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.08)
        await runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())
    assert not q.empty() and q.get_nowait()["symbol"] == "SYM0"
    assert created_clients[1].run_attempts >= 2
    assert all(c.stopped for c in created_clients)

    # Empty symbols must not hang
    empty_runner = WsRunner(url="wss://example", trade_type="spot", queue=q,
                            cache_path=tmp_path / "empty.sqlite", ws_client_factory=factory)
    async def exercise_empty():
        await empty_runner.start(symbols=[], replay_on_start=False)
        await empty_runner.stop()
    asyncio.run(exercise_empty())


def test_runtime_supervisor_spawns_one_ws_spec_per_market_and_two_writers(tmp_path):
    from core.pipeline.runtime_supervisor import RuntimeSupervisor
    from core.storage.candle_manager import CandleFileManager

    exg_dir = tmp_path / "exg"
    exg_dir.mkdir()
    exg_mgr = CandleFileManager(exg_dir, "pickle")
    exg_mgr.save_data_file("exginfo_spot", pd.DataFrame({"symbol": [f"S{i}USDT" for i in range(450)]}))
    exg_mgr.save_data_file("exginfo_swap", pd.DataFrame({"symbol": [f"F{i}USDT" for i in range(550)]}))

    h = SimpleNamespace(
        base_dir=tmp_path, base_interval="5m", exchange="binance", proxy=None,
        num_writers=2, num_base_candles=8640,
        flat_pkl_max_bars=lambda: 240, base_5m_retention_bars=lambda: 8640,
        exg_mgr=exg_mgr,
        spot=SimpleNamespace(base_candle_dir=tmp_path / "s5m", once_update_candles=99),
        swap=SimpleNamespace(base_candle_dir=tmp_path / "f5m", once_update_candles=99),
        get_market_handle=lambda tt: h.spot if tt == "spot" else h.swap,
    )
    sup = RuntimeSupervisor(handle=h, alert_webhook_url="")
    assert sup.num_writers == 2 and len(sup.queues) == 2
    spot_specs = sup._build_ws_runner_specs("spot")
    swap_specs = sup._build_ws_runner_specs("swap")
    assert len(spot_specs) == 1 and len(spot_specs[0].symbols) == 450
    assert len(swap_specs) == 1 and len(swap_specs[0].symbols) == 550
    assert spot_specs[0].cache_path.name == "spot.sqlite"
    assert swap_specs[0].cache_path.name == "swap.sqlite"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_ws_supervisor.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL（`core.ws` 与 `core.pipeline.runtime_supervisor` 尚未创建）。

- [ ] **Step 3: 实现 `core/ws/*` 与 `core/pipeline/runtime_supervisor.py`**

- `core/ws/ws_local_cache.py`：迁入 `v2.0.2` 并新增 `prune_before(cutoff_ms: int) -> int`（`DELETE FROM kline WHERE begin_time_ms < ?` + `conn.commit()` + `PRAGMA wal_checkpoint(PASSIVE)`，返回 `cursor.rowcount`）。
- `core/ws/ws_kline_client.py`：迁入 `v2.0.2` 原生 `aiohttp` WS 客户端（去隐形水印）。
- `core/ws/ws_runner.py`：改造为单进程多连接协程编排——按 `self._protocol.max_streams_per_conn` 将 `symbols` 切片为多个 chunk，每 chunk 构造一个 `WsKlineClient(url=self._protocol.ws_url(self.trade_type, idx), ...)`（支持 `ws_client_factory` 注入测试桩）；`run()` 并发运行各 chunk 的独立退避重试循环（单 chunk 报错记录日志并退避重连，绝不取消其他 chunk）+ 一个每小时执行 `prune_before(now_ms - 48h)` 的定时任务；`start()` 重放后亦做一次 `prune_before`；`symbols` 为空时安全返回且 `run()` 仅等待取消/`stop()`。
- `core/pipeline/runtime_supervisor.py`：默认 `NUM_WRITERS = 2` 且优先读 `getattr(handle, "num_writers", self.NUM_WRITERS)`；`_build_ws_runner_specs(trade_type)` 返回**单元素列表** `[WsRunnerSpec(..., cache_path=self.ws_cache_dir / f"{trade_type}.sqlite", symbols=symbols)]`，子进程名 `bmac-{exchange}-ws-{trade_type}`；`start_writers()` 传入 `handle.flat_pkl_max_bars()` 与 `handle.base_5m_retention_bars()` 给 `run_writer_subprocess`。

- [ ] **Step 4: 运行测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_ws_supervisor.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add core/ws/ core/pipeline/ tests/test_2b_ws_supervisor.py
git -C D:/alpha commit -m "feat(2b-1): slim ws_runner to 1 proc/market, cap writers=2, add 48h sqlite prune"
```

---

### Task 4: 5m/1h 存储解耦、冷启/缺盘自愈与暗雷 3（消除 8-spawn `resample` + `int_dtypes` 还原）

**Files:**
- Create: `D:/alpha/core/pipeline/resample.py`
- Modify: `D:/alpha/core/resample_candle.py`（薄转发至 `core.pipeline.resample`）
- Modify: `D:/alpha/core/history_data/{candle,history,utils}.py`
- Test: `D:/alpha/tests/test_2b_history_and_resample.py`

**Interfaces:**
- Consumes: `core.handle.BmacHandle`（`effective_kline_count_1h`, `history_trim_ts`, `base_5m_retention_days`）, `core.storage.{candle_manager,ts_manager}`
- Produces:
  - `core.pipeline.resample.fill_gaps(df, interval_delta)`（还原 `int_dtypes`，如 `trade_num` 保持整数）
  - `core.pipeline.resample.update_resample_candle(df_resample, run_time, offset_str, candle_mgr, symbol, num_candles=None, *, min_ts=None)`（`min_ts=None` 时不裁头；`min_ts` 非 None 时按 `candle_begin_time >= min_ts` 固定裁剪）
  - `core.pipeline.resample.update_resample_from_base(handle, trade_type, run_time) -> dict`（内部使用 `ThreadPoolExecutor(max_workers=2)`，0 `spawn` 进程）
  - `core.pipeline.resample.resample_history(handle, trade_type, run_time, full_backfill_symbols=None) -> None`（常规快路径增量合并并丢弃截断窗口首根不完整 1h bar；自愈路径写满缺失 offset 后将 `full_backfill_symbols` 的 `base_5m` 分区裁回 `base_5m_retention_days`）
  - `core.history_data.candle.detect_full_backfill_symbols(handle, trade_type, symbols, run_time) -> set[str]`（严格只包含条件 1：任一启用 offset 缺/空/坏；或条件 2：任一启用 offset 最新 bar 早于 `run_time - timedelta(days=base_5m_retention_days - 2)`）
  - `core.history_data.history.run_download_all_history(handle) -> tuple[datetime, dict[str, set[str]]]`（或通过返回值/状态同步传递 `full_backfill_symbols` 给 `resample_history`，同时兼容仅解包 `run_time` 的旧调用方：单独导出 `run_download_all_history_with_backfill(handle)` 并让 `run_download_all_history(handle)` 返回 `run_time` 保持旧签名 100% 兼容！）

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_history_and_resample.py`**

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pandas as pd
import pytest


def test_fill_gaps_restores_integer_trade_num_dtype():
    from core.pipeline.resample import fill_gaps
    times = pd.to_datetime(["2026-11-23 00:00", "2026-11-23 00:10"], utc=True)
    df = pd.DataFrame({
        "candle_begin_time": times, "open": [1.0, 2.0], "high": [1.0, 2.0],
        "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [10.0, 20.0],
        "quote_volume": [10.0, 40.0], "trade_num": pd.Series([5, 8], dtype="int64"),
        "taker_buy_base_asset_volume": [3.0, 4.0], "taker_buy_quote_asset_volume": [3.0, 8.0],
    })
    out = fill_gaps(df, timedelta(minutes=5))
    assert len(out) == 3
    assert pd.api.types.is_integer_dtype(out["trade_num"].dtype)
    assert out["trade_num"].iloc[1] == 0


def test_detect_full_backfill_only_flags_missing_or_stale_never_existing_shorter_history(tmp_path):
    """alphatest (~2025-04-16) must take fast path with zero implicit head backfill."""
    from core.history_data.candle import detect_full_backfill_symbols
    from core.storage.candle_manager import CandleFileManager

    res_dir = tmp_path / "25m"
    res_dir.mkdir()
    mgr = CandleFileManager(res_dir, "pickle")
    run_time = datetime(2026, 11, 23, 1, 25, tzinfo=timezone.utc)
    # Existing alphatest-style coin: starts 2025-04-16, fresh up to run_time - 1h -> NOT flagged
    fresh_df = pd.DataFrame({"candle_begin_time": pd.to_datetime(["2025-04-16 00:25", "2026-11-23 00:25"], utc=True), "close": [1.0, 2.0]})
    mgr.save_data_file("BTCUSDT", fresh_df)
    # Stale > 28d -> flagged (Condition 2)
    stale_df = pd.DataFrame({"candle_begin_time": pd.to_datetime(["2025-04-16 00:25", "2026-10-20 00:25"], utc=True), "close": [1.0, 2.0]})
    mgr.save_data_file("STALEUSDT", stale_df)
    # Missing NEWOFFSET -> flagged (Condition 1)
    h = SimpleNamespace(
        enabled_hour_offsets=("25m",), base_5m_retention_days=30,
        get_market_handle=lambda _: SimpleNamespace(resample_mgr_map={"25m": mgr}),
    )
    flagged = detect_full_backfill_symbols(h, "spot", ["BTCUSDT", "STALEUSDT", "MISSINGUSDT"], run_time)
    assert flagged == {"STALEUSDT", "MISSINGUSDT"}


def test_resample_history_fast_path_preserves_old_head_and_drops_incomplete_first_bar(tmp_path):
    from core.pipeline.resample import resample_history
    from core.storage.candle_manager import CandleFileManager
    from core.storage.ts_manager import TSManager

    run_time = datetime(2026, 11, 23, 2, 25, tzinfo=timezone.utc)
    # Existing 1h history back to 2025-03-08
    res_dir = tmp_path / "spot_1h" / "25m"
    res_dir.mkdir(parents=True)
    res_mgr = CandleFileManager(res_dir, "pickle")
    old_1h_times = pd.to_datetime(["2025-03-08 00:25", "2026-11-22 23:25", "2026-11-23 00:25"], utc=True)
    old_1h = pd.DataFrame({
        "candle_begin_time": old_1h_times, "open": [100.0, 200.0, 300.0], "high": [100.0, 200.0, 300.0],
        "low": [100.0, 200.0, 300.0], "close": [100.0, 200.0, 300.0], "volume": [1.0, 1.0, 1.0],
        "quote_volume": [1.0, 1.0, 1.0], "trade_num": [1, 1, 1],
        "taker_buy_base_asset_volume": [1.0, 1.0, 1.0], "taker_buy_quote_asset_volume": [1.0, 1.0, 1.0],
    })
    res_mgr.save_data_file("BTCUSDT", old_1h)

    # Base 5m starts mid-hour at 2026-11-23 00:40 (incomplete 00:25 bar!) through 02:20
    base_dir = tmp_path / "spot_5m"
    ts_mgr = TSManager(base_dir / "BTCUSDT")
    base_times = pd.date_range("2026-11-23 00:40", "2026-11-23 02:20", freq="5min", tz="UTC")
    ts_mgr.update(pd.DataFrame({
        "candle_begin_time": base_times, "open": 999.0, "high": 999.0, "low": 999.0, "close": 999.0,
        "volume": 2.0, "quote_volume": 2.0, "trade_num": 2,
        "taker_buy_base_asset_volume": 1.0, "taker_buy_quote_asset_volume": 1.0,
    }))

    exg_dir = tmp_path / "exg"
    exg_dir.mkdir()
    exg_mgr = CandleFileManager(exg_dir, "pickle")
    exg_mgr.save_data_file("exginfo_spot", pd.DataFrame({"symbol": ["BTCUSDT"]}))

    h = SimpleNamespace(
        base_interval="5m", resample_interval="1h", enabled_hour_offsets=("25m",),
        base_5m_retention_days=30, history_trim_ts=lambda: None,
        effective_kline_count_1h=lambda tt, rt: 15000,
        exg_mgr=exg_mgr,
        get_market_handle=lambda _: SimpleNamespace(base_candle_dir=base_dir, resample_mgr_map={"25m": res_mgr}),
    )
    resample_history(h, "spot", run_time, full_backfill_symbols=set())
    out = res_mgr.read_candle("BTCUSDT")
    # 2025-03-08 head preserved; 00:25 untouched (still close=300.0, NOT overwritten by partial 999.0); 01:25 appended
    assert out["candle_begin_time"].iloc[0] == pd.Timestamp("2025-03-08 00:25", tz="UTC")
    assert out.loc[out["candle_begin_time"] == pd.Timestamp("2026-11-23 00:25", tz="UTC"), "close"].iloc[0] == 300.0
    assert pd.Timestamp("2026-11-23 01:25", tz="UTC") in set(out["candle_begin_time"])


def test_update_resample_from_base_uses_thread_pool_not_process_pool(monkeypatch, tmp_path):
    from core.pipeline import resample as res_mod
    from core.storage.candle_manager import CandleFileManager

    def forbid_process_pool(*a, **kw):
        raise AssertionError("ProcessPoolExecutor forbidden in update_resample_from_base (Mine 3)")
    monkeypatch.setattr(res_mod, "ProcessPoolExecutor", forbid_process_pool, raising=False)

    exg_dir = tmp_path / "exg"
    exg_dir.mkdir()
    exg_mgr = CandleFileManager(exg_dir, "pickle")
    exg_mgr.save_data_file("exginfo_spot", pd.DataFrame({"symbol": []}))
    h = SimpleNamespace(
        base_interval="5m", resample_interval="1h", enabled_hour_offsets=("25m",),
        history_trim_ts=lambda: None, exg_mgr=exg_mgr,
        get_market_handle=lambda _: SimpleNamespace(base_candle_dir=tmp_path / "s5m", resample_mgr_map={"25m": CandleFileManager(tmp_path / "s1h", "pickle")}, once_update_candles=99),
    )
    assert res_mod.update_resample_from_base(h, "spot", datetime(2026, 11, 23, 1, 25, tzinfo=timezone.utc)) == {}


def test_resample_history_self_heal_builds_missing_offset_and_trims_base_5m_to_30d(tmp_path):
    from core.pipeline.resample import resample_history
    from core.storage.candle_manager import CandleFileManager
    from core.storage.ts_manager import TSManager

    run_time = datetime(2026, 11, 23, 2, 25, tzinfo=timezone.utc)
    base_dir = tmp_path / "spot_5m"
    ts_mgr = TSManager(base_dir / "BTCUSDT")
    # Temporary deep 5m partitions (2025-04 + 2026-11) fetched for self-heal
    t_old = pd.date_range("2025-04-01 00:00", periods=24, freq="5min", tz="UTC")
    t_new = pd.date_range("2026-11-22 23:00", "2026-11-23 02:20", freq="5min", tz="UTC")
    all_t = t_old.append(t_new)
    ts_mgr.update(pd.DataFrame({
        "candle_begin_time": all_t, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
        "volume": 1.0, "quote_volume": 10.0, "trade_num": 1,
        "taker_buy_base_asset_volume": 0.5, "taker_buy_quote_asset_volume": 5.0,
    }))
    res_dir = tmp_path / "spot_1h" / "25m"
    res_dir.mkdir(parents=True)
    res_mgr = CandleFileManager(res_dir, "pickle")
    exg_dir = tmp_path / "exg"
    exg_dir.mkdir()
    exg_mgr = CandleFileManager(exg_dir, "pickle")
    exg_mgr.save_data_file("exginfo_spot", pd.DataFrame({"symbol": ["BTCUSDT"]}))

    h = SimpleNamespace(
        base_interval="5m", resample_interval="1h", enabled_hour_offsets=("25m",),
        base_5m_retention_days=30, base_5m_retention_bars=lambda: 30 * 24 * 12,
        history_trim_ts=lambda: None, effective_kline_count_1h=lambda tt, rt: 15000,
        exg_mgr=exg_mgr,
        get_market_handle=lambda _: SimpleNamespace(base_candle_dir=base_dir, resample_mgr_map={"25m": res_mgr}),
    )
    resample_history(h, "spot", run_time, full_backfill_symbols={"BTCUSDT"})
    out_1h = res_mgr.read_candle("BTCUSDT")
    assert out_1h is not None and out_1h["candle_begin_time"].min() <= pd.Timestamp("2025-04-01 01:25", tz="UTC")
    # Temporary old 5m partition must be trimmed back to 30d after resample completes
    assert "2025-04" not in ts_mgr.list_partitions()
    assert "2026-11" in ts_mgr.list_partitions()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_history_and_resample.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL。

- [ ] **Step 3: 实现 `core/pipeline/resample.py`、`core/resample_candle.py` 与 `core/history_data/{candle,history,utils}.py`**

- `core/pipeline/resample.py`：
  - 基于 `v2.0.2/core/pipeline/resample.py` 并合并现行 `D:/alpha/core/resample_candle.py` 全部强化：`fill_gaps` 补回 `int_dtypes` 还原；`update_resample_candle` 增加 `min_ts=None`（默认不裁头，`min_ts` 非 None 时按 `candle_begin_time >= min_ts` 过滤，若仅传旧参数 `num_candles` 且未传 `min_ts` 保持向后兼容既有 `test_module1_*` 单元测试）；
  - `update_resample_from_base` 改为 `ThreadPoolExecutor(max_workers=2)` 零 `spawn` 子进程，并传 `min_ts=handle.history_trim_ts()`；
  - `resample_history(handle, trade_type, run_time, full_backfill_symbols=None)`：对非自愈 symbol 且目标 offset 已有非空 `resample_1h` 时，丢弃截断窗口首根不完整 1h bar 并走 `update_resample_candle(..., min_ts=trim_ts)` 增量合并；对 `full_backfill_symbols` 中缺盘/断档 offset 全量重采样写入，完成后立刻将这批 symbol 的 `base_5m` `TSManager` 月分区裁回 `base_5m_retention_days`（30 天）。
- `core/history_data/candle.py` & `history.py`：
  - 新增 `detect_full_backfill_symbols(handle, trade_type, symbols, run_time)`；
  - 在 `download_all_candles` 中为常规 symbol 设定 30 天 `base_5m` 目标起点（同时清理超出 30 天的旧月分区），仅对 `full_backfill_symbols` 按 `handle.effective_kline_count_1h(trade_type, run_time)` 下满 5m 历史；保留 `asyncio.Semaphore(50)`、30s 节流 INFO 日志、`return_exceptions=True` 与 429 冷却；
  - `run_download_all_history_with_backfill(handle)` 返回 `(run_time, {"spot": spot_backfill, "swap": swap_backfill})`；`run_download_all_history(handle)` 包装其返回 `run_time` 兼容旧签名。

- [ ] **Step 4: 运行新旧回归测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_history_and_resample.py D:/alpha/tests/test_module1_fill_gaps_dtype.py D:/alpha/tests/test_module1_update_resample_candle_incremental.py D:/alpha/tests/test_module2_update_candle_incremental.py D:/alpha/tests/test_new_listing_1112.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add core/pipeline/resample.py core/resample_candle.py core/history_data/ tests/test_2b_history_and_resample.py
git -C D:/alpha commit -m "feat(2b-1): decouple 5m/1h storage with self-heal, fix fill_gaps int dtype, remove 8-spawn resample"
```

---

### Task 5: 资金费双路径、`CoinCap`、`DataAPI`、`backfill` 与 `data_update` 完整平移

**Files:**
- Modify: `D:/alpha/core/exchange/rest/binance_fetcher.py`, `D:/alpha/core/history_data/funding.py`
- Create: `D:/alpha/core/strategy/{__init__,funding_backfill,funding_hours,funding_publication}.py`, `D:/alpha/core/ingest/{__init__,coin_cap,coin_cap_schema,quantclass,data_api_update}.py`, `D:/alpha/core/pipeline/{data_update,backfill}.py`
- Modify (薄转发): `D:/alpha/core/{funding_backfill,funding_hours,funding_publication,coin_cap,coin_cap_schema,quantclass_async,data_api_update,binance_update,binance_fetcher_async,binance_market_async,backfill}.py`
- Test: `D:/alpha/tests/test_2b_funding_coincap_backfill.py`

**Interfaces:**
- Consumes: `core.handle.BmacHandle`（`effective_kline_count_1h`, `history_trim_ts`, `base_5m_retention_bars`, `coin_cap_use_proxy`）, `core.pipeline.resample`
- Produces:
  - `core.exchange.rest.binance_fetcher.BinanceFetcher` 保留 `get_realtime_funding_rate` 校验与 `get_hist_funding_rate(symbol, *, _reserve_request=None, **kwargs)` 原始 ms 时间戳与参数校验
  - `core.history_data.funding`：Binance 路径保留 `FUNDING_MAX_CONCURRENCY = 4` + 滚动 `450/300s` 限流 + `settled_funding_hours` + `save_funding_hours(..., min_ts=...)`
  - `core.pipeline.data_update.update_meta(handle, run_time)`（Binance 走并发 `funding_task` + 8s 预算 `backfill_recent_funding_rates` + `save_funding_updates`；OKX 保留 `v2.0.2` 逻辑）与 `update_binance_base_candles`（全旧月分区 30d 裁剪）
  - `core.ingest.coin_cap`（`COIN_CAP_DTYPES` + `coin_cap_use_proxy` 隔离）与 `core.ingest.data_api_update`（固定 `min_ts` 保留）
  - `core.pipeline.backfill.maybe_backfill(handle)`（适配 `get_market_handle` 与增量合并）

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_funding_coincap_backfill.py`**

```python
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest


def test_save_funding_hours_preserves_full_history_when_min_ts_none_and_trims_when_set(tmp_path):
    from core.storage.candle_manager import CandleFileManager
    from core.strategy.funding_hours import save_funding_hours

    mgr = CandleFileManager(tmp_path / "funding", "pickle")
    mgr.base_dir.mkdir()
    old = pd.DataFrame({
        "candle_begin_time": pd.to_datetime(["2025-03-08 00:00", "2026-11-22 00:00"], utc=True),
        "funding_rate": [0.001, 0.002],
    })
    mgr.save_data_file("BTCUSDT", old)
    new = pd.DataFrame({
        "candle_begin_time": pd.to_datetime(["2026-11-23 00:00"], utc=True),
        "funding_rate": [0.003],
    })
    # min_ts=None -> no head trim
    save_funding_hours(mgr, "BTCUSDT", new, min_ts=None)
    assert len(mgr.read_candle("BTCUSDT")) == 3
    # Explicit min_ts -> fixed timestamp cut
    save_funding_hours(mgr, "BTCUSDT", new, min_ts=pd.Timestamp("2026-11-22 00:00", tz="UTC"))
    out = mgr.read_candle("BTCUSDT")
    assert len(out) == 2 and out["candle_begin_time"].iloc[0] == pd.Timestamp("2026-11-22 00:00", tz="UTC")


def test_update_binance_base_candles_trims_all_partitions_beyond_30d(tmp_path):
    from core.pipeline.data_update import update_binance_base_candles
    from core.storage.ts_manager import TSManager

    base_dir = tmp_path / "spot_5m"
    ts_mgr = TSManager(base_dir / "BTCUSDT")
    ts_mgr.update(pd.DataFrame({
        "candle_begin_time": pd.to_datetime(["2025-04-01 00:00", "2025-05-01 00:00", "2026-11-20 00:00"], utc=True),
        "open": 1.0, "close": 1.0,
    }))
    h = SimpleNamespace(
        base_interval="5m", base_5m_retention_bars=lambda: 30 * 24 * 12,
        get_market_handle=lambda tt: SimpleNamespace(base_candle_dir=base_dir if tt == "spot" else tmp_path / "swap_5m"),
    )
    run_time = datetime(2026, 11, 23, 0, 25, tzinfo=timezone.utc)
    spot_dfs = {"BTCUSDT": pd.DataFrame({"candle_begin_time": pd.to_datetime(["2026-11-23 00:20"], utc=True), "open": 2.0, "close": 2.0})}
    update_binance_base_candles(h, run_time, spot_dfs, {})
    parts = ts_mgr.list_partitions()
    assert "2025-04" not in parts and "2025-05" not in parts and "2026-11" in parts
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_funding_coincap_backfill.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL。

- [ ] **Step 3: 迁入并整合资金费、`CoinCap`、`DataAPI`、`data_update` 与 `backfill`**

- `core/strategy/{funding_backfill,funding_hours,funding_publication}.py`：迁入现行本地实现，并在 `save_funding_hours` 支持 `min_ts=None`（传 `min_ts` 时按 `>= min_ts` 固定裁剪；未传 `min_ts` 且 `depth=None` 时不裁头）；
- `core/exchange/rest/binance_fetcher.py` & `core/history_data/funding.py`：保留 `FUNDING_MAX_CONCURRENCY = 4`、`450/300s` 限流、`_reserve_request` 与返回校验；首次全量资金费起点改用 `handle.effective_kline_count_1h("swap", run_time)`；
- `core/pipeline/data_update.py`：整合 `v2.0.2` 的 `update_meta` + `fetch_update_binance_candles(..., symbols_subset=None)` 与现行 Binance 并发 `funding_task` + 8s 预算 `backfill_recent_funding_rates` + 全分区 30d 裁剪的 `update_binance_base_candles`；
- `core/ingest/{coin_cap,coin_cap_schema,quantclass,data_api_update}.py`：补回 `COIN_CAP_DTYPES`、`coin_cap_use_proxy` 隔离，以及 `data_api_update` 固定 `min_ts` 保留；
- `core/pipeline/backfill.py`：适配 `handle.get_market_handle`、`min_ts` 增量合并与截断首根丢弃；
- 顶层对应旧模块均改为薄转发层。

- [ ] **Step 4: 运行全部资金费/CoinCap/元数据回归测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_funding_coincap_backfill.py D:/alpha/tests/test_funding_acquisition.py D:/alpha/tests/test_funding_publication.py D:/alpha/tests/test_coin_cap_proxy.py D:/alpha/tests/test_coin_cap_types_1112.py D:/alpha/tests/test_exchange_info_reuse.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add core/ tests/test_2b_funding_coincap_backfill.py
git -C D:/alpha commit -m "feat(2b-2): port funding rate-limit/backfill, coin_cap proxy/types, data_api, and backfill"
```

---

### Task 6: 预处理全定制平移、Arrow `history_trim_date` 透传与暗雷 5 峰值治理（`4.54 GiB → < 2.5 GiB`）

**Files:**
- Create: `D:/alpha/core/strategy/{preprocess,factor_calc,arrow_publish,arrow_demand,candle_origins}.py`
- Modify (薄转发): `D:/alpha/core/{preprocess,factor_calc,arrow_publish,arrow_demand,candle_origins}.py`
- Test: `D:/alpha/tests/test_2b_preprocess_peak_and_trim.py`

**Interfaces:**
- Consumes: `factors/c_ext/*`, `core.strategy.{funding_publication,funding_hours}`, `core.handle.BmacHandle`
- Produces:
  - `publish_arrow_snapshot(market_dict, arrow_path, publish_kline_num=5000, *, write_ready=True, history_trim_date: Optional[str] = None) -> Path`（非 None 时在 schema metadata 写入 `b'history_trim_date': history_trim_date.encode('utf-8')`）
  - `preprocess_offset_data(handle, run_time, spot_begin_ts=None, swap_begin_ts=None)`：严格顺序执行 `spot` → 释放 → `swap`（含 `gc.collect()` 与 Linux `malloc_trim(0)`）
  - `preprocess_type_data(...)`：发布 Arrow 后先提取 `origins` 并立即 `del market_dict` 再建 Pivot，最后写 `.ready` 与 `remember_ready_origins`；`gen_batch_data_incremental` 按 `min_ts=handle.history_trim_ts()` 固定下限保留；`gen_pivot` 与 `gen_pivot_incremental` 双路径均 100% append-only 保列合并（已下架币历史列永不丢）

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_preprocess_peak_and_trim.py`**

```python
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pytest


def test_arrow_publish_writes_and_omits_history_trim_date_metadata(tmp_path):
    from core.strategy.arrow_publish import publish_arrow_snapshot

    df = pd.DataFrame({"candle_begin_time": pd.to_datetime(["2026-11-23 00:25"], utc=True), "close": [10.0]})
    p_none = tmp_path / "spot_dict_1.arrow"
    publish_arrow_snapshot({"BTCUSDT": df}, p_none, publish_kline_num=5000, history_trim_date=None)
    with pa.memory_map(str(p_none), "r") as src:
        meta = pa.ipc.open_file(src).schema.metadata or {}
        assert b"history_trim_date" not in meta

    p_set = tmp_path / "spot_dict_2.arrow"
    publish_arrow_snapshot({"BTCUSDT": df}, p_set, publish_kline_num=5000, history_trim_date="2025-06-01")
    with pa.memory_map(str(p_set), "r") as src:
        meta = pa.ipc.open_file(src).schema.metadata or {}
        assert meta.get(b"history_trim_date") == b"2025-06-01"


def test_preprocess_offset_data_runs_spot_then_swap_sequentially(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from core.strategy import preprocess as pp

    order = []
    def fake_type_data(handle, run_time, trade_type, *a, **kw):
        order.append(("start", trade_type))
        order.append(("end", trade_type))

    monkeypatch.setattr(pp, "preprocess_type_data", fake_type_data)
    monkeypatch.setattr(pp, "load_factor_modules_params", lambda *a, **kw: [])
    exg_mgr = SimpleNamespace(read_candle=lambda _: pd.DataFrame({"spot": [], "swap": []}))
    h = SimpleNamespace(
        exg_mgr=exg_mgr, precompute_factor_configs={}, precompute_factor_workers=1,
        preprocess_handle=SimpleNamespace(preprocess_mgr_map={"25m": SimpleNamespace(base_dir=tmp_path)}),
        publish_kline_num=5000, arrow_snapshot_keep=1,
        arrow_demand_enabled=False, arrow_demand_min_rows=5000,
    )
    pp.preprocess_offset_data(h, pd.Timestamp("2026-11-23 01:25", tz="UTC"))
    assert order == [("start", "spot"), ("end", "spot"), ("start", "swap"), ("end", "swap")]


def test_gen_pivot_fallback_preserves_delisted_columns_on_existing_year_file(tmp_path):
    from core.storage.candle_manager import CandleFileManager
    from core.strategy.preprocess import gen_pivot

    mgr = CandleFileManager(tmp_path / "25m", "pickle")
    mgr.base_dir.mkdir()
    t1 = pd.Timestamp("2026-11-22 23:25", tz="UTC")
    t2 = pd.Timestamp("2026-11-23 00:25", tz="UTC")
    old_pivot = {
        "open": pd.DataFrame({"BTCUSDT": [100.0], "ANTHROPICUSDT": [50.0]}, index=[t1]),
        "close": pd.DataFrame({"BTCUSDT": [101.0], "ANTHROPICUSDT": [51.0]}, index=[t1]),
        "vwap1m": pd.DataFrame({"BTCUSDT": [100.5], "ANTHROPICUSDT": [50.5]}, index=[t1]),
    }
    mgr.save_data_file("market_pivot_spot_2026", old_pivot)

    new_market = {
        "BTCUSDT": pd.DataFrame({
            "candle_begin_time": [t1, t2], "open": [100.0, 102.0], "close": [101.0, 103.0],
            "volume": [10.0, 10.0], "quote_volume": [1005.0, 1025.0],
        })
    }
    gen_pivot(new_market, "spot", t2 + pd.Timedelta(hours=1), mgr)
    out = mgr.read_candle("market_pivot_spot_2026")
    assert "ANTHROPICUSDT" in out["close"].columns
    assert out["close"].loc[t1, "ANTHROPICUSDT"] == 51.0
    assert pd.isna(out["close"].loc[t2, "ANTHROPICUSDT"])


def test_gen_batch_data_incremental_keeps_full_history_when_trim_ts_none(tmp_path):
    """When history_trim_ts() is None, batch PKL must NOT slide-trim older rows even beyond kline_count_1h."""
    from core.storage.candle_manager import CandleFileManager
    from core.strategy.preprocess import gen_batch_data_incremental
    from types import SimpleNamespace

    times = pd.date_range("2025-03-08 00:25", periods=20, freq="h", tz="UTC")
    df_1h = pd.DataFrame({
        "candle_begin_time": times, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "volume": 1.0, "quote_volume": 1.0, "trade_num": 1,
        "taker_buy_base_asset_volume": 0.5, "taker_buy_quote_asset_volume": 0.5,
    })
    res_dir = tmp_path / "spot_1h" / "25m"
    pre_dir = tmp_path / "pre" / "25m"
    exg_dir = tmp_path / "exg"
    for d in (res_dir, pre_dir, exg_dir):
        d.mkdir(parents=True)
    res_mgr = CandleFileManager(res_dir, "pickle")
    res_mgr.set_candle("BTCUSDT", times[-1] + pd.Timedelta(hours=1), df_1h)
    pre_mgr = CandleFileManager(pre_dir, "pickle")
    exg_mgr = CandleFileManager(exg_dir, "pickle")
    exg_mgr.save_data_file("exginfo_spot", pd.DataFrame({"symbol": ["BTCUSDT"]}))
    match = pd.DataFrame([{"spot": "BTCUSDT", "swap": "BTCUSDT", "first_begin_time": times[0], "last_begin_time": times[-1]}])
    h = SimpleNamespace(
        kline_count_1h=5, kline_count_1h_spot=5, kline_count_1h_swap=5,
        history_trim_ts=lambda: None, fetch_funding_rate=False, funding_mgr=None,
        data_api=None, exg_mgr=exg_mgr,
        get_market_handle=lambda _: SimpleNamespace(resample_mgr_map={"25m": res_mgr}),
        get_binance_handle=lambda _: SimpleNamespace(resample_mgr_map={"25m": res_mgr}),
        preprocess_handle=SimpleNamespace(num_batch=10, preprocess_mgr_map={"25m": pre_mgr}),
    )
    market_dict, *_ = gen_batch_data_incremental(h, "spot", times[-1] + pd.Timedelta(hours=1), match, {}, {})
    # Must keep all 20 rows (NOT truncated to kline_count_1h=5)
    assert len(market_dict["BTCUSDT"]) == 20
    assert market_dict["BTCUSDT"]["candle_begin_time"].iloc[0] == times[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_preprocess_peak_and_trim.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL。

- [ ] **Step 3: 迁入 `core/strategy/{preprocess,factor_calc,arrow_publish,arrow_demand,candle_origins}.py` 并落地峰值治理与保列兜底**

- 完整迁入现行 `D:/alpha/core/{preprocess,factor_calc,arrow_publish,arrow_demand,candle_origins}.py` 至 `core/strategy/`，顶层保留薄转发；
- `arrow_publish.py`：支持可选参数 `history_trim_date: Optional[str] = None` 写入 schema metadata `b'history_trim_date'`；
- `preprocess.py`：
  - `gen_batch_data_incremental` 采用固定时间戳下限 `min_ts = handle.history_trim_ts()`（替代滑动 `.tail(kline_count)`，确保 `history_trim_date=None` 时不裁头）；
  - `preprocess_offset_data` 改为顺序执行 `spot` → `_release_memory()` → `swap`（`_release_memory()` 调 `gc.collect()` + Linux `ctypes.CDLL("libc.so.6").malloc_trim(0)`，非 Linux/异常静默忽略）；
  - `preprocess_type_data` 调完 `publish_arrow_snapshot_for_type(..., history_trim_date=getattr(handle, "history_trim_date", None))` 后立刻提取轻量 `origins` 并 `del market_dict`（在 `gen_pivot_incremental` 前释放全历史因子字典），再写 Pivot，最后写 `.ready` 与 `remember_ready_origins`；
  - `gen_pivot` 全量路径在落盘前检查目标年份旧 Pivot 文件是否存在，若存在则对每个字段保留旧表中已下架币种历史列（新时间戳补 `NaN`、`funding_rate` 补 `0.0`），确保增量与全量双路径均 100% append-only 保列。

- [ ] **Step 4: 运行全部预处理/因子/Arrow/重启回归测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_preprocess_peak_and_trim.py D:/alpha/tests/test_module3_gen_batch_data_incremental.py D:/alpha/tests/test_module4_gen_pivot_incremental.py D:/alpha/tests/test_module5_gen_factor_data.py D:/alpha/tests/test_module6_momentum_factor.py D:/alpha/tests/test_module7_gen_factor_parallel.py D:/alpha/tests/test_module8_arrow_publish.py D:/alpha/tests/test_module9_embedded_factors.py D:/alpha/tests/test_module10_preprocess_pipeline.py D:/alpha/tests/test_arrow_single_snapshot.py D:/alpha/tests/test_restart_candle_origin.py D:/alpha/tests/test_pivot_year_completion_1112.py D:/alpha/tests/test_price_revision_publication.py D:/alpha/tests/test_shared_factor_pool.py D:/alpha/tests/test_corrected_momentum.py D:/alpha/tests/test_illq_nonfinite_oracle.py D:/alpha/tests/test_module_cci.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: 全部 PASS。

```bash
git -C D:/alpha add core/ tests/test_2b_preprocess_peak_and_trim.py
git -C D:/alpha commit -m "feat(2b-2): port preprocess/Arrow/C-factors, add history_trim_date metadata, cut peak RAM via sequential spot->swap"
```

---

### Task 7: Barrier 提前退出、非阻塞 WS `update_cycle`、`core/app.py`、`realtime_data.py` 与 `deploy_alpha.sh`

**Files:**
- Create: `D:/alpha/core/pipeline/{barrier,update_cycle,publication_sources}.py`, `D:/alpha/core/app.py`
- Modify: `D:/alpha/core/publication_sources.py`（薄转发）, `D:/alpha/realtime_data.py`, `D:/alpha/tools/deploy_alpha.sh`
- Update existing scheduler tests if module target moved: `D:/alpha/tests/{test_realtime_recovery,test_dispatch_prewarm,test_publication_source_coverage}.py`
- Test: `D:/alpha/tests/test_2b_ws_cycle_and_app.py`

**Interfaces:**
- Consumes: Task 1–6 全部生产端模块
- Produces:
  - `wait_for_ready_barrier(symbols, base_5m_dir, run_time, timeout_sec=45.0, poll_interval_sec=0.1, ws_wait_sec=10.0, rest_done=None, rest_submitted=None, rest_confirmed_empty=None, hard_timeout_sec=180.0) -> tuple[set[str], set[str]]`
  - `choose_publication_inputs(handle, run_time, binance_result, data_api_result, *, binance_done, data_api_done, confirmed_empty=None)`
  - `run_ws_cycle(handle, run_time, supervisor) -> CycleResult`（非阻塞发布拓扑：Binance 100% 覆盖后不等待 `task_data_api`/`task_coin_cap` 立即启动 `_run_preprocess`）
  - `run_rest_cycle(handle, run_time, cfg=None) -> CycleResult`（保留预热池、`_fetch_wake_time`、非阻塞 `choose_publication_inputs` 与 `update_binance_base_candles`）
  - `core.app.{_fetch_wake_time, init_history, update_all, run_realtime}`（非启用 offset 跑 `update_meta` + `update_symbol_match` + `supervisor` 检查 + `maybe_backfill`；`init_history` 含 `apply_explicit_history_trim`；`run_realtime` 保留 `initialized` 恢复守卫 + `defer_funding_backfill()`）

- [ ] **Step 1: 编写失败测试 `D:/alpha/tests/test_2b_ws_cycle_and_app.py`**

```python
import threading, time
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest


def test_barrier_exits_early_after_rest_done_for_unopened_listing_without_race(tmp_path):
    """Review Focus #1: unopened new listing exits immediately after rest_done (no 45s wait),
    while a symbol whose .ready lands right before rest_done.set() is NEVER falsely skipped."""
    from core.pipeline.barrier import wait_for_ready_barrier
    from core.storage.candle_manager import CandleFileManager

    mgr = CandleFileManager(tmp_path, "pickle")
    run_time = datetime(2026, 11, 23, 1, 25, tzinfo=timezone.utc)
    mgr.save_ws_ready("BTCUSDT", run_time)
    rest_done = threading.Event()
    submitted = set()
    confirmed_empty = set()

    def rest_worker():
        time.sleep(0.08)
        # Simulate REST recovering LATEUSDT and confirming NEWLISTING empty right before setting rest_done
        mgr.save_ready("LATEUSDT", run_time)
        confirmed_empty.add("NEWLISTING")
        rest_done.set()

    threading.Thread(target=rest_worker, daemon=True).start()
    t0 = time.monotonic()
    success, skipped = wait_for_ready_barrier(
        symbols=["BTCUSDT", "LATEUSDT", "NEWLISTING"],
        base_5m_dir=tmp_path, run_time=run_time,
        timeout_sec=10.0, poll_interval_sec=0.02, ws_wait_sec=0.05,
        rest_done=rest_done, rest_submitted=submitted, rest_confirmed_empty=confirmed_empty,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0  # Did NOT wait 10s soft timeout
    assert success == {"BTCUSDT", "LATEUSDT"}
    assert skipped == {"NEWLISTING"}


def test_ws_cycle_launches_preprocess_without_blocking_on_slow_data_api(monkeypatch, tmp_path):
    from core.pipeline import update_cycle as uc
    events = []
    api_gate = threading.Event()
    stamp = pd.Timestamp("2026-11-23 01:25", tz="UTC")
    bounds = (stamp - pd.Timedelta(hours=2), stamp - pd.Timedelta(hours=1))

    class FakeAuxPool:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def submit(self, fn, *args):
            class F:
                def result(self):
                    if fn is uc.update_data_api_candles:
                        events.append("api_result_waited")
                        api_gate.wait(timeout=2)
                        return ({"BTCUSDT": bounds}, {"BTCUSDT": bounds})
                    events.append("coin_cap_waited")
                    return True
            return F()

    monkeypatch.setattr(uc, "ProcessPoolExecutor", FakeAuxPool)
    monkeypatch.setattr(uc, "_start_rest_fallback", lambda *a: (SimpleNamespace(join=lambda **kw: None, is_alive=lambda: False),
                        {"ok": True, "done": threading.Event(), "submitted": {"spot": set(), "swap": set()},
                         "confirmed_empty": {"spot": set(), "swap": set()}, "failed": {"spot": set(), "swap": set()}}))
    monkeypatch.setattr(uc, "_barrier_and_resample", lambda h, tt, rt, rs: {
        "trade_type": tt, "success": 1, "total": 1, "skipped": [], "begin_ts": {"BTCUSDT": bounds},
    })
    def fake_preprocess(h, rt, sp, sw):
        events.append("preprocess_started")
        api_gate.set()
    monkeypatch.setattr(uc, "_run_preprocess", fake_preprocess)

    exg_mgr = SimpleNamespace(read_candle=lambda _: pd.DataFrame({"symbol": ["BTCUSDT"]}))
    res_mgr = SimpleNamespace(has_symbol=lambda s: True)
    h = SimpleNamespace(
        resample_interval="1h", data_api=object(), coin_cap_handle=object(), preprocess_handle=object(),
        exg_mgr=exg_mgr, get_market_handle=lambda _: SimpleNamespace(resample_mgr_map={"25m": res_mgr}),
        get_binance_handle=lambda _: SimpleNamespace(resample_mgr_map={"25m": res_mgr}),
    )
    res = uc.run_ws_cycle(h, stamp.to_pydatetime(), SimpleNamespace(router=None))
    assert res.is_binance_success is True
    assert events.index("preprocess_started") < events.index("api_result_waited")


def test_app_non_enabled_offset_runs_meta_supervisor_and_backfill_without_60s_sleep(monkeypatch):
    from core import app
    calls = []
    async def fake_meta(h, rt): calls.append("meta")
    monkeypatch.setattr(app, "sleep_until_run_time", lambda t: calls.append(("sleep_until", t)))
    monkeypatch.setattr(app, "update_meta", fake_meta)
    monkeypatch.setattr(app, "update_symbol_match", lambda h, rt: calls.append("match"))
    monkeypatch.setattr(app, "maybe_backfill", lambda h: calls.append("backfill"))
    monkeypatch.setattr(app.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError(f"unexpected sleep {s}")))
    sup = SimpleNamespace(
        refresh_ws_subscriptions=lambda h: calls.append("refresh_ws"),
        health_check=lambda alert: calls.append("health"),
    )
    h = SimpleNamespace(enabled_hour_offsets=("25m",), base_interval="5m")
    cfg = SimpleNamespace(error_webhook_url="")
    app.update_all(h, datetime(2026, 11, 23, 1, 10, tzinfo=timezone.utc), cfg, supervisor=sup)
    assert "meta" in calls and "match" in calls and "refresh_ws" in calls and "health" in calls and "backfill" in calls
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_2b_ws_cycle_and_app.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: FAIL。

- [ ] **Step 3: 实现 `barrier.py`、`publication_sources.py`、`update_cycle.py`、`core/app.py`、`realtime_data.py` 与 `deploy_alpha.sh`**

- `core/pipeline/barrier.py`：实现 `rest_confirmed_empty` 参数与竞态安全的提前退出——在每次 `while` 循环顶部（检查 `pending` 之前）记录 `rest_done_was_set = rest_done is not None and rest_done.is_set()`；检查 `pending` 完成后，若 `ws_grace_passed and rest_done_was_set and not (pending & set(rest_submitted or ()))`，立即 `break`。
- `core/pipeline/publication_sources.py`：支持可选 `confirmed_empty: Optional[dict[str, set[str]]]`（WS 模式下仅 `sym in confirmed_empty[tt]` 且本地 `resample_mgr` 无非空历史时允许视作未开盘新币跳过；REST 失败绝不误跳过）。
- `core/pipeline/update_cycle.py`：
  - `_rest_fallback_worker` 记录 `state["submitted"]`、`state["confirmed_empty"]`（仅 `fetch_update_binance_candles` 返回该币为空 DataFrame）、`state["failed"]`；
  - `run_ws_cycle` 保持非阻塞拓扑：`aux_exe` 异步起 `task_data_api`/`task_coin_cap`，`br_exe` 跑完 `spot`/`swap` `barrier + resample` 后立即调 `choose_publication_inputs(..., binance_done=True, data_api_done=handle.data_api is None, confirmed_empty=ws_confirmed_empty)`；若已覆盖则**立刻**在 `_run_preprocess` 前带固定 `publication_sources` 跑完预处理，然后再收 `task_coin_cap` 和 `task_data_api`；若未覆盖且有 `task_data_api` 才等 `task_data_api` 并二次裁决；
  - `run_rest_cycle` 保留既有预热进程池、`_fetch_wake_time`、`as_completed` 非阻塞发布与 `update_binance_base_candles`。
- `core/app.py` 与 `realtime_data.py`：
  - `update_all` 在非启用 offset 跑 `sleep_until_run_time(run_time - 15s)` → `update_meta` → `update_symbol_match` → `supervisor.refresh_ws_subscriptions` / `health_check` → `sleep_until_run_time(run_time)` → `maybe_backfill`；
  - `init_history` 顺序：`init_dirs` → `apply_explicit_history_trim(handle, handle.history_trim_ts())` →（可选 `snapshot_restore`）→ `run_download_all_history_with_backfill` → `resample_history(..., full_backfill_symbols=...)` → `copy_data_api_candles`；
  - `run_realtime` 保留 `initialized = False` 守卫（首次初始化失败 60s 重试直到成功，成功后调 `defer_funding_backfill()`；WS 模式起 supervisor + `run_startup_catchup`；运行期异常绝不重跑 `init_history` 且告警含 `'保留已有产物'`）；
  - `realtime_data.py` 作为 Binance 正式入口封装 `core.app`，并同步更新 `test_realtime_recovery.py`、`test_dispatch_prewarm.py`、`test_publication_source_coverage.py`（确保全部既有测试精确覆盖新模块路径）与 `tools/deploy_alpha.sh::verify()`（纳入全部 `test_2b_*.py`）。

- [ ] **Step 4: 运行 `D:/alpha` 全量离线测试套件并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_*.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b`
Expected: `D:/alpha/tests/` 全部测试 100% PASS（0 failure）。

```bash
git -C D:/alpha add core/ realtime_data.py tools/deploy_alpha.sh tests/
git -C D:/alpha commit -m "feat(2b-2): wire non-blocking WS cycle, race-safe barrier early exit, and resilient core.app scheduler"
```

---

### Task 8: `D:/alphaonline` — Pivot `removed_columns` 零持仓分级治理与单市场熔断

**Files:**
- Branch: `D:/alphaonline` 创建 `feat/phase5-2b-consumer-hardening`（从本地 `main=2001075`，保留工作区已有未提交文档改动）
- Modify: `D:/alphaonline/tools/robust40/handoff.py:100-209`
- Test: `D:/alphaonline/tests/test_2b_pivot_removed_columns.py`

**Interfaces:**
- Consumes: `tools.robust40.handoff.{UpstreamUniverseDropError, REMOVED_RAW_MIN_LIMIT, REMOVED_RAW_RATIO_LIMIT}`, `tools.robust40.offline.canonical`, `tools.robust40.telemetry`
- Produces:
  - `handoff.first_revision(...)` 扩展支持 `pivot/{spot|swap}/*` 的 `removed_columns` 两级治理：单市场并集移除列超过 `max(removed_raw_min_limit, int(removed_raw_ratio_limit * old_pivot_count))` 抛 `UpstreamUniverseDropError` 并记录 `circuit_breaker='removed_pivot_columns_surge'`；阈值内零持仓保持 incremental（不追加 `a.index.min()`），持仓（按 `(kind, canonical(col))` 匹配）记录 `removed_held_symbols` 并保守追加 `a.index.min()`。

- [ ] **Step 1: 在 `D:/alphaonline` 创建隔离分支（保留现有未提交文档）**

Run: `git -C D:/alphaonline checkout -b feat/phase5-2b-consumer-hardening main`
Expected: 切换到 `feat/phase5-2b-consumer-hardening`，现有未提交文档改动完整保留在工作区且不被提交污染。

- [ ] **Step 2: 编写失败测试 `D:/alphaonline/tests/test_2b_pivot_removed_columns.py`**

```python
import json
import pandas as pd
import pytest
from tools.robust40 import handoff, telemetry


def _capture(monkeypatch):
    msgs = []
    monkeypatch.setattr(telemetry, "_write", msgs.append)
    return msgs


def _event(msgs):
    return json.loads(msgs[-1].split("MODEL_REPLAY ", 1)[1])


def test_pivot_removed_unheld_column_stays_incremental(monkeypatch):
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=168, freq="h")
    old = {
        "pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0, "ANTHROPICUSDT": 50.0}, index=idx),
        "pivot/spot/open": pd.DataFrame({"BTCUSDT": 100.0, "ANTHROPICUSDT": 50.0}, index=idx),
    }
    new = {
        "pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0}, index=idx),
        "pivot/spot/open": pd.DataFrame({"BTCUSDT": 100.0}, index=idx),
    }
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols={("spot", "BTCUSDT")})
    assert rev is None
    ev = _event(msgs)
    assert ev["removed_columns"] == 2
    assert "removed_held_symbols" not in ev and "circuit_breaker" not in ev


def test_pivot_removed_held_column_canonical_alias_triggers_conservative_revision(monkeypatch):
    """Review Focus #5: alias 'BTC-USDT' vs 'BTCUSDT' must match held set and trigger revision."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=168, freq="h")
    old = {"pivot/swap/close": pd.DataFrame({"BTC-USDT": 100.0, "ETHUSDT": 20.0}, index=idx)}
    new = {"pivot/swap/close": pd.DataFrame({"ETHUSDT": 20.0}, index=idx)}
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols={("swap", "BTCUSDT")})
    assert rev == idx[0]
    assert "swap:BTCUSDT" in _event(msgs)["removed_held_symbols"]


def test_pivot_removed_columns_surge_trips_circuit_breaker_even_without_close_table(monkeypatch):
    """Review Focus #5: missing pivot/spot/close still computes safe non-zero denominator and trips breaker."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    cols = [f"C{i}USDT" for i in range(40)]
    old = {"pivot/spot/open": pd.DataFrame({c: 1.0 for c in cols}, index=idx)}
    new = {"pivot/spot/open": pd.DataFrame({"C0USDT": 1.0}, index=idx)}  # 39 removed > limit 30
    with telemetry.round_log("25m-test", 1795137900):
        with pytest.raises(handoff.UpstreamUniverseDropError, match="pivot"):
            handoff.first_revision(old, new, held_symbols=set())
    assert _event(msgs)["circuit_breaker"] == "removed_pivot_columns_surge"


def test_non_pivot_removed_column_stays_conservative():
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    old = {"raw/spot/BTCUSDT": pd.DataFrame({"open": 1.0, "close": 1.0}, index=idx)}
    new = {"raw/spot/BTCUSDT": pd.DataFrame({"close": 1.0}, index=idx)}
    assert handoff.first_revision(old, new, held_symbols=set()) == idx[0]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/test_2b_pivot_removed_columns.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: FAIL（`pivot/*` 移除列仍无条件返回 `idx[0]` 且无熔断）。

- [ ] **Step 4: 修改 `D:/alphaonline/tools/robust40/handoff.py`**

- 在 `first_revision()` 顶部 `raw/*` 熔断之后、逐表比对之前，遍历 `old.keys() & new.keys()` 中以 `pivot/spot/`、`pivot/swap/` 开头的键，收集各市场并集移除列 `removed_pivot_spot` / `removed_pivot_swap`（用 `canonical` 规范化去重）；分母优先取 `len(old['pivot/{kind}/close'].columns)`，若该表缺失则取 `max((len(old[k].columns) for k in old if k.startswith(f'pivot/{kind}/')), default=0)`；超限记录 `circuit_breaker='removed_pivot_columns_surge'` 并抛 `UpstreamUniverseDropError`。
- 当存在 `removed_raw_*` 或 `removed_pivot_*` 时懒加载 `resolved_held`（并将 `resolved_held` 成员规范化为 `(kind, canonical(sym))` 集合）。
- 在 `if len(removed_columns):` 分支中：若 `key.startswith(('pivot/spot/', 'pivot/swap/'))`，检查 `removed_columns` 中是否有任一 `(kind, canonical(c))` 在 `resolved_held`（若 `resolved_held is None` 保守视为持仓），仅在命中持仓时记录 `removed_held_symbols` 并追加 `a.index.min()`；非 `pivot/` 键维持原保守逻辑。

- [ ] **Step 5: 运行新旧治理测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/test_2b_pivot_removed_columns.py D:/alphaonline/tests/test_removed_series_governance.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: 全部 PASS。

```bash
git -C D:/alphaonline add tools/robust40/handoff.py tests/test_2b_pivot_removed_columns.py
git -C D:/alphaonline commit -m "feat(2b): extend zero-holding relaxation and surge circuit breaker to pivot removed_columns"
```

---

### Task 9: `D:/alphaonline` — 滚动 S3 初始化起点（零账户改动、零 `ModelRobust40Strategy.py` 改动）

**Files:**
- Modify: `D:/alphaonline/tools/robust40/server.py`
- Modify: `D:/alphaonline/tools/robust40/cli/server_robust40.py:29-50, 171-225`
- Test: `D:/alphaonline/tests/test_2b_rolling_s3_origin.py`

**Interfaces:**
- Consumes: `tools.robust40.initialization_config.INITIALIZATION`, `tools.robust40.server.{configured_s3, cutoff}`
- Produces:
  - `server.resolve_initialization_s3_origin(configured_origin: pd.Timestamp, earliest_bar: pd.Timestamp, n: int, min_kline_num: int, end_bar: pd.Timestamp, *, min_model_days: int = 365) -> pd.Timestamp`
  - `server.scan_arrow_earliest_bar(arrow_path: Path, minimum_history_bars: int) -> pd.Timestamp`（纯 PyArrow 列式读取 `candle_begin_time` 首值，兼容 `s`/`ms`/`us`/`ns` 与 naive/UTC）
  - `server_robust40.apply_profile(config, origin, snapshot, initializing)`：单调前向校验（`cutoff(origin) >= configured` 且对齐 `00:{minute}:00` UTC 日网格；早于 `configured` 或错位分钟抛含 `'configured date'` 的 `ValueError`）

- [ ] **Step 1: 编写失败测试 `D:/alphaonline/tests/test_2b_rolling_s3_origin.py`**

```python
from types import SimpleNamespace
import pandas as pd
import pyarrow as pa
import pytest
from tools.robust40 import server
from tools.robust40.cli.server_robust40 import apply_profile


def test_resolve_initialization_s3_origin_keeps_configured_when_history_sufficient():
    configured = pd.Timestamp("2025-05-20 00:25:00")
    earliest = pd.Timestamp("2025-03-08 00:25:00")
    end_bar = pd.Timestamp("2026-11-23 00:25:00")
    # 2025-03-08 + 1200h (min_kline_num=480, n=720) = 2025-04-27 < 2025-05-20 -> stays 2025-05-20
    assert server.resolve_initialization_s3_origin(configured, earliest, 720, 480, end_bar) == configured


def test_resolve_initialization_s3_origin_advances_to_utc_minute_grid_and_enforces_365d():
    configured = pd.Timestamp("2025-05-20 00:25:00")
    earliest = pd.Timestamp("2025-06-01 05:25:00")
    end_bar = pd.Timestamp("2027-01-01 00:25:00")
    # 2025-06-01 05:25 + 1200h = 2025-07-21 05:25 -> ceil to 00:25 grid = 2025-07-22 00:25
    resolved = server.resolve_initialization_s3_origin(configured, earliest, 720, 480, end_bar)
    assert resolved == pd.Timestamp("2025-07-22 00:25:00")
    # Less than 365 days remaining -> ValueError
    with pytest.raises(ValueError, match="365"):
        server.resolve_initialization_s3_origin(configured, earliest, 720, 480, pd.Timestamp("2026-06-01 00:25:00"))


def test_scan_arrow_earliest_bar_handles_ms_and_ns_units_without_pandas(tmp_path):
    """Review Focus #2: PyArrow-only scan works across ms and ns UTC timestamps and skips short listings."""
    arrow_path = tmp_path / "swap_dict_1.arrow"
    # Batch 0: 10 rows (qualifies), ms precision; Batch 1: 3 rows (too short, ignored even though earlier)
    t_long = pa.array(pd.date_range("2025-06-01 00:25", periods=10, freq="h", tz="UTC"), type=pa.timestamp("ms", tz="UTC"))
    t_short = pa.array(pd.date_range("2024-01-01 00:25", periods=3, freq="h", tz="UTC"), type=pa.timestamp("ms", tz="UTC"))
    schema = pa.schema([("candle_begin_time", pa.timestamp("ms", tz="UTC"))], metadata={b"symbols": b'["BTC","NEW"]'})
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(pa.RecordBatch.from_arrays([t_long], schema=schema))
            w.write_batch(pa.RecordBatch.from_arrays([t_short], schema=schema))
    assert server.scan_arrow_earliest_bar(arrow_path, minimum_history_bars=5) == pd.Timestamp("2025-06-01 00:25:00")


def test_apply_profile_accepts_forward_grid_origin_and_rejects_earlier_or_misaligned(tmp_path):
    pool = [{"filter_list": [("VolumeMaxBias_进出场阈值分离官方", (720, 0.1, 0.9, "2025-05-20"), "val:==1")]}]
    def make_cfg():
        return SimpleNamespace(strategy_pool=pool, incremental_lookback_hours=0, data_config={},
                               account_config={"hour_offset": "25m"}, strategy_config={"params": {"runtime": {}}})
    # Forward on 00:25 grid -> accepted and injected into strategy_pool
    cfg = make_cfg()
    apply_profile(cfg, "2025-07-22 00:25:00", tmp_path, True)
    assert cfg.strategy_pool[0]["filter_list"][0][1][3] == "20250722T002500"
    # Earlier than configured -> rejected
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-04-26 00:25:00", tmp_path, True)
    # Wrong hour or minute -> rejected
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-07-22 01:25:00", tmp_path, True)
    with pytest.raises(ValueError, match="configured date"):
        apply_profile(make_cfg(), "2025-07-22 00:40:00", tmp_path, True)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/test_2b_rolling_s3_origin.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: FAIL（`resolve_initialization_s3_origin`、`scan_arrow_earliest_bar` 尚未定义；`apply_profile` 仍按严格等式校验）。

- [ ] **Step 3: 实现 `resolve_initialization_s3_origin`、`scan_arrow_earliest_bar` 并接入 `_initialize` 与 `apply_profile`**

- 在 `tools/robust40/server.py` 新增 `resolve_initialization_s3_origin` 与 `scan_arrow_earliest_bar`（用 `pa.memory_map` + `pa.ipc.open_file`，逐 batch 检查 `batch.num_rows >= minimum_history_bars` 并取 `batch.column('candle_begin_time')[0].as_py()` 经 `cutoff()` 归一化，零 pandas 表分配；若无达标币抛 `ValueError('Full raw history is not ready for initialization')`）。
- 在 `tools/robust40/cli/server_robust40.py`：
  - `apply_profile()` 改为校验 `parsed = cutoff(origin)` 非 NaT、`parsed >= configured`、`parsed.hour == 0 and parsed.minute == minute and parsed.second == 0 and parsed.microsecond == 0 and parsed.nanosecond == 0`（不满足则抛含 `'configured date'` 的 `ValueError`）；
  - `_initialize()` 在 `freeze_inputs(..., full_arrow=True)` 后，从 `workspace / 'frozen_market' / f'{minute}m' / f'swap_dict_{stamp}.arrow'` 调用 `scan_arrow_earliest_bar` + `resolve_initialization_s3_origin(configured_origin, earliest_bar, n, int(values.get('min_kline_num') or 0), end_bar)` 解析实际 `origin`（若推进于 `configured_origin` 打印明确中文日志说明原因），再传入 `ROBUST40_SERVER_WORKER` 载荷。

- [ ] **Step 4: 运行新旧测试验证通过并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/test_2b_rolling_s3_origin.py D:/alphaonline/tests/test_server_manual_origin_freeze.py D:/alphaonline/tests/test_low_memory_initialization.py D:/alphaonline/tests/test_model_config_semantics.py D:/alphaonline/tests/test_server_dispatch.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: 全部 PASS。

```bash
git -C D:/alphaonline add tools/robust40/server.py tools/robust40/cli/server_robust40.py tests/test_2b_rolling_s3_origin.py
git -C D:/alphaonline commit -m "feat(2b): add rolling S3 initialization origin with 365d guard and monotonic profile validation"
```

---

### Task 10: `D:/alphaonline` — `history_trim_date` Arrow Header 预检与退出码 `75` 自动联动

**Files:**
- Modify: `D:/alphaonline/core/utils/arrow_reader.py`
- Modify: `D:/alphaonline/tools/robust40/server.py:65-75, 239-265`
- Modify: `D:/alphaonline/tools/robust40/cli/server_robust40.py:171-225, 263-366`
- Test: `D:/alphaonline/tests/test_2b_history_trim_handoff.py`

**Interfaces:**
- Consumes: `core.utils.arrow_reader.arrow_snapshot_path`, `tools.robust40.replay.REBUILD_REQUIRED_EXIT` (`75`), `tools.robust40.server.{begin, complete, finish_live, cutoff}`
- Produces:
  - `arrow_reader.read_arrow_history_trim_date(arrow_path: Path) -> Optional[str]`
  - `server.complete(path, account, values, end, origin, *, history_trim_date: Optional[str] = None)`（默认向后兼容，写入 `warmup.pkl`；`finish_live` 原样保留 `meta.get('history_trim_date')`）
  - `server_robust40.check_upstream_history_trim_date(bmac_data_path, minute, stamp, saved_trim) -> tuple[bool, Optional[str], Optional[str]]`（供 `_trade()` 在 `handoff.prepare()` 前调用：校验 `spot`/`swap` Arrow header 一致性，不一致抛 `ValueError`；仅当 `upstream_trim` 非 None 且严格晚于 `saved_trim` 时触发重建返回 `True`）

- [ ] **Step 1: 编写失败测试 `D:/alphaonline/tests/test_2b_history_trim_handoff.py`**

```python
import pandas as pd
import pyarrow as pa
import pytest
from core.utils.arrow_reader import read_arrow_history_trim_date
from tools.robust40 import server
from tools.robust40.cli import server_robust40 as cli


def _write_arrow(path, trim_date=None):
    meta = {b"symbols": b'["BTC"]', b"full_history": b"1"}
    if trim_date is not None:
        meta[b"history_trim_date"] = trim_date.encode("utf-8")
    schema = pa.schema([("candle_begin_time", pa.timestamp("ns", tz="UTC"))], metadata=meta)
    batch = pa.RecordBatch.from_arrays([pa.array(pd.to_datetime(["2026-11-23 00:25"], utc=True))], schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as w:
            w.write_batch(batch)


def test_read_arrow_history_trim_date_missing_and_present(tmp_path):
    p1 = tmp_path / "a.arrow"
    p2 = tmp_path / "b.arrow"
    _write_arrow(p1, None)
    _write_arrow(p2, "2025-06-01")
    assert read_arrow_history_trim_date(p1) is None
    assert read_arrow_history_trim_date(p2) == "2025-06-01"


def test_check_upstream_trim_date_handles_none_advance_backward_and_mismatch(tmp_path):
    """Review Focus #4: mismatch raises ValueError; backward date does not rebuild; advance triggers rebuild."""
    stamp = int(pd.Timestamp("2026-11-23 01:25", tz="UTC").timestamp())
    folder = tmp_path / "25m"
    # Both None -> no rebuild (Day-1 upgrade)
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", None)
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", None)
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None) == (False, None, None)

    # Spot/Swap mismatch -> ValueError (skip round, never corrupt snapshot)
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", "2025-06-01")
    with pytest.raises(ValueError, match="history_trim_date"):
        cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None)

    # Both advanced None -> '2025-06-01' -> rebuild required
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", "2025-06-01")
    need, old_t, new_t = cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim=None)
    assert (need, old_t, new_t) == (True, None, "2025-06-01")

    # Equal -> no rebuild
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim="2025-06-01")[0] is False

    # Backward ('2025-05-01' < '2025-06-01') -> no rebuild
    _write_arrow(folder / f"spot_dict_{stamp}.arrow", "2025-05-01")
    _write_arrow(folder / f"swap_dict_{stamp}.arrow", "2025-05-01")
    assert cli.check_upstream_history_trim_date(tmp_path, 25, stamp, saved_trim="2025-06-01")[0] is False


def test_server_complete_persists_history_trim_date(tmp_path):
    snap = tmp_path / "snap"
    (snap / "sections").mkdir(parents=True)
    (snap / "sections" / "s3.pkl").write_bytes(b"ok")
    server.complete(snap, "acct", {}, pd.Timestamp("2026-11-23 00:25"), "2025-05-20 00:25:00", history_trim_date="2025-06-01")
    meta = pd.read_pickle(snap / "warmup.pkl")
    assert meta["history_trim_date"] == "2025-06-01"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/test_2b_history_trim_handoff.py -v -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: FAIL。

- [ ] **Step 3: 实现 `read_arrow_history_trim_date`、`server.complete`/`finish_live` 透传与 `_trade`/`_initialize`/`compute` 接线**

- `core/utils/arrow_reader.py`：实现 `read_arrow_history_trim_date(arrow_path)`（mmap 只读 schema metadata `b'history_trim_date'`，空/缺失返回 `None`）。
- `tools/robust40/server.py`：`complete(..., history_trim_date=None)` 写入 `warmup.pkl`；`finish_live()` 调用 `complete(..., history_trim_date=meta.get('history_trim_date'))`。
- `tools/robust40/cli/server_robust40.py`：
  - 新增 `check_upstream_history_trim_date(bmac_data_path, minute, stamp, saved_trim)`：同时读 `spot` 与 `swap` Arrow header（若文件不存在则返回 `(False, saved_trim, None)` 让下游走既有缺失报错），不一致抛 `ValueError`；`upstream_trim is not None and upstream_trim != saved_trim` 且 (`saved_trim is None` or `cutoff(upstream_trim) > cutoff(saved_trim)`) 返回 `(True, saved_trim, upstream_trim)`；
  - `_initialize()` 校验冻结后的 `spot`/`swap` Arrow `history_trim_date` 一致并写入 `ROBUST40_SERVER_WORKER` 载荷，`compute()` 传给 `server.complete()`；
  - `_trade()` 在 `handoff.prepare()` 之前调用 `check_upstream_history_trim_date(config.bmac_data_path, end.minute, stamp, original.get('history_trim_date'))`，若返回 `True` 则调用 `server.begin(destination)`、写遥测 `mode='rebuild_required', status='rebuild_required', recovery_reason=f'upstream history_trim_date changed: {old_t} -> {new_t}'` 并返回 `REBUILD_REQUIRED_EXIT`（`75`）。

- [ ] **Step 4: 运行 `D:/alphaonline` 全量离线回归并提交**

Run: `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/ -q -p no:cacheprovider --basetemp=C:/tmp/pt2b_on`
Expected: 全仓回归 100% PASS（0 failure）。

```bash
git -C D:/alphaonline add core/utils/arrow_reader.py tools/robust40/server.py tools/robust40/cli/server_robust40.py tests/test_2b_history_trim_handoff.py
git -C D:/alphaonline commit -m "feat(2b): detect upstream Arrow history_trim_date and trigger exit-75 rebuild"
```

---

### Task 11: 跨仓离线端到端契约验证与文档同步

**Files:**
- Create: `D:/alpha/tests/test_2b_cross_repo_contract.py`
- Modify: `D:/alphaonline/docs/DEVELOPMENT.md`, `D:/alphaonline/docs/README.md`, `D:/alphaonline/docs/kb/roadmap-and-backlog.md`, `D:/alphaonline/docs/kb/branches-and-sync.md`, `D:/alphaonline/docs/2026-11-23-phase5-and-v202-migration-plan.md`

**Interfaces:**
- Consumes: `D:/alpha` Task 1–7 产物与 `D:/alphaonline` Task 8–10 产物（通过文件系统 Arrow/PKL 边界联调，零运行时跨仓 import 耦合）
- Produces: 端到端离线跨仓契约测试 + 完整更新的知识库与开发日志（标明本地提交号、离线验证结果及待授权测试机 24h 门禁清单）

- [ ] **Step 1: 编写跨仓离线契约测试 `D:/alpha/tests/test_2b_cross_repo_contract.py`**

覆盖两条端到端真实路径（若同级 `../alphaonline` 不存在则 `pytest.skip`）：
1. **重启零重建链路**：生产端发布 Round 1（含 `BTCUSDT` + `ANTHROPICUSDT`）→ `init_dirs()` 模拟重启 → 发布 Round 2（仅 `BTCUSDT`，`history_trim_date=None`）→ 消费端 `check_upstream_history_trim_date` 返回 `False`，`handoff.first_revision` 返回 `None`（0 重建、0 漂移）。
2. **计划内磁盘瘦身链路**：生产端运行 `apply_explicit_history_trim(handle, pd.Timestamp('2025-06-01', tz='UTC'))` 并发布带 `history_trim_date='2025-06-01'` 的 Arrow → 消费端 `check_upstream_history_trim_date(..., saved_trim=None)` 返回 `(True, None, '2025-06-01')`，且 `scan_arrow_earliest_bar` + `resolve_initialization_s3_origin` 正确将 S3 起点推进到 `2025-07-21 00:25:00`（满足 `min_kline_num + n` 且剩余 ≥ 365 天）。

- [ ] **Step 2: 运行双仓全量最终离线验证（验证前必看原始退出码与汇总）**

Run:
```powershell
C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alpha/tests/test_*.py -q -p no:cacheprovider --basetemp=C:/tmp/pt2b_final_a
C:/Users/Administrator/miniconda3/envs/alpha311/python.exe -m pytest D:/alphaonline/tests/ -q -p no:cacheprovider --basetemp=C:/tmp/pt2b_final_b
```
Expected: 两条命令均以退出码 `0` 结束，0 failed。

- [ ] **Step 3: 同步更新全部文档并提交测试**

- 提交 `D:/alpha/tests/test_2b_cross_repo_contract.py` 并将其加入 `D:/alpha/tools/deploy_alpha.sh::verify()`；
- 核对 `D:/alphaonline/positions/ModelRobust40Strategy.py` 与 `D:/alphaonline/accounts/*.py` `git diff` 为空；
- 更新 `D:/alphaonline/docs/DEVELOPMENT.md`、`docs/README.md`、`docs/kb/roadmap-and-backlog.md`、`docs/kb/branches-and-sync.md`、`docs/2026-11-23-phase5-and-v202-migration-plan.md`（保留已有未提交内容，增量记录 2B-1 & 2B-2 离线落地与 2B-3 待授权测试机门禁状态）。
- 注：此时**不**自动部署/重启测试机或正式机，待向用户交付离线验证报告并获得明确授权后再启动阶段 2B-3。
