# 正式服务器 alpha2 主干部署

2026-11-05（UTC+8），按用户要求将行情生产端 `D:/alpha` 当前已提交主干部署到 `ubuntu@43.167.235.164:/home/ubuntu/alpha2`。本次不是交易端 alphaonline 发布，不启动或重启服务。

## 版本与配置

- 部署前 HEAD：`8d9747bde3af33ecd3914c552490ecb0d78cbb67`。
- 部署后 HEAD：`d9c837199844587086bd780e6fcca3f5f5c27d04`，分支 main，受跟踪工作区干净；服务器私有 origin/main 同步。
- 使用仅包含服务器既有版本之后四个提交的增量 bundle，没有再次传输旧开发历史、未跟踪研究文件、机器凭据或 Windows 二进制。
- 服务器原有 `core/arrow_publish.py` 补丁经比较与新主干相同。配置原有 `publish_kline_num=0` 已由主干覆盖；原有仅 `25m` 和代理设置按有效优先级保存在 Git 忽略的 `config.json`。已有凭据、storage_path 及 JSON 优先级保留，权限 600。
- 实际配置：仅 `25m`、完整 Arrow 发布、数据目录 `/home/ubuntu/alpha2/data`。未清数据或更改其他 offset。

## 验证与维护

本地 alpha311 部署专项 111 项通过（10.01 秒）。服务器四个 C 扩展构建成功；update 在 Git 快进之前已载入旧版 Bash 函数，因此首次运行其原有 100 项测试（10.02 秒）。随后重新调用新版 `bash tools/deploy_alpha.sh verify`，包括新增 Arrow 用例共 111 项通过（10.57 秒）。`pip check` 无冲突，本次未安装或升级依赖。

最终确认 alpha2 没有生产者或计算 worker，旧 `/home/ubuntu/alpha` 主进程 PID 1366912 保持。没有启动、重启、下单或修改交易端。构建仍有既有 setuptools 配置弃用提示和 CCI 编译器警告，不影响本轮构建与测试；不作为实时轮次运行验收。

- 部署前私有备份：`/home/ubuntu/alpha-deploy-backups/alpha2-main-20261104T161056Z`（名称为 UTC 时间）。含原 config.py、config.json、Arrow 文件和 tracked patch，目录 700、文件 600。
- 最终测试日志：`/home/ubuntu/alpha-deploy-logs/alpha2-main-d9c8371-20261105-verify.log`。
- 后续服务器差异统一编辑 `config.json`；源码使用干净主干 fast-forward。启动或切换由单独明确指令决定，不能同时写同一数据目录。
- 若需回退，先确保 alpha2 停止，切至 `8d9747b` 后重新构建与验证；该旧版本不原生包含本次接管的完整历史 Arrow 补丁，需要同时恢复上述备份，不能仅回退提交就视为行为恢复。

本次没有新增业务代码修改；本地其他未跟踪文件保留。
