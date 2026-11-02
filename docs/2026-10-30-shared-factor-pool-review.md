# alpha 因子共享线程池：实现与人工复核

后续确认：用户明确要求改用4，已将本地alpha/config.py的precompute_factor_workers设为4，且不存在config.json覆盖。核对当前preprocess.py与research/shared_factor_core_review.patch完全一致。以下默认0及待启用内容记录首次交付状态；当前磁盘配置为4，未代用户重启服务。

本次范围是生产者的因子任务调度。用户已确认实施；生产启用线程数仍需人工复核。本次不启动 realtime_data、startup，不重启、不下单、不删除生产数据，不改因子公式或 C 扩展。

## 最小修改清单

| 文件 | 本轮修改 | 人工复核重点 |
|---|---|---|
| `D:/alpha/core/preprocess.py` | 两个函数及两个标准库导入，共 +24/-8 | `preprocess_type_data` 的批内任务提交、结果收集；`preprocess_offset_data` 的共享池生命周期 |
| `D:/alpha/config.py` | 新增 `precompute_factor_workers=0` 及说明；加入原 JSON 配置白名单 | 0 保持两市场内逐币串行；正整数为两市场共用的因子线程额度 |
| `D:/alpha/realtime_data.py` | 本轮零修改 | 保留工作区已有 backfill 修改，不能误算成本轮差异或覆盖 |
| `D:/alpha/core/backfill.py` | 本轮零修改 | 仍调用相同 `preprocess_offset_data`，共享此配置 |
| `D:/alphaonline/core`、`startup.py`、账户 | 本轮零修改 | 保留此前改动，不扩大本轮审核范围 |
| 因子 Python/C、Arrow、batch、pivot 函数 | 本轮零修改 | 保持数学、输入深度、持久化及发布契约 |

新增离线测试 `D:/alpha/tests/test_shared_factor_pool.py`；实测脚本 `D:/alphaonline/research/benchmark_shared_factor_pool.py`。这些文件不接入服务入口。

## 调用方式与行为

`preprocess_offset_data` 公开接口不变。匹配数据可用后，每轮只解析一次因子模块和参数；配置大于 0 且存在因子时创建共享因子池。原有两个市场线程继续准备现货、合约数据；因子池与市场池独立，避免嵌套等待死锁。生命周期限定于本次 offset 调用，返回或异常时关闭，无全局池、无跨进程序列化。

`preprocess_type_data` 增加可选关键字参数 `factor_executor` 和 `factor_modules_params`，已有四参数调用仍可用且串行。调用原 batch 函数时传 `with_factors=False`，原始 PKL 和 pivot 中间文件先按旧逻辑写入，然后复用 `_calc_symbol_factors` 计算每个币种的所有参数。因子仍计算完整输入，Arrow 发布阶段才裁到 5000 根。

结果用有序 `map` 消费，保留币种与因子列顺序。现货、合约分别收集结果，同名币种不会串市场。当前批次全部完成后才进入下一批；batch=180 时每个市场最多提交本批 180 项，不一次排入全部币种。没有新加完整 DataFrame 复制、进程传输、逐币对拍或验证门禁；原 `df.assign` 的复制行为保持不变。

因子异常继续向上传播，失败市场不会走到 Arrow/pivot 最终发布或 dict ready。另一个市场可能正常完成，这是原有两市场并行语义；本次不引入跨市场事务，也不改变消费者同轮 ready 契约。原来 Arrow 发布失败回退 PKL 的处理保持不变。

配置为 0 或没有因子时不创建因子池。未来接入具有共享可变状态的自定义因子，应维持 0 或先离线确认线程安全；不根据 CPU 数量自动扩容。2/4 只表示因子线程，之外仍有两个市场线程及框架原有 I/O 线程。

## 验证

TDD 红阶段：新共享池接线测试 2 failed / 3 passed，失败明确为配置 2/4 没有共享执行器。实现后首轮 16 passed。另补四个任务屏障测试，验证两个市场的任务能在四个因子线程同时前进，不存在共用市场池造成的嵌套等待。

