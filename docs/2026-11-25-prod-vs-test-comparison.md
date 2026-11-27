# 2026-11-25 新测版本发布后：测试机 vs 正式机全项数据对比（含原始数据留底）

> **采集时间**：`2026-11-25 13:46–13:55 CST`（UTC `05:46–05:55`）主采集 + `14:05–14:12 CST` 25m 存储专项补采，全程 **SSH 只读**：未重启、未下单、未改配置、未触碰正式机并行旧实例。
> **对象**：测试机 `alphatest 43.173.122.28`（今晨 `10:57:53` 发布第二批 `f44d7ee..7a44299`，PM2 `realtime_data` PID `1066920`）vs 正式机 `alphaprod 43.167.235.164`（`alpha2 b5eb945` / `alphatrade 06c8b15`，未改动）。
> **可比窗口**：两机均有完整日志的 `2026-11-25 00:25 → 04:25 UTC` 共 5 个 `25m` 切点（消费端读上一小时 cutoff，对应 `09:25–13:25 CST` 轮次）；部署后暖态重点看 `03:25` / `04:25 UTC`。
> **原始数据留底**：`D:/alphaonline/docs/evidence/2026-11-25-prod-vs-test/`（下称 `evidence/`，共 **59 个文件**、含 7 个可复现脚本，索引见 `33-evidence-index.txt`，已于 `14:12` 刷新）。本报告所有表格数值均由其中原始输出逐项提取，未做平滑或剔除；异常轮单独标注。
> **时点说明**：`cutoff` / `run_time` 为日志内 UTC 字符串；文件 mtime、日志时间戳、资源采样均为 CST（UTC+8）。

---

## 0. 原始数据留底清单与采集方式

| 类别 | 原始文件（evidence/） | 内容 |
| --- | --- | --- |
| 首轮环境快照 | `00-first-probe.txt` | 两机 hostname / 时间 / uptime / CPU / free / df / PM2 全表 / 进程表（`13:46:09` 与 `13:46:19` 终端输出转录，数值原样） |
| 目录与日志定位 | `01-test-paths.txt`、`02-prod-paths.txt`、`03-test-versions.txt`、`04-prod-versions.txt`、`05`–`07`、`10`–`11` | 目录清单、git HEAD 与工作区状态、日志路径、日志格式样本 |
| 逐轮原始日志 | `12-test-replay-end-raw.txt`（5 行）、`13-prod-replay-end-raw.txt`（28 行） | 今日全部 `MODEL_REPLAY` 且 `"event":"end"` 的原始 JSON 行，一字未改 |
| 上游原始日志 | `14-test-upstream-raw.txt`（600 行）、`15-prod-upstream-raw.txt`（1411 行） | ready 文件 mtime、barrier / REST 兜底 / resample / 预处理 / io-stat / 采集耗时 / 错误计数 |
| 产物原始明细 | `16`、`17`（PKL schema）、`18-test-products.json`、`19-prod-products.json` | 三切点 `select_coin` / `position_results` 全字段摘要 + 最新切点 **3781 行逐行数据** |
| 结构化派生 | `20-replay-rounds.csv`（33 轮）、`21-upstream-rounds.csv`（139 周期）、`22-product-diff.txt`、`23-summary.txt`、`29-extra-analysis.txt` | 由上述原始日志机械解析，含逐轮明细与中位汇总，不含人工修饰值 |
| 补充采集 | `25-test-extras.txt`、`26-prod-extras.txt`、`27-test-release.txt`、`28-prod-error-and-files.txt` | 资源第二次采样、净值、换仓清单、存储占用、发布文件 mtime、错误日志分类计数 |
| 收尾快照 | `31-final-snapshot.txt`（正式机）、`32-test-final-snapshot.txt`（测试机） | `13:55` 精确原始输出（PM2 表未做转录） |
| **25m 存储专项补采** | `34`–`49`、`53`（两机全量 `du -s -B1` / `du -sh`、arrow 文件明细、Arrow footer 元数据、今日新增文件聚合、25m 目录精确清单）；`50-25m-storage-diff.csv`、`51-25m-storage-conclusion.txt` | `14:05–14:12` 按 25m 口径逐目录精确字节对拍，回答「5m K 线减小后其他文件夹是否增大」 |
| 可复现脚本 | `collect_products.py`、`analyze.py`、`extra_analysis.py`、`storage_growth.py`、`arrow_meta.py`、`storage_diff.py`、`storage_diff_25m.py` | 远端 stdin 只读采集、Arrow footer 只读解析、本地解析与对拍，全部留底 |
| 运行记录 | `24-analyze-console.txt`、`30-extra-console.txt`、`52-25m-console.txt`、`33-evidence-index.txt` | 三个分析脚本的完整控制台输出、文件大小/行数索引（已刷新至 59 个文件） |

**主要采集命令（两机对称执行，均只读）**：

