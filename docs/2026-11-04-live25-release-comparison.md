# 25m实盘升级前对比：已确认安排与执行入口

## 用户确认的边界

2026-11-04，GrillMe逐项确认：升级现有生产25m，保留生产状态；自然运行先观察2–3小时，证据不足延长。每轮及时独立留存。自然对比发现差异才补实际旧版/完整候选版的同输入、同起始状态离线对拍，不预设为先决条件。严格一致，浮点微差查因后人工审核。报告通过并再次人工放行后，同次升级并启用真实下单，不额外要求生产新版本先跑一轮dry_run。出现异常先停新订单，人工核对成交和持仓后回退。

本次“确认”授权实施采集/比较，不是提前授权真实下单或代码发布。

## 机器与当前状态

| 项目 | 测试 | 现有生产目录 |
| --- | --- | --- |
| 主机 | ubuntu@43.173.122.28 | ubuntu@43.167.235.164 |
| 应用 | /home/ubuntu/alphatrade | /home/ubuntu/alphatrade |
| 账户 | 25m-eart1020.py | 同名 |
| 模式 | dry_run | dry_run |
| 上游 | /home/ubuntu/alpha/data | /home/ubuntu/alpha2/data |
| 原调度 | alphatrade-dryrun.service | PM2 pmt-startup-alphatrade |
| 采集日志源 | reports/startup.log | /home/ubuntu/.pm2/logs/pmt-startup-alphatrade-out.log |

初查有效策略配置一致；相同ready时间不等于相同行情。生产实际版本比最近4文件改动更旧，缺少新共享流程及16个批量因子接口，完整发布必须另行核对依赖闭包。自然结果一致不能充分证明新代码接续旧生产snapshot，用户选择的验收仍有此覆盖缺口，放行报告必须明示。

10:18（香港时间）两端独立采集器启动，完成baseline后监听未来事件。测试baseline截止2026-11-03 21:25 UTC，生产截止2026-11-04 00:25 UTC。计划10:25为追赶轮，11:25、12:25、13:25为三个普通共同轮次，是否实际完成以事件为准。5/15只作offset与稳定性证据，45传统策略只作共享框架兼容证据，不与25数值直接比较。

## 实现及验证

- `research/live25_capture.py`：独立低优先级进程，仅写research证据目录，不改core/startup/accounts/alpha，不持有业务锁，无交易或通知调用。
- `research/live25_compare.py`：离线导出当前决策、完整季度模型/训练样本、S3状态、168h修订输入；严格比较，不设置浮点容差、不自动放行。
- tests/test_live25_capture.py、tests/test_live25_compare.py：先失败后实现。当前12项通过，覆盖错误轮次/attempt、复制中变化、半成品warmup、空间预留、完整Arrow无损压缩、空头保留、外部依赖、微差及缺产物。
- 独立审查发现的输入依赖、baseline竞态、空间余量、重试归属、环境信息问题已修复。尚未完成真实轮次采集时不能标记线上采集验收通过。

两端共同证据根目录：`/home/ubuntu/alphatrade/research/live25_20261104/evidence`。

`baseline/`保留约1GiB的原始共同状态、有效账户参数投影、全局计算开关/依赖版本、实际源码及C扩展归档。不复制账户凭据或config.py。目录700、文件按umask077创建；源码归档仍作为服务器私有材料管理，不公开发送。

完成轮次目录为`<stamp>-<attempt>`，失败复制为`.partial`，不能计入完整样本。每次保存：完整signed runtime权重、最新子策略选择和比例/曲线/再择时、父权重、完整snapshot（含S3及MODEL）、目标手数、缓存市场信息及因子Parquet、同轮全长Arrow（zstd无损）、Pivot、四ready、coin_cap、25m BTC外部源、producer exginfo。

完整snapshot每轮约1GiB；全Arrow原始约3.2GiB但采用逐record-batch无损压缩保存，未截断历史。附加IO属于观测成本，单列capture_s，不归入算法计算性能。按下一文件大小保守保留4GiB空闲（不是跨进程原子磁盘预留）；其他业务同时写盘仍可能消耗余量。空间不足拒绝本轮，记录错误，不删除业务数据或已有证据。原生产约15GiB空闲，baseline后约14GiB；实测每轮总量后再评估4轮容量。