最终专项 17 passed（4.25 秒）：新测试 6 项，加原 embedded factor / preprocess pipeline 11 项。覆盖 0/2/4、冷启动、真实新增一根的第二轮、跨 batch、同名现货/合约、列/索引/类型/NaN 精确一致、原始 PKL 无因子列、pivot 完整、发布顺序、异常传播、无因子和线程池生命周期。独立简单滚动均值作为调度测试 oracle；原有因子公式未改，不重建 golden。

命令（alpha311）：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/alpha311/python.exe' -X utf8 -B -m pytest tests/test_shared_factor_pool.py tests/test_module9_embedded_factors.py tests/test_module10_preprocess_pipeline.py -q -p no:cacheprovider
```

日志：`research/shared_factor_tests_delivery.log`。中途文件在21:01:49及21:11:19被外部恢复旧版，测试/范围检查正确报警；用户已确认并非主动撤回，重新应用本轮补丁后上述专项通过。只读进程检查未定位改写来源，没有终止任何服务。独立保存本轮core补丁 `research/shared_factor_core_review.patch`，人工启用前需排除其他编辑或同步任务继续覆盖源码。

## 真实数据性能测量

使用冻结的 55m 行情、现货/合约各 180 个长历史币种、当前 12 个因子列，固定共同截止时间。每个配置在独立 Python 进程内执行冷启动及两轮实际新增 K 线；调用真实预处理入口、写真实 PKL/Arrow/pivot/ready，全部输出到 research。数据采集、重采样、网络、交易及进程启动不纳入计时；离线样本不读取资金费率、只建立所选币种的市场映射，因此不代表完整线上交易测试。

原版基线从当前 alpha HEAD 的 `core/preprocess.py` 复制至隔离目录，用 `core._benchmark_serial` 加载，不改写工作区源码。2/4 线程使用当前工作区实现。比较所有 batch/pivot DataFrame 及每轮 Arrow 的完整发布内容，要求 exact，包括索引、类型、列顺序；对拍、参考结果落盘、输入冻结均在计时外。内存按 50ms 采样 RSS，仅用于本机比较，不能直接外推 1269 币种或 Linux。

| 配置 | 冷启动秒 | 两轮增量秒 | 增量中位秒 | 相对原版 | 运行阶段最大采样 RSS MiB |
|---|---:|---|---:|---:|---:|
| 原版两市场各自逐币串行 | 28.13 | 22.78 / 23.06 | 22.92 | — | 3677 |
| 共享 2 线程 | 25.31 | 21.45 / 21.21 | 21.33 | -6.9% | 2561 |
| 共享 4 线程 | 25.10 | 20.18 / 20.52 | 20.35 | -11.2% | 2532 |

2/4线程的三个阶段、两市场所有比较产物均精确匹配原版。新增线程本次未增加峰值内存；将因子计算移到 batch 函数返回后，也允许原函数持有的旧批次等临时对象提前释放，但 RSS 还受分配器和执行顺序影响，不能承诺固定省内存比例。两个增量样本不足以给出稳定统计置信区间，且未覆盖全1269币种、不同offset及Linux负载；本地候选4线程，目标机器仍需比较2/4。默认配置保持0，未自动启用。

原始结果和阶段日志在 `research/shared_factor_perf_20261030/final/result_{0,2,4}.json`、`run_{0,2,4}.log`，输入说明在 `input.json`。测试进程启动时已加载并行代码；后续磁盘文件被外部恢复不改变该进程内代码，四线程冷启动及两轮输出均已完成对拍。

复测同一输入可运行 `python -X utf8 -B research/benchmark_shared_factor_pool.py --reuse-input --run-name repeat1`（alpha311，从alphaonline目录运行）。使用新的输出子目录，拒绝覆盖已有测试结果。所有对拍和50ms内存采样仅存在于此离线脚本，没有进入生产热路径。

## 人工启用检查

- 核对本轮两个函数的 diff、配置白名单，以及原始 PKL/Arrow/pivot 契约未变。
- 根据本机与目标机器的测量选择 2 或 4；JSON 配置存在时优先确认覆盖值。回退只需设为 0。
- 本地代码完成不等于线上已部署；Linux 4 vCPU/约 8 GB 的线程数仍需目标机器测量。不能将内存中因子微基准的 39% 加速当作全流程收益。
- 没有因子语义、缓存格式或 C ABI 变化，无需为此次调度优化重新编译 C 或删除历史数据。运行中进程的生效时机由后续人工部署/重启安排决定。
