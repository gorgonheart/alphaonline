# 项目开发入口

- 开始工作先读 [统一开发说明](docs/DEVELOPMENT.md)，再读相关专题和两端 git diff。另一端项目为 D:/alpha。
- 当前第三方版本 2.3.1，保留 Arrow/Polars/C 预计算及本地续算/交易定制；不能整体覆盖 core 丢失这些功能。
- Python 只用 conda alpha311；可靠解释器为 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`。
- 因子逻辑变化先写独立 oracle 的失败测试，再修改；核对预计算列名、完整历史、缺列回退及交易端引用。不要以生成新 golden 代替修复。
- 变更完成更新统一开发说明中的功能、接口、验证和未决事项。历史计划不等于已实现。
- 默认离线验证，不启动实盘/生产者、不下单、不重启、不删生产数据；这些操作需要对应明确授权。保留用户已有未提交修改。
