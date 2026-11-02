# Hybrid 编译缓存优化与 Ubuntu 部署复核

2026-11-02更新：已完成Ubuntu部署及完整离线验收（768通过/70跳过），保持新实例停止；显式spawn也已落地。实际部署路径、独立解释器和人工更新以[DEPLOYMENT](DEPLOYMENT.md)为准。下面Windows计时是11月1日缓存改造证据，不代表Ubuntu性能。

## 已实施的最小范围

生产初始化和续算直接调用 `positions.HybridRotationDriftStrategy.calc_ratio`，不再经由禁用缓存的 AST 离线加载器。原 `@njit(cache=True)` 终于在 MODEL worker 中生效。离线 `tools.robust40.offline.hybrid_function` 保持原行为，不向参考工程写缓存。

运行代码只有两处：

| 文件 | 修改 | 人工复核重点 |
|---|---|---|
| positions/ModelRobust40Strategy.py | 正常导入 calc_ratio，初始化/续算两个调用点改用该函数 | 两条路径一致；无策略参数、状态协议、恢复逻辑变化 |
| positions/HybridRotationDriftStrategy.py | PosStrategyConfig 导入移到 TYPE_CHECKING 中 | 文件已有 postponed annotations，该类型仅用于注解；数值函数、常量、Numba装饰器均与main一致 |

没有本轮新增 core、startup、accounts、factors、sections、alpha 修改。没有取消框架功能。共享 Hybrid 数学仍用 main AST 对照，仅允许这项类型导入调整。若外部工具自行使用 `typing.get_type_hints`，需提供该类型的命名空间；当前框架按函数调用，不依赖运行时解析这条注解。

## 测试与性能证据

新增合同先出现2项失败（导入触发框架配置、MODEL未使用正常导入），实现后通过。

相关批次：`test_hybrid_cache.py`、`test_server_model_init.py`、`test_legacy_strategy_isolation.py`、`test_model_continuation_performance.py`，**36 passed / 50 skipped**。跳过原因是旧5/15/20/25/45账户配置在当前目录缺失，不计作通过；共享源码AST及框架隔离检查实际通过。新增专项3项全部执行。

两个全新Python进程使用临时缓存目录，第二进程hit>0、miss=0；长/短动态组、周期/条件再平衡、非零init_time、轮动及附带baseline与原加载路径逐值相同。

真实40m状态：12994小时、14子策略，2025-05-09 00:40至2026-11-01 09:40（UTC口径）。两次独立进程都与原加载器的整张权重表、索引、baseline、动态组完全一致：

| 新进程 | 正常导入计算 | 原离线加载路径 | 缓存 |
|---|---:|---:|---|
| 第一次冷缓存 | 3.427秒 | 2.193秒 | miss=1 |
| 第二次热缓存 | 0.727秒 | 2.912秒 | hit=1, miss=0 |

计时含Hybrid计算，旧路径另含AST加载，不含state读取和进程启动。单次同机顺序对照，不能视为Ubuntu实测、稳定P95或整轮节省承诺。第二次约省2.18秒；第一次并不更快。

证据：`research/hybrid_cache_20261101/{cold,warm}.json`；可复跑脚本 `research/benchmark_hybrid_cache_20261101.py`。只读生产state，输出研究JSON；没有修改snapshot、启动服务或下单。

## Ubuntu 落地规则

1. 使用Ubuntu本机的alpha311环境编译缓存，不部署Windows的`.pyd`、`.pyc`、`.nbc`、`.nbi`。Linux跨新进程缓存合同已在11月2日完整验收通过。Numba缓存涉及CPU型号/指令集；不要把跨平台复制当预热。[Numba 0.60缓存说明](https://numba.readthedocs.io/en/0.60.0/developer/caching.html)
2. 首次迁移先保持已验证的Python 3.11、Numba 0.60、NumPy 2.0.2、pandas 2.2.3、SciPy 1.14.1、PyArrow 17等依赖组合；Polars等其余版本从部署环境清单锁定，不同时升级大版本。生产者C扩展另在Linux构建并执行既有C/Arrow对拍，本轮未改alpha或其构建产物。
3. 在任何Numba导入之前，通过服务环境设置可写的本地缓存目录。服务用户和离线预热用户一致。可用systemd `CacheDirectory=alphaonline`管理权限，再设置例如 `NUMBA_CACHE_DIR=/var/cache/alphaonline/numba/release-20261101`。不要放到snapshot中，也不要与账户交易状态混为一类。
4. 同一发布、同一环境的各offset可共享编译缓存；缓存的是代码，不含账户权重和资金状态。配置参数作为函数参数传入，不因offset不同重建一份数学内核。优先本地磁盘；网络盘共享依赖原子替换语义。[Numba缓存并发说明](https://numba.readthedocs.io/en/0.60.0/developer/caching.html)
5. 更换Python/Numba/LLVM或部署版本时使用新缓存目录，避免运行中删除缓存。Numba不能自动追踪所有跨文件全局依赖；本次Hybrid内核及EPS同文件，原公式未新增外部可变依赖。保留正常CPU优化；仅异构同架构机器必须共享缓存时评估`NUMBA_CPU_NAME=generic`，不默认设置。[环境变量](https://numba.readthedocs.io/en/0.60.0/reference/envvars.html)
6. 文件更新会使对应Numba缓存失效。本次也修改了MODEL源文件，第一次MODEL模拟仍可能重新编译，不能把首次上线慢判为缓存无效。后续新worker才体现收益；无需清空业务状态。

已部署服务器的独立缓存专项命令：

```bash
cd /home/ubuntu/alphatrade
/home/ubuntu/alphatrade-env/alpha311/bin/python -X utf8 -B \
  -m pytest tests/test_hybrid_cache.py -q -p no:cacheprovider
```

pytest使用自己的临时缓存，不能视为生产缓存已预热；合同检查第二进程hit>0、miss=0及与原路径结果一致，不设固定毫秒阈值。research/benchmark_hybrid_cache_20261101.py是开发机的历史证据脚本，不随生产发布、不作为服务器更新依赖。新安装没有业务snapshot，不做真实状态预热；未来第一次业务计算仍可能编译。

## 进程启动方式的后续落地

11月1日发现时序池没有显式mp_context；11月2日部署时已在 `core/select_coin.py` 的该池显式使用spawn，并验证真实小样本因子池和MODEL子进程配置继承。Python3.11 Linux默认fork，不能依据Windows成功推断fork安全。[Python 3.11](https://docs.python.org/3.11/library/multiprocessing.html) · [Polars官方建议](https://docs.pola.rs/user-guide/misc/multiprocessing/)

这项保留原有Arrow、Polars及增量功能，最小core人工复核见[发布清单](2026-11-02-core-release-review.md)。完整Linux离线验收已经通过；真实业务初始化和交易未执行。逐因子公式优化仍只是[分析计划](2026-11-01-timeseries-factor-audit.md)，未随部署擅自实施。
