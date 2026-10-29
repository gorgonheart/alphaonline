# 2026-10-29 实盘基线整理与回测一致性审查

## 范围与版本事实

本轮排除 Tail-Only。先整理 D:/alphaonline 本地已有变更到 main，再从该基线建立修复分支。没有部署、重启、撤单、下单或改动生产数据。服务器存在多个历史实例，实际运行服务/目录尚待指定；本地文件版本不能冒充运行进程版本。

整理前 main=481cf95（2.2.1），当前分支 upgrade/core-2.3.0=056dd21（只增加升级分析文档）。工作树 core/version.py 已是 2.3.1 / 20260911；有 26 个已跟踪路径差异及新增源码。仓库没有配置 remote，本轮合并指本地 main，不进行推送。

## 已有未提交内容及处理

- 交易框架：2.3.1/OKX、bStocks、maker/IOC 路由及不明订单查询、dry_run 保护、成交记录、拒单处理。
- 数据和选币：预计算缺列全历史回退、Polars 回退范围、线程写盘异常传播、entry_exit 重叠覆盖、空选币时间轴与历史列保留。
- 因子：输入隔离/因果版/动量 V2 及新增资金费率、选币辅助因子。
- 仓管和工具：Hybrid、LOCAL 与尚未完成接线的 Robust4H；现存源码纳入基线，但不将其存在等同于可上线。
- 统计：策略归属回填和资金曲线图例布局；依赖、开发文档和对应测试。
- config.py 保留已有 exchange_name、factor_col_limit 等调整；不重写账户设置。
- 已被用户移走的 accounts/40m_dad0604_2.2.1.py 按现状记录删除，不恢复旧策略。新 accounts、accounts backup、研究数据、equitymerger 结果及本地工具设置保留原位，不批量纳入源码提交；账户可能含凭据，不使用 git add -A。
- 2.3.0core、2.3.1core、core.zip 为上游参考副本，加入忽略规则；不删除文件。
- D:/alpha 另有动量 V2/C 包装/参数及测试未提交，以及 pyc/诊断产物；本轮只核对依赖，不把生产者的所有工作一并提交。

## 基线验证

alpha311 离线执行 tests：**280 passed、4 skipped、95 warnings，38.12 秒**。跳过项对应缺少真实 30m Arrow 快照的 reader/PKL 对账；现有 pandas 警告保留。没有将跳过计为通过，也没有由此推断 Robust4H 可用。git diff --check 通过。

## 回测参考发现

用户指定 E:/quant/position-mgmt_v2.3.0_{55m,20m,40m}。各目录的 config_MODEL_ROBUST40_<offset>.py 使用 MomentumPrecursors1023、planned/MODEL_ROBUST40_<offset>.pkl 和 deltas/MODEL_ROBUST40_<offset>.pkl；换仓死区为 0.4，执行封顶 2.45。

本地实盘 HybridRobust4HStrategy 使用 Hybrid 父权重、0.2 死区和全账户净多头折扣，并非以上回测策略的等价移植。回测研究的 MODEL delta 按 S5 贡献（含仓管与一次再择时）形成，执行时只从正净权重扣除并截断到零；不能直接改为全账户乘 0.5。回测父权重还含 ROBUST40 的专家组合机制，不能只复制 14 项静态 cap_ratios。

等待确认具体回测基准后再修改策略语义。三目录当前作为只读参照；不改写冻结的 planned/deltas 或历史结果。

## 修复分支必须处理的事项

- config.PROJECT_ROOT 缺失导致执行器报错；偏移错误地回退到 0m；风险状态更新没有业务调用。
- 风险文件位于每轮清理的 cache；需明确账户/偏移隔离、有效期和决策窗口。
- runtime 列名与手数数组的币种映射可能错位。
- 季度模型在无历史模型时回退 latest_model、时区比较、训练/实盘特征口径不一致。
- core 的可选策略钩子需通过 PosStrategyConfig.core_funcs 显式调用，不能仅检查不存在的属性。
- 保持完整历史聚合与 simulate 产物，不实施 Tail-Only。

本文件记录整理时的事实，不是这些问题已修复的声明。
