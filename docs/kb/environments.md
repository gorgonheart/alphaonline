# 三套环境与 Conda / 进程拓扑

> 所属体系：[统一开发入口](../README.md) · 核对时间：`2026-11-19`（正式机、测试机 `ssh alphatest` 与本地均已现场只读核对）。

---

## 1. 环境总览对比

| 维度 | 本地开发机（Windows） | 测试服务器（Ubuntu） | 正式服务器（Ubuntu） |
| --- | --- | --- | --- |
| 主机 / 路径 | `D:/alphaonline`、`D:/alpha` | `ubuntu@43.173.122.28`：`/home/ubuntu/alphatrade`、`/home/ubuntu/alpha` | `ubuntu@43.167.235.164`：`/home/ubuntu/alphatrade`、`/home/ubuntu/alpha2`（另存独立旧实例 `alpha` / `alphatradetest`） |
| 交易端 Python | `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（Python `3.11.11`） | `/home/ubuntu/alphatrade-env/upgrade233/alpha311/bin/python`（独立 2.3.3 环境） | `/home/ubuntu/alphatrade-env/upgrade233/alpha311/bin/python`（Python `3.11.14`，独立 2.3.3 环境） |
| 生产者 Python | 同上 `alpha311` | `/home/ubuntu/miniconda3/envs/alpha311/bin/python`（Python `3.11.18`） | `/home/ubuntu/miniconda3/envs/alpha311/bin/python`（Python `3.11.14`） |
| 核心数值库 | pandas `2.2.3` / numpy `2.0.2` / polars `1.36.1` / pyarrow `17.0.0` / numba `0.60.0` / lightgbm `4.7.0` / narwhals `1.37.0` | 同左（数值库跨端锁定一致） | 同左（数值库跨端锁定一致） |
| 交易框架依赖 | 共享 `alpha311` 仍为 `xbx-py11==0.1.23`、`ccxt==4.5.53`（2.3.3 依赖仅在 `test/` 隔离验证过） | 独立 `upgrade233/alpha311`：`xbx-py11==0.1.24`、`ccxt==4.5.88` | 独立 `upgrade233/alpha311`：`xbx-py11==0.1.24`、`ccxt==4.5.88`；共享 `alpha311` 保持 `ccxt==4.5.53` |
| 运行模式 | 离线开发与测试为主 | `dry_run`（跳过下单/账户设置） | `alphatrade` 为 `none`（实盘）；`alphatradetest` 独立运行 |
| 代理 | 本地配置按需 | Mihomo `v1.19.33`（`AUTO` fallback 组：P1 江苏联通转日本BGP `212.87.193.53:65227` → P2 日本 NTT `216.247.110.57:65102` → P3 香港 BGP `45.207.152.141:65231`，出口均在白名单内，监听 `127.0.0.1:7897`）；`alpha` 默认 `proxy=null` | 无本地代理依赖（直连/按机器 `config.json`） |
| 安装与部署方式 | Git 主干开发仓库 | 非 Git 源码增量同步（`alpha` 保留历史导入标识，以逐文件审计为准） | Git 裸仓 fast-forward：`/home/ubuntu/repos/alphatrade.git`、`/home/ubuntu/repos/alpha.git`（稀疏检出排除根 `tests/`） |

---

## 2. 本地 Windows 环境详情

- **工作仓库**：
  - 交易端：`D:/alphaonline`（`main` 与 `production/main` 已完全同步至 `5cab8f8`（2026-11-27 09:30 正式机部署），0 未发布提交）
  - 生产者：`D:/alpha`（当前 `main` = `5694033`，与 `production/main` 同步）
- **Conda 环境列表**（本机仅使用 `alpha311`）：
  - `alpha311`（`C:\Users\Administrator\miniconda3\envs\alpha311`）：唯一指定开发/测试环境。
  - 其他历史环境（`alpha`、`alpha3`、`alpha311fordata`、`alpha_test` 等）：**不得用于本项目**（如旧 `alpha` 环境因 NumPy 版本差异在反序列化新 pickle 时报 `numpy._core` 错误）。
- **C 扩展编译要求（Windows）**：需 MSVC x64 与 Windows SDK（`vcvars64.bat` + `rc.exe` 在 `PATH` + `DISTUTILS_USE_SDK=1`），在 `D:/alpha` 根目录运行 `factors/c_ext/{build,momentum_v2_build,illq_build,cci_build}.py`。

---

## 3. 测试服务器详情（`43.173.122.28`）

- **SSH 免密连接**：已配置 `~/.ssh/config` 别名 `alphatest`（`HostName 43.173.122.28`、`User ubuntu`、`IdentityFile C:\Users\Administrator\.ssh\visibility-deploy.pem`），直接使用 `ssh alphatest` 或 `scp ... alphatest:...`。
- **硬件与 Swap**：2026-11-04 将 `/swap.img` 扩容至 **20 GiB**（活动 `21,474,832,384` 字节，权限 `600`；`2026-11-26` 起调优 `vm.swappiness=15` 并持久化至 `/etc/sysctl.d/99-alphatest-swappiness.conf`）。
- **服务托管**：PM2 管理 `alphatrade-dryrun`（使用 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`）与 `realtime_data`（使用 `/home/ubuntu/miniconda3/envs/alpha311`，Python `3.11.18`，Requests 间接依赖 `chardet==5.2.0` 固定以消除警告）。
- **配置特性**：
  - `shared_factor_pipeline=True`、32 币批次、4050 续算、`ARROW_DEMAND_ENABLED=True`（公共下限 5000）、`CHECKPOINT_POINTS=26`。
  - 市值代理开关 `coin_cap_use_proxy=True` 已在测试 `alpha` 私有 `config.json` 开启验证（2026-11-05）。
  - 2026-11-06 23:10–23:47 完成 2.3.3 主干对齐与 1h 损益修复部署；**尚未同步 2026-11-19 的 `edbbba3`（universe 扩张修复）**。
