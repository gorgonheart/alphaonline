# PR-11：行情读取 Arrow IPC `mmap` 零拷贝快路径（自动回退 `pkl`）

- **拟提交标题**：`perf(io): add memory-mapped Arrow IPC fast path for load_bmac_data with automatic pkl fallback`
- **优先级**：P2（**双端协同项**；仅当上游数据中心同步支持 Arrow 快照发布时兑现收益）
- **上游 `alpha` Arrow 依赖**：**🔴 强依赖上游 `alpha` 数据中心发布 `{spot,swap}_dict_{ts}.arrow`**（未发布时自动零侵入回退原 `pkl` 路径）
- **其他 PR 依赖**：无（推荐配合 **PR-04** 向量化对齐一起使用，最大化读入阶段收益）

---

## 1. 场景

`load_bmac_data` 每轮需从数据中心目录加载全市场 1000+ 个现货/合约币种的 K 线字典。原版 `v2.4.1` 通过 `pd.read_pickle` 反序列化多份 `batch*.pkl`，受 Python pickle 单核解码、大对象内存复制和全历史载入拖累。

## 2. 目标

在 `core/utils/arrow_reader.py` 与 `core/utils/datatools.py` 中新增基于 `pyarrow.memory_map` 的 Arrow IPC 零拷贝读取快路径：
- 按文件名内嵌时间戳 `{symbol_type}_dict_{ts}.arrow` 天然校验 `run_time`；
- 支持在转为 pandas 之前按 `max_rows` 对 Arrow `RecordBatch` 做零拷贝 `.slice()` 裁尾，避免把超长历史全部反序列化进内存；
- 文件缺失、manifest 损坏或任何 `ArrowReadError` 时，无缝自动回退到 `v2.4.1` 原有 `batch*.pkl` 路径。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/utils/datatools.py:210-238`：
1. `load_bmac_data` 只能遍历 `rglob(f'{symbol_type}_dict_batch*.pkl')` 执行 `pd.read_pickle`，跨进程无法共享 Linux Page Cache，且即便策略只需要最近 4050~5000 根 K 线，也必须把完整历史（如 14000+ 根）全部反序列化为 pandas DataFrame 后再切片丢弃。
2. 缺乏文件名时间戳硬校验，极端情况下容易读到上一轮残留的 `batch*.pkl`。

## 4. 项目文档中的相关分析与设计

- 消费者零拷贝设计：[2026-06-27 消费者 mmap-Arrow 读取设计](../superpowers/specs/2026-06-27-consumer-mmap-arrow-read-design.md)
- 实施计划：[2026-06-27 消费者 mmap-Arrow 读取计划](../superpowers/plans/2026-06-27-consumer-mmap-arrow-read.md)
- 跨项目数据契约：[开发规范与数据契约](../kb/standards-and-contracts.md)（§5 七条契约）
- 参考提交：`b8a4478`（新增 `core/utils/arrow_reader.py`）、`0943575`（`load_bmac_data` 接入与 fallback）、`9451bdd`（`iter_arrow_market` + `max_rows` 预切片）

## 5. 依赖

- **🔴 上游 `alpha` 数据中心 Arrow 输出契约（强依赖，收益生效前提）**：
  1. 路径：`{bmac_data_path}/{minute}m/{spot|swap}_dict_{int(run_time.timestamp())}.arrow`；
  2. 格式：Apache Arrow IPC File 格式（单文件，每币种对应一个 `RecordBatch`）；
  3. Metadata：`reader.schema.metadata[b'symbols']` 为 UTF-8 JSON 数组，长度与 `num_record_batches` 严格相等；
  4. 发布顺序：上游必须完成原子写盘（`.tmp + os.replace`）后再写 `.ready`；
  5. **重要边界（防坑）**：上游裁尾深度至少须满足 `max(5000, get_kline_num + min_kline_num + 1)`，**绝不可直接裁成精确 `4050` 根**，否则会破坏 `align_spot_swap_mapping` 的映射预热（见 [避坑清单](../kb/rejected-and-failed-attempts.md)）。
- **无上游升级时的行为**：自动判定 `.arrow` 不存在并返回 `None`，回退 `v2.4.1` 原 `pkl` 路径，零破坏。
- **第三方依赖**：需 `pyarrow`（`v2.4.1` 使用 `polars` + `parquet`，环境通常已自带 `pyarrow`；若缺失可延迟 `import pyarrow` 并在 `ImportError` 时自动回退 `pkl`）。

## 6. 收益与证据

| 测量项与口径 | 效果 |
| :--- | :--- |
| `tests/test_arrow_pkl_parity.py`（真实 30m 1066 币种逐币/逐列/逐行对账） | 与 `pkl` 路径值 100% 一致（仅历史 gap 导致的 per-symbol 整数/浮点 dtype 差异，值完全相同） |
| 零拷贝预切片 `max_rows`（5000 vs 14060 全量） | 消费端只把末尾必要窗口转为 pandas，免除全历史 3 倍内存分配与反序列化；线上修订/行情读取由 `~18.3 s` 降至 `5–8 s` |
| `read` 阶段端到端（配合 PR-04 向量化对齐，1066 币） | `25.05 s → 19.08 s`（`-24%`） |

## 7. 代码改动

### 目标文件
- `core/utils/arrow_reader.py`（新增）
- `core/utils/datatools.py`（修改 `load_bmac_data`）

### 核心改动要点
1. `core/utils/arrow_reader.py`：
   - `arrow_snapshot_path(bmac_data_path, symbol_type, run_time)`
   - `iter_arrow_market(arrow_path, max_rows=None, *, require_full=False, required_columns=())`：使用 `pa.memory_map` 打开 IPC File，校验 `b'symbols'` manifest，对每个 batch 先 `.slice(max(0, batch.num_rows - max_rows))` 再 `.to_pandas()`；
   - `read_arrow_market_dict_for_run_time(...)`：缺失或 `ArrowReadError` 返回 `None`。
2. `core/utils/datatools.py::load_bmac_data`：
   - 计算 `arrow_rows = max(5000, conf.get_kline_num() + conf.min_kline_num + 1)`；
   - 优先尝试 `read_arrow_market_dict_for_run_time`；命中则走快路径，返回 `None` 时无缝走原 `batch*.pkl` 分支。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 风险成因与适配建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 安全 | Page Cache 充裕，多账户错峰共享 Arrow 映射效果极佳。 |
| **2c8g** | 🟢 低风险 | 8G 内存可稳定缓存单轮 ~1.5 GB（5000 行）spot+swap Arrow 快照；建议同时开启上游 `arrow_snapshot_keep=1` 控磁盘。 |
| **2c4g** | 🟡 **中风险（Page Cache 挤出 + 瞬时堆分配）** | 1. 2c4g 可用物理内存仅 `~3.0–3.3 GiB`，而 `read_arrow_market_dict` 会把全市场 `dict[str, DataFrame]` 一次性装入内存，叠加 OS Page Cache 极易逼近上限；<br>2. 在 2c4g 上建议：① 严格控制 `get_kline_num`（如 `1500–4050`）使 `max_rows` 保持紧凑；② 优先消费完即 `del arrow_dict` 并配合 `pa.default_memory_pool().release_unused()` 归还 PyArrow 内存池；③ 若要彻底消除全市场 `dict` 驻留内存，需进一步叠加 **PR-12** 的流式逐币消费。 |
