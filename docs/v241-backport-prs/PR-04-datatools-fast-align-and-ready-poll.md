# PR-04：映射对齐向量化、删冗余 `dropna` 与 `check_flags` 轮询加速

- **拟提交标题**：`perf(datatools): vectorize align_spot_swap_mapping, drop no-op symbol dropna, and speed up ready flag polling`
- **优先级**：P0（强烈推荐首批合入）
- **上游 `alpha` Arrow 依赖**：**无**（注意：`check_flags` 移除就绪后硬等待依赖上游原子写 `.ready` 契约，见 §5）
- **其他 PR 依赖**：**无**

---

## 1. 场景

每轮实盘启动后，`startup.py` 先调用 `check_bmac_update_flag` 等待上游 `.ready` 门控文件，随后 `load_bmac_data` 对全市场 1000+ 个币种逐一调用 `read_and_merge_bmac_data` 与 `align_spot_swap_mapping` 处理重复上下架币种的现货/合约映射对齐。

## 2. 目标

1. 用纯 NumPy 向量化替代 `align_spot_swap_mapping` 中高开销的 `df.groupby('group').cumcount()`；
2. 移除 `read_and_merge_bmac_data` 中对必非空 `symbol` 列的空扫 `dropna`；
3. 缩短 `check_flags` 的轮询间隔并消除 `.ready` 已就绪后的冗余 `time.sleep(3)` 硬等待。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/utils/datatools.py`：
1. `align_spot_swap_mapping` 每币创建 3 个临时列并执行 `df.groupby('group').cumcount()`；实测在 1066 个币种上单 `align` 一项就占逐币 merge 总耗时的 **`46%`（`5.67 s`）**。
2. `df.dropna(subset=['symbol'], inplace=True)` 对上千个币种全表扫描检查 `symbol` 空值（实测全 0 NaN），白耗 **`~1.73 s`**。
3. `check_flags` 轮询间隔为 `1 s`（平均白等 `0.5 s`），且检测到所有 `.ready` 存在后还硬编码 `time.sleep(3)`，每轮白白推迟开算 **`~3.5 s`**。

## 4. 项目文档中的相关分析与设计

- 实测拆解与向量化证明：提交 `63a4842`（merge op 级 profiler 拆解与 200 seed fuzz 等价测试）、`ee62015`（对齐 `v2.4.1` 按 `column_name` 置 `NaN` 语义并去 `sleep(1)`）
- 设计文档：[2026-11-25 v2.4.1 升级与消费端第二批设计](../superpowers/specs/2026-11-25-v241-and-consumer-batch2-design.md)（§5.1 A1、§5.2 B1）
- 回归测试：`tests/test_align_fast_equivalence.py`、`tests/test_upgrade_241.py`

## 5. 依赖

- **内部代码依赖**：无（全部改动集中在 `core/utils/datatools.py`）。
- **上游 `alpha` Arrow 依赖**：**无**（`align_spot_swap_mapping_fast` 对 `pkl` 路径同样 100% 适用，建议在 `2.4.1` 中默认启用或通过 `align_fn` 注入）。
- **⚠️ 上游写盘原子性前置条件（仅针对 `check_flags` 去 `sleep(3)`）**：
  - `alphaonline` 移除 `sleep(3)` 的前提是上游数据中心严格遵守「数据文件 `.tmp + os.replace` 落盘后再写 `.ready`」。
  - 若上游 `v2.4.1` 对应的某些旧版数据中心存在先写 `.ready` 或跨 NFS 同步延迟，建议将 `ready_settle_seconds` 做成可配置参数（默认 `0.0` 或保守 `0.5`，原版为 `3.0`），避免极少数非原子上游读到半写文件。
- **防空安全提醒（仅针对删 `dropna(subset=['symbol'])`）**：仅当确认上游恒用标量填充 `symbol` 时删除；若需兼容未知第三方自定义数据源，可用 O(1) 快速检查 `if df['symbol'].isna().any(): df.dropna(...)` 或仅在 `notna().all()` 时跳过（仍可省绝大部分拷贝开销）。

## 6. 收益与证据

| 测量项（30m，1066 币种实盘日志对拍） | 优化前 | 优化后 | 收益 |
| :--- | ---: | ---: | :--- |
| `align` 阶段（`groupby.cumcount` → NumPy） | `5.67 s` | `~1.60 s` | **省 `~4.07 s`（`-72%`）** |
| 删空扫 `dropna(subset=['symbol'])` | `1.73 s` | `0 s` | **省 `~1.73 s`** |
| `read` 阶段端到端耗时 | `25.05 s` | `19.08 s` | **`-24%`** |
| `check_flags` 等待延迟（`1.0s` 轮询 + `sleep(3)` → `0.1s` 轮询 + `0s`） | 平均 `+3.5 s` | `< 0.1 s` | **每轮提前 `~3.4 s` 启动** |

- **等价验证**：`tests/test_align_fast_equivalence.py` 涵盖 8 组边界用例 + 200 组随机序列 fuzz（含 `v2.4.1` 新语义：空值掩码按 `column_name` 自身判定，不耦合 `symbol_swap`），与原版逐行精确一致。

## 7. 代码改动

### 目标文件
- `core/utils/datatools.py`

### 核心改动要点
1. 新增 `align_spot_swap_mapping_fast(df, column_name, n)`：
   - 用 `col = df[column_name].to_numpy()`、`col_ne = col != ''` 与 `prev_eq` 构造边界掩码；
   - `group = np.cumsum(col_ne & prev_eq)`；
   - 在 `valid = col_ne` 子集上用 `np.maximum.accumulate(np.where(is_start, seq, 0))` O(N) 算出每组内 `cumcount`，将 `< n` 的位置置 `''`；
   - 保留原版 `align_spot_swap_mapping` 作为测试 Oracle。
2. `read_and_merge_bmac_data`：默认使用 `align_fn=align_spot_swap_mapping_fast`；将无条件 `dropna(subset=['symbol'])` 改为先判断或移除。
3. `check_flags(run_time, flag_paths, poll_interval=0.1, settle_seconds=0.0)`：将轮询间隔从 `1.0 s` 降为 `0.1 s`，并将硬编码 `time.sleep(3)` 改为可配置 `settle_seconds`（上游原子发布时为 `0`）。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 直接启用。 |
| **2c8g** | 🟢 无风险 | 纯单线程 CPU/内存降耗，无任何风险。 |
| **2c4g** | 🟢 无风险 | 去掉每币 3 个 pandas 临时列与 `groupby` 哈希表，内存更省；`0.1 s` `Path.exists()` 轮询 CPU 占用可忽略不计（`< 0.1%`）。 |
