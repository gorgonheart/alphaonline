# 2.3.0 升级冲突分析：自研特性兼容性

**日期**: 2026-08-12  
**关联文档**: 
- [2.3.0 升级分析](2026-08-12-upgrade-core-2.3.0-analysis.md)
- [2.2.1 升级设计](2026-07-12-upgrade-core-2.2.1-design.md)
- [Arrow 读取设计](2026-06-27-consumer-mmap-arrow-read-design.md)

## 冲突概述

2.2.1 版本是在 vendor 2.1.0 基础上的**外科式合并**，保留了以下自研特性：
1. **Arrow 零拷贝读取**（`utils/arrow_reader.py` + `datatools.py` 快路径）
2. **precompute_factor_configs**（预计算因子配置，避免重复计算）
3. **Polars 向量化截面因子**（`calc_cross_sections` 性能优化）
4. **Statistics 小改**（编码、格式、调用方式调整）

而 2.3.0 官方版本：
1. ✅ **保留** Arrow 快路径（与自研方向一致）
2. ❌ **移除** precompute_factor_configs 支持
3. ✅ **保留** Polars 截面因子（但有内存管理变更）
4. ⚠️ **Statistics 可能被覆盖**（需验证）

## 🔴 致命冲突：precompute_factor_configs

### 问题描述

**2.2.1 自研特性**（保留的功能）:
```python
# model/backtest_config.py
class BacktestConfig:
    def __init__(self, ..., precompute_factor_configs=None):
        self.precompute_factor_configs = precompute_factor_configs or {}

# select_coin.py::calc_factors
precompute_set = {
    fn: set(ps)
    for fn, ps in getattr(conf, 'precompute_factor_configs', {}).items()
}
use_precompute = (
    bool(declared)
    and all(p in declared for p in factor_param_list)
    and all(col in candle_df.columns for col in needed_cols)
)
if use_precompute:
    for col in needed_cols:
        factor_series_dict[col] = candle_df[col].values
else:
    factor_series_dict.update(calc_factor_vals(...))
```

**2.3.0 官方版本**:
- 完全移除 `precompute_factor_configs` 逻辑
- `calc_factors` 简化为直接调用 `calc_factor_vals`
- 所有因子改为实时计算

**影响的账户文件**:
```python
# accounts/40m_dad0604_2.2.1.py（当前生产账户）
precompute_factor_configs = {
    '固定回撤阈值触发币数占比开仓门槛': ['0.01_0.2_5'],
    '动态回撤阈值': ['5'],
}
```

### 冲突原因

**设计冲突**:
- **2.2.1 自研目标**: 利用数据中心发布的预计算因子列，避免重复计算，提升性能
- **2.3.0 官方决策**: 简化因子计算路径，移除预计算支持（可能因为 Arrow 快路径已足够快，或数据中心不再发布预计算列）

**技术债务**:
- 如果数据中心仍然发布预计算因子列（如 `固定回撤阈值触发币数占比开仓门槛_0.01_0.2_5`），2.3.0 会**忽略这些列**，重复计算
- 如果数据中心**不再发布**预计算列，2.2.1 的 `use_precompute` 分支永远 False，等价于已废弃

### 解决方案

#### 方案 A：接受官方决策，移除预计算（推荐）

**前提条件**:
- 验证 Arrow 快路径是否足够快（因子计算是否成为瓶颈）
- 确认数据中心是否仍发布预计算列

**操作**:
1. 从所有账户文件中删除 `precompute_factor_configs`
2. 接受 2.3.0 的简化逻辑
3. 依赖 Arrow 零拷贝和 Polars 向量化提供性能

**优点**:
- 代码路径简化，减少分支复杂度
- 与官方同步，降低长期维护成本
- Arrow 快路径可能已使预计算收益不明显

**缺点**:
- 如果因子计算仍是瓶颈，性能可能退化

#### 方案 B：保留预计算，手动合并（高风险）

**操作**:
1. 不整体替换 `core/`，而是逐文件合并（类似 2.2.1 升级方式）
2. 在 `select_coin.py::calc_factors` 中**手动保留** 2.2.1 的 precompute 逻辑
3. 在 `model/backtest_config.py` 中**手动保留** `precompute_factor_configs` 属性

**优点**:
- 保留性能优化
- 与当前生产配置兼容

