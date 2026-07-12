# 为 动态回撤阈值 / 固定回撤阈值触发币数占比开仓门槛 增加 signal_polars

日期：2026-07-12
分支：upgrade/core-2.2.1

## 背景与问题

`core/select_coin.py::calc_cross_sections` 通过 `all(...)` 判断是否所有截面因子都实现了
`signal_polars`：

```python
all_polars = all(
    hasattr(FactorHub.get_by_name(fn), 'signal_polars')
    for fn in section_params_dict.keys()
)
if all_polars:
    calc_cross_sections_polars(conf)   # 高性能 Polars lazy 路径
else:
    calc_cross_sections_pandas(conf)   # 低性能 pandas 路径
```

只要账户配置里**任何一个**截面因子缺少 `signal_polars`，整个截面因子计算就会退回
pandas 路径。`sections/动态回撤阈值.py` 与 `sections/固定回撤阈值触发币数占比开仓门槛.py`
目前只有 pandas `signal`，因此拖累了同一账户内其它已实现 Polars 的截面因子，导致性能下降。

参照对象：
- `sections/TopKTo2K.py`、`sections/RankBias.py`：纯 lazy Polars 实现（无跨时间状态）。
- `sections/VolumeMaxBias_进出场阈值分离官方.py`：collect→pandas 适配器（因核心是 numba
  状态机，跨时间截面传递持仓，无法表达为纯 lazy 算子，才走该模式）。

## 目标

为两个 section 各新增一个 `signal_polars(lf, params, factor_name) -> pl.LazyFrame`，
使配置了它们的账户能进入 `calc_cross_sections_polars` 高性能路径，且与原 pandas
`signal` 严格等价（strict parity，包括 inf 边界行为）。

## 方案选择

两个因子都是**纯截面**（仅按 `candle_begin_time` 分组，无跨时间状态），可直接映射为
Polars 窗口表达式，全程 lazy。因此采用**纯 lazy Polars** 方案（对标 TopKTo2K/RankBias），
而非 VolumeMaxBias官方 的 collect→pandas 适配器 —— 后者只在存在 numba 状态机时才必要，
本场景不适用，用它会把大部分慢速逻辑留在原地。

数据可用性已确认：`calc_cross_sections_polars` 加载的 base parquet 列集为
`KLINE_COLS = ['candle_begin_time','symbol','is_spot','close','next_close','symbol_spot','symbol_swap','是否交易']`，
依赖因子 `当前回撤_{n}` 通过 `section_depend_factor_col_name_list` 追加。两个因子所需的
全部列都已在 lf 中。

## 详细设计

### sections/动态回撤阈值.py —— 截面 z-score

原 pandas 逻辑：按 `candle_begin_time` 对 `当前回撤_{n}` 求 mean/std（`transform`，
ddof=1），`z=(x-mean)/std`，`z.fillna(0)`，因子=z。

Polars 实现：

```python
def signal_polars(lf, params, factor_name):
    n = params
    col = f'当前回撤_{n}'
    # nan→null，统一缺失表示，使 mean/std 的 skipna 语义与 pandas 一致
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    lf = lf.with_columns([
        pl.col('_dd').mean().over('candle_begin_time').alias('_mean'),
        pl.col('_dd').std().over('candle_begin_time').alias('_std'),   # ddof=1，与 pandas 一致
    ])
    lf = lf.with_columns(
        ((pl.col('_dd') - pl.col('_mean')) / pl.col('_std')).alias('_z')
    )
    # fillna(0)：nan/null→0；inf 保留（strict parity）
    lf = lf.with_columns(pl.col('_z').fill_nan(0).fill_null(0).alias(factor_name))
    lf = lf.drop(['_dd', '_mean', '_std', '_z'])
    return lf
```

### sections/固定回撤阈值触发币数占比开仓门槛.py —— 触发币数占比开仓门槛

原 pandas 逻辑：`domain_mask`（col.notna & 是否交易==1 & is_spot==0 & symbol_spot!='' &
symbol_swap!=''）；`trigger_mask = domain & (col < threshold)`；按 `candle_begin_time`
统计 `pool_count=domain.sum`、`trigger_count=trigger.sum`；`max_count=ceil(pool*max_pct)`；
仅当 `trigger & (trigger_count <= max_count)` 时因子=1，其余=0（初始化全 0，无 null）。

Polars 实现：

```python
def signal_polars(lf, params, factor_name):
    window, threshold, max_pct = params
    col = f'当前回撤_{window}'
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    domain = (
        pl.col('_dd').is_not_null()
        & (pl.col('是否交易') == 1)
        & (pl.col('is_spot') == 0)
        & (pl.col('symbol_spot') != '')
        & (pl.col('symbol_swap') != '')
    )
    trigger = domain & (pl.col('_dd') < threshold)
    lf = lf.with_columns([
        domain.alias('_domain'),
        trigger.alias('_trigger'),
    ])
    lf = lf.with_columns([
        pl.col('_domain').cast(pl.Int64).sum().over('candle_begin_time').alias('_pool'),
        pl.col('_trigger').cast(pl.Int64).sum().over('candle_begin_time').alias('_trig_cnt'),
    ])
    lf = lf.with_columns((pl.col('_pool') * max_pct).ceil().alias('_maxc'))
    lf = lf.with_columns(
        pl.when(pl.col('_trigger') & (pl.col('_trig_cnt') <= pl.col('_maxc')))
        .then(1).otherwise(0).cast(pl.Int64).alias(factor_name)
    )
    lf = lf.drop(['_dd', '_domain', '_trigger', '_pool', '_trig_cnt', '_maxc'])
    return lf
```

同时删除该文件顶部无用的 `filter_dependencies_as_conditions = False`
（全仓库无任何代码读取该属性，确认为死配置）。

## 关键 parity 风险

pandas `notna()` / `transform('mean'|'std')` 把 NaN 视为缺失并跳过；Polars 三值逻辑中
浮点 `nan` 与 `null` 不同（`is_not_null()` 对 nan 返回 True，`mean` 遇 nan 会传播）。
在函数入口用 `fill_nan(None)` 把 nan 统一为 null，使 skipna 语义与 pandas 对齐。
依赖列从 parquet 出来到底是 nan 还是 null，正是真实数据 oracle 测试要钉死的点。

`std=0` 的截面会产生 `inf`（x≠mean 时），pandas `fillna(0)` 不处理 inf，Polars 亦保留
inf —— strict parity，测试需覆盖该情形。

## 测试（仓库 oracle-equivalence 惯例）

新增 `tests/test_section_polars_parity.py`：
- `alpha311` 环境；`skipif` 守卫真实数据中心数据是否存在；动态发现最新 snapshot ts（不硬编码）。
- 按 `calc_cross_sections_polars` 相同方式构造 base 帧 + `当前回撤_{n}` 依赖列。
- 对每个因子：`signal`（oracle，pandas）与 `signal_polars` 分别计算，`pd.testing.assert_frame_equal`
  断言等价（对齐 dtype；inf 一致处理）。
- 覆盖一个 `std=0` 截面，锁定 inf 保留行为。

## 验收

1. 运行新测试 + 现有 `tests/` 套件通过（已知 `test_arrow_pkl_merge_parity` 数据驱动失败与本改动无关）。
2. 确认使用这两个 section 的账户配置现在进入 `calc_cross_sections_polars` 分支（而非 pandas）。

## 不做（YAGNI）

- 不改 `calc_cross_sections` 的 `all(...)` 分派逻辑。
- 不动其它已实现 Polars 的 section。
- 不为无 legacy oracle 的场景引入额外抽象。