```bash
# 环境与版本
ssh alphatest 'hostname; date; uptime; free -m; df -h /; pm2 list --no-color; ps -eo pid,ppid,etime,pcpu,pmem,cmd | grep realtime_data'
ssh alphaprod  'git -C /home/ubuntu/alphatrade rev-parse --short HEAD; git -C /home/ubuntu/alpha2 rev-parse --short HEAD; git status --short'
# 逐轮性能（原始 JSON 行）
ssh alphatest 'grep -a "MODEL_REPLAY" /home/ubuntu/alphatrade/reports/startup.log | grep -a "\"cutoff\": \"2026-11-25" | grep -a "\"event\": \"end\""'
ssh alphaprod  'grep -a "MODEL_REPLAY" /home/ubuntu/alphatrade/logs/server_robust40.log | grep -a "\"cutoff\": \"2026-11-25" | grep -a "\"event\": \"end\""'
# 上游就绪（原始行）
ssh alphatest 'find /home/ubuntu/alpha/data/preprocess_1h_resample/25m -name "*.ready" -printf "%f\t%TY-%Tm-%Td %TH:%TM:%TS\n"; grep -a -E "barrier\[|REST 兜底|Fast Path|io-stat|预处理|Resample" /home/ubuntu/.pm2/logs/realtime-data-out.log'
ssh alphaprod  'find /home/ubuntu/alpha2/data/preprocess_1h_resample/25m -name "*.ready" -printf "%f\t%TY-%Tm-%Td %TH:%TM:%TS\n"; grep -a -E "Binance|预处理|io-stat|合并币安" /home/ubuntu/.pm2/logs/alpha2-out.log'
# 产物内容级对拍（远端 stdin，不落盘）
ssh alphatest '/home/ubuntu/alphatrade-env/upgrade233/alpha311/bin/python - /home/ubuntu/alphatrade 11 12 13' < collect_products.py
# 25m 存储专项补采（14:05–14:12，两机对称；17 个目录的完整逐行清单见 48 / 49 的 [T] [U] 段，另用 find -type f / -name "*.ready" 计数）
ssh alphatest 'du -s -B1 /home/ubuntu/alpha/data/preprocess_1h_resample/25m /home/ubuntu/alpha/data/binance_*_1h_resample/25m /home/ubuntu/alpha/data/data_api_*_1h_resample/25m /home/ubuntu/alpha/data/binance_*_5m /home/ubuntu/alpha/data/ws_cache /home/ubuntu/alphatrade/data/25m-eart1106 /home/ubuntu/alphatrade/data/snapshot/25m-eart1106* /home/ubuntu/alphatrade/data/子策略回测结果/25m-eart1106* /home/ubuntu/alphatrade/data/server_initialization/25m-eart1106'
ssh alphaprod  'du -s -B1 /home/ubuntu/alpha2/data/preprocess_1h_resample/25m /home/ubuntu/alpha2/data/binance_*_1h_resample/25m /home/ubuntu/alpha2/data/data_api_*_1h_resample/25m /home/ubuntu/alpha2/data/binance_*_5m /home/ubuntu/alphatrade/data/25m-eart1106 /home/ubuntu/alphatrade/data/snapshot/25m-eart1106* /home/ubuntu/alphatrade/data/子策略回测结果/25m-eart1106* /home/ubuntu/alphatrade/data/server_initialization/25m-eart1106'
# 25m Arrow footer 只读解析（证明产物未膨胀，脚本为 arrow_meta.py）
ssh alphatest '/home/ubuntu/alphatrade-env/upgrade233/alpha311/bin/python - /home/ubuntu/alpha/data/preprocess_1h_resample/25m/spot_dict_1795584300.arrow' < arrow_meta.py
```

---

## 1. 版本、进程与运行模式（原始值）

| 项目 | 测试机 `43.173.122.28` | 正式机 `43.167.235.164` | 证据 |
| --- | --- | --- | --- |
| 主机 / CPU | `VM-0-8-ubuntu`，`Xeon E5-26xx v4` ×4 | `VM-0-15-ubuntu`，`Xeon Platinum 8255C @2.50GHz` ×4 | `00`、`31`、`32` |
| 交易端 | 非 Git 目录（`fatal: not a git repository`），基线 `2001075` + 2B 文件增量 | Git HEAD **`06c8b15`**（`M config.py`、删除 3 个跟踪账户、6 个未跟踪实盘账户） | `03`、`04` |
| 生产者 | Git HEAD **`7790bc1`** + 大量工作区修改（今晨同步的 2B `v2.0.2` 文件） | `alpha2` Git HEAD **`b5eb945`**（仅 `M tools/deploy_alpha.sh`） | `03`、`04` |
| 今晨发布文件 mtime | `10:53:08` barrier.py / `10:53:14` resample.py / `10:53:19` update_cycle.py / `10:53:27` writer.py / `10:53:32` preprocess.py / `10:53:36` ws_local_cache.py；`config.py` = `00:28:41`（第一批） | 核心文件 `2026-11-23 12:57:22` / `2026-11-06 15:09:55`，**今晨零改动** | `27`、`28` |
| 重启时刻 | PM2 `realtime_data` PID **1066920**，`10:57:52` error log mtime，`13:46` uptime `2h48m` | `alpha2` PID 3809122（2D）、`startup.py` PID 464274（18D），另有并行旧实例 `alpha` 3982664 / `alphatradetest` 3983440（未触碰） | `00`、`27`、`31`、`32` |
| 账户 / 模式 | 单户 `25m-eart1106`，`dry_run`，最小金额 现货 10 / 合约 5（历史起点 `2025-04-16`） | 6 户 MODEL 10 分钟错峰，`none` 实盘，最小金额 现货 15 / 合约 10 | `docs/README.md` §4、`00` |
| 采集链路 | WS + `127.0.0.1:7897` 代理，`num_writers=4`、`ws_wait_sec=14.0` | 纯 REST + `3.0 s` 延后 | `14`、`15`、`docs/DEVELOPMENT.md` |

> 发布备份目录 `/home/ubuntu/alpha/test/release-compute-io-overlap-20261125/` 在本次 `find -maxdepth 2` 下**未检出文件**（`27` 该文件对应段落为空）；发布事实以上述 6 个核心文件 mtime、`00:28` config.py 与 `10:57:52` 重启日志为准，未据此推断备份完整性。

---

## 2. 消费端逐轮性能（全部原始轮次，单位秒）

### 2.1 同账户 `25m-eart1106`、同一切点，5 个共同 cutoff（UTC）

