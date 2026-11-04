# 2026-11-03 测试服务器原始数据迁移

## 范围

从 `ubuntu@43.167.235.164:/home/ubuntu/alpha2/data/` 向 `ubuntu@43.173.122.28:/home/ubuntu/alpha/data/` 复制以下同名目录：

- binance_spot_5m
- binance_swap_5m
- coin_cap
- funding

首次源端审计约5.0GiB/6.4GiB/116MiB/60MiB，分别8327/10770/1817/1890个文件；目标空目录、可用约158GiB，无alpha生产者。源端可用约4.8GiB，因此不创建源端整包/快照，不通过本地落盘中转。源端旧alpha仍运行，不操作其服务。

## 执行方式

迁移脚本本地 `research/sync_alpha_seed_data.sh`，测试服务器 `/home/ubuntu/alpha-deploy-logs/sync_alpha_seed_data.sh`；运行日志 `/home/ubuntu/alpha-deploy-logs/data-seed-20261103.log`。

脚本逐目录用rsync直接拉取，低CPU/IO优先级，16MiB/s传输上限，zstd一级压缩；支持partial-dir续传及delay-updates，不使用delete、inplace、ignore-existing或额外文件校验摘要。完成每个目录后进行大小/mtime dry-run，发现首轮差异再补一次；持续变化则报错，不能把失败当完成。不会请求交易所API或启动数据生产者。

服务器间通过本次临时SSH key认证。生产端authorized_keys仅增加强制`rrsync -ro /home/ubuntu/alpha2/data`且禁用转发的条目，源端rsync也以低优先级执行；生产私钥不离开本地。源主机公钥由已有可信SSH连接读取并固定，禁用未知主机自动接受。完成后删除本次授权和临时私钥，保留其他授权条目。脚本保留用于维护，重新执行需要重新准备受限临时授权。

本次只复制原始5m、市值和资金费，不复制任何账户凭据、Git历史、1h重采样、因子Arrow、交易snapshot或下单产物。目标启动仍按正常流程补最新行情、重建派生数据；原始历史不等于实盘同轮ready或策略状态。

## 验收

2026-11-03 15:30:02—15:56:45（UTC+8）完成，耗时26分43秒；脚本退出0，四目录分别VERIFIED，最终DATA_COPY_COMPLETE。

| 目录 | 源/目标文件数 | 源/目标总字节数 |
| --- | ---: | ---: |
| binance_spot_5m | 8327 | 5271053987 |
| binance_swap_5m | 10770 | 6751703765 |
| coin_cap | 1817 | 115235715 |
| funding | 1890 | 56218059 |
| 合计 | 22804 | 12194211526 |

每目录rsync大小/mtime差量检查无遗漏，源/目标文件数与总字节数相同。每类选一份PKL读取成功，未引入全量内容哈希或额外摘要；目标部分文件数0，可用约146GiB。此验收证明复制正常，不替代完整行情连续性或策略一致性测试。

完成后已从生产端authorized_keys准确移除本次1条临时授权，保留其他条目；测试机临时私钥、公钥和主机固定文件已删除。测试机无生产者/交易进程；未停启原服务器服务、未删除源数据或目标业务数据，无core/startup/realtime_data修改。
