# S12 LOCAL 实盘接口（2026-10-16）

状态：本地实现与离线验证完成；未修改账户配置，未部署、重启或下单。默认shadow。不是新增收益研究；不能把工程一致性当作独立OOS。

## 修改边界

新增 positions/HybridLocalRiskStrategy.py、tools/local_risk_model.py、tests/test_local_risk_model.py 和本文。统一开发说明增加入口。D:/alpha 不需要任何修改；两端core、startup、原Hybrid和已有账户配置均不修改。

原框架已完成选币→模拟子策略净值→process_equities→positions接口→时间转换/再择时→聚合/下单。本模块只在原Hybrid输出的第12列乘[0.8,1]，其他列不变，不转仓、不归一化；仍由框架处理时间移位和一次再择时。只接收14成员、1H仓管、rebalance_cap_step=1且没有额外symbol_ratio_limit的原始接口。

## 算法与文件

训练原样复用研究LOCAL的40特征、单成员S12样本、100树7叶深3、随机种子17、单线程。未来168h负对数收益平方和开方取log为标签；特征只到t-1，训练标签结束严格小于t。样本每24h、窗口730天、预测168h、重训672h。相对特征仍依赖8个固定成员（S3/S5/S6/S8/S9/S10/S11/S12）。保留研究版“共同可用样本”的近邻成熟门槛，但不重复做未使用的距离搜索。

历史一年成熟预测的经验分位q映射到1-0.2q；至少40条历史预测，小时变化最多0.01。以bootstrap首个UTC时间为不可变origin，首次预测origin+6720h，训练/预测不随重启重置。activation只控制开始降仓，提前的预测用于分位预热。不要更改origin、activation、成员顺序、因子、再择时或账户语义后继续使用旧状态。

每个账户/分钟偏移一个独立state.pkl，包含历史8成员close、预测、模型文本和调度状态。仅加载可信本地文件；pickle不可接收互联网文件。一次原子替换提交历史与模型，排他.lock防止同账户并发。异常保留旧文件。进程强杀可能遗留.lock：确认没有该账户工作进程后人工移走，禁止自动按时间删除锁。放在data/local_risk，不放会被clean_start删除的snapshot/cache。

普通小时不加载LightGBM，仅核对重叠收益、追加历史和生成乘数。到期才用同一sys.executable启动隔离worker（120秒超时、单线程）训练/预测，避免本机已复现的Windows OpenMP初始化冲突；worker在pandas/numba之前初始化LightGBM。不要把训练重新放回实盘主进程。最多追补历史缺失决策，每次只用当时成熟标签；长期断档导致超时，应离线恢复，不延长在线下单等待。

## 初始化数据是上线前置条件

使用同配置、同币池/账户类型、同分钟偏移、完整14成员24H预处理上下文生成的process_equities输出。这里24H是完整子策略池预处理上下文，仓管接口仍为1H。不直接读原始行情、不拼真实交易账户净值、不用研究0m状态冒充15m。

工具导出8个成员共同时间轴，禁止缺小时/重复/非正净值。bootstrap至少18500小时（含特征预热及730天附近的历史），起点与研究不同则预测/训练锚也不同：是部署新起点，不得宣称完全复现0m原策略。activation应晚于足够历史预测的形成时间，例如先使用未来计划启用日，以便预热；不足40条时明确拒绝而非改阈值。

当前本地15m S12单独3816小时，8成员共同导出仅3061小时，不能bootstrap。不得自动把实盘get_kline_num调成多年行情，否则4核8GB全币回算可能超内存。应在离线研究机生成同偏移历史并核对，或持续积累足够一致的历史；当前旧0m研究缓存只能测试，不能部署。

续接incoming窗口前1000小时视为移动起点预热，不参与重叠核对；剩余至少168小时，逐成员对数收益须在atol=1e-8/rtol=1e-6内一致。仅净值整体尺度不同可按最后重叠点连接；收益变了就报错。缺时、错偏移、旧数据和成员变动均拒绝，不ffill伪造历史。长期状态保存的小时close量很小，当前不裁切以保持锚定与可重放性。

## 本地命令（离线，不调用交易入口）

```powershell
cd D:\alphaonline
$py = 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe'
& $py -B tools/local_risk_model.py export --children 'data/子策略回测结果/15m-nheart0701_' --csv 'research/local_risk/15m_closes.csv'
# export同时输出local_expected_names。当前短历史仅可检查，不能用于下面bootstrap。
& $py -B tools/local_risk_model.py bootstrap --csv 'research/local_risk/15m_full_history.csv' --state 'data/local_risk/15m-baseline3-v1/state.pkl' --identity '15m-baseline3-v1' --activation '2026-11-01T00:15:00Z'
& $py -B tools/local_risk_model.py inspect --state 'data/local_risk/15m-baseline3-v1/state.pkl'
```

