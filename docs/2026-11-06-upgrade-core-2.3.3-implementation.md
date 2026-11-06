# 2.3.3 增量集成记录

分支：`codex/upgrade-core-2.3.3`，基线main `2827d27`，分析提交 `d5c1f22`，运行代码提交 `c49d6de`。用户授权在分支按规划实施、更新2.3.3；不合并main或部署。

## 实际行为与范围

- `AccountConfig`增加每轮单币参考名义金额限额，默认不限制；显式币种优先于`*`，现货/合约独立。低限额不删除、不放大到最小下单金额；非法字典/键/非有限正数拒绝，受限订单缺有效报价在发送前失败。目标、交易模式、原拆单/IOC/GTC/重试和干跑保护保留。
- bStocks支持全半角括号及两个来源合并；完整成功可替换旧名单，部分失败保留旧成员，冷启动部分空不伪造成功时间，全部失败保留既有告警。缓存命中、无凭据或禁用过滤不请求。没有增加磁盘名单状态。
- 统一账户概览用一次balance响应分配钱包：USDT UM原值归合约侧；基础币正UM/CM计入完整基础币金额，现货交易分量排除已经分配的正USDT UM。概览总权益独立在排除前计算，原负债/浮盈规则保留。100U margin+20U UM得到100/20，目标合计120，概览120；不会得到140。独立归集用的get_swap_usdt_balance、普通账户、OKX、统计actualEquity专用视图、只读余额recvWindow及页面杠杆3不变。
- 新增手动`加密apikey.py`，隐藏输入、导入无执行副作用，沿用现有AESEncryptor协议，支持可选OKX password。实际凭据由用户手动输入，未进行真实加密或打印。
- 版本标识2.3.3 / v2.3.3.20261030，两份requirements固定xbx-py11=0.1.24与ccxt=4.5.88。配置仅补公开字段说明，活动accounts不变。

未改动alpha源码、因子/截面/仓管数学、Arrow/PKL格式、预计算列名/回退、数据长度、JIT、MODEL/S3协议或startup调度。

## 钱包核算边界

原设计将钱包项暂缓；本轮用户要求完成2.3.3后，采用同源响应分配而非直接叠加上游两行。字段参考[Binance Portfolio Margin账户接口](https://developers.binance.com/en/docs/catalog/advanced-trading-derivatives-trading-portfolio-margin/api/rest-api/account)，wallet与未实现盈亏分列。不假设两个请求时刻的UM wallet与crossWalletBalance恒等。

保留现有交易资金政策：某些借贷、利息、非USDT负CM/UM只进入概览negative_balance，不能声称本次把全部交易权益统一为完整净清算权益。非基础币钱包和币价过滤也不扩大修复。若要重构这些既有政策，需要独立任务；此次验证的是新增正钱包不丢失、不重复，负USDT UM仍保留合约侧负值。

## 依赖验证

通过alpha311将包安装到可删除的`test/upgrade233/deps`，共享环境未修改，仍为xbx0.1.23/ccxt4.5.53。新环境实际加载xbx0.1.24/ccxt4.5.88，新名单方法存在，同步和异步客户端导入成功，最终`pip check`无冲突。

只安装两顶层包时pip check发现传递依赖不满足，已补齐。安装过程曾针对全局旧xbx0.1.23提示ccxt冲突；隔离PYTHONPATH下最终检查已确认消除，不代表全局环境被升级。部署不能使用`--no-deps`或将验证目录加入生产运行路径，应在目标独立环境正常解析requirements。

网络组件实际解析包含aiohttp3.14.5、cryptography50.0.2、urllib3 2.8.0、cffi2.0.0及ccxt所需其余组件；Windows winloop与平台相关。NumPy2.0.2、pandas2.2.3、Polars1.36.1、PyArrow17.0.0、Numba0.60.0不变，不重编译C。当前验证平台Windows；Ubuntu环境解析和真实请求尚未测试。

## 验证证据

所有交易检查在临时源码副本、空账户/空凭据配置下运行，socket连接被禁止；日志位于`test/upgrade233`。

| 批次 | 结果 | 范围 |
|---|---|---|
| 新增测试首次RED | 42失败、6通过 | 确认旧版缺限额/校验/名单保护/钱包归属/加密工具 |
| 旧依赖相关组合 | 137通过 | 升级、原2.3.1、名单初始化、统计、时间戳、干跑 |
| 新完整依赖相关组合 | 170通过，74.83秒 | 上述范围加C预计算→Arrow→消费缺列回退、Polars主链、执行器缓存 |
| 补现货路由后的升级专项 | 58通过，3.18秒 | 含现货买卖IOC/GTC、合约五模式、配置/报价/低门槛/名单/钱包/加密 |
| 独立审查修复后的最终组合 | 175通过，53.83秒 | 59个升级专项及上述全部相关测试，无失败/跳过 |

批次有重叠，不能相加。中间一次3失败来自新下单替身缺ticker、旧名单夹具缺第二来源、旧统计测试仍断言读取第二余额接口；按真实接口调整夹具/预期后通过，没有放宽数值容差或重建golden。保留原pandas FutureWarning，不扩展到告警清理。

独立代码审查发现显式None被当作空配置而解除限额。先补用例得到1失败/12通过，再删除None兜底，最终175项全部通过；默认不传字段仍为{}，显式None现在报错。审查未发现其他确定Critical/Important/Minor项；真实响应、成交及平台性能仍属于未覆盖边界。

本机纯限额函数1500行、20次热测：关闭中位0.0006ms，启用且全部超限中位7.7898ms/P95 7.9316ms；日志为无IO替身，不包含订单计算其余步骤、网络、真实日志或整轮性能。名单快路测试确认不调用两接口；冷刷新多一个串行请求及其重试，不称零成本。

复跑相关集合（在隔离源码及新依赖环境下）：

```text
python -m pytest -q -p no:cacheprovider tests/test_upgrade_233.py tests/test_bstocks_initialization.py tests/test_statistics_bn_snapshot.py tests/test_binance_timestamp_retry.py tests/test_dry_run_entrypoints.py tests/test_upgrade_231.py tests/test_precompute_contract.py tests/test_main_chain_parity.py tests/test_section_polars_parity.py tests/test_robust40_execution_cache.py --producer-root=D:/alpha
```

Python必须使用alpha311，临时数据与Numba缓存放在本轮test目录。不得将此命令与生产入口混用。

## 未覆盖及使用约束

每轮限制包含清仓，可能延长退出时间；不会保存旧补单队列。同小时重跑获得新额度，实际精度/滑点可能超过计划参考金额。BNB补币、保证金调整等绕过订单计算的操作不受此限额约束。

未启动/重启服务、下单、部署或更改活动账户；未验证真实交易所响应、Ubuntu新依赖或整轮性能。现有用户发布记录及未跟踪参考包保留。新参数默认关闭，不代表已给任一活动账户设置300U。
