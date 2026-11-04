# alphatrade Ubuntu部署、更新与回滚

## 2026-11-04 当前发布收尾（优先于下文历史记录）

本轮临时线上监控已清理，当前版本已完成离线验证，提交以本地main记录为准，未发布或重启生产。共享因子/截面/选币及GC/日期优化已在Ubuntu测试；下文“新流程尚未发布/尚未验收”均为对应历史时间状态。详细范围、开关、监控归档与当前服务器状态见[发布收尾](2026-11-04-release-readiness.md)。

**当前生产有旧逐文件修改和账户改名，不可直接套用下文首次安装或干净Git update示例。** 现有服务器25m账户、私有配置和有效状态需保留，不能用本地20/40/55账户覆盖。测试机已由用户切换none，正式机仍dry_run；本轮不改变模式。业务统计和必要MODEL日志保留。


本项目是交易消费者，与行情生产者alpha2独立部署。维护入口为本文件；策略/仓管参数仍分别维护在accounts，MODEL细节见[仓管指南](model-robust40-deployment.md)。部署及verify命令不启动startup，不连接交易所私有接口，不发送通知或下单。

## 当前目录与配置职责

| 项目 | 位置/职责 |
|---|---|
| 生产工作副本 | `/home/ubuntu/alphatrade`，分支main |
| SSH私有Git仓库 | `/home/ubuntu/repos/alphatrade.git`；服务器工作副本origin指向它 |
| 独立conda alpha311 | `/home/ubuntu/alphatrade-env/alpha311/bin/python`，不修改alpha/alpha2的运行环境 |
| 机器差异 | `config.json`，Git忽略，权限600；行情目录、代理、通知和运行模式 |
| 账户凭据 | `accounts.local.json`，Git忽略，权限600；按账户文件stem匹配，仅API/secret/passphrase/password及通知连接字段 |
| 策略参数 | `accounts/*.py`及shared_account_config.py，Git管理；分钟offset、仓管、子策略、S3日期、BNB设置等 |
| 运行产物 | 本安装的data/和logs/，Git忽略；不与其他交易实例共写 |
| 编译缓存 | `.numba_cache/<提交号>`，可写、Git忽略；与snapshot无关，不复制Windows缓存 |

当前公开运行账户为20/40/55m MODEL；传统策略回归保留在tests/fixtures，不参与运行账户扫描。下划线开头文件为样例，不参与账户扫描。private JSON不能覆盖策略和执行参数，避免再维护一份offset/杠杆配置。账户改名时同步修改private JSON的键，凭据更换不修改策略定义。

配置模板为config.example.json和accounts.local.example.json。新仓库的安全默认是dry_run、无代理、空凭据；机器JSON优先。Windows原有效凭据/代理已保存在本机被忽略文件；不能把Windows代理直接复制到Ubuntu。

Git历史含旧机器配置，仓库只作SSH私有存储，不发布公共远程。工作副本.git和服务器bare仓库仅ubuntu用户可读；私有JSON权限600。数据、日志、诊断research、账户归档、IDE配置及平台二进制不作为发布内容。

## 首次部署（维护者执行）

代码通过Git完整提交传递，不逐文件覆盖，也不复制data目录。独立环境可从已验证的alpha311克隆，再按本项目直接依赖安装；这些是安装步骤，不是每次更新自动执行：

```bash
conda create --yes --clone /home/ubuntu/miniconda3/envs/alpha311 \
  --prefix /home/ubuntu/alphatrade-env/alpha311
git clone --branch main /home/ubuntu/repos/alphatrade.git /home/ubuntu/alphatrade
cd /home/ubuntu/alphatrade
/home/ubuntu/alphatrade-env/alpha311/bin/python -m pip install -r requirements-deploy.txt
/home/ubuntu/alphatrade-env/alpha311/bin/python -m pip check
cp config.example.json config.json
cp accounts.local.example.json accounts.local.json
chmod 600 config.json accounts.local.json
# 人工填写本机配置及凭据，随后离线验证。
bash tools/deploy_alphatrade.sh check
bash tools/deploy_alphatrade.sh verify
```

本项目没有需要构建的独立C扩展。C预计算来自alpha2；其四个扩展必须在Ubuntu本机编译并经生产者测试，不复制Windows.pyd。消费端integration测试通过`--producer-root`读取生产者源码，并在子进程以合成数据验证C→Arrow→消费者，临时产物均写pytest临时目录。

当前Ubuntu已有Source Han Sans SC字体；统计使用matplotlib导出，不为本次部署安装浏览器。其他机器缺中文字体时先安装可用的中文字体并验证统计图片，不静默替换成无法显示中文的字体。

## 开发机提交、主干合并和传递

在功能分支完成代码与离线测试，先检查diff/私有文件忽略状态，再提交并fast-forward主干。依赖变化、核心接口和策略数学需单独写明。

```powershell
cd D:/alphaonline
git status --short
git diff --check
git add <本次代码、测试和文档>
git commit -m "说明具体行为变化"
git switch main
git merge --ff-only <功能分支>
git push production main
```

production remote为服务器SSH私有仓库。密钥由ssh-agent或临时GIT_SSH_COMMAND提供，不写进代码或命令文档；本轮传输辅助脚本留在本地research，不属于服务器运行依赖。不要用force push更新生产main。

不能直连Git时可用Git bundle传递已提交版本；服务端bare仓库fetch该bundle后，仍走工作副本的update。不得把忽略的私有JSON、数据和日志打入源码压缩包。

## 服务器人工更新

