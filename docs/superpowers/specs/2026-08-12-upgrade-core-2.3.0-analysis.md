# 2.3.0 版本升级分析

**日期**: 2026-08-12  
**当前版本**: 2.2.1 (build 20260626)  
**目标版本**: 2.3.0 (build 20260717)  
**发布日期**: 2026-07-17  
**分支**: upgrade/core-2.3.0

## 官方发布说明

v2.3.0 的核心变更：

### 新增功能
- ✅ **OKX 交易所支持**：新增 OKX 交易所客户端与 `exchange_hub` 分发层，按账户配置切换交易所（Binance / OKX）
- ✅ **OKX 统一账户模式**：支持统一账户模式（需开启跨币种保证金，要求 5000U 资产）
- ✅ **bStocks 过滤**：动态过滤 Binance bStocks 股票代币，不参与选币交易
- ✅ **容错增强**：增加币种临时不可交易的状态容错，规避极端情况下的中断

### 修复问题
- 🐛 **进出场选币模式**：续算缓存改为每轮用最新数据覆盖窗口内的重叠截面，修复上一小时 K 线未闭合时脏数据被永久冻结的问题
- 🐛 **进出场选币模式**：修复 `hold_period=24H` 等长持仓周期下，兜底下限 N 的时间单位不对称导致的报错
- 🐛 修复选币为空时，子策略资金曲线数据缺失的问题
- 🐛 修复统一账户下「最近1h成交额」统计恒为 0 的问题（bn 更新接口字段）
- 🐛 修复 24h 盈亏中币种的策略归属显示

### 更新说明
- 📦 建议 **整体替换 `core/` 目录**（涉及文件较多）
- 📦 替换 `delist.py`, `monitor.py`
- 📦 新增 `accounts/_OKX统一账户样例.py`（OKX 用户参考）
- 📦 `config.py` 仅新增注释，不要直接覆盖

### 已知限制
- ⚠️ BTC 现货 K 线目录目前需要手动切换（binance → `binance_spot_1h_resample`，okx → `okx_spot_1h_resample`）

## 概述

2.3.0 版本是在 2.2.1 稳定版本基础上的**重大架构升级**，核心是 **多交易所支持** 和 **exchange_hub 分发层**。框架各耦合点（下单、账户、行情、统计等）统一经由 `exchange_hub` 路由，实现交易所无关的业务逻辑。

## 主要变更领域

### 1. 架构重构：exchange_hub 分发层（核心变更）

**新增目录结构**:
```
core/
├── account_manager.py          # 新增：账户管理器（封装 init_system）
├── mef_manager.py              # 新增：MEF 管理器
├── trade.py                    # 新增：交易执行层（从 real_trading.py 拆分）
├── rebalance.py                # 新增：重平衡逻辑
├── simulator.py                # 新增：模拟器（可能从 equity.py 拆分）
├── binance/                    # Binance 交易所客户端
│   ├── base_client.py
│   ├── portfolio_margin_client.py  # 组合保证金账户
│   └── standard_client.py          # 标准账户
├── okx/                        # OKX 交易所客户端
│   ├── base_client.py
│   └── portfolio_margin_client.py  # 统一账户（跨币种保证金）
└── model/
    ├── account_type.py         # 新增：账户类型枚举
    ├── rebalance_mode.py       # 新增：重平衡模式枚举
    └── timing_signal.py        # 新增：择时信号
└── utils/
    └── exchange_hub.py         # 新增：交易所路由中心
```

**exchange_hub 架构**:
- **统一路由层**：所有交易所调用（下单、账户查询、行情、统计）经由 `exchange_hub` 分发
- **透明切换**：业务代码不感知具体交易所，通过账户配置自动路由到 Binance 或 OKX
- **客户端工厂**：`get_dummy_client()` 根据配置返回对应交易所的客户端实例
- **符号标准化**：`normalize_symbol_filter_list()` 处理黑白名单的交易所差异

