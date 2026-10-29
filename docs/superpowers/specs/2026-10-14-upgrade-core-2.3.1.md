# 2.3.1 升级记录与跨项目回归

日期：2026-10-14。标准代码来源：仓库内 `2.3.1core/`，build `20260911`。

## 实际基线

升级前 HEAD 为 `056dd21`，工作代码版本为 2.2.1；2.3.0 的 Git 记录是分析文档，OKX / exchange_hub 尚未落地。因此使用 `2.2.1core/`、当前代码和 `2.3.1core/core/` 三方比较，同时核对 2.3.0 的增量。历史分析不是最终实现证据。

升级前测试：230 passed、2 failed、2 skipped。两项失败来自 Arrow 包含预计算因子而批次 PKL 不包含，两个跳过来自真实快照测试写死的历史时间戳。

## 合并内容

- 引入 OKX 客户端、exchange_hub、bStocks 过滤和相关行情、统计路由。
- 引入 2.3.1 maker/IOC 混合下单、BNB 抵扣开关同步、Binance -4518 容错及 OKX 档位/限价修复。账户默认 `maker_ratio=0`，建仓/清仓保持 IOC；未更改任何账户配置、策略、因子或仓位管理策略文件。
- 下单接口按新版传入 run_time，并统一成功回执/失败订单返回契约。
- 配置只增加默认 `exchange_name='binance'`，BTC 数据目录由它生成；账户初始化也继承全局交易所配置。现有 Binance 数据路径不变。
- 引入 entry_exit 重叠截面覆盖修复、长持仓周期缓存下限单位修复、空选币聚合时间轴修复和统计修复。
- 空选币快照保留历史币种列，并在 concat 前对齐新旧列，避免新版取并集后在无仓位截面产生 NaN。
- 保留 dry_run 的撤单/下单保护，并补齐账户刷新、归集资产和基础保证金调整的干跑保护，避免新增 BNB 设置在干跑时触发账户写操作。

## 保留的本地功能与边界

- Arrow mmap 读取、损坏/缺失时 PKL 回退、向量化映射对齐和分阶段耗时日志。
- `precompute_factor_configs` 配置传递、预计算列复用、缺列/未声明时的本地计算回退。
- 截面 Polars 实现、并行写盘、回测阶段 ProcessPoolExecutor 与 LPT 调度。
- equity 继续通过两参数 `read_bmac_market_pivot(hour_offset, symbol_type)` 读取生产者 PKL；没有恢复 Arrow 现场重建 pivot。
- 保留原有页面杠杆设置、禁用运行归档的选择、历史文件保留数量等定制。根据主链路统计一致性的补充要求，接通标准成交记录保存与最近 1h 成交额统计；dry_run 不产生虚假成交记录。
- `accounts/`、`shared_account_config.py`、`factors/`、`sections/`、`positions/`、`startup.py` 未改动。
- D:/alpha 仅用于只读代码/数据核对及隔离临时目录中的测试，没有修改其代码、配置或生产数据。
- 根目录原本没有 delist.py / monitor.py，本次没有新增或启用独立监控服务。发行包中的 delist 修复仍保留在参考目录；若以后部署该独立服务，应使用 2.3.1 版本。

## 数据结构与 C 加速契约

生产者 `D:/alpha/core/preprocess.py::apply_precomputed_factors` 调用 C 包装因子，将 `{factor}_{param}` 列嵌入返回的数据字典，供 Arrow 发布；批次 PKL 有意保留原始行情。不能以“Arrow 与 PKL 列集合完全相等”作为正确性标准，也不能在消费者删除这些因子列。

测试分三层：

1. 真实行情对账：仍逐 symbol/列/行比较行情值与顺序，只允许生产者配置明确声明的预计算附加列；缺失行情列、未知附加列和行情数值差异仍失败。原有整数列 dtype 例外保持不变。
2. 生产者计算：动量、CCI、ILLQ 的 C/Python/已有 golden 对账，以及 Arrow 发布、首次与增量因子嵌入原有测试。
3. 跨项目消费：在隔离进程内使用生产者真实 C 因子和 `apply_precomputed_factors`，将 18000 根合成 K 线计算结果裁至 5000 根发布成 Arrow；消费者读入后逐值精确保留三种因子，且断言不调用本地重复计算。另外验证缺列或未声明时确实回退。

