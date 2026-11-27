# PR-08：Binance 客户端跨进程磁盘缓存（`bStocks` / 杠杆档位）与惰性杠杆精调

- **拟提交标题**：`perf(binance): add cross-process disk cache for bStocks/leverage brackets, tolerate dirty rows, and lazily skip swap account refresh`
- **优先级**：P1（极低风险、立竿见影减少 REST API 调用与 429 风险）
- **上游 `alpha` Arrow 依赖**：**无**
- **其他 PR 依赖**：**无**

---

## 1. 场景

`v2.4.1` 通过 `startup.py` 每小时为每个账户拉起全新的独立 `account_exec.py` 子进程（账户组甚至在同轮拉起 `--stage calc` 与 `--stage trade` 两个独立子进程）。子进程退出即销毁全部内存类级缓存，导致每个子进程都要重新请求低频变动的交易所元数据（`bStocks` 股票代币名单、`leverage_bracket` 合约杠杆档位），并在每次下单前强刷 `get_swap_account(require_update=True)`。

## 2. 目标

1. 为 `bStocks` 名单（12h TTL）与 `leverage_bracket` 杠杆档位（6h TTL）增加原子跨进程 JSON 磁盘缓存；
2. 修复 `bStocks` 解析对个别脏记录（`name=None` 或空 `coin`）过于脆弱导致整批失败的问题；
3. 优化 `v2.4.1` 的 `adjust_leverage_by_target_notional`：先在本地用缓存的 `brackets` 计算所有目标币种的理论所需杠杆，若全部目标仓位均安全落在当前期望上限 `max_leverage` 的档位内，直接跳过 `get_swap_account(require_update=True)` REST 请求。

## 3. 问题

对照 `position-mgmt-trading_v2.4.1/core/binance/base_client.py`：
1. `BinanceClient.bstocks_coins` 与 `self._leverage_bracket_cache` 仅存在于内存；由于每轮都是新子进程，12h 内存 TTL 实际完全失效，每小时重复拉取 `sapi_get_capital_config_getall`、`sapi_get_equity_market_tokenized_assets` 与 `papiGetUmLeverageBracket` / `fapiPrivateGetLeverageBracket`。
2. `adjust_leverage_by_target_notional`（L680）在遍历目标仓位前无条件调用 `self.get_swap_account(require_update=True)`，即使全部目标 notional 远小于 1 档容量上限、根本无需调杠杆，也会多发一次重型私有接口请求。

## 4. 项目文档中的相关分析与设计

- 实测与修复记录：[重要性能分析](../kb/performance-analysis.md)（§2 收口优化）、[DEVELOPMENT.md](../DEVELOPMENT.md)（2026-11-26 消费端收口）
- 参考提交：`5cab8f8`（`core/binance/base_client.py` + `tests/test_consumer_post1126_optimizations.py`）

## 5. 依赖

- **内部代码依赖**：无（全部改动集中在 `core/binance/base_client.py`，复用既有 `core/utils/path_kit.py::get_file_path`）。
- **上游 `alpha` Arrow 依赖**：**无**。
- **兼容性**：通过 `self._disk_cache_enabled = True` 控制；写盘采用 `f'{p.name}.{os.getpid()}.tmp'` + `os.replace`，多进程并发安全；缓存损坏或权限不可写时静默降级为原 API 请求。

## 6. 收益与证据

| 指标 | 优化前（每新子进程） | 优化后（命中缓存） | 收益 |
| :--- | :--- | :--- | :--- |
| `fetch_bstocks_coins`（2 个 SAPI 请求） | 每次冷启动均发网络请求（数百 ms ~ 秒级） | 12h 内读本地 JSON **`< 0.1 ms`** | 省 2 次 SAPI 请求；脏行容错后线上稳定过滤 `118` 个股票代币 |
| `get_swap_leverage_bracket` | 每次下单轮均拉全市场档位 | 6h 内读本地 JSON **`< 0.5 ms`** | 省 1 次全市场权重请求 |
| `adjust_leverage_by_target_notional` | 每次必刷 `get_swap_account(require_update=True)` | 常规小单（未触及 tier-1 `notionalCap * 0.9`）**0 次账户查询** | 下单前关键路径再省 1 次私有 REST RTT |

- **测试覆盖**：`tests/test_consumer_post1126_optimizations.py` 完整覆盖脏行跳过/全脏报错、跨进程 12h 缓存命中与过期降级保旧、6h 杠杆档位缓存与大小名义价值分支。

## 7. 代码改动

### 目标文件
- `core/binance/base_client.py`

### 核心改动要点
1. 新增静态方法 `_read_json_disk_cache(cache_path)` 与 `_write_json_disk_cache(cache_path, payload)`（同目录 PID 临时文件 + `os.replace` 原子写）。
2. `_parse_bstocks_coins`：跳过个别缺失/空置 `coin` 或 `name` 的脏行，但非空列表无任何有效行时仍抛 `ValueError`。
3. `fetch_bstocks_coins`：内存未命中时查 `data/bstocks_cache.json`（12h TTL）；拉取成功后回写磁盘；拉取失败时保留过期磁盘缓存成员防裸奔。
4. `get_swap_leverage_bracket(force_refresh=False, expire_seconds=3600*6)`：读写 `data/leverage_brackets_{api_name}.json`（6h TTL）。
5. `adjust_leverage_by_target_notional`：先遍历计算 `planned` 列表，仅当存在 `overflowed or new_lev < max_leverage or needed_lev < symbol_max_lev[sym]` 时才触发 `get_swap_account(require_update=True)` 并调杠杆。

## 8. 2c4g / 2c8g 主机风险与适配建议

| 主机规格 | 风险等级 | 说明与参数建议 |
| :--- | :---: | :--- |
| **4c8g** | 🟢 无风险 | 直接启用。 |
| **2c8g** | 🟢 无风险 | JSON 缓存文件体积仅数十~数百 KB，内存与磁盘占用几乎为零。 |
| **2c4g** | 🟢 **无风险（推荐必上）** | 降低网络等待与 JSON 反序列化开销，零内存压力；唯一前提是 `data/` 目录可写（已有业务依赖）。 |
