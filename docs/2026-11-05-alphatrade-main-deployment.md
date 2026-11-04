# 2026-11-05 正式alphatrade主干部署

用户明确要求将当前main部署到43.167.235.164:/home/ubuntu/alphatrade。本次源码基线9451bdd；已推送既有production SSH私有仓库，服务器工作副本main及索引同步。没有公开传输历史仓库；工作树由当前提交源码包安装。

部署前PM2 pmt-startup-alphatrade与alpha2均已stopped，部署后保持；其他运行实例没有停止或重启。三个约292天的旧account_exec进程cwd为已删除的alphatrade1安装，按现有stopped检查规则排除，本轮未操作这些进程。

服务器25m-eart1020.py账户、accounts.local.json已逐字节核对不变，config.json保持dry_run、alpha2数据路径及原代理/报警。保留原statistics_config和clean_start；未安装本地20/40/55m账户，也未改S3日期或补BNB。有效data/snapshot保持，没有部署测试snapshot或启动重建。

应用已验收功能配置：config.json shared_factor_pipeline=true；INITIALIZATION data_pipeline=True、factor_symbol_batch_size=32，其余历史长度/资源预算及MODEL_JOB_NUM保持当前主干。初始化与正常4050路径使用同套共享因子/截面/选币接口。

备份/home/ubuntu/alphatrade-backups/20261105-main-9451bdd：before保存被替换源码、private保存原账户与机器配置、candidate保存本次源码。rollback.sh已bash -n通过，仅供停止实例后人工回退源码与原config.json，不回退账户或snapshot、不启动服务。

依赖pip check与机器配置check均通过。完整Ubuntu离线pytest及最终状态见下方结果；日志verify.log位于上述备份目录。本次不验证真实成交。服务器仍使用机器账户和初始化功能覆盖项，git status中的账户删除/资源配置修改是有意差异，不能reset --hard或整体checkout消除。

最终验收：初轮完整Ubuntu套件1391通过、80跳过，47失败全部来自测试账户依赖（46项缺MODEL样本、1项传统同名账户误用）。补测试专用MODEL fixture、传统fixture优先读取后，六个全部失败文件复验433通过、17跳过、0失败（17.45秒）；包含所有47个原失败用例，未改变数值断言。计数分批列出，不把重叠用例相加。机器配置check、pip check通过，未启动业务。

追加提交7395fbc、8266c6d仅为测试移植修复，无业务源码变化。当前正式机部署main对应这两个后续提交；本记录会作为随后纯文档提交同步。保持dry_run、alpha2/alphatrade stopped；其他PM2实例PID不变。未新增采集监控或微信通知。