**账户类型支持**:
- **Binance Standard**: 标准账户（原有）
- **Binance Portfolio Margin**: 组合保证金账户
- **OKX Portfolio Margin**: 统一账户（跨币种保证金模式，要求 5000U 资产）

**影响**:
- 交易所逻辑从 `ccxt` 直接调用解耦为客户端抽象
- 所有耦合点必须通过 `exchange_hub` 访问交易所
- 支持 OKX 统一账户（新增交易所）

### 2. 性能优化：归档频率控制

**real_trading.py 新增功能** (lines 53-62):
```python
# 归档频率控制：非归档轮次将 lookback_days 置零，降低实盘增量运算开销
should_save_archive = (run_time.hour % lookback_save_interval_hours == 0) if lookback_save_interval_hours != 0 else False
if should_save_archive:
    logger.info(f'[{acct_conf.name}] 本轮为归档轮次 (间隔={lookback_save_interval_hours}h)，保留原 lookback_days')
else:
    for conf in me_conf.factory.config_list:
        conf.lookback_days = 0
    logger.info(f'[{acct_conf.name}] 本轮非归档轮次 (间隔={lookback_save_interval_hours}h)，已将 lookback_days 置 0')
```

**新增配置项**:
- `lookback_save_interval_hours`: 控制归档间隔（小时）

**收益**:
- 非归档轮次跳过历史数据加载，减少内存占用和计算时间
- 实盘运行效率提升（每轮只需最新数据）

### 3. 移除预计算因子逻辑

**select_coin.py 变更** (calc_factors 函数):

**移除**:
```python
# 2.2.1 版本
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

**简化为**:
```python
# 2.3.0 版本
factor_series_dict.update(calc_factor_vals(candle_df, factor_name, factor_param_list))
```

**原因分析**:
- 预计算因子功能可能与 Arrow 数据路径冲突
- 简化因子计算逻辑，统一代码路径
- 数据中心不再发布预计算列（或由 Arrow 快速路径替代）

### 3.5. 进出场选币模式：续算缓存修复（重要）

**问题描述**:
- **脏数据冻结问题**：上一小时 K 线未闭合时，脏数据被永久冻结在缓存中，导致选币结果偏离回测
- **时间单位不对称问题**：`hold_period=24H` 等长持仓周期下，兜底下限 N 的时间单位不对称，导致报错 `续算缓存截面数 < 兜底 N`

**修复方案**:
- 续算缓存改为 **每轮用最新数据覆盖窗口内的重叠截面**（与截面因子路径同语义）
- 修复兜底下限 N 的时间单位换算

**影响**:
- 使用 `select_mode='entry_exit'` 的策略必须更新，否则选币结果与回测不一致
- 长持仓周期策略（24H+）可正常运行

### 4. 截面因子计算优化

**select_coin.py calc_cross_sections_polars**:

**变更前** (2.2.1):
```python
# 提前 collect 成内存 DataFrame
df_base = lf.collect()
del lf
# chunk 循环内 df_base.lazy() 的根节点是内存，不再重复扫描磁盘
lf_chunk = df_base.lazy()
```

**变更后** (2.3.0):
```python
# 基础 LazyFrame（只包含 KLINE + 依赖列），用于分片计算
lf_base = lf
# ...
lf_chunk = lf_base
```

**收益**:
- 保持 LazyFrame 惰性求值，减少内存峰值
- 每个 chunk 独立 collect，分段释放内存
- 适配更大规模数据集

### 5. 并行执行策略调整

**backtest.py 变更**:

**移除**:
```python
# 2.2.1: ProcessPoolExecutor 并行执行 step4/5/6
_sorted4 = sorted(conf_list, key=lambda c: len(c.strategy_list), reverse=True)
with ProcessPoolExecutor(max_workers=min(len(conf_list), 4)) as executor:
    futures = {executor.submit(_step4_worker, conf, _multi_process): conf for conf in _sorted4}
    for f in as_completed(futures):
        f.result()
