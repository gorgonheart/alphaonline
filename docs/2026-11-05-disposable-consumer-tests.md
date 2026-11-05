# 生产alphatrade可删除测试目录

生产环境测试代码、副本、测试数据、缓存与报告统一放项目test/。开发仓库仍维护tests/，生产通过Git sparse checkout排除根tests、tools/benchmarks及专用benchmark/verify/parity CLI。工具tools/run_deployment_tests.py是可重建测试的维护入口，不参与startup计算；test目录删除后无需恢复它即可正常交易。

bash tools/deploy_alphatrade.sh verify从当前已提交Git版本创建test/verify-*/source，使用隔离空行情目录、dry_run配置、空凭据；生产config.json与accounts.local.json不复制。pytest临时目录、Numba缓存、Python缓存与日志也均位于该verify子目录。调用方机器check仍只读取配置，不连接交易所。

test可在没有验证任务时人工整目录删除；正在验证时删除只会中断该次测试，不是业务状态。下次verify自动重建。不建立业务指向test的symlink，不将test加入服务解释器路径、cwd、PYTHONPATH或NUMBA_CACHE_DIR。业务解释器仍为既有conda alpha311，业务snapshot、初始化临时工作区与恢复点属于正常功能，不属于可删除测试目录。

历史tests、research验证目录、独立reports和已知测试缓存归档到test/archive。迁移前必须检查进程/启动配置引用；若存在业务引用需先移除引用，不能直接删除。部署备份中的测试源码和验证日志也迁入test/archive，运行源码和私有配置备份保留在原处；回退不依赖test。

本轮同时发布失败初始化/回放生命周期清理，详见2026-11-05-scratch-lifecycle-implementation.md。不得将部署测试目录的可删除性质延伸到data/snapshot或data/server_initialization的在用任务。
