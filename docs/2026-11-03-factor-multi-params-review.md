# 当前 MODEL 旧因子批量接口迁移

本轮由用户指定的 gpt-6.1-sol 子 agent 实现，另一 gpt-6.1-sol 子 agent 独立只读复核，主 agent 最终联合验证。业务修改只在17个因子文件；core、startup、accounts、sections、signals、positions及alpha/alpha2本轮无新增修改，未提交、部署、重启或交易。工作区其他既有未提交内容保留。

## 实现范围

参照项目已有 `LowPrice`、`PctChange` 等双接口因子，保留 `signal(*args)`，在同模块增加 `signal_multi_params(df, param_list) -> dict`。返回原参数键到pandas Series，保持实际值、dtype、索引和异常语义；空参数返回空字典。

当前三份20/40/55m MODEL子策略池涉及16个：Atr、Bias (2)、BiasV1、Cci过滤无现货删数据、CirculatingMcap、CorrBTC、HoursSinceSpotAndSwap、MinMax、SelectCoin、SpotSwapBoth、ZfMeanQ、ZfStd、双均线、新币、新币落九天用的、资金费率绝对值MAX。另增加父仓管使用的Bias，共17个。

新函数不转调旧signal，不复制整份行情；共享可复用的价格、收益率、振幅等中间Series。BiasV1只取必要三列后过滤；需要匹配旧行为的资金费率规范化在局部Series中逐参数执行。旧signal保留为兼容入口及独立对照，17个函数AST均与HEAD一致。

现有 `core.factor.calc_factor_vals` 会自动优先选择批量接口，不需要修改core或账户开关；已缓存模块的进程不会动态重载。参数、公式、预计算列、因子名称及snapshot协议不变。父仓管目前按参数分别调用，不能宣称其三个Bias窗口已合成一次计算。

## 保留的特殊行为

- BiasV1删除缺close行并reset_index，可能返回较短RangeIndex。此行为本轮不修正，不能把新接口一概声明为行数/索引不变。
- HoursSinceSpotAndSwap保留首次True索引标签当iloc位置的旧行为；非标准索引可能错位或异常。
- 新币落九天用的对非空参数、空行情仍保留旧iloc[0]异常；没有参数时不读取行情。
- 资金费率是先0转缺失、ffill、to_numeric，再做 `abs(rolling.max())`，不是 `rolling.max(abs(x))`。脏字符串规范化可能产生NaN，第二参数再次ffill会改变数据，因此保留旧连续调用的参数顺序依赖；仅局部Series变化，共享输入不修改。
- CCI与新币落九天的映射条件使用可空字符串时，旧loc将布尔NA当False；新mask显式沿用该规则。
- `core.factor`原有外部数据桥接仍会注入辅助列；“输入不修改”指批量因子自身，不声称整个现有预处理都是只读。

## TDD、复核与最终验证

1. 首轮缺接口测试：239失败，确认因新接口缺失而失败。
2. 空参数测试：15失败/2通过，增加早返回后通过。
3. 独立复核发现脏资金费率连续调用差异：6失败，保留旧逐参数预处理后通过。
4. 独立复核发现nullable映射条件差异：2失败，显式匹配loc规则后通过。
5. 最终专项349通过，独立review同样349通过，无遗留阻塞问题。批次有重叠，不能相加。
6. 主 agent 联合验收353通过、145 warnings、10.09秒。警告为保留旧pandas行为的FutureWarning及一次pytest缓存目录权限警告，无测试失败；未借迁移修改pandas版本或缺失值默认规则。

联合命令：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' -X utf8 -B -m pytest tests/test_model_legacy_multi_params.py tests/test_initialization_pipeline.py::test_actual_offset_pools_load_independently tests/test_full_arrow_initialization.py::test_strict_reuse_survives_mixed_parameters_and_detects_removed_column -q --tb=short
```

专项包括旧signal exact对照、独立数值边界、NaN/Inf、float32、重复及非RangeIndex、nullable字符串、空/短行情、参数顺序/重复参数、禁止新接口调用signal、共享输入不变、真实core调度与shift，以及真实data_bridge读取临时辅助数据并注入。

实际参数来自三个本地MODEL账户；4050/14050长历史单测使用合成行情并直接比较multi与旧连续signal。真实core dispatch由另外的短输入和外部桥接测试覆盖，不能称长历史全市场端到端验收。三账户加载及严格Arrow预计算列复用/缺列拦截另4项通过。

主 agent另验17旧signal AST不变、每模块恰好一个新接口。保留文件既有行尾，检查使用 `git -c core.whitespace=cr-at-eol diff --check`；没有修改Git配置或统一重写全文件行尾。

## 局部性能证据

预热后旧/新交替5次；统计17因子各自完整调度中位数之和，包括旧整表copy、多参数调用、shift/values与新批量调度。不是整轮墙钟中位数，不包含行情加载、辅助源I/O、进程启动/传输、IPC保存、截面、选币或MODEL。

| 样本 | 旧调度中位数之和 | 新调度中位数之和 |
| --- | ---: | ---: |
| 14050行BTC真实OHLCV PKL；映射、市值、funding为合成辅助字段 | 380.676 ms | 232.114 ms |
| 4050行BTC真实Arrow；市值为合成字段 | 175.876 ms | 93.870 ms |

两组btc_close取同一样本BTC close。全部输出逐列exact对拍、输入不变。来源及逐因子计时见[微基准JSON](../research/model_legacy_multi_params/benchmark.json)，复跑脚本见[benchmark_model_legacy_multi_params.py](../tests/benchmark_model_legacy_multi_params.py)。这些数字不代表17因子在整个币池均按相同比例提速，也不证明已降低整轮内存峰值。

## 后续范围

本轮不实现统一列式引擎、数组模拟交接、S3重写、聚合v2切换或目标仓位接口重构。相关特殊逻辑、可迁移部分及应保留的pandas/状态边界见[列式与数组边界审计](2026-11-03-columnar-boundaries-review.md)。当前新初始化入口默认关闭的设置仍保持不变。