真实 Arrow 测试改为自动发现最新快照；路径构造单元测试仍使用固定时间，避免混淆两类测试。

## 主数据链路同步核查

2.3.0 / 2.3.1 标准框架没有引入 Arrow、Polars 或 C 预计算数据格式变更，但 2.3.0 的交易所过滤、进出场续算、聚合时间轴和统计修改会影响本地加速链路的业务结果。因此按业务语义合入，不直接覆盖本地数据实现。

本轮核查额外发现并修复：

- Polars 截面裁切遗漏 `incremental_lookback_hours`，导致相同输入下 pandas 保留 21 行而 Polars 仅 6 行；两条路径现在使用相同回退窗口。
- 选币 offset 的中间值及落盘值统一使用 Int16，168H 等周期不再因 Int8 溢出而失败。
- 合约选币、现货执行的配置同时加载现货数据，与标准框架保持一致。
- 两处并行因子写入消费 executor.map 结果，让写入异常正常上抛，避免继续消费旧文件。
- 将实际下单返回值接入标准成交统计输入，覆盖缺失或为零的 cumQuote，排除尚未成交的 GTC 委托。

新增 `test_main_chain_parity.py` 共 11 项：真实 Arrow 发布/读取与 PKL 的交易所过滤对账；pandas/Polars 截面计算经普通或进出场选币、仓位聚合、目标持仓、订单数量到策略统计归属的逐项对账（含增量窗口）；长周期 offset；现货数据加载；成交额保存/读取；并行写入故障传播。生产者 C 到 Arrow 到消费者复用由 `test_precompute_contract.py` 另行覆盖。上述是分层集成验证，未连接真实交易所执行订单。

## Python 依赖

按照补充的官方发布要求，将 alpha311 中 `xbx-py11 0.1.10` 升级至 `0.1.23`，并增加 requirements.txt 固定已验证版本。包自身依赖将 ccxt 从 4.4.14 升至 4.5.53、Polars 从 1.37.1 调整至 1.36.1、pycryptodome 从 3.23.0 调整至 3.21.0，并安装其余所需依赖。NumPy 2.0.2、pandas 2.2.3、PyArrow 17.0.0 保持不变。

alpha311 是两个项目共享的环境，所以下列两组最终测试均在依赖更新完成后运行。

## 验证结果

- alphaonline：**269 passed，0 failed，0 skipped**（67.44 秒）。包含原有 234 项与新增 35 项。
- alpha 数据框架：**24 passed**（1.79 秒）：`test_module6_momentum_factor.py`、`test_module_cci.py`、`test_module_illq.py`、`test_module8_arrow_publish.py`、`test_module9_embedded_factors.py`。
- `python -m pip check`：No broken requirements found。
- 所有运行时 Python 源码语法解析通过；新版 Binance 手续费燃烧/盘口相关 ccxt 方法存在。
- `git diff --check` 通过。

交易端新增覆盖默认 IOC、建仓/清仓路由、非法 maker_ratio、GTC 接单后不重复提交、BNB 配置同步、长周期缓存下限、重叠截面修正、空选币历史仓位、dry_run 与新版下单调用链。

复现命令（在对应项目目录执行，临时目录需使用新的空目录）：

```powershell
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-test-recheck

# 在 D:/alpha 中执行
& 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_module6_momentum_factor.py tests/test_module_cci.py tests/test_module_illq.py tests/test_module8_arrow_publish.py tests/test_module9_embedded_factors.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-producer-recheck
```

现有 pandas 链式赋值 FutureWarning 仍存在，未在本次升级中扩大范围清理。验证包含真实本地行情与模拟交易所接口，不代表已验证真实交易所成交；未启动 startup.py、下单、部署或重启服务。