上面的activation仅为命令示例，不是研究延期或自动开仓预约。bootstrap拒绝覆盖已有状态；需另选版本目录。

账户配置末尾显式接入（本次未应用）：

```python
strategy_config['name'] = 'HybridLocalRiskStrategy'
strategy_config['params'].update(
    local_mode='shadow',  # 验证完成后人工改active
    local_identity='15m-baseline3-v1',
    local_state_path='data/local_risk/15m-baseline3-v1/state.pkl',
    local_expected_names=[f'S{i+1}-{s["name"]}' for i,s in enumerate(strategy_pool)],
)
```

local_expected_names必须与export结果及factory生成的conf.name逐项一致；不要为绕过异常删除核对。身份字符串代表此账户/offset/策略定义版本，定义变更须新建身份与重建状态，模块不自动检查所有因子代码变化。

shadow更新状态并记录拟用乘数，但返回原Hybrid仓位；状态缺失或错误打印异常后仍返回基线。active异常直接抛出，阻止本轮新目标产生，不保证框架此前尚未撤单，更不会自动平仓；已有持仓由原交易系统处理。没有静默满仓回退或过期无限沿用。

## Linux部署流程

1. 将两个模块、测试和文档复制到服务对应的alphaonline根目录；生产者alpha无需改动。保留原文件/账户配置备份，不覆盖core。服务器项目路径以实际服务配置为准（例如/home/ubuntu/alphaonline）。不要复制Windows环境、pyd或本机缓存目录。
2. 在隔离的alpha311 Python3.11环境安装既有requirements；核对本机验证版本lightgbm=4.7.0、numpy=2.0.2、pandas=2.2.3、scikit-learn=1.5.2，先解决依赖冲突，不在运行中生产环境全量升级。LightGBM Linux wheel若报libgomp.so.1缺失，在Ubuntu用管理员安装libgomp1。无需新增C因子编译。
3. 确认服务用户可写data/local_risk及临时文件；每账户独立目录，保留磁盘余量；state含可信pickle，只允许受信任用户写。源历史CSV与模型文本可跨平台，实际部署state优先在Linux同环境重新bootstrap。
4. 在项目根目录运行相关测试：

```bash
conda activate alpha311
python -B -m pytest tests/test_local_risk_model.py -q -p no:cacheprovider
python -B tools/local_risk_model.py bootstrap --csv /path/to/15m_full_history.csv --state data/local_risk/15m-baseline3-v1/state.pkl --identity 15m-baseline3-v1 --activation 2026-11-01T00:15:00Z
python -B tools/local_risk_model.py inspect --state data/local_risk/15m-baseline3-v1/state.pkl
```

5. 用同偏移历史做正式backtest.py及离线实盘接口重放，检查其他13成员不变、末根乘数、时点和再择时一次。先独立dry_run环境测试重启、缺数、过期、锁和worker失败，不能直接用startup.py当单元测试（启动可能清snapshot）。
6. 经人工部署授权，账户先启用shadow并由原服务管理器安排重启；观察至少一次真实168h决策和一次672h重训，检查正常小时/到期小时耗时及内存。影子模式只保证LOCAL不改预算，不会禁用原账户交易；需无交易验证时额外使用框架dry_run。
7. 通过工程核对和策略风险评估后，再人工切active。回滚账户仓管名为HybridRotationDriftStrategy，保留状态供诊断；回滚恢复基线预算可能增仓，需按部署流程处理。本文不执行任何服务器命令。

## 性能和验证证据

alpha311离线专项6项通过：重叠归一衔接/历史修订拒绝、缺口重复拒绝、平滑/重启/过期、原子事务失败保护、隔离训练续算与全量一致、只缩S12/影子恒等。研究0m共50184小时、259预测复现：预测最大误差4.44e-16、乘数最大误差2.22e-16；隔离worker全历史重放约5.78秒（本机Xeon E5-2666v3，非4核服务器实测）。参考正式S12_LOCAL已有backtest.py结果，本次未生成新投资表现。

初始测试确实发现LightGBM进程内加载异常，隔离worker后通过；不将测试失败隐去。本机性能不能承诺Linux服务器时限，建议4核8GB预留模块0.3–0.8GB、正常小时<1秒、决策/训练小时2–10秒的初步预算，实际上线测P50/P95/峰值。不包含原框架选币与14曲线生成；已有单账户187.95秒日志不能推算目标服务器。历史初始化的子策略重算成本另计。

仍未完成：同偏移多年历史准备、Linux实测、线上影子观察、LOCAL过拟合统计审计。当前14成员本地曲线已导出只用于诊断，无足够历史，不生成可交易状态。