**缺点**:
- 与官方版本分叉，后续升级困难
- 需持续维护这个分支（2.4.0、2.5.0...）
- 如果数据中心不再发布预计算列，这个逻辑变死代码

#### 方案 C：灰度测试，数据驱动决策（最稳妥）

**操作**:
1. 在测试账户上整体替换为 2.3.0（移除预计算）
2. 对比性能基准：
   - 单轮回测时间（含因子计算）
   - 内存峰值
   - 选币结果一致性
3. 如果性能退化 < 20%，接受方案 A；否则评估方案 B

**测试方法**:
```powershell
# 2.2.1 基准（有预计算）
Measure-Command { python core/account_exec.py <ts> }

# 2.3.0 测试（无预计算）
Measure-Command { python core/account_exec.py <ts> }

# 对比因子计算阶段的 debug 日志时间戳
```

### 推荐路径

**短期**（本次升级）:
- 采用方案 A（移除预计算）
- 前提：先在小账户灰度验证性能可接受

**理由**:
1. Arrow 零拷贝已带来数量级提升（pkl 反序列化从 ~1min → ~1-2s）
2. Polars 向量化截面因子计算同样大幅优化
3. 预计算的边际收益可能已不明显
4. 官方移除预计算表明他们认为不再需要（可能数据中心已不发布，或 Arrow 路径已覆盖）

**长期**:
- 监控生产性能，如果因子计算成为新瓶颈，考虑：
  - 向数据中心请求恢复预计算列发布
  - 或在消费者侧引入本地因子缓存机制（与 precompute 不同，更通用）

## ⚠️ 潜在冲突：Polars 截面因子内存管理

### 差异

**2.2.1 自研版本**:
```python
# select_coin.py::calc_cross_sections_polars
# 提前 collect 成内存 DataFrame
df_base = lf.collect()
del lf
# chunk 循环内 df_base.lazy() 的根节点是内存
lf_chunk = df_base.lazy()
```

**2.3.0 官方版本**:
```python
# 基础 LazyFrame（只包含 KLINE + 依赖列），用于分片计算
lf_base = lf
# ...
lf_chunk = lf_base  # 保持惰性求值
```

### 差异原因

- **2.2.1 策略**: 提前 collect 一次，避免每个 chunk 重复扫描磁盘（假设 lf 来自磁盘扫描）
- **2.3.0 策略**: 保持 LazyFrame 惰性求值，每个 chunk collect 时独立执行，分段释放内存

### 兼容性分析

**好消息**: 这个变更**不冲突**，因为：
- 2.2.1 的 `df_base.lazy()` 和 2.3.0 的 `lf_base` 都是 LazyFrame
- 下游 chunk 计算逻辑完全相同
- 只是内存管理策略不同

**性能影响**:
- 如果 `lf` 来自内存（如 Arrow 快路径的 `to_pandas()` → Polars），2.2.1 和 2.3.0 性能相当
- 如果 `lf` 来自 Parquet 扫描，2.3.0 可能**稍慢**（重复扫描）但**内存峰值更低**

**结论**: 接受 2.3.0 的版本，这是内存优化，不会引入功能回归

## ⚠️ 潜在冲突：Statistics 家族

### 2.2.1 自研改动

在 2.2.1 升级时，以 vendor 2.2.1 为基座，叠加了以下小改：
- `label='Equity'`（去掉账户名前缀）
- 读 csv 用 `encoding='gbk'`
- `text_to_signal` 的 match-case 版本
- `strategy_pool[int(idx)]` 索引
- `sub_stg_file if not exists: continue` 守卫
- pnl 报告调用注释掉

### 2.3.0 官方版本

- 修复「最近1h成交额」统计（Binance 接口字段变更）
- 修复 24h 盈亏中币种的策略归属显示

### 冲突可能性

如果 2.3.0 整体替换 `statistics.py` / `statis_func.py` / `statistics_debug.py`，会**丢失** 2.2.1 的小改。

### 解决方案

#### 方案 A：接受 2.3.0，重新应用小改（推荐）

1. 备份 2.2.1 的 statistics 三文件
2. 替换为 2.3.0 版本
3. 逐个重新应用 2.2.1 的小改（用 diff 定位）
4. 测试统计输出格式符合预期

#### 方案 B：不替换 statistics，仅 patch 修复

1. 保留 2.2.1 的 statistics 三文件
2. 手动应用 2.3.0 的两个修复：
   - 最近1h成交额字段名
   - 币种策略归属逻辑
