# S3初始化覆盖检查：后上市币误拦截热修复

生产5/25/45m初始化均由GUNUSDT触发：14008根跨越minimum_history_bars=14000门槛，起点2025-03-31 12:offset；原max(starts)+721小时误把S3起点推至4月30日13:offset。BTC14063根始于3月29日，市场输入本已覆盖原4月30日00:offset。不能要求后上市币具备上市前历史。

修复仅tools/robust40/server.py validate_initial_history一处max改min，保持14000门槛与min_kline_num+n余量。与原S3按市场最早时间检查、各币因子notna入场的行为一致，不改S3参数、日期、因子、选择、权重或交易。没有增加读取或遍历，仍复用已加载raw，正常续算不执行该校验。

新增12offset、顺序无关、真实不足、4050拒绝回归。TDD原版12失败13通过；修改后与手工日期/原S3状态回归共79通过（19.62秒），独立复核新增25项通过。另以相同边界两币28071行复现原S3接受而旧辅助校验拒绝。

热修复从已部署main7177978独立构建，不包含codex/next-runtime-efficiency未提交改造。生产测试仍仅位于test/，有效snapshot、账户、模式、S3日期保持，既有incomplete状态由原流程触发重建。不会因为此次修复主动清空data、强制重训或重启交易。已在运行的旧Python worker不会自动重载，后续新进程使用修复。
