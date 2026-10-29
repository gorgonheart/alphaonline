# 2026-10-29 实盘基线整理与回测一致性审查

## 范围与版本事实

本轮排除 Tail-Only。用户最终指定以 D:/alphaonline 本地 2026-10-22 之前的版本整理 main，再建立修复分支。没有部署、重启、撤单、下单或改动生产数据。服务器核对对象已指定为 `/home/ubuntu/alphatrade1`，具体证据见 [差异报告](2026-10-29-live-backtest-differences.md)。

整理前 main=481cf95（2.2.1），当前分支 upgrade/core-2.3.0=056dd21（只增加升级分析文档）。工作树 core/version.py 已是 2.3.1 / 20260911；有 26 个已跟踪路径差异及新增源码。仓库没有配置 remote，本轮合并指本地 main，不进行推送。

## 已有未提交内容及处理

- 交易框架：2.3.1/OKX、bStocks、maker/IOC 路由及不明订单查询、dry_run 保护、成交记录、拒单处理。
- 数据和选币：预计算缺列全历史回退、Polars 回退范围、线程写盘异常传播、entry_exit 重叠覆盖、空选币时间轴与历史列保留。
- 因子：输入隔离/因果版/动量 V2 及新增资金费率、选币辅助因子。
- 仓管和工具：保留 Hybrid（本地 10 月 15 日已有）和 LOCAL（10 月 16 日文档/测试）。Robust4H 为 10 月 29 日新草稿，按用户时间边界从主干排除。
- 统计：策略归属回填和资金曲线图例布局；依赖、开发文档和对应测试。
- config.py 保留已有 exchange_name、factor_col_limit 等调整；不重写账户设置。
- 已被用户移走的 accounts/40m_dad0604_2.2.1.py 按现状记录删除，不恢复旧策略。新 accounts、accounts backup、研究数据、equitymerger 结果及本地工具设置保留原位，不批量纳入源码提交；账户可能含凭据，不使用 git add -A。
- 2.3.0core、2.3.1core、core.zip 为上游参考副本，加入忽略规则；不删除文件。
- D:/alpha 另有动量 V2/C 包装/参数及测试未提交，以及 pyc/诊断产物；本轮只核对依赖，不把生产者的所有工作一并提交。

## 基线验证

alpha311 离线执行 tests：**280 passed、4 skipped、95 warnings，38.12 秒**。跳过项对应缺少真实 30m Arrow 快照的 reader/PKL 对账；现有 pandas 警告保留。没有将跳过计为通过，也没有由此推断 Robust4H 可用。git diff --check 通过。

按时间边界移除四个 10 月 29 日草稿文件后，针对 tools 命名空间包与保留的 LOCAL 路径运行 `tests/test_local_risk_model.py`：**6 passed，11.75 秒**；主干 core/原 Hybrid/因子未再修改，未重复全仓测试。core、positions、tools、tests 无对已排除 Robust 模块的 Python 引用；未跟踪的新账户草稿仍保留，不能把它们视作可启动配置。

## 回测参考发现

用户指定 E:/quant/position-mgmt_v2.3.0_{55m,20m,40m}。各目录的 config_MODEL_ROBUST40_<offset>.py 使用 MomentumPrecursors1023、planned/MODEL_ROBUST40_<offset>.pkl 和 deltas/MODEL_ROBUST40_<offset>.pkl；换仓死区为 0.4，执行封顶 2.45。

本地实盘 HybridRobust4HStrategy 使用 Hybrid 父权重、0.2 死区和全账户净多头折扣，并非以上回测策略的等价移植。回测研究的 MODEL delta 按 S5 贡献（含仓管与一次再择时）形成，执行时只从正净权重扣除并截断到零；不能直接改为全账户乘 0.5。回测父权重还含 ROBUST40 的专家组合机制，不能只复制 14 项静态 cap_ratios。

三个目录作为只读参照；不改写冻结的 planned/deltas 或历史结果。用户指定的主干基线是本地 10 月 22 日前版本，不能用这些新研究配置或服务器 1020 账户替换本地基线。新策略移植仍需区分原 Hybrid 的偏移一致性与 MODEL_ROBUST40 规则的改变。

## 主干时间边界修正

初次整合提交 5098bb7 包含全部当时源码，已快进 main。用户随后明确时间边界，因此以后续提交纠正主干树，不改写已有提交历史；5098bb7 另保存在 `codex/robust4h-draft-20261029`。

- 从主干移除 `positions/HybridRobust4HStrategy.py`、`tools/robust_risk_4h.py`、`tools/train_robust_model.py` 和 `tools/__init__.py`，四者本地创建时间均为 10 月 29 日，并与新模型/账户草稿对应。源码仍可从保存分支取回。
- 保留 10 月 14 日升级与因子、10 月 16 日 LOCAL 与资金费辅助因子、10 月 19 日图例。10 月 22 日和 27 日统计报告明确为诊断而非业务修改，不能仅凭报告日期回退既有统计实现。
- 新增审查文档是本次回顾记录，不是宣称 10 月 22 日前已经编写。10 月 24 日 CSV 修复记录保留历史事实；未将该生产数据带入代码基线。
- Git 在 8 月之后没有中间提交，无法恢复一个经过逐字节存档的“10 月 21 日收盘快照”；本次是按本地文档、文件来源和差异重建的功能基线，不能用文件创建时间单独证明旧文件从未被改动。
- 本地旧账户最后修改时间为 10 月 16 日，`accounts backup/实盘策略1018.py` 为 10 月 18 日。它们与新 Robust 账户均保持原位，凭据不纳入提交；`main` 不是可直接启动的账户部署包。

## 保存在草稿分支中的 Robust4H 问题（尚未修复）

- config.PROJECT_ROOT 缺失导致执行器报错；偏移错误地回退到 0m；风险状态更新没有业务调用。
- 风险文件位于每轮清理的 cache；需明确账户/偏移隔离、有效期和决策窗口。
- runtime 列名与手数数组的币种映射可能错位。
- 季度模型在无历史模型时回退 latest_model、时区比较、训练/实盘特征口径不一致。
- core 的可选策略钩子需通过 PosStrategyConfig.core_funcs 显式调用，不能仅检查不存在的属性。
- 保持完整历史聚合与 simulate 产物，不实施 Tail-Only。

本文件记录整理时的事实，不是这些问题已修复的声明。
