# 开发规范、TDD 原则与跨项目数据契约

> 所属体系：[统一开发入口](../README.md) · 本文件汇总 `AGENTS.md`、`CLAUDE.md`、`docs/DEVELOPMENT.md` 与 `D:/alpha/AGENTS.md` 的现行开发硬约束。历史设计/计划文档仅作背景，不代表已实现；代码与可复现测试优先于旧文档。

---

## 1. 每次对话开场流程

1. 先读统一入口 [docs/README.md](../README.md) 及当前任务对应的 [docs/kb/](../README.md) 专题文件。
2. 检查两端仓库工作区与近期提交，**不得覆盖用户已有未提交修改**：
   - 消费者端（交易）：`D:/alphaonline`
   - 生产者端（行情）：`D:/alpha`
3. 历史计划文档不等于已上线状态；上线事实以生产服务器实际代码与 [changelog-main.md](changelog-main.md) / [DEVELOPMENT.md](../DEVELOPMENT.md) 的发布记录为准。

---

## 2. Python 环境与测试运行规范

- **本地 Python 唯一解释器**：conda `alpha311`，可靠绝对路径为 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`（避免使用不稳定的 `conda run` 或旧 `alpha` 环境）。
- **Windows pytest 注意事项**：本机默认系统临时目录可能触发 `pytest-of-Administrator` 权限错误，且深路径易触发 Numba 缓存 260 字符限制；测试统一传入短路径 `--basetemp` 与 `-p no:cacheprovider`，例如：
  ```powershell
  & 'C:\Users\Administrator\miniconda3\envs\alpha311\python.exe' -m pytest tests/test_xxx.py -q -p no:cacheprovider --basetemp=D:/alphaonline/.tmp-check
  ```
- **验证与风险成比例**：小改动跑最相关路径快速测试；涉及共享 `core/`、因子、截面、恢复或发布时扩大回归范围。
- **离线验证优先**：测试中应禁止外网连接（monkeypatch `socket.socket.connect` / `socket.create_connection`），绝不以单元测试通过冒充真实交易所成交验收。

---

## 3. TDD 与数值等价（Oracle）原则

- **先写失败测试，再改业务代码**：因子逻辑、截面计算、模拟、恢复、仓管、订单等变更必须先基于独立 Python oracle 或手工推导写出可复现失败测试（RED），再做最小实现使测试通过（GREEN）。
- **禁止重生成 golden 掩盖差异**：不得用修改后的 C/新实现输出反向覆盖 golden 文件来让测试通过。
- **精度与等价口径**：
  - 布尔事件、NaN 掩码、冷却边界、索引顺序、输入不变性、参数顺序必须 **exact** 一致。
  - C 与 Python 浮点允许有明确文档记录的极小容差（如动量 V2 使用 `rtol=atol=2e-11`）；同一实现的 Arrow 往返、增量与全量对拍要求 **exact**。
  - 因子修改必须全链路核对：预计算列名、完整历史回退、缺列本地计算、交易端/账户配置引用，不能只检查模块可 `import`。
- **禁止顺手引入额外校验门禁**：默认不主动引入 SHA-256、checksum registry、manifest、hash receipt 等额外验证门禁。

---

## 4. 生产红线与安全规范

- **默认只读/离线**：未经当次明确授权，不得启动实盘/生产者进程、不得下单/撤单、不得重启生产服务、不得删除生产数据或清空 `data/snapshot`。
- **凭据脱敏**：账户文件（`accounts/*.py`、`accounts.local.json`、`config.json`）含密钥、webhook 与代理凭据，**严禁**写入日志、文档、测试输出或提交进 Git。
- **禁止 `import config` 副作用**：`alphaonline/config.py` 在导入时会校验数据中心路径（不存在直接 `exit()`）或加载账户配置，切勿为查看配置而直接运行实盘入口。
- **生产测试全归 `test/`**：生产服务器上所有离线测试、对拍、基准与回退备份必须落在应用根目录 `test/` 内，整个 `test/` 必须可随时人工删除而不影响业务运行（详见 [production-limits-and-ops.md](production-limits-and-ops.md)）。
- **代码风格**：注释、日志与文档沿用中文习惯；保留核心文件顶部既有的授权头注释；保持文件原有行尾格式（不整文件改 CRLF/LF）。

---

## 5. 双项目数据链路与不变契约（`alpha` ↔ `alphaonline`）

```text
D:/alpha (生产者 / BMAC)                         D:/alphaonline (消费者 / 实盘交易)
────────────────────────                         ─────────────────────────────────
1. 采集 5m K线 (Binance优先 / DataAPI兜底)
   + 24h已结算资金费回补 + 市值CSV(float64)
2. 按分钟 offset 增量重采样 1h + enrich
3. 持久化 batch PKL (保留完整历史 ~14060 根,
   绝不把预计算因子写回 batch PKL)
4. 在完整历史内存字典上计算 C 预计算因子
   (动量V2 / ILLQ / CciV3)
5. 按需求深度裁切 tail (普通≥5000 / 初始化全量)
   原子发布 {spot,swap}_dict_{ts}.arrow
   (单文件 IPC, 每币一个 RecordBatch, metadata 含
    symbols 与 full_history 标记; 保留 keep=1)
6. 按年增量更新 market_pivot_{spot,swap}_*.pkl
   并发布各自 .ready 门控文件
7. 最后发布 {spot,swap}_dict_{ts}.ready  ──────► 1. check_bmac_update_flag 等待 .ready
                                                 2. snapshot_bmac_exginfo 本地快照防 EOF
                                                 3. MODEL 修订预检 (168h, 区分扩张与真修订)
                                                 4. Arrow mmap 优先读取 (缺/损/缺长因子回退 PKL)
                                                 5. 时序因子 → 截面(Polars优先) → 选币/ee续算
                                                    → 聚合 → 子策略模拟(读年度 Pivot PKL)
                                                    → MODEL仓管(26点恢复) → 目标手数 → 下单 → 异步统计
```

### 不可破坏的 7 条跨项目契约

1. **两个独立产物与 `.ready` 门控**：Market Dict（Arrow + PKL 回退）与年度 Market Pivot PKL 互相独立；消费端 `read_bmac_market_pivot(hour_offset, symbol_type)` 直接读 PKL，**禁止**恢复消费端从 Arrow 现场重建 Pivot，也**禁止**生产者停止生成 Pivot PKL。
2. **先算完因子、后发 `.ready`**：Arrow 必须先在完整历史计算因子、再 tail 裁切原子替换，最后才写 `.ready`。
3. **batch PKL 永久不落因子且保留全历史**：作为增量缓存、全量初始化及消费端缺列回退源。
4. **预计算复用三条件**：账户声明 + 参数匹配 + 列存在；模块名即语义版本。标记 `requires_full_history` 的动量 V2 缺列时，必须整份回退 PKL 并在裁切前补算，严禁在短 Arrow 窗口初始化长 EMA/异常状态。
5. **策略起点持久化**：`alpha` 在 `<storage>/preprocess_1h_resample_origins/<offset>/<market>.pkl` 维护最后成功发布的 `first_candle_time`，重启时对重叠序列沿用起点，防止下游 168h 修订窗口误报伪修订。
6. **C 扩展分平台构建**：Windows `.pyd` 不可复制到 Linux；Linux 目标机必须独立编译 4 个 CFFI 扩展（旧动量、新动量 V2、ILLQ、CciV3，其中 CCI 在 GCC 关闭 FMA contraction 以与 MSVC 精确一致）。
7. **增量定制保护**：第三方框架当前为 2.3.3（本地增量集成），严禁整体覆盖 `core/` 丢失 Arrow/Polars/C 预计算、MODEL/S3/ee 续算、dry_run 及资源控制。

---

## 6. 变更收尾与文档维护规则

- 每次功能或修复完成后，更新：
  - [docs/DEVELOPMENT.md](../DEVELOPMENT.md)（按时间倒序追加本次详细记录）
  - 对应 `docs/kb/*.md` 专题文件（保持现行状态准确，不让知识库过时）
- 涉及双端接口变更时，必须在文档中明确写清生产者、消费者双方改动及回退路径。
- 无需为普通变更创建冗余摘要文件或哈希校验门禁；若需要深度专题报告，放 `docs/YYYY-MM-DD-<topic>.md` 并在 `docs/kb/` 中挂载链接。