先停止**本安装**的调度，等其计算/统计子进程退出；保留其他目录的实例。不要只停止startup而让旧worker继续读写本目录。

```bash
cd /home/ubuntu/alphatrade
bash tools/deploy_alphatrade.sh status
bash tools/deploy_alphatrade.sh update
```

update会检查本目录仍在使用的Python进程、拒绝有本地改动的受跟踪代码，只允许fast-forward，然后执行check和离线测试。它不自动安装依赖、清理业务数据、重建snapshot、注册服务或启动交易；验证失败时保持停止并报错。config.json/accounts.local.json/data/logs不被Git更新覆盖。

依赖清单有变化时，在服务停止的情况下人工执行独立环境的pip install和pip check，再verify。不要自动升级当前环境所有包。使用不同路径时，以ALPHATRADE_PYTHON指定alpha311绝对解释器，以ALPHA_PRODUCER_ROOT指定配套生产者；默认分别为上述独立前缀和/home/ubuntu/alpha2。

## 离线验证范围

```bash
cd /home/ubuntu/alphatrade
bash tools/deploy_alphatrade.sh verify
```

覆盖MODEL初始化/续算/跨日/季度/修订回放、共同恢复、S3状态、配置继承、spawn、Numba跨新进程缓存、统计原生值/格式及下单路由的离线mock。传统5m和无凭据的15/25/45m公开fixture验证策略池加载与默认功能；旧历史账户或真实生产snapshot不存在时对应测试明确skip，不能计作通过。框架oracle固定为引入MODEL前的main提交，合并main不会自动改变oracle。

这里不启动完整历史初始化，不验证真实API权限或真实成交，也不以离线mock宣称完成真实交易验收。完整测试不依赖D:/E:盘；显式传入producer-root后读取配套生产者，未提供/不存在则integration会skip，首次服务器验收应提供有效路径。

## 首次启用与状态

本次部署结束保持停止，不注册自启动。未来人工启用前逐项确认：

1. producer路径对应实际运行的alpha2，所需offset已ready，完整140xx PKL与12列Arrow预计算发布正常；JSON机器路径不指向旧Windows目录。
2. 各账户凭据、分钟offset、S3日期和运行模式已检查。当前dry_run只保护下单，不等于所有私有查询都关闭；本次verify不会进入账户初始化。
3. 同一账户只能由一个交易实例管理；当前alphatrade1/alphatradetest仍运行，不能直接并行接管相同API账户。
4. 本次不复制旧消费者snapshot或历史资金文件。首次未来启动MODEL按既有流程自动用完整历史初始化，随后4050 Arrow续算；初始化需要额外磁盘和计算时间。部署verify不生成这套业务状态。
5. clean_start、状态保留和恢复沿用当前MODEL规则，不能因为更新代码就手工删除data。更改策略或S3日期需按对应文档决定重建。

未来沿用单一进程管理器，cwd为/home/ubuntu/alphatrade，解释器使用独立alpha311绝对路径。控制台日志与程序轮转日志使用不同文件。不要在部署脚本中附带startup.py启动命令。

## 回滚

服务保持停止。先找之前已验证提交，保留机器JSON和完整业务状态，切换代码后验证：

```bash
cd /home/ubuntu/alphatrade
git log --oneline -10
git reflog -10
git switch --detach PREVIOUS_GOOD_COMMIT
bash tools/deploy_alphatrade.sh verify
```

不要reset --hard或git clean清理配置/data。旧代码若不能理解当前状态，代码回滚并不能安全回滚业务；应按匹配的完整状态备份或明确初始化流程恢复。依赖变化时使用匹配环境再验收。回到主干用git switch main，随后正常update。

本轮core/startup完整人工复核范围见[发布复核清单](2026-11-02-core-release-review.md)。

## 2026-11-02首次验收记录

运行代码`debac03`在Ubuntu完整离线测试**768通过、70跳过**（333.52秒）；跳过为旧历史账户/生产snapshot等当前安装没有的输入，未计通过。包含实际配套alpha2的C→Arrow→消费端测试，既有数值oracle及容差未放宽。本地首轮774通过、2个路径夹具失败、62跳过，补显式临时数据目录后相关11项复测通过，即全部原失败用例已通过；不把重叠批次相加。

随后发布格式仅将`.gitattributes`限制为shell脚本LF；原35个混合行尾Python文件保留原字节，全部AST和忽略行尾的比较一致，没有因子公式变更。人工执行文档中的update命令也完整通过：**768通过、70跳过**（205.49秒），工作副本干净，未启动worker，业务data文件数0。准确最终版本以main的git log为准。初次/更新测试日志分别为`/home/ubuntu/alphatrade-verify.log`、`/home/ubuntu/alphatrade-update-verify.log`，依赖日志`/home/ubuntu/alphatrade-dependencies.log`，均在工作副本外。

独立环境pip check通过，机器配置为alpha2数据、无代理、dry_run；四账户凭据在权限600的私有JSON。本次没有启动/注册alphatrade服务、没有下单/通知，没有复制旧data或修改运行中的其他安装。最终检查服务器剩余磁盘约51GB（后台生产者仍在产出数据，数值会变化）；未来业务初始化仍需按实际数据规模安排容量。

实际行情静态检查：alpha2当前仅有55m共同ready，spot/swap Arrow均有12个所需预计算列，样本5000行，BTC原始历史14060根。检查时共同截止发布为`1793580900`，对应最后K线2026-11-01 23:55 UTC；这不是实时健康证明。**5m/20m/40m尚无完整ready，未来启用相应账户前需补齐并检查新鲜度。** 不混读旧alpha，不在本次部署中重配/重启生产者。
