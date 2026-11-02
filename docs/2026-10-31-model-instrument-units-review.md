# MODEL停机续算缺交易单位：定位、修复与复核

## 实际原因

20m本地MODEL保存截止为2026-10-30 15:20 UTC，重启后14个子策略已补算至23:20 UTC，需衔接8根小时线。S3在22:20、23:20 UTC新选中现货AIUSDT，比例均为1/36。该币不在MODEL旧列中，也不在初始化冻结的账户市场规则中；但本轮data/cache/exginfo_spot.pkl中存在AIUSDT，status=TRADING，lot_size=0.1，子策略实际模拟使用的单位同样为0.1。

原MODEL单位来源只有“已冻结虚拟单位 + 账户market_info.min_qty”。子策略模拟则读取数据中心exginfo。统一账户客户端会通过杠杆现货允许买卖列表过滤市场规则，完整市场与账户可交易范围不相同。新增历史选币遇到此差异，就会在build_context报Missing instrument unit；停机使本次集中补算8小时，当前配置允许续算240小时，并非4050容量耗尽。并未保存报错当轮全部私有市场接口响应；本次离线复现使用已冻结账户规则和同轮本地数据。

不能删掉AIUSDT的历史选币或把缺失单位填成1来消除异常，这会改变虚拟账户资金曲线。也不能为解决虚拟模拟问题扩展真实交易白名单。真实执行仍在core/position.py取目标与账户symbol_list的交集，AIUSDT没有交易权限时不会因此获得下单许可。

## 最小修复

本轮运行代码只修改tools/robust40/live.py，core、startup、alpha、账户配置均无新增改动：

1. 提取virtual_lot_sizes，继续优先使用已有虚拟单位，其次保留现有账户单位来源。
2. 仅当子策略ratio列仍缺单位时，读取与core.equity相同的read_bmac_exginfo缓存，以真实lot_size补齐。已知币种无额外IO；本次20m只额外读取一次现货小型元数据，不发网络请求、不读取逐币K线。
3. 不修改旧冻结单位，不改变账户market_info、不放宽交易权限；重复规范币名报错，无法取得有效正单位继续拒绝计算。
4. 缺单位异常明确列出市场和币名，例如spot:AIUSDT，便于后续直接诊断。

初始化与续算共用该处理。已有正常币种的计算路径和历史单位不变；其他仓管策略不调用此MODEL适配器。

## 验证

- TDD新增17项先失败，修复后MODEL运行时/初始化/计算完成共34项通过：全部5分钟offset、新增非账户可交易币、冻结单位优先、已知币零额外IO、空值/零/负/无穷单位仍拒绝。日志research/robust40_instrument_units_red.log、research/robust40_instrument_units_tests.log。
- 真实20m离线重放：原单位来源重现spot:AIUSDT缺失；补齐0.1后，历史重叠校验通过，9个虚拟专家账户及最终MODEL账户续算8根至23:20 UTC。逐子策略独立加权汇总与父目标对比，spot/swap最大差均为0。MODEL模拟权益2222944.734634338是虚拟回测数值，不是实际账户余额。
- 本次重放没有跨越新的offset决策日，因此直接沿用保存的模型/预测，未做跨日重新训练验收；无需读取新的原始行情来刷新日度模型。进程含首次Numba编译共42.41秒，不能当作稳态增量耗时。
- 重放前后snapshot全部既有文件大小和mtime一致；未保存推演结果到生产state、未下单/调用交易所/重启/发送通知。脚本research/replay_20m_instrument_units.py；结果research/20m_instrument_units_replay.json，日志同名.log。
- 原虚拟账户断点续算（挂单/手续费）及snapshot生命周期另外23项通过，两组合计57项通过，见research/robust40_units_continuation_tests.log。git diff --check通过。

## 当前包为什么不会立即继续增量

这次失败发生在子策略已更新、MODEL未更新之后，warmup.pkl已按既有协议标记complete=False。当前子策略截止23:20 UTC而MODEL截止15:20 UTC，不是完整配对状态。原协调器会把它识别为incomplete calculation，并在后续匹配offset数据就绪时走自动完整初始化，保留失败现场/原起点策略。此次没有把标记强行改回True，也没有删除snapshot。

代码修复解决下次初始化和以后增量的单位来源问题。离线重放证明当前8小时的MODEL数学续算可成立，但不等于已经恢复并安装生产成对状态；不能据此宣称本轮已完成下单。是否额外开发“不完整包的验证后修复安装”属于独立恢复流程，本轮不扩展现有失败重建约定。

## 人工复核清单

- [ ] 仅MODEL适配器读取完整市场单位，真实下单白名单仍不变。
- [ ] 历史冻结单位不覆盖；未知单位仍报错并列出币名。
- [ ] 同轮缓存与子策略单位一致；正常轮次不增加网络或大文件读取。
- [ ] 当前complete=False仍走既有自动重建，未手工伪造成功标记。

本轮独立差异：research/model_instrument_units_review.patch。以前关于统计、时间戳和初始化的未提交修改均保留。