| cutoff | 测试 mode / status | 正式 mode / status | `total_s` 测试 / 正式 / 差 | `prepare_s` 测试 / 正式 | `account_calc` 测试 / 正式 | `input_read` 测试 / 正式 | `changed_series` 测试 / 正式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `00:25` | incremental / ok | replay / ok | **118.870 / 143.768 / −24.898** | 4.112 / 23.120 | 109.504 / 102.807 | 2.323 / 6.244 | 0 / 39 |
| `01:25` | replay / ok | replay / ok | 140.248 / 146.836 / −6.588 | 20.352 / 29.831 | 114.094 / 99.235 | 3.282 / 11.654 | 3 / 49 |
| `02:25` ⚠ | replay / ok | replay / ok | **193.731 / 128.547 / +65.184** | 26.914 / 23.385 | 160.178 / 87.401 | 4.234 / 6.248 | **390 / 84** |
| `03:25`（发布后冷首轮） | incremental / ok | replay / ok | 138.140 / 131.458 / +6.682 | 8.768 / 23.316 | 118.986 / 87.553 | 6.780 / 7.238 | 0 / 23 |
| `04:25`（暖态） | incremental / ok | replay / ok | **120.178 / 128.545 / −8.367（−6.5%）** | **6.219 / 23.459（−73.5%）** | 106.628 / 87.378 | 4.271 / 7.031 | 0 / 43 |
| 中位（5 轮全量） | 3 incremental / 2 replay | 5 replay | 138.140 / 131.458 | 8.768 / 23.385 | 114.094 / 87.553 | 4.234 / 7.031 | 0 / 43 |
| 中位（剔除 `02:25` 异常轮，4 轮） | — | — | **129.159 / 137.613 / −8.454（−6.1%）** | 7.494 / 23.387 | 111.799 / 93.394 | 3.776 / 7.135 | 0 / 41 |

⚠ `02:25` 原始轮详情（`29-extra-analysis.txt`）：`replay_hours=7.0`、`revised_from=2026-11-24 20:25:00`、`changed_cells=3407`、`changed_series=390`、`calculation_s=190.415`、`total_s=193.731`。属 7 小时长回放的非稳态轮（疑与 `00:32` 首批重启后修订回填相关），**未剔除，单独标注**；上表同时给出剔除后的口径。

### 2.2 正式机 6 户今日全部 28 轮原始值（`20-replay-rounds.csv` 全量）

| cutoff (UTC) | 账户 | mode | `total_s` | `account_calc` | `prepare_s` | `input_read_s` | `changed_cells` | `changed_series` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 00:05 | 5m-onheart1104 | replay | 153.558 | 102.650 | 27.225 | 10.399 | 14 | 3 |
| 00:15 | 15m-nheart1104 | replay | 141.078 | 99.300 | 29.239 | 11.679 | 257 | 44 |
| 00:25 | 25m-eart1106 | replay | 143.768 | 102.807 | 23.120 | 6.244 | 212 | 39 |
| 00:35 | 35m_t1104 | replay | 150.477 | 101.832 | 28.603 | 11.352 | 198 | 37 |
| 00:45 | 45m_gonheart1104 | replay | 148.974 | 101.885 | 22.666 | 7.028 | 456 | 80 |
| 00:55 | 55m_o.rgonheart1104 | replay | 147.879 | 102.919 | 22.969 | 6.151 | 110 | 21 |
| 01:05 | 5m-onheart1104 | replay | 154.935 | 101.997 | 29.028 | 10.862 | 1533 | 259 |
| 01:15 | 15m-nheart1104 | replay | 145.203 | 103.441 | 29.206 | 11.086 | 421 | 70 |
| 01:25 | 25m-eart1106 | replay | 146.836 | 99.235 | 29.831 | 11.654 | 280 | 49 |
| 01:35 | 35m_t1104 | replay | 150.803 | 101.539 | 29.345 | 11.710 | 316 | 55 |
| 01:45 | 45m_gonheart1104 | replay | 143.905 | 100.797 | 23.092 | 7.270 | 255 | 50 |
| 01:55 | 55m_o.rgonheart1104 | replay | 145.906 | 102.314 | 21.802 | 4.957 | 252 | 47 |
| 02:05 | 5m-onheart1104 | replay | 129.722 | 87.752 | 22.352 | 5.719 | 560 | 95 |
| 02:15 | 15m-nheart1104 | replay | 125.859 | 88.856 | 23.935 | 7.564 | 542 | 89 |
| 02:25 | 25m-eart1106 | replay | 128.547 | 87.401 | 23.385 | 6.248 | 463 | 84 |
| 02:35 | 35m_t1104 | replay | 141.009 | 93.861 | 29.334 | 12.419 | 332 | 63 |
| 02:45 | 45m_gonheart1104 | replay | 133.237 | 89.206 | 23.670 | 7.062 | 303 | 56 |
| 02:55 | 55m_o.rgonheart1104 | replay | 133.775 | 87.770 | 23.826 | 6.905 | 210 | 41 |
| 03:05 | 5m-onheart1104 | replay | 131.016 | 87.931 | 23.061 | 6.302 | 495 | 92 |
| 03:15 | 15m-nheart1104 | replay | 134.097 | 89.182 | 29.754 | 11.542 | 402 | 79 |
| 03:25 | 25m-eart1106 | replay | 131.458 | 87.553 | 23.316 | 7.238 | 116 | 23 |
| 03:35 | 35m_t1104 | replay | 129.297 | 86.612 | 24.295 | 7.567 | 176 | 32 |
| 03:45 | 45m_gonheart1104 | replay | 130.464 | 87.362 | 22.739 | 6.987 | 129 | 24 |
| 03:55 | 55m_o.rgonheart1104 | replay | 130.675 | 87.566 | 22.890 | 6.570 | 66 | 15 |
| 04:05 | 5m-onheart1104 | replay | 139.353 | 93.776 | 26.143 | 10.019 | 113 | 24 |
| 04:15 | 15m-nheart1104 | replay | 132.151 | 90.465 | 28.962 | 11.372 | 346 | 59 |
| 04:25 | 25m-eart1106 | replay | 128.545 | 87.378 | 23.459 | 7.031 | 224 | 43 |
| 04:35 | 35m_t1104 | replay | 136.458 | 88.407 | 28.169 | 10.157 | 293 | 56 |
| **中位** | 28/28 `replay`，`status=ok`、`committed=true` | — | **137.910** | **92.120** | **23.880** | **7.420** | — | **49.5**（范围 3–259） |