```

**改为**:
```python
# 2.3.0: 串行执行 + mem_save_exe 内存管理
for conf in factory.config_list:
    logger.info(f'{conf.name}的{len(conf.strategy_list)}个子策略选币')
    mem_save_exe(select_coins, conf, multi_process=bool(get_snapshot_time(conf_all.name)))
```

**原因分析**:
- 进程池开销 vs 收益权衡（小规模策略池串行更快）
- `mem_save_exe` 提供进程级内存隔离，效果等价于 PPE
- 避免 Windows multiprocessing pickle 复杂性

### 6. bStocks 黑名单机制（新增功能）

**backtest.py 新增** (line 179-181):
```python
from core.utils.exchange_hub import apply_bstocks_black_list

# bStocks 黑名单显式并入：conf_all 会被 pickle 进子进程，进程内类级缓存不会跟着带过去，
# 提前并入 black_list 才能保证子进程里的数据过滤/选币判断同样生效
apply_bstocks_black_list(conf_all)
```

**功能**:
- **动态过滤 Binance bStocks 股票代币**（如 ACINUSDT、ACMUSDT 等），不参与选币交易
- 统一黑名单管理（退市币、问题币等）
- 保证主进程和子进程黑名单一致性

**bStocks 说明**:
- Binance 股票代币（Stock Tokens）是锚定传统股票的代币产品
- 因监管和流动性问题，框架自动过滤此类币种

### 7. 新增文件和工具

**顶层新增**:
- `delist.py`: **退市币自动监控**（21KB）
  - 通过爬取币安官网公告识别下架币种
  - 监控间隔 60s（设置太短会被 BN 封禁）
  - 自动 TWAP 平仓退市币持仓
  - **仅支持 Binance 账户**（OKX 需手动监控）
- `monitor.py`: **保证金率监控工具**（8KB）
  - 监控合约账户保证金率阈值
  - 触发阈值时自动减仓 10%（可配置）
  - **不适用于统一账户**（Portfolio Margin）
- `_OKX统一账户样例.py`: OKX 账户配置示例

**core/ 新增**:
- `account_manager.py`: 账户管理器（封装 `init_system` / `load_multi_accounts`）
- `mef_manager.py`: MEF (MultiEquityFactory) 管理器（`init_mef_system`）
- `rebalance.py`: 重平衡逻辑（12KB，可能支持多种重平衡模式）
- `simulator.py`: 模拟器（8KB，可能从 equity.py 拆分）
- `trade.py`: 交易执行层（19KB，包含 TWAP 拆单 `split_order_twap` / `split_order_balanced`）
- `utils/exchange_hub.py`: 交易所路由中心

### 8. equity.py 修复：空选币时列合并问题

**问题**: 选币为空时，子策略资金曲线数据缺失

**修复** (create_ratio_snapshot):
```python
# 2.3.0: 合并列取并集并排序，确保子策略无选币时不丢失历史币种列
final_columns = sorted(set(ratio_df_0.columns) | set(ratio_df.columns))
```

**原因**: 
- 当某轮选币为空，`ratio_df` 缺少币种列
- 旧逻辑 `final_columns = ratio_df.columns` 导致历史币种列被丢弃
- 新逻辑取并集保留历史列，避免曲线断裂

### 9. 统计修复

**修复项**:
- 统一账户下「最近1h成交额」统计恒为 0（Binance 更新接口字段导致）
- 24h 盈亏中币种的策略归属显示错误

### 10. 容错增强

**新增容错**:
- 增加币种临时不可交易的状态容错（如交易对维护、暂停交易）
- 规避极端情况下的中断（如网络抖动、交易所 API 限流）

### 配置变更

**新增配置项**:
- `lookback_save_interval_hours`: 归档间隔（默认值待确认）
- `cross_section_chunk_size`: 截面因子分片大小（已存在于 2.2.1，沿用）

**移除配置项**:
- `precompute_factor_configs`: 预计算因子配置（不再支持）

### API 变更

**BacktestConfig**:
- 不再支持 `precompute_factor_configs` 属性

**real_trading.run_by_account**:
- 新增 `lookback_save_interval_hours` 参数处理逻辑

**新增模块**:
- `core.utils.exchange_hub`: 交易所通用工具
- `core.model.account_type`: 账户类型枚举
- `core.model.rebalance_mode`: 重平衡模式枚举
- `core.model.timing_signal`: 择时信号

## 迁移路径（基于官方建议）

### 官方推荐步骤

1. **📋 整体替换 core/ 目录**
   - 备份当前 `core/` → `core.2.2.1.backup/`
   - 复制 `2.3.0core/core/` → `core/`
   - **建议整体替换**（官方说明：涉及文件较多）

2. **📋 替换顶层工具文件**
   - 复制 `2.3.0core/delist.py` → `delist.py`
   - 复制 `2.3.0core/monitor.py` → `monitor.py`

3. **🔧 配置文件调整**
   - 参考 `2.3.0core/_OKX统一账户样例.py`（如需 OKX 支持）
   - 手动合并 `config.py` 的 OKX 注释（**不要直接覆盖**）
   - 在 `config.py` 添加 `lookback_save_interval_hours = 24`（推荐值）
   - 移除账户文件中的 `precompute_factor_configs` 引用

4. **🔄 重启服务**
   - 重启 `startup.py`
   - 重启 `delist.py`（如果运行中）
   - 重启 `monitor.py`（如果运行中）

### 详细迁移步骤

#### Phase 1: 代码替换（低风险）

1. **备份当前版本**
   ```powershell
   git add -A && git commit -m "chore: 2.3.0 升级前备份"
   cp -r core core.2.2.1.backup
   ```

2. **整体替换 core/**
   ```powershell
   rm -rf core/*
   cp -r 2.3.0core/core/* core/
   ```

3. **替换顶层工具**
   ```powershell
   cp 2.3.0core/delist.py .
   cp 2.3.0core/monitor.py .
   cp "2.3.0core/_OKX统一账户样例.py" accounts/
   ```

4. **验证文件完整性**
   ```powershell
   git status
   # 应看到 core/ 大量修改，delist.py / monitor.py 更新
   ```

#### Phase 2: 配置调整（中风险）

5. **更新 config.py**
   - 手动添加 `lookback_save_interval_hours = 24`（或 0 表示每轮归档）
   - 检查是否有 OKX 相关注释需要合并

6. **清理账户文件中的废弃配置**
   ```python
   # 移除这些配置（不再支持）
   # precompute_factor_configs = {...}
   ```

7. **（可选）配置 OKX 账户**
   - 参考 `accounts/_OKX统一账户样例.py`
   - 配置 API key / secret
   - 设置 `account_type = AccountType.PORTFOLIO_MARGIN`
   - 开启 OKX 进阶模式 → 跨币种保证金（要求 5000U 资产）

#### Phase 3: 测试验证（高风险）

8. **运行测试套件**
   ```powershell
   conda run -n alpha311 --no-capture-output python -m pytest tests/ -q
   ```
   - 预期：旧测试应通过（除非测试本身依赖 2.2.1 的预计算）
   - 如有失败，分析是否为破坏性变更导致

9. **灰度验证（推荐）**
   - 选择小资金测试账户
   - 设置 `is_simulate = 'dry_run'` 运行一轮
   - 检查日志无报错，选币结果合理

10. **生产切换**
    - 设置 `is_simulate = 'none'` 启用实盘
    - 监控前 3 轮订单与资金曲线
    - 对比 2.2.1 回测结果验证一致性

#### Phase 4: 可选功能启用

11. **启用 delist.py 退市监控**（Binance 账户）
    ```powershell
    # 后台运行
    python delist.py
    ```

12. **启用 monitor.py 保证金监控**（非统一账户）
    ```powershell
    python monitor.py
    ```

13. **（可选）迁移到 OKX**
    - 配置 OKX 账户（参考样例文件）
    - 手动切换 BTC 现货 K 线目录（待自动化支持）
    - 小资金灰度验证

## 风险点

### 🔴 高风险

1. **core/ 整体替换**: 涉及所有核心逻辑，可能引入未知回归
   - **缓解**: 全面测试，保留 2.2.1 备份随时回滚

2. **进出场续算缓存语义变更**: 选币结果可能与历史不同
   - **缓解**: 对比回测结果，接受修复后的正确行为

3. **移除预计算因子**: 如果数据中心未发布因子列，计算会失败
   - **缓解**: 验证 Arrow 数据路径包含所有需要的因子

### 🟡 中风险

4. **归档频率控制**: `lookback_days=0` 可能导致依赖历史数据的策略失效
   - **缓解**: 审查策略是否需要 lookback > 0，或设置 `lookback_save_interval_hours = 0` 每轮归档

5. **并行执行变串行**: 多策略配置下回测时间可能增加
   - **缓解**: 对比性能基准，评估是否可接受

6. **exchange_hub 路由**: 自定义代码直接调用交易所 API 会失效
   - **缓解**: 搜索代码中的 `ccxt` / `.bn.exchange` 调用，改为 `exchange_hub`

### 🟢 低风险

7. **OKX 支持成熟度**: 新交易所可能有隐藏 bug
   - **缓解**: Binance 用户暂不启用 OKX，等待社区验证

8. **delist.py / monitor.py 稳定性**: 爬虫和监控工具可能受外部 API 变化影响
   - **缓解**: 非核心功能，可选启用

## 已知限制和待办

### 当前限制

1. **BTC 现货 K 线目录需手动切换**
   - Binance: `binance_spot_1h_resample`
   - OKX: `okx_spot_1h_resample`
   - 后续版本将支持自动转换

2. **delist.py 仅支持 Binance**
   - OKX 用户需手动监控官网公告

3. **monitor.py 不适用于统一账户**
   - Portfolio Margin 账户保证金率计算逻辑不同

### 待确认项

1. `lookback_save_interval_hours` 的最佳默认值
   - 0 = 每轮归档（安全，但慢）
   - 24 = 每日归档（推荐）
   - 更大值 = 更少开销，但断电丢失更多数据

2. `simulator.py` 与 `equity.py` 的关系
   - 是否完全替代还是补充？

3. `trade.py` 的职责边界
   - 是否完全替代 `real_trading.py` 的交易逻辑？

4. Arrow 数据路径是否仍然发布因子列
   - 影响预计算移除的可行性

## 回滚策略

如果 2.3.0 升级后出现问题：

```powershell
# 1. 回滚代码
git checkout upgrade/core-2.2.1
# 或手动恢复备份
rm -rf core
cp -r core.2.2.1.backup core

# 2. 恢复配置
git checkout config.py
git checkout accounts/*.py

# 3. 重启服务
python startup.py
```

## 总结

2.3.0 是一次 **重大架构升级**，核心是 **exchange_hub 分发层** 和 **多交易所支持**。主要收益：

✅ **OKX 支持**：扩展交易所选择，降低单点依赖  
✅ **架构解耦**：业务逻辑与交易所实现解耦，易于扩展  
✅ **Bug 修复**：进出场续算缓存、空选币曲线、统计错误等多项修复  
✅ **运维工具**：delist.py / monitor.py 提升实盘稳定性  

**建议**：
- Binance 用户：可稳妥升级，受益于 bug 修复和性能优化
- OKX 用户：灰度验证后再全量切换
- 进出场模式用户：必须升级（修复了严重的脏数据问题）

**时间窗口**：选择低风险时段（非交易高峰）进行升级，预留 1-2 小时验证。
