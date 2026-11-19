# 各环境 `accounts/` 配置与差异矩阵

> 所属体系：[统一开发入口](../README.md) · 严格脱敏：仅记录账户名、偏移、策略类型、运行模式与公开阈值差异，不记录任何 API Key、Secret、Webhook 或私有覆盖细节。

---

## 1. 四套目录账户全表

| 环境与路径 | 账户文件 | 偏移分布 | 仓管策略 | 运行模式 | 关键参数差异 |
| --- | --- | --- | --- | --- | --- |
| **正式主实盘**<br>`43.167.235.164:/home/ubuntu/alphatrade/accounts/` | `5m-onheart1104.py`<br>`15m-nheart1104.py`<br>`25m-eart1106.py`<br>`35m_t1104.py`<br>`45m_gonheart1104.py`<br>`55m_o.rgonheart1104.py` | 6 户错峰<br>（`5/15/25/35/45/55m`，间隔 10 分钟） | 全部 `MODEL_ROBUST40_V1_25m`（14 子策略 + S3 + MODEL 仓管 + `RebRobust40`） | `none`（实盘） | 订单最小金额 **现货 15 / 合约 10**；各自独立 `data/snapshot/<account>_MODEL_ROBUST40_V1_25m`；`clean_start` 对 MODEL 账户保留有效快照；启用 `ARROW_DEMAND_ENABLED=True` |
| **正式独立旧实例**<br>`43.167.235.164:/home/ubuntu/alphatradetest/accounts/` | `10m_meiling1020.py`<br>`20m_heart1020.py`<br>`30m_meiling1020.py`<br>`40m_dad1020.py`<br>`50m_rgonheart1022.py` | 5 户错峰<br>（`10/20/30/40/50m`） | 传统框架策略（非主线 MODEL） | 独立运行 | 独立消费 `/home/ubuntu/alpha`，与主实盘 `/home/ubuntu/alphatrade` 完全隔离 |
| **测试服务器**<br>`43.173.122.28:/home/ubuntu/alphatrade/accounts/` | `25m-eart1106.py`（主干整合后单户；此前曾阶段性运行 5/15/25m MODEL + 45m 传统 `HybridRotationDriftStrategy`） | `25m` | `MODEL_ROBUST40_V1_25m` | `dry_run` | 订单最小金额 **现货 10 / 合约 5**；走本地 `127.0.0.1:7897` 代理；禁用发单、撤单、杠杆/持仓模式修改及自动补 BNB 写操作 |
| **本地 Git 仓库**<br>`D:/alphaonline/accounts/` | `20m_robust_4h.py`<br>`40m_robust_4h.py`<br>`55m_robust_4h.py`<br>（加下划线模板 `_模板.py`、`_55mBTC样例.py`） | `20/40/55m` | MODEL_ROBUST40 本地跟踪配置 | 本地按 `config.py` / `config.json` | 正式机通过工作区删除这 3 个跟踪文件并用未跟踪生产账户替代；`load_multi_accounts()` 自动跳过 `_` 开头模板 |

---

## 2. 关键行为差异与注意事项

1. **配置加载分层（后覆盖前）**：
   - `config.py` 默认值 → 当前账户文件 `accounts/<name>.py`（受 `allowed_attrs` 白名单约束） → 根目录 `config.json`（受 JSON 白名单约束） → 机器私有覆盖（如 `accounts.local.json`）。
2. **`clean_start=True` 的双轨语义**：
   - **传统账户**（如 `HybridRotationDriftStrategy`）：`startup.py` 启动时清空 `data/snapshot`，按冷启动重建。
   - **MODEL 账户**（启用 `server_init=True`）：避开 `startup.py` 默认全量清快照逻辑，由 `tools/robust40/server.py` 校验 `warmup.pkl` 元数据（`complete=True` + 配置/偏移/截止核对），仅在缺状态或超恢复范围时进入后台初始化队列。
3. **测试机与正式机不可直接比较自然权重**：
   - 正式 6 户（最小金额 15/10、实盘资金与真实成交、独立 `alpha2` 行情与 MODEL 状态）与测试 `25m`（最小金额 10/5、`dry_run`、独立 `alpha` 行情）即使功能源码逐字节一致，自然轮次权重和选币也会因资金规模、门槛、历史采集微小差异而不同（详见 [2026-11-04-four-coins-root-causes.md](../2026-11-04-four-coins-root-causes.md)）。
4. **单币每轮限额（2.3.3 新增）**：
   - 通过 `AccountConfig` 的单币参考名义金额限额配置，默认空字典 `{}`（关闭）；支持显式币种优先于 `*`、现货/合约独立；显式传 `None` 会被拒绝报错；目前正式与测试活动账户均保持默认关闭。
5. **BNB 自动补币与 `dry_run` 保护**：
   - 活动 MODEL 账户保留 `if_use_bnb_burn=True`、`buy_bnb_value=11`、`if_transfer_bnb=False`。
   - `dry_run` 模式下已严格禁止前置杠杆/持仓模式修改、自动补 BNB 与 dust 兑换（2026-11-04 补齐）。
6. **传统回归测试与活动账户解耦**：
   - 自 `7395fbc` / `8266c6d` 起，MODEL 与传统回归测试优先使用 `tests/fixtures/` 中冻结夹具，不再强依赖 `accounts/` 中恰好存在哪几个活动账户文件。
