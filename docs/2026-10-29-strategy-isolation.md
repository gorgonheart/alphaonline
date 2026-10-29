# 2026-10-29 策略与框架隔离审查

范围：在 `codex/live-offset-parity` 收敛 MODEL_ROBUST40 移植，兼容基准是 main=e6fc3bd。没有修改活动账户、部署服务器或运行交易。此前 4b0b5af 修改 7 个 core 文件、+50/−13；现在相对 main 为 **4 个文件、+27/−4**。

## 收敛内容

- 恢复 `core/model/account_config.py`、`core/model/strategy_config.py`、`core/rebalance.py`，保持原账户配置、父策略模型和调仓算法。
- core 只保留显式 hook 开关（real_trading）、执行器完整权益声明（position）、按配置实例传递与读取历史起点（backtest_config/datatools）。默认均沿用旧行为；core 无 MODEL 依赖。
- 新策略加载时附加注册独立执行器 `RebRobust40`，不覆盖内置类；价格检查与类型转换位于 `tools/robust40/live_execution.py`。新增名称在同一进程的模式列表可见，但只有显式选中它的账户才使用它。
- 旧共享 TopKTo2K、VolumeMaxBias 恢复 main。研究差异用独立 `TopKTo2KRobust40`、`VolumeMaxBiasRobust40` 名称，只更新 examples 中的 MODEL 配置和生成器。旧 TopK pandas/Polars 既有边界差异不在此次变更中修正。
- 修复 `importlib.reload(config)` 时 `required_history_start` 残留问题：每次先重置 None；模拟器配置原本就每次重新初始化。先复现失败，再补默认值并验证通过。当前 accounts 静态扫描没有这两个新增模块级配置，不会被新覆盖接口意外启用。

## 五个指定账户

读取方式为 AST 提取策略定义和公开订单参数，不执行凭据配置、不连接交易所。五个账户都是 HybridRotationDriftStrategy / 1H / 14 子策略，未启用新 hook、固定历史或 MODEL 执行器。

| 账户 | 分钟偏移 | 现货最小订单 | 合约最小订单 |
|---|---:|---:|---:|
| 5m-onheart0607.py | 5 | 100 | 10 |
| 15m-nheart0701.py | 15 | 100 | 50 |
| 20m_heart0723.py | 20 | 100 | 50 |
| 25m-eart0607.py | 25 | 100 | 50 |
| 45m_gonheart0607.py | 45 | 100 | 10 |

逐个加载父策略及 14 子策略配置，保留原再择时和子策略 RebAlways。另对五账户 × 三个原执行模式（RebAlways/RebByEquityRatio/RebByPositionRatio）× 三种合约净方向共 45 组，以相同合成持仓、价格、目标调用当前与主干 calc_target_position，结果精确相同；包含无目标旧持仓和不可交易目标。先注册 MODEL 后再对照，检查进程内并存的兼容性。订单门槛单独检查配置值，未模拟真实交易所拒单。

`tests/test_upgrade_231.py` 覆盖 0/5/15/20/25/45 分钟、dry_run/none 与 hook 开关组合，均为 mock 隔离流程；关闭开关时即使存在同名 hook 也不会触发。共享账户模型、父策略模型、三个原执行模式、两个共享因子及 Hybrid 源码 AST 与 main 一致。

现有 `accounts/20m_robust_4h.py` 是另一套旧草稿，引用 HybridRobust4HStrategy；该模块在 main 和本分支均不存在，这是本次之前已有的问题。不能将这份草稿视为已经可运行的 MODEL 账户。

## 验证与限制

- alpha311 全仓离线：**389 passed / 4 skipped / 129 warnings，99.14 秒**。跳过项需要缺失的真实 30m Arrow；warnings 为现有 pandas/Numba 等提示。日志 `research/strategy_isolation_final.log`。
- 覆盖既有 Arrow/PKL 回退、预计算缺列、entry_exit、再择时、交易路由、干跑和订单链路测试；不改 alpha 采集端。
- 再跑 `tools/verify_robust40_integration.py`：20/40/55m 各最近 2,000 根实际聚合目标最大误差为 0，末根目标手数不一致数为 0。8,013 根正式回测对照最大误差分别 6.55e-11、2.18e-11、2.91e-11。日志 `research/model_robust40_v1/isolation_integration.log`。
- 原 240h/24h 续算对照属于前次已完成验证；本次未改数学和续算引擎，没有重新训练或改写原基准。
- 这证明已覆盖路径的默认行为和同输入结果兼容，不等于所有未知策略、生产数据或实际成交已验证。尚需生产同源原始行情重算全部 14 子策略、供数及交易条件验收。Tail-Only 未启用。

重现：alpha311 运行 `pytest tests -q -p no:cacheprovider --basetemp research/pytest_strategy_isolation_<新目录>`；指定账户测试需要本地相应账户文件，测试不会导入其凭据。MODEL 真实数据集成测试需要已建立的三个 research bootstrap 和回测产物。