**关键事实**：正式机今日 **28/28 轮全部 `replay`**（修订面中位 49.5 个 series）；测试机 5 轮中 3 轮 `incremental`（`changed_series=0`），部署后的 `03:25` / `04:25` 连续两轮零修订。`incremental` 轮不打印 `changed_cells` / `changed_series` / `copy_s` / `restore_s`，故原值为空，本报告按 0 记。

---

## 3. 上游采集与就绪延迟（部署后逐轮原始值）

### 3.1 `25m` 主交易偏移，`.ready` 相对 `XX:25:00` 的延迟（秒）

| run_time (CST) | 测试 spot / swap `.ready` | 正式 spot / swap `.ready` | 测试 barrier spot / swap (s) | 测试 missed spot / swap | 测试 market_dict spot / swap (s) | 正式 market_dict spot / swap (s) | 正式 REST 采集 spot / swap (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `11:25`（冷首轮） | **60 / 82** | 82 / 104 | 1.08 / 1.99 | 0 / 0 | 30.0 / 51.3 | 38.4 / 62.2 | 19.6 / 24.1 |
| `12:25` | **43 / 71** | 95 / 115 | 1.12 / 2.26 | 0 / 0 | 17.9 / 43.7 | 41.0 / 68.4 | 19.6 / 23.9 |
| `13:25` | **55 / 74** | 97 / 113 | 1.14 / 2.40 | 0 / 0 | 29.4 / 46.4 | 42.3 / 68.1 | 19.6 / 23.7 |
| **中位** | **55 / 74** | **95 / 113** | **1.12 / 2.26** | **0 / 0** | **29.4 / 46.4** | **41.0 / 68.1** | **19.6 / 23.9** |

原始 `.ready` 文件 mtime（`14` / `15` 首段）：
- 测试机 `13:25` 轮：`spot_dict_1795584300.ready = 2026-11-25 13:25:55.886`、`swap_dict_1795584300.ready = 13:26:14.403`、`market_pivot_spot_2026_1795584300.ready = 13:25:55.871`、`market_pivot_swap_2026_1795584300.ready = 13:26:14.383`；另有 batch 级 ready 三轮（`11:26`、`12:25`、`13:25`）。
- 正式机 `13:25` 轮：`spot_dict_1795584300.ready = 13:26:37.310`、`swap_dict_1795584300.ready = 13:26:53.592`、`market_pivot_spot = 13:26:37.276`、`market_pivot_swap = 13:26:53.537`。

### 3.2 部署后全部 11 个上游周期（测试机原始逐轮）

| run_time (CST) | barrier spot / swap (s) | missed spot / swap | resample spot / swap 就绪 (s) | market_dict spot / swap (s) | spot / swap `.ready` (s) |
| --- | --- | --- | --- | --- | --- |
| 11:15 | 1.05 / 1.79 | 0 / 0 | 16 / 24 | 41.5 / 103.1 | 76 / 158 |
| 11:25 | 1.08 / 1.99 | 0 / 0 | 16 / 25 | 30.0 / 51.3 | 60 / 82 |
| 11:45 | 1.31 / 2.54 | 0 / 0 | 18 / 26 | 51.2 / 70.0 | 127 / 113 |
| 12:05 | 0.89 / 2.00 | 0 / 0 | 15 / 24 | 41.4 / 81.8 | 91 / 133 |
| 12:15 | 1.10 / 2.41 | 0 / 0 | 14 / 22 | 46.0 / 61.7 | 114 / 97 |
| 12:25 | 1.12 / 2.26 | 0 / 0 | 13 / 21 | 17.9 / 43.7 | 43 / 71 |
| 12:45 | 1.12 / 2.25 | 0 / 0 | 15 / 21 | 73.7 / 71.0 | 104 / 152 |
| 13:05 | 1.12 / 2.34 | 0 / 0 | 14 / 20 | 50.7 / 91.4 | 80 / 138 |
| 13:15 | 1.21 / 2.40 | 0 / 0 | 13 / 21 | 27.7 / 77.5 | 59 / 113 |
| 13:25 | 1.14 / 2.40 | 0 / 0 | 14 / 21 | 29.4 / 46.4 | 55 / 74 |
| 13:45 | 1.19 / 2.31 | 0 / 0 | 14 / 20 | 29.1 / 59.1 | 59 / 91 |
| **中位** | **1.12 / 2.31** | **0 / 0（11/11 轮）** | 14 / 21 | 41.4 / 70.0 | 76 / 113 |

**对照**：测试机今日全量 56 个周期中 `missed_swap=0` 仅 29/56；`11:00` 后 11/11 轮 spot/swap 全零，barrier 由 `6.51 / 13.21 s` 降至 `1.12 / 2.31 s` —— 今晨「WAL 直通屏障」修复已在线上兑现。正式机无 barrier / missed 概念（纯 REST），`14` / `15` 中该字段为空。

### 3.3 按偏移分组（`11:00` 后，swap `.ready` 秒，中位）

| 偏移 | 测试机 | 正式机 |
| --- | --- | --- |
| `05m` | 135.5（133, 138） | 102.0（102, 102, 100） |
| `15m` | 113.0（158, 97, 113） | 103.0（114, 103, 103） |
| **`25m`** | **74.0（82, 71, 74）** | **113.0（104, 115, 113）** |
| `35m` | 无周期记录 | 106.0（106, 115, 101） |
| `45m` | 113.0（113, 152, 91） | 115.0（115, 116, 104） |
| `55m` | 无周期记录 | 109.5（106, 113） |

> 测试机 `11:00` 后仅在 `05/15/25/45` 四个偏移产生 pivot/ready 记录（`29-extra-analysis.txt`），`35m/55m` 无记录，符合单户按需发布设计；正式机 6 户错峰覆盖全部 6 个偏移、17 个周期。**新版提速集中在 `25m` 主交易偏移**，非 25m 偏移因写全历史 Arrow 仍有 `113–158 s` 波动，不构成全面提速。

### 3.4 错误与稳定性

| 指标 | 测试机 | 正式机 |
| --- | --- | --- |
| 交易端日志 `[ERROR]/Traceback/Exception` 计数 | **0**（`reports/startup.log`） | **0**（`logs/server_robust40.log`） |
| 上游 out 日志错误计数 | **0**（`realtime-data-out.log`） | **0**（`alpha2-out.log`） |
| PM2 error log | 211 行 / 10 `KeyboardInterrupt` / 10 `Traceback` / 0 `[ERROR]`，文件 mtime **`10:57:52`**（重启时子进程退出栈） | `alphatrade-error.log` 166 行 / 83 `FutureWarning: 'H' is deprecated` / 0 `Traceback`；`alpha2-error.log` **0 字节** |
| OOM | `/var/log/kern.log` 不可读，未验证 | 同左 |

---

## 4. 产物数据一致性（`11` / `12` / `13` 三个切点，内容级对拍）

### 4.1 `select_coin` 原始摘要（`18` / `19` JSON）

| 切点 | shape 测试 / 正式 | 分组数 测试 / 正式 | `Σcap_weight` 测试 / 正式 | 仅测试组 | 仅正式组 | 数值差异组 | 父策略差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `_11` | 3777×15 / 3778×15 | 257 / 258 | 1308.79166667 / 1309.04166667 | 0 | 1 | 2 | 2 |
| `_12` | 3788×15 / 3789×15 | 255 / 256 | 1313.375 / 1313.625 | 0 | 1 | 2 | 2 |
| `_13` | 3781×15 / 3781×15 | 251 / 251 | 1313.29166667 / 1313.29166667 | 1 | 1 | 1 | 2 |

**逐条差异明细（全部）**：

| 切点 | 组 / 父策略 | 测试机 | 正式机 |
| --- | --- | --- | --- |
| `_11`/`_12` | `SHELLUSDT｜#3.线上实盘多头｜1｜1｜0` | **0 行** | 3 行 / 0.75 |
| `_11`/`_12` | `LISTAUSDT｜#3.线上实盘多头｜1｜1｜0` | 2 行 / 0.50 | 1 行 / 0.25 |
| `_13` | `LISTAUSDT｜#3.线上实盘多头｜1｜1｜0` | 1 行 / 0.25 | **0 行** |
| `_13` | `SHELLUSDT｜#3.线上实盘多头｜1｜1｜0` | **0 行** | 2 行 / 0.50 |
| `_11`/`_12`/`_13` | `COWUSDT｜#1.Funding0021NegativeDomain｜-1｜1｜0` | 24/23/22 行 | 23/22/21 行（测试恒多 1 行 / 0.25） |
| `_11`/`_12`/`_13` | `S5-动量实盘多头`（父策略） | 28/28/23 行，7.00/7.00/5.75 | 30/30/24 行，7.50/7.50/6.00 |
| `_11`/`_12`/`_13` | `S13-Funding0021NegativeDomain`（父策略） | 2611/2617/2615 行，652.75/654.25/653.75 | 2610/2616/2614 行，652.50/654.00/653.50 |

**`_13` 切点 3781 行逐行对拍**（`29-extra-analysis.txt`）：按 `(symbol, strategy, 方向, 是否交易, is_spot, offset, order_first, parent_name, cap_weight)` 配对 **3779 行匹配**；`close` **0/3779 差异**、`next_close` **0/3594 差异**；`target_alloc_ratio` 2858/3779 行有微小差（最大 `1.13657e-05`，中位 `1.651e-07`，来自资金规模差）。仅 4 行信号键差异：`COWUSDT`（S13）、`LISTAUSDT`（S5）仅在测试，`SHELLUSDT`（S5）2 行仅在正式。差异集中在 `#3.线上实盘多头`（依赖真实持仓）与历史起点相关的递归因子，**非新测版本代码差异**。

### 4.2 `position_results`（538 币种）

| 切点 | shape | 非零 测试 / 正式 | 多 / 空 / 平 | 方向符号差异 | `Σ｜target_lots｜` 测试 / 正式 |
| --- | --- | --- | --- | --- | --- |
| `_11` | 538×6 / 538×6 | 122 / 122 | 50 / 72 / 416（双方一致） | **0 / 538** | 3,321,029 / 3,975,096（−16.5%） |
| `_12` | 538×6 / 538×6 | 122 / 122 | 50 / 72 / 416（双方一致） | **0 / 538** | 3,252,974 / 3,882,659（−16.2%） |
| `_13` | 538×6 / 538×6 | 120 / 120 | 48 / 72 / 418（双方一致） | **0 / 538** | 3,282,024 / 3,889,513（−15.6%） |

规模差约 −16%（资金规模、最小下单量、历史起点），结构与方向 **100% 一致**，`target_lots` 全有限。

### 4.3 账户净值与换仓清单（`equity.csv` / `账户换仓信息/` 原始末行与行数）

`13:25 CST` 净值行原文（两机各 1 行，`25` / `26`）：
```
test: 2026-11-25 13:25:00,2026-11-25 13:30:32.770620,17715.44498149,7107.879646601925,12755.41459981,19863.294246411926,-9619.223167929998,55,69,0.18647546499873896,0.09512159302070598,binance_pm_actualEquity,USD,25
prod: 2026-11-25 13:25:00,2026-11-25 13:30:28.561220,17717.75489201,502.65870524699994,12756.69618545,13259.354890697,-9618.092896109998,53,69,0.05935782656493735,0.09498355577539445,binance_pm_actualEquity,USD,25
```
净值差仅 **2.31**（`17715.44` vs `17717.75`）；但多头现货 `7107.88` vs `502.66`、多头合计 `19863.29` vs `13259.35` 差异显著，属实盘/`dry_run` 资金分布不同。

换仓清单行数（含表头）：`_11` **74 / 29**、`_12` **79 / 31**、`_13` **77 / 26**（测试 / 正式），反映两机既有仓位不同，非信号不一致。

---

## 5. 资源、存储与进程水位（三次原始采样）

| 采样时刻 (CST) | 主机 | load 1/5/15 | Mem used / avail (MiB) | Swap used (MiB) | 根分区 |
| --- | --- | --- | --- | --- | --- |
| `13:46` | 测试 | 3.94 / 1.56 / 1.19 | 5924 / **1761** | 1727 | 120G/178G，**71%（余 51G）** |
| `13:46` | 正式 | 1.94 / 2.41 / 2.29 | 6335 / **1349** | 880 | 148G/178G，**87%（余 23G）** |
| `13:52` | 测试 | 0.39 / 0.87 / 1.02 | 3293 / 4392 | 1578 | 71% |
| `13:52` | 正式 | 3.85 / 2.53 / 2.31 | 5024 / 2660 | 1042 | 87% |
| `13:55` | 测试 | 0.61 / 0.69 / 0.92 | 3293 / 4392 | 1577 | 71% |
| `13:55` | 正式 | 1.66 / 2.31 / 2.27 | 1507 / **6177** | 1026 | 87% |

PM2 `realtime_data` 常驻内存：`13:46` **2.0 GB**（计算期）→ `13:55` **254.6 MB**（空闲，`31`/`32`），与新版 `pa.default_memory_pool().release_unused()` 生效一致。

存储占用原始值（`25` / `26`，`du -sh`）：

| 目录 | 测试机 `alpha` | 正式机 `alpha2` | 差 |
| --- | --- | --- | --- |
| `preprocess_1h_resample/25m` | 3.7 G | 3.8 G | 同量级 |
| `binance_spot_5m` | **407 M** | **5.3 G** | −92% |
| `binance_swap_5m` | **590 M** | **6.8 G** | −91% |
| `snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m` | 1.1 G | 1.1 G | 一致 |

> 5m 原始 K 线存储的大幅下降来自 WS `v2.0.2` 底座的「5m/1h 存储解耦 + 30 天保留」，是测试机磁盘 71% vs 正式机 87% 的主因之一。正式机根分区 87%（余 23 GiB）仍是 P0 风险项。

### 5.1 25m 口径专项：5m K 线减小后，其他数据文件夹是否增大（`14:05–14:12` 补采）

> **口径**：只按 `25m` 主链路统计。纳入 `preprocess_1h_resample/25m`、四个 `*_1h_resample/25m`、`25m-eart1106`（含四个子目录）、`snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m` 及 `.previous`、`子策略回测结果/25m-eart1106_MODEL_ROBUST40_V1_25m`、`server_initialization/25m-eart1106`；另单列**全偏移共享底座** `binance_spot_5m` / `binance_swap_5m` / `ws_cache`。**排除**其他偏移（`5m/15m/35m/45m/55m`）、正式机其余 5 个账户、`alphatrade/research`（`52 GiB`，文件全部为 `2026-11-04/11-06`，今日新增 `0`，见 `46`/`53`）、正式机停用旧实例 `/home/ubuntu/alpha`（`47 GiB`）。
> **原始输出**：`34`–`49`、`53`（两机对称 `du -sh` / `du -s -B1` / `find -printf` / Arrow footer 只读解析）；精确对拍 `50-25m-storage-diff.csv`、结论 `51-25m-storage-conclusion.txt`。单位 MiB，均为磁盘占用原始字节换算。

| 目录（25m 口径） | 测试机 | 正式机 | 差 | 差 % | 文件数 测/正 | `.ready` 测/正 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `preprocess_1h_resample/25m` | 3764.1 | 3869.3 | −105.2 | −2.72% | 43 / 22 | 25 / 4 |
| `binance_spot_1h_resample/25m` | 430.7 | 451.8 | −21.1 | −4.67% | 1046 / 1046 | 523 / 523 |
| `data_api_spot_1h_resample/25m` | 422.1 | 422.2 | −0.1 | −0.02% | 1030 / 1042 | 515 / 521 |
| `binance_swap_1h_resample/25m` | 559.6 | 580.8 | −21.2 | −3.64% | 1544 / 1544 | 772 / 772 |
| `data_api_swap_1h_resample/25m` | 542.8 | 542.1 | **+0.7** | +0.13% | 1520 / 1520 | 760 / 760 |
| `25m-eart1106`（含子目录） | 57.9 | 63.0 | −5.1 | −8.08% | 659 / 703 | 0 / 0 |
| ├ `账户信息` | 30.0 | 34.2 | −4.2 | −12.2% | 10 / 10 | — |
| ├ `select_coin` | 21.7 | 21.6 | **+0.04** | +0.16% | 72 / 72 | — |
| ├ `position_results` | 4.6 | 4.6 | 0 | 0% | 168 / 168 | — |
| └ `账户换仓信息` | 1.7 | 1.8 | −0.1 | −6.5% | 409 / 452 | — |
| `snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m` | 1028.8 | 1077.1 | −48.2 | −4.48% | 79 / 79 | — |
| `snapshot/25m-eart1106_MODEL_ROBUST40_V1_25m.previous` | 1028.7 | 1077.0 | −48.3 | −4.49% | 79 / 79 | — |
| `子策略回测结果/25m-eart1106_MODEL_ROBUST40_V1_25m` | 64.4 | 68.0 | −3.6 | −5.24% | 104 / 104 | — |
| `server_initialization/25m-eart1106` | 2.3 | 2.8 | −0.6 | −19.42% | 11 / 11 | — |
| **25m 专属小计** | **7901.5** | **8154.1** | **−252.6** | **−3.10%** | — | — |
| `ws_cache`（共享底座，正式机无） | **86.4** | 0 | **+86.4** | 新增 | 6 / 0 | — |
| `binance_spot_5m`（共享底座） | 406.2 | 5416.9 | −5010.7 | −92.50% | 2653 / 8335 | 520 / 0 |
| `binance_swap_5m`（共享底座） | 589.8 | 6951.8 | −6361.9 | −91.52% | 3898 / 10782 | 767 / 0 |
| **共享底座小计** | 1082.4 | 12368.7 | −11286.3 | −91.25% | — | — |
| **全部合计** | **8983.9** | **20522.8** | **−11538.9** | **−56.22%** | — | — |

**结论：25m 口径下没有实质增大的数据文件夹。** 唯一实质新增是 `ws_cache` `90,619,904 B / 86.4 MiB`：目录创建于 `11:05:59`（今早 `10:57:52` 重启之后），正式机不存在；内含 `spot.sqlite 34,271,232`、`swap.sqlite 51,068,928` 及 WAL/shm（`53`），属新 WS 底座的内存盘缓冲。其余两项「增大」是噪声级：`data_api_swap_1h_resample/25m +757,760 B (+0.13%)`、`25m-eart1106/select_coin +36,864 B (+0.16%)`；另多 21 个 `.ready` 标记（每个 15–17 字节，合计约 350 B）。

**25m 核心产物逐字节一致，可排除「K 线数据搬到别处」**：`spot_dict_1795584300.arrow 607,597,786`、`swap_dict_1795584300.arrow 908,924,202`，`market_pivot_spot_2026.pkl 98,376,405`、`market_pivot_swap_2026.pkl 193,407,919` 两机完全相同（`48`/`49` 的 `[U]` 段）；Arrow footer 显示两机 `25m` 都是 `520 / 767` 个 record batch、每批 `5000` 行（`42`/`43`）。测试机只是多留了逐轮 `.ready` 标记、`2025` 历史 pkl 略小（`56,712,803` vs `65,235,755`）、batch pkl 略小，因此 `preprocess_1h_resample/25m` 反而 **−105.2 MiB**。

> **口径提醒**：`binance_spot_5m` / `binance_swap_5m` / `ws_cache` 是**全偏移共享底座**，不专属 25m，无法按偏移分摊。严格只算 25m 专属目录，净变化 **−252.6 MiB（仍略减）**；把共享底座一并计入，5m K 线省下 `−11,372.6 MiB` 远大于新增 `86.4 MiB`，净节省 **−11,286.3 MiB ≈ 11.0 GiB**。两种口径都不存在「5m K 线减小但 25m 其他文件夹增大」。
>
> 全机口径下确实有增大项，但**均在 25m 之外**，按本次要求排除：`preprocess_1h_resample/{5m,15m,45m}` 各 `5.90 GiB` vs 正式机 `4.06 GiB`（Arrow 每批 `14,111` 行 vs `5,000` 行，见 `36`–`39`、`42`–`43`）、`funding111 49.9 MiB`、`alphatrade/research 52 GiB`（历史 11-04 快照，今日新增 0）。

---

## 6. 结论与待办

1. **新测版本上线的核心收益已兑现**：部署后 11 个上游周期 barrier `1.12 / 2.31 s`（spot/swap 中位）、`missed=0/0` 覆盖 **11/11 轮**，彻底消除假缺口 REST 兜底；`25m` 轮 `.ready` 由正式机 `95 / 113 s` 提前至 **55 / 74 s**（spot −40 s、swap −39 s），`12:25` 暖态轮甚至达 `43 / 71 s`。
2. **消费端零修订**：部署后 `03:25` / `04:25` 连续 `incremental`（0 修订），`prepare_s` 由正式机 `23.3–23.5 s` 降至 `8.77 / 6.22 s`（−62% / −73%），省掉 `copy_s + restore_s`。暖态 `04:25` 端到端 `total_s 120.18 vs 128.54`（**−8.37 s / −6.5%**）；剔除异常轮后 4 轮中位 `129.16 vs 137.61`（−8.45 s / −6.1%）。
3. **抵消项要说清**：测试机 CPU 较弱，`account_calculation_s` 暖态 `106.63 vs 87.38`（+19.25 s），冷首轮 `03:25` 达 `118.99`（+31.43 s，冷缓存/REST 校准），该轮 `total_s` 反超正式机 `+6.68 s`。端到端净收益来自 `prepare` 与收尾段，不是计算段更快。
4. **数据正确性**：三个切点 `position_results` **538/538 方向零差异**；`select_coin` 最新切点 3779 行匹配，`close` / `next_close` **零差异**，仅 4 行信号键差异（全部落在依赖实盘持仓的 `#3.线上实盘多头` 与历史起点敏感的 `S13`/`S5`），`target_alloc_ratio` 最大差 `1.14e-05`。**未发现新测版本引入的策略信号变化**。正式机今日 28/28 轮 `replay`（修订面中位 49.5）与测试机形成鲜明对照。
5. **未决与待办**：
   - ① `02:25 UTC` 测试机异常轮（7h 回放、`changed_series=390`、`total_s=193.73`）需定位，疑与 `00:32` 首批重启后的修订回填有关；建议先排除后重算稳态中位。
   - ② 非 `25m` 偏移 swap `.ready` 仍 `113–158 s`，且 `35m/55m` 在 11:00 后无发布记录，需确认是否符合按需发布预期，并评估是否要全覆盖提速。
   - ③ 观察窗口仅 3 个部署后 `25m` 轮（含 1 个冷首轮），需 ≥24h 暖态连续轮验证「零修订连续性 + `.ready` 稳定在 `~40–75 s`」。
   - ④ 正式机磁盘 **87%（余 23 GiB）** 持续增长，按生产磁盘策略处置（本次未做任何清理）。
   - ⑤ 测试机 PM2 error log 的 10 条 `KeyboardInterrupt` / 10 条 `Traceback` 集中在 mtime `10:57:52` 的该文件内，内容为重启时子进程退出栈（未逐条带时间戳，按文件 mtime 与栈内容判定）；正式机 83 条 `'H' is deprecated` FutureWarning 属既有告警，两机运行期 `[ERROR]` 计数均为 0。
   - ⑥ 若要严格证明 `select_coin` 的 4 行差异完全由账户状态造成，需在同一快照上喂两端重算（本次未做交叉重算）。
6. **25m 口径存储专项（`14:05–14:12` 补采，详见 §5.1）**：5m K 线缩小 `−11,372.6 MiB` 未被 25m 侧吃回；25m 专属目录净 `−252.6 MiB`（也略减），唯一实质新增是 `ws_cache 86.4 MiB`（今早 `11:05:59` 新建），另两项增大均 ≤ `+0.16%` 属噪声；25m 的 arrow / pivot 产物两机**逐字节一致**。非 25m 偏移 arrow 膨胀（每批 `14,111` 行 vs `5,000` 行）与 `research 52 GiB` 历史快照在本次口径外，但建议后续单独评估其每轮覆盖写的 IO 放大（不累积占用）。

---

## 附录：关键原始日志摘录（逐字）

```text
# 测试机 11:25 部署后首轮（14-test-upstream-raw.txt）
🌀 barrier[spot] 完成: success=520/520 skipped=0 耗时=1.08s
🌀 barrier[swap] 完成: success=767/767 skipped=0 耗时=1.99s
🌀 REST 兜底 grace(14.0s) 后计算 missed: spot=0 swap=0 (run_time=2026-11-25 11:25:00+08:00)

# 测试机 13:25 暖态轮（14-test-upstream-raw.txt）
✅ Resample spot 内存合成就绪: 520/520 symbol (后台待刷盘=520), 当前时间=2026-11-25 13:25:14+0800
✅ Resample swap 内存合成就绪: 767/767 symbol (后台待刷盘=767), 当前时间=2026-11-25 13:25:21+0800
✅ 预处理 Market Dict (含因子) spot 完成, 当前时间=2026-11-25 13:25:51+0800, 耗时 29.4 秒
✅ 预处理 Pivot Table spot 完成, 当前时间=2026-11-25 13:25:55+0800, 耗时 2.3 秒
🌀 [io-stat] phase spot symbols=520 batches=3 market_dict=29.4s publish=2.2s pivot=2.3s
✅ 预处理 Market Dict (含因子) swap 完成, 当前时间=2026-11-25 13:26:08+0800, 耗时 46.4 秒
🌀 [io-stat] phase swap symbols=767 batches=5 market_dict=46.4s publish=3.0s pivot=3.0s

# 正式机 13:25 轮（15-prod-upstream-raw.txt）
✅ Binance spot API, 获取 5m 成功, Resample 并更新 1h 成功, 耗时=19.6秒, 当前时间=2026-11-25 13:25:24+0800
✅ Binance swap API, 获取 5m 成功, Resample 并更新 1h 成功, 耗时=23.7秒, 当前时间=2026-11-25 13:25:28+0800
🌀 开始预处理 spot, 当前时间=2026-11-25 13:25:30+0800
🌀 开始预处理 swap, 当前时间=2026-11-25 13:25:30+0800
✅ 预处理 Market Dict (含因子) spot 完成, 当前时间=2026-11-25 13:26:12+0800, 耗时 42.3 秒
✅ 预处理 Pivot Table spot 完成, 当前时间=2026-11-25 13:26:37+0800, 耗时 8.8 秒
✅ 预处理 Market Dict (含因子) swap 完成, 当前时间=2026-11-25 13:26:38+0800, 耗时 68.1 秒
✅ 预处理 Pivot Table swap 完成, 当前时间=2026-11-25 13:26:53+0800, 耗时 9.1 秒
🌀 [io-stat] phase swap symbols=767 batches=5 market_dict=68.1s publish=5.8s pivot=9.1s

# 测试机暖态 04:25 UTC 轮（12-test-replay-end-raw.txt）
🌀 MODEL_REPLAY {"account": "25m-eart1106", "mode": "incremental", "status": "ok", "committed": true, "cutoff": "2026-11-25 04:25:00", "input_frames": 1294, "input_read_s": 4.270743, "compare_s": 1.021598, "prepare_s": 6.218728, "account_calculation_s": 106.628272, "account_flow_s": 108.589998, "total_s": 120.177573, "event": "end", "elapsed_s": 120.177611}

# 正式机同切点（13-prod-replay-end-raw.txt，完整原始行）
2026-11-25 13:29:06,416 [INFO] - MODEL_REPLAY {"account": "25m-eart1106", "stamp": 1795584300, "kind": "trade", "attempt": "217850-1795584417870093104", "mode": "replay", "committed": true, "trained_quarters": 0, "reused_quarters": 0, "scoring_calls": 0, "status": "ok", "offset": 25, "cutoff": "2026-11-25 04:25:00", "old_read_s": 0.496608, "input_start": "2026-11-18 04:25:00", "input_frames": 1294, "revision_arrow_spot_s": 5.075527, "revision_arrow_swap_s": 1.126656, "revision_pivot_s": 0.828072, "input_read_s": 7.031441, "changed_cells": 224, "changed_series": 43, "changed_fields": ["BABYUSDT", "BULLAUSDT", "CVXUSDT", "DODOXUSDT", "DOGEUSDT", "ENSOUSDT", "ERAUSDT", "EVAAUSDT", "FUSDT", "GUAUSDT", "ICNTUSDT", "INITUSDT", "INJUSDT", "IOTAUSDT", "LSKUSDT", "ONGUSDT", "OPGUSDT", "SAHARAUSDT", "SLXUSDT", "SQQQUSDT", "XLMUSDT", "XRPUSDT", "ZKPUSDT", "ZROUSDT", "close", "quote_volume", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "trade_num", "volume"], "compare_s": 1.092894, "revised_from": "2026-11-25 03:25:00", "copy_s": 6.566638, "restore_s": 8.056561, "restored_at": "2026-11-25 02:25:00", "replay_hours": 2.0, "prepare_s": 23.458595, "input_write_s": 0.313059, "calculation_s": 113.469793, "account_calculation_s": 87.377906, "account_flow_s": 102.392175, "total_s": 128.544727, "event": "end", "elapsed_s": 128.54476}
```

> 完整 33 条 `MODEL_REPLAY` 原始 JSON 行见 `12` / `13`，139 个上游周期逐轮数值见 `21-upstream-rounds.csv`，产物 3781 行逐行数据见 `18` / `19` JSON。