3. 风险：可能遗漏其他隐含变更

**推荐**: 方案 A，工作量小且更安全

## ✅ 兼容的自研特性

### Arrow 零拷贝读取

**2.2.1 设计**:
- `utils/arrow_reader.py`（mmap 零拷贝）
- `datatools.py::load_bmac_data` Arrow 快路径 + pkl fallback
- `read_bmac_market_pivot` 消费者侧重建 pivot

**2.3.0 官方版本**:
- **保留** Arrow 快路径
- 可能有细节优化，但方向一致

**结论**: **无冲突**，2.3.0 的 `core/utils/arrow_reader.py` 和 `datatools.py` 应与 2.2.1 自研版本兼容或更优

### 进出场选币引擎（entry_exit）

**2.2.1 引入**:
- `model/strategy_config.py` 的 entry_exit 引擎
- `utils/ee_continuation.py` 状态持续化

**2.3.0 官方版本**:
- **保留并修复** entry_exit 引擎
- 修复续算缓存脏数据问题
- 修复长持仓周期下的时间单位不对称

**结论**: **无冲突**，2.3.0 的 entry_exit 是 2.2.1 的修复版，应直接升级

## 升级策略总结

### 推荐路径：接受官方 2.3.0，最小化自研分叉

1. **整体替换 core/**（官方推荐）
2. **移除 precompute_factor_configs**（配置文件清理）
3. **重新应用 statistics 小改**（手动 patch）
4. **灰度验证性能**（与 2.2.1 基准对比）

### 风险缓解

- 保留 `core.2.2.1.backup/` 随时回滚
- 在测试账户先验证 3 轮
- 对比选币结果与资金曲线（entry_exit 修复后可能合理差异）
- 监控因子计算时间（如果显著变慢，评估恢复 precompute）

### 不推荐：维护自研 fork

**原因**:
- 2.3.0 是重大架构升级（exchange_hub 分发层）
- 手动合并工作量大，且后续 2.4.0/2.5.0 持续维护成本高
- precompute 的边际收益可能已被 Arrow + Polars 优化抵消

**例外情况**:
- 如果灰度验证发现因子计算时间从 10s → 2min（数量级退化），则需重新评估
- 此时可考虑向邢不行反馈，或在数据中心侧优化因子发布

## 检查清单

升级前必须验证：

- [ ] 数据中心是否仍在 Arrow 文件中发布预计算因子列？
  - 方法：`python -c "import pyarrow as pa; f = pa.memory_map('<arrow文件>'); print(pa.ipc.open_file(f).schema)"`
  - 如果有 `固定回撤阈值触发币数占比开仓门槛_0.01_0.2_5` 等列 → 数据中心仍发布
  - 如果只有 OHLCV + 基础因子 → 数据中心已停止发布预计算列
- [ ] 当前账户配置中使用 precompute 的策略有哪些？
  - `grep -r "precompute_factor_configs" accounts/`
- [ ] 因子计算在当前流程中的耗时占比？
  - 查看 startup.py 日志中的 "完成计算因子" 时间戳
- [ ] Arrow 快路径是否已在生产启用？
  - 查看日志是否有 "📊 截面因子 - 数据加载: X.XXs"（Arrow 路径特征）
- [ ] 2.2.1 的 statistics 小改是否在当前生产关键？
  - 如果统计报告格式被下游系统依赖，需谨慎

## 最终建议

**对于当前 d:/alphaonline 项目**:

✅ **推荐升级到 2.3.0**，因为：
1. Arrow 快路径已在 2.2.1 生产验证，2.3.0 保留并优化
2. entry_exit 修复解决了严重的脏数据问题（如果使用该模式）
3. exchange_hub 架构为未来扩展奠定基础
4. 多项 bug 修复提升稳定性

⚠️ **必须处理 precompute 冲突**:
- 短期：移除配置，接受实时计算
- 灰度验证性能可接受
- 长期监控，如需优化可在数据中心侧或本地缓存层解决

🔧 **升级后需重新应用**:
- Statistics 小改（label、encoding、match-case 等）
- 任何其他未记录的本地定制

📊 **性能基准对比**（升级前后必做）:
- 单轮回测总时间
- 因子计算阶段时间
- 内存峰值
- 选币结果一致性（entry_exit 修复后可能合理差异）
