# 2.3.3 测试环境部署

2026-11-06（UTC+8），按用户“测试并部署测试环境”的授权，将分支 `codex/upgrade-core-2.3.3` 的运行提交 `c49d6de` 增量发布至 `43.173.122.28:/home/ubuntu/alphatrade`。18:58:30 重启测试交易服务，18:59 复核正常启动。未部署生产、未合并主干、未修改上游行情服务。

## 验证与部署范围

远端本次涉及的四个业务文件、版本文件和部署依赖清单与 `2827d27` 基线一致。以远端现有源码为底，叠加已提交的升级文件，使用空账户、独立行情/缓存目录和 socket 禁网运行相关测试。Ubuntu 结果：**175 passed，0 failed，0 skipped，176 warnings，66.37 秒**。包含升级专项、bStocks 初始化、统计、时间戳重试、干跑、2.3.1 兼容、C→Arrow→消费者预计算回退、主链/Polars 对拍和执行器缓存。告警为现有 pandas/pyparsing 弃用提示。没有运行生产入口作为测试，也没有生成新 golden。

发布8个文件：`core/model/account_config.py`、`core/binance/base_client.py`、`core/binance/portfolio_margin_client.py`、`core/utils/exchange_hub.py`、`core/version.py`、`requirements-deploy.txt`、`requirements.txt`、`加密apikey.py`。本地 `config.py` 的改动仅为配置注释，远端保留机器原文件；未整体覆盖 core、账户或其他本地优化。没有给账户新增限额，未配置时仍不限制；限额语义仍为每轮参考名义金额，而非小时累计。

## 依赖与进程

从原消费者 alpha311 克隆新的独立环境 `/home/ubuntu/alphatrade-env/upgrade233/alpha311`，正常安装已提交的 `requirements-deploy.txt` 及传递依赖。`pip check` 返回 `No broken requirements found`；实际安装目录禁网导入验证确认 xbx-py11=0.1.24、ccxt=4.5.88，同步/异步客户端可导入，新名单接口存在，版本为2.3.3 / v2.3.3.20261030。

NumPy2.0.2、pandas2.2.3、Polars1.36.1、PyArrow17.0.0、Numba0.60.0、llvmlite0.43.0、scikit-learn1.5.2保持。按部署清单补装此前未安装的LightGBM4.7.0，并将threadpoolctl3.7.0/Pillow13.0.0对齐为3.6.0/11.0.0；网络依赖由pip正常解析，包含Linux uvloop。上述完整环境通过同一175项测试；未重编译C或宣称整轮性能不变。

仅将 `research/live25_restart_20261104/run-dryrun.sh` 的解释器路径切换到新环境，PM2原名称/工作目录/参数保留并执行pm2 save。原环境 `/home/ubuntu/alphatrade-env/alpha311` 保留用于回退；运行不依赖test目录。

| 项目 | 结果 |
|---|---|
| 测试交易服务 | alphatrade-dryrun，PID2066058→2583547，online |
| 测试行情服务 | realtime_data，PID1421306未变，online |
| 实际解释器 | /home/ubuntu/alphatrade-env/upgrade233/alpha311/bin/python3.11 |
| 活动账户 | 25m-eart1106.py，沿用远端当前账户，不恢复旧eart1020 |
| 实际模式 | dry_run，启动日志确认跳过杠杆/持仓/保证金模式修改 |

替换前确认交易父进程无活动子进程；停止该服务后，6个配置/账户/snapshot_time文件逐字节一致、2个warmup文件大小及mtime一致，安装和禁网导入后再次核对，再重启。未手动删除业务数据、修改快照或触发强制初始化。安装文件与已测试payload逐字节一致。

## 证据与回退

远端 `/home/ubuntu/alphatrade/test/upgrade233-deploy-20261106` 保存 `ubuntu-tests.log`、`install.log`、`clone.log`、候选、payload和部署/检查脚本；`runtime-before/`保存原文件及启动脚本，目录700、备份文件600。原版公开源码另存before-public.tar.gz。无额外checksum或校验注册表。

需要回退时，等待测试交易空闲并停止 `alphatrade-dryrun`，按相对路径恢复 `runtime-before/` 内文件（含启动脚本），恢复脚本执行权限，再重启该服务、pm2 save并核对旧解释器/模式。新增加密工具和requirements.txt不被旧业务引用，可保留；不触碰账户、数据或上游服务。此次未实际执行回退。

## 验收边界

已验证Ubuntu相关测试、新依赖、安装文件、实际版本/解释器、启动自检及调度等待。未等待下一25m账户完整自然干跑轮，不将启动成功或175项离线测试当作真实整轮验收；未验证真实成交、真实钱包响应或新名单接口返回内容。没有开启实盘或调整账户限额。生产环境维持原状态。
