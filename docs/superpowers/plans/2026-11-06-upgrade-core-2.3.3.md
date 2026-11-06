# Core 2.3.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 在现有升级分支完成2.3.3增量集成，保留本地性能和交易保护。
**Architecture:** 每轮订单计算末端限额；名单双来源容错；统一账户从同一余额响应分配钱包，防重复；隔离依赖验证后更新版本。
**Tech Stack:** Python alpha311、pandas、ccxt4.5.88、xbx-py11 0.1.24。
**Spec:** ../specs/2026-11-06-upgrade-core-2.3.3-design.md

## Global Constraints

- 用户本轮已授权按规划在当前分支实施并更新2.3.3；直接执行，不再次请求实施许可。
- 仅每轮参考名义金额限额，默认关闭，无小时累计状态；不改活动账户、策略、因子或生产服务。
- 保留用户未提交发布文档；测试隔离数据/日志/缓存、禁止交易所网络。
- 固定xbx-py11=0.1.24及ccxt=4.5.88，不改共享alpha311安装；用临时target验证新依赖。
- 原NumPy/Numba/pandas/Arrow/Polars版本不动；默认只跑相关测试。

## Review Focus

- 受限行缺报价：发送前失败；未受限行保留旧路径（任务1）。
- 清仓及翻仓被截短：交易标签和路由保留，下一轮按实际仓位重算（任务1）。
- 名单部分失败/无效响应：保留旧名单，冷空不伪成功（任务2）。
- UM/CM正负余额及浮盈：目标合计无重复、概览负债不重复、负基础余额不钳零（任务3）。
- 新依赖影响原交易接口及长因子：双环境订单回归、跨项目预计算合同和缓存检查（任务4）。

## Task 1: 每轮限额

Files: core/model/account_config.py、tests/test_upgrade_233.py、config.py说明。
Interface: AccountConfig.limit_symbol_order_amount(order_df) -> DataFrame；calc_order_amount仍返回原结构。
- [x] 先写关闭不变、通配/覆盖、市场独立、负单/清仓/翻仓、非法配置、无报价、输入不污染和实际拆单发送测试；运行确认缺功能失败。
- [x] 构造时拒绝非字典/非法键/非有限正数；保留低限额，不静默解除。
- [x] 计算实际金额前检查受限行报价，末端按比例裁剪；不重算交易模式、不变目标。
- [x] alpha311 pytest tests/test_upgrade_233.py -q，预期相关项通过。

## Task 2: 双来源名单

Files: core/binance/base_client.py、core/utils/exchange_hub.py、tests/test_upgrade_233.py、tests/test_bstocks_initialization.py。
Interface: fetch_bstocks_coins(expire_seconds=43200) -> set 不变。
- [x] 写全半角、双来源、部分/全部失败、格式异常、缓存/禁用/无凭据测试，确认旧实现失败。
- [x] 合并两个成功来源；部分失败保留旧集合；冷部分空不更新时间；两来源都成功可清空；保留全失败既有通知。
- [x] 更新旧初始化夹具为真实双端点结构，运行相关名单测试通过。

## Task 3: 钱包唯一归属

Files: core/binance/portfolio_margin_client.py、tests/test_upgrade_233.py、tests/test_statistics_bn_snapshot.py。
Interface: get_account_overview()字典结构不变，get_swap_usdt_balance()其他用途不动。

实施裁定：用户现已要求完成2.3.3，钱包项由暂缓转为独立实现。balance响应含UM/CM钱包，概览不再另读UM账户crossWalletBalance，避免两个时刻/口径混算。USDT UM原值留合约侧；基础币正UM/CM全部纳入完整基础币金额，再从现货交易分量剔除已分配合约侧的正USDT UM。account_equity在剔除前按完整基础币+原浮盈/负债规则计算。原负债及非基础币过滤不扩大修改。
- [x] 独立常量oracle：margin100+UM20=>spot100/swap20/total120/account120；正CM、USDC UM、负UM、负基础余额、锁仓/借贷/利息/浮盈及空响应；确认旧实现失败。
- [x] 同一响应计算上述分量；校正注释；保留统计原生视图/recvWindow/操作保护。
- [x] 在相关测试里确认不重复读取UM账户余额，运行钱包/统计/时间戳专项通过。

## Task 4: 加密、依赖、版本与回归

Files: 加密apikey.py、requirements.txt、requirements-deploy.txt、core/version.py、AGENTS.md、docs/DEVELOPMENT.md、本计划/设计。
- [x] 公共加密工具用合成apikey/secret/password做往返测试，先确认工具缺失失败；不在import时执行，使用交互隐藏输入，复用现有AESEncryptor。
- [x] 临时target安装精确两包；对照实际Requires-Dist、pip解析和接口存在性，保持共享解释器旧安装。
- [x] 新依赖下运行新测试及原升级/干跑/名单/时间戳/统计专项、跨项目预计算与执行器缓存相关测试；不跑生产程序。
- [x] 实测限额开关局部耗时及缓存命中请求数；记录测量边界。
- [x] 全部通过后更新sys_version=2.3.3/build20261030、两requirements和开发说明；独立代码复核并修复重要问题。
- [x] 只提交本轮文件，不合并main或部署。

## Execution Record

2026-11-06：用户明确授权在已有分支开发，采用本会话直接执行和最后独立复核。现有两份发布文档含并发更新，提交时仅纳入本轮增量。原技能全仓测试/额外ledger要求服从用户的比例验证与不新增ledger约束，进度记录仅保存在本计划。

实施完成：代码提交c49d6de；首次42失败/6通过，旧依赖相关137通过，新依赖组合170通过；最终修复审查None配置问题（1失败后修复）后175通过/无跳过，包含59个升级专项。pip check通过，共享环境未升级。数值/ABI不变，额外网络传递依赖仅隔离验证。

裁定：钱包从暂缓转为同一balance源明确分配，避免跨响应重复；如与实际账户字段分布不符会影响目标资金，已用独立样例固定数值，真实环境仍需部署前验证。原负债政策、参考限额精度/滑点偏差和模式不重分类保持用户批准范围；本轮不宣称生产成交或性能通过。独立审查未留下其他确定阻断项或minor。
