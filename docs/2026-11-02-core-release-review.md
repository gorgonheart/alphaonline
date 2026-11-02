# 当前发布core/startup人工复核清单

本轮要求整理当前版本提交并合并main。下表列出本次发布相对合并前main的已实现接口，不把过去计划视为新实现。已保留Arrow/Polars/C预计算消费、原有传统策略默认路径，不整体覆盖core。

## 本轮为部署新增的最小业务适配

| 文件 | 修改项 | 功能影响 |
|---|---|---|
| core/select_coin.py | 一个multiprocessing导入；时序ProcessPoolExecutor显式get_context('spawn') | 保留因子/输入/结果顺序与异常传播，避免Python3.11 Linux fork继承Polars线程；不取消功能 |
| config.py | 本机proxy在JSON覆盖前定义；接入凭据JSON；默认配套alpha2相对路径/空通知 | 机器配置与策略分开，不改变账户offset、BNB、子策略或仓管参数 |
| tools/deployment.py | 仅连接凭据覆盖和停止状态/机器路径检查 | 不加载交易账户，不请求API，不操作业务状态 |
| startup.py | 本轮部署整理没有新增修改 | 下述既有调度/统计改动随当前版本发布 |

没有新增全局set_start_method、没有改变job_num/因子公式、没有以部署为由开启Tail-Only或MODEL incremental_lookback。显式spawn是保留功能的跨平台调整，不应恢复成可能继承本机线程的fork。

## 当前发布中的既有改动

| 文件 | 需人工核对的接口 |
|---|---|
| core/model/backtest_config.py | 显式input_mode、section_state、checkpoint_hours、历史范围与模拟配置下传；默认旧策略仍auto/off/0 |
| core/real_trading.py | 仓管prepare_live/transform_targets仅由账户enable_live_hooks启用 |
| core/position.py | 显式策略注册的换仓适配器；传统RebAlways/ByEquity/ByPosition沿原路径 |
| core/equity.py | 共同行情来源、恢复点采集、模拟状态续接，原incremental_lookback传统用法保留 |
| core/select_coin.py | opt-in截面状态调度；停机后无选币区间补齐；本轮另加spawn |
| core/utils/datatools.py | 完整PKL初始化/严格Arrow续算、缺列行为和BTC参考行情；默认auto回退保持 |
| core/utils/section_state.py | 仅显式section_state账户保存/恢复S3等截面状态 |
| core/utils/checkpoints.py | 通用共同恢复点；不储存另一份账户策略参数 |
| core/utils/commons.py | 每次重试重新计算签名timestamp；保留原重试/错误分支 |
| core/binance/base_client.py | 统计notional可选保留，默认交易概览口径保持 |
| core/binance/portfolio_margin_client.py | 同源BN原生权益/UniMMR统计入口，保留原交易账户获取逻辑 |
| core/utils/statis_func.py | 实时市值、成本未匹配时待对账、原生盈亏和高效快照/展示 |
| core/utils/statistics.py | BN统计与小时快照/高低点/UniMMR/子策略名称、减少重复历史查询 |
| core/utils/statistics_debug.py | 与正式统计同展示规则 |
| startup.py | MODEL初始化/续算调度、clean_start保留有效配套状态；有界统计任务与配置开关 |

统计中旧逐笔/历史账本推算不再用于未经对账的实时成本，这是用户先前明确选择BN原生统计后的功能调整；相关规则见11月1日BN快照复核文档，不借本轮部署重新扩展历史收益算法。真实私有接口、下单和通知在部署验收中不执行。

已知研究/离线旧实现文件迁入positions单文件或tools/robust40/cli，对应旧tools模块删除，没有兼容壳。仓管参数完整保留在各accounts，shared Hybrid数学保持；tools只维护资源/调度/快照等功能设置。

## 证据与待人工确认

新增部署合同先4项失败、实现后4项通过，包括真实spawn因子池及凭据覆盖。首轮全量774通过/2失败/62跳过；2项失败为测试缺显式机器路径，已改临时producer目录并相关11项复测通过。跳过项主要是缺失历史账户/生产snapshot；新增无凭据15/25/45 fixture加当前5m实际公开配置的加载检查执行通过。Linux完整结果将在部署验收后补记。

人工审核以Git diff为准，重点查看本节四处部署适配、JSON覆盖顺序、spawn子进程的MODEL profile继承及正常策略隔离。没有新取消core功能；不能整体恢复core2.3.1而丢失已有Arrow/Polars/续算定制。