日志以现有MODEL_REPLAY的account/kind/attempt/stamp/end/status/committed筛选。用snapshot共同截止时间、warmup完整标记、runtime时间标记及attempt时间范围归属，用源文件inode/size/mtime前后核对复制稳定性；没有引入额外哈希协议。start时记录行情/外部源的文件状态，end时再次检查，变化记录在`input_changed_since_start`。该字段非空或未知时不能声称所留输入与本轮实际消费完全一致。

### 仍需明确的证据限制

- `capture_complete`只表示列明文件复制完成，不代表回测一致或允许实盘。
- 内存中的公共client过滤结果、执行时权益/持仓/拟订单完整表没有业务外旁路可读文件，未改交易流程去拦截。缓存exginfo和position_results提供部分证据，不能假装冻结了所有执行输入。
- 相同权重下手数可能因价格/权益观察时刻不同。若此层有差异，固定执行输入离线对拍；不把dryrun拟订单当成交。
- 历史状态本身可能已不同。当前行一致也不能证明全部历史一致；完整snapshot已留存供定位。
- 紧凑导出只减少跨机传输，保留原始标签/顺序；未取得证据、未覆盖场景或无法复现必须明确报告，不算通过。

## 运维及后续执行

采集服务：两机各自的**用户systemd** `live25-evidence-20261104.service`，Nice=10，IO idle，运行4小时后正常退出。不是主调度服务。只停此单元不会停止交易。

```bash
systemctl --user status live25-evidence-20261104
tail -n 30 /home/ubuntu/alphatrade/research/live25_20261104/collector.log
cat /home/ubuntu/alphatrade/research/live25_20261104/evidence/events.jsonl
```

测试SSH：E:/siteup/env/visibility-deploy.pem。生产通过本地research/alphatrade_deploy_transport.ps1，使用现有oldserveragent身份，禁止输出密钥。生产python=/home/ubuntu/miniconda3/envs/alpha311/bin/python；测试python=/home/ubuntu/alphatrade-env/alpha311/bin/python。

本地辅助脚本live25_start_test.sh、live25_start_production.sh仅部署采集器，不应重复运行已active的同名单元。live25_status_production.sh只读；不使用其旧进程PID作为任何停止对象。

比较步骤：

1. 检查两端events、完整轮次目录及capture_error/.partial，确认共同stamp/截止时间和起始状态连续性；单列追赶、失败及重试。
2. 在各端仅针对已完成证据运行`live25_compare.py export <round_dir> <comparison.pkl>`，小包下载本地。生产包可用已有SSH身份只读下载，不改应用。
3. 本地alpha311：`python research/live25_compare.py compare <test.pkl> <production.pkl> <report.json>`；非零表示发现差异，不是工具故障。
4. 按输入→因子/子策略→曲线/再择时→S3/MODEL→最终权重→固定执行输入手数/订单定位。紧凑导出中有差异，再从原证据取对应完整数据；旧新复现只在隔离目录执行，禁止交易/真实通知。
5. 区分数值、时间标签、列/行顺序、dtype、历史覆盖和配置来源差异；保留NaN、空头、重复选择行。不扩大容差来获得通过。
6. 汇总3个共同普通轮次、其他offset稳定性、采集本身耗时/占用以及未覆盖项。2–3小时是首轮窗口，若13:25轮未完成，等到其完成再收敛，不自动放行。

生产发布由人工看报告后决定。planned停机需确认worker、状态提交及下单均空闲：PM2 treekill会终止子树，不能当作仅停新调度。发布保留生产状态；事故先停新订单并核对交易所，禁止盲目恢复snapshot重跑已成交小时。


## 17:25后状态更新（覆盖前述过期安排）

原聊天定时任务已取消，旧全量采集已结束，生产证据已迁至测试production-copy；不再重启旧采集。仅测试主调度按授权迁入PM2并重启，生产只允许独立统计采集，不增加验证/保护逻辑。新有界轻量采集已完成17:25首个共同轮，后续目标18:25/19:25；S5出现新NIGHTUSDT差异并由同公式两端行情跨后滤阈值复现。仍未放行。具体操作、失败修复、空间、数值及缺口见[17:25报告](2026-11-04-test-pm2-1725-review.md)。