- **代理**：Mihomo `v1.19.33` 非 root systemd 服务常驻 `127.0.0.1:7897`，2026-11-21 11:23 起为 **`AUTO` fallback 组**（P1 江苏联通转日本BGP `212.87.193.53` → P2 日本NTT `216.247.110.57` → P3 香港BGP `45.207.152.141`，健康检查 10 s，实测 10–16 s 跳档）；同日曾试 65202，因该端口 TCP 稳定率仅 26/40 已弃用；详见 [网络代理与币安 IP 白名单速查](network-and-proxy.md)、[安装沿革](../2026-11-03-test-server-proxy.md)。

---

## 4. 正式服务器详情（`43.167.235.164`）

- **SSH 免密连接（严格只读优先）**：已配置 `~/.ssh/config` 别名 `alphaprod`（`HostName 43.167.235.164`、`User ubuntu`、`IdentityFile C:\Users\Administrator\.ssh\alphaprod-oldserveragent.pem`，源 `D:\alphaonline\env\oldserveragent.pem`），直接使用 `ssh alphaprod`。

### 4.1 现行运行实例与进程（2026-11-20 实测）

| 实例目录 | 角色 | Git HEAD | 进程 / PID | Python 环境 |
| --- | --- | --- | --- | --- |
| `/home/ubuntu/alphatrade` | **主实盘交易（MODEL 6 户）** | `5cab8f8` | `python /home/ubuntu/alphatrade/startup.py`（PID `374502`，2026-11-27 09:30:47 重启部署） | `/home/ubuntu/alphatrade-env/upgrade233/alpha311`（通过短名 `python` + 独立 `PATH` 启动） |
| `/home/ubuntu/alpha2` | **主行情生产者（供给 `alphatrade`）** | `5694033` | `python /home/ubuntu/alpha2/realtime_data.py`（PID `374475`，2026-11-27 09:30:45 重启，`v2.0.2` WS 4 核） | `/home/ubuntu/miniconda3/envs/alpha311` |
| `/home/ubuntu/alphatradetest` | 独立旧测试/并行交易实例（5 户） | 独立维护 | `python /home/ubuntu/alphatradetest/startup.py`（PID `3983440`） | 独立旧环境（部署主线时严禁误动） |
| `/home/ubuntu/alpha` | 独立旧生产者（供给旧实例） | 独立维护 | `python /home/ubuntu/alpha/realtime_data.py`（PID `3982664`） | 独立旧环境（部署主线时严禁误动） |
| `/home/ubuntu/alphatrade1` | 历史无 Git 归档目录 | 无 Git | 停用 | — |

### 4.2 正式机 Git 裸仓与部署注意事项

- 私有裸仓：`/home/ubuntu/repos/alphatrade.git`（消费者，当前 `main=5cab8f8`）、`/home/ubuntu/repos/alpha.git`（生产者，当前 `main=5694033`）。
- 生产工作区采用 **sparse checkout** 排除根 `tests/`；离线验收统一用 `tools/run_deployment_tests.py` 从已提交 Git 树在 `test/verify-*` 生成隔离副本运行。
- **PM2 绝对路径陷阱（2026-11-06 踩坑）**：PM2 的 `ForkMode.js` 用 `interpreter.includes('bun')` 判断是否启用 Bun 容器，而 `/home/ubuntu/...` 包含子串 `bun`，传入绝对路径会触发 `ProcessContainerForkBun.js` 语法错误。**必须**使用短名 `python` 并通过环境变量 `PATH` 指定 `/home/ubuntu/alphatrade-env/upgrade233/alpha311/bin`。
- **正式机本地未提交机器配置差异**（升级/pull 时必须保留）：`config.py`、`tools/robust40/initialization_config.py`（`data_pipeline=True`、`factor_symbol_batch_size=32`、`ARROW_DEMAND_ENABLED=True`、`dynamic_burst_enabled=True`）、`tools/deploy_alphatrade.sh` 及 `accounts/*.py`。

---

## 5. 相关专题文档

- [2026-11-06-main-consolidation.md](../2026-11-06-main-consolidation.md)（双线 2.3.3 切换、PM2/Bun 事故与全文件审计）
- [network-and-proxy.md](network-and-proxy.md)（测试机代理节点 ↔ 币安出口 IP 白名单速查与排障 SOP）
- [2026-11-03-test-server-proxy.md](../2026-11-03-test-server-proxy.md)（测试机 Mihomo 代理安装沿革）
- [2026-11-04-test-server-swap.md](../2026-11-04-test-server-swap.md)（测试机 20GiB swap）
- [D:/alpha/docs/DEPLOYMENT.md](../../../alpha/docs/DEPLOYMENT.md)（生产者 Linux 部署与 C 扩展编译）
