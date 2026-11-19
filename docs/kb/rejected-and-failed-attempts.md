# 效果不好、已撤回或明确不采用的尝试（避坑记录）

> 所属体系：[统一开发入口](../README.md) · 在提出新优化或重构前先查此表，避免重复踩已被实测证伪的坑。

---

## 1. 已实施测试后因负收益或精度问题撤回的尝试

| 尝试项 | 日期 | 做法 | 实测结果与撤回原因 | 证据文档 |
| --- | --- | --- | --- | --- |
| **市值因子倍率 `log10` / 去重求幂优化** | 2026-11-06 | 在 `core/data_bridge.py` 保留逐值 `np.log10`、统一取整、按唯一指数求幂 | 1258 币精确一致、市值 CPU `29.35→19.49` 核秒，但三组交替完整因子进程中位 **`102.678 s → 107.477 s`**（未稳定提速，父写块与宿主波动掩盖收益）；**已精确撤回** | [2026-11-06-alpha1112-factor-block-implementation.md](../2026-11-06-alpha1112-factor-block-implementation.md) |
| **修订输入 Arrow IPC 解码列投影** | 2026-11-04 | 修改 `tools/robust40/handoff.py` 在 IPC 解码时做投影读取 | 512 现货/756 合约 ABBA 暖态反慢：**`1.733 s → 2.147 s`**；**已撤回** | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **选币每组删除 `gc.collect()`** | 2026-11-04 | 尝试在 `core/select_coin.py` 移除每组 GC | 完整选币反慢：**`112.764 s → 117.927 s`**；不能将局部映射 gen-0 GC 收益外推为“删所有 GC 更快”；**已撤回** | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **资金曲线 CSV→内存旁路（绕过 CSV 重读）** | 2026-11-05 | 让 `process_equities` 直接复用内存资金曲线，绕过 CSV 写读 | 14050 行合成曲线 CSV 文本往返导致 **3022 个 float 尾数变化**（最大 `4.44e-16`），破坏严格逐位一致目标；**暂缓未启用** | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md) |
| **因子块有界缓冲 `pending` 8 → 16** | 2026-11-06 | 在隔离 harness 将 pending 上限从 8 提至 16 | 受宿主并发干扰，三组样本 `95.1s / 100.6s / 80.7s` 无稳定收益；**未采用** | [2026-11-06-factor-block-causal-review.md](../2026-11-06-factor-block-causal-review.md) |
| **初始化 cgroup `MemoryHigh` 4.5 GiB → 7 GiB** | 2026-11-04 | 提高内存上限验证是否减少页回收换页 | 映射释放（`22.8s`）与 MODEL 原始读取（`26.7s`）均未显著改善；**保持 4.5 GiB 默认** | [2026-11-04-shared-io-optimization-review.md](../2026-11-04-shared-io-optimization-review.md) |
| **无状态截面线程数 2 → 4** | 2026-11-04 | 测试将默认截面并发从 2 提至 4 | 暖态完整耗时 2 线程 `60.137 s` vs 4 线程 `60.077 s`（仅差 **`0.06 s`**）；**保持默认 2 线程** | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **直接把上游 Arrow 发布缩短为精确 4050 根** | 2026-11-05 | 尝试让上游只发布 4050 根以贴合 `LIVE_BARS` | 反例实测破坏现货/合约映射预热，导致最终 4050 选币输入不等价；修正为 **`max(5000, 4050 + min_kline_num + 1)`** | [2026-11-05-runtime-efficiency-implementation.md](../2026-11-05-runtime-efficiency-implementation.md) |
| **聚合备用 v2 算法直接切换** | 2026-11-04 | 尝试用备用 v2 加速子策略权重聚合 | 严格对拍首先在索引名（`None` vs `candle_begin_time`）失败，且未完成浮点累加/缺口/日频/空输出全验收；**未启用** | [2026-11-04-speed-followup-review.md](../2026-11-04-speed-followup-review.md) |
| **早期“消费端从 Arrow 重建 Pivot”方案** | 2026-07-13 | 消费端放弃 PKL Pivot 改从 Arrow 现场构造 | 冗余高成本且增加不一致面；**已彻底删除并禁止恢复** | [2026-07-13-remove-redundant-arrow-pivot-rebuild-design.md](../superpowers/specs/2026-07-13-remove-redundant-arrow-pivot-rebuild-design.md) |

---

## 2. 经审计明确不推进的方向

| 方向 | 明确拒绝原因 |
| --- | --- |
| **重写 `Bias` 公式 / 给 Bias 写 C 扩展** | 14×14050×3 周期合成纯公式仅 **`0.0479 s`**，毫无优化价值。 |
| **重写 `Hybrid` 仓管数学** | 普通小时中位仅 **`0.428 s`**。 |
| **普通小时专家账户盲目并行** | 数值推进阶段总计仅 **`1.55 s`**；并行复制行情/矩阵/检查点在 4c8g 上反增内存与序列化成本。 |
| **按文件名年份跳读 Pivot PKL** | 真实续算 Pivot 读取仅 **`1.07–1.66 s`**，不是主要瓶颈，且引入文件名边界风险。 |
| **Tail-Only 裁切运行模式** | 多次审查明确排除：S3 固定初始化日期与长窗口因子（13608 等）需要完整保留历史。 |
| **用数据库替代现有 pickle/Arrow 状态协议** | 普通状态读写合并保存合计约 10s，重写整个状态协议与回放事务风险收益比极差。 |
| **全局禁用 GC 或 `gc.freeze()`** | 会导致大表与 Windows mmap 句柄无法释放，只能使用非嵌套的局部 gen-0 `temporary_gc`。 |
| **在 `restore_model` 另做第二套重映射（`plan.md` 方案 A3）** | 状态层 `merge_context`/`expand_result`/`Simulator` 本身已按 symbol 对齐，改动恢复主路径风险高且无必要。 |
| **通过单账户直接改写上游全局 `publish_kline_num` / `config.json`** | 会覆盖同机其他 offset 或未注册传统消费者需求，已改用独立 `.arrow-demand/` 控制文件协议。 |

---

## 3. 基础设施与工具链踩坑清单

1. **PM2 绝对解释器路径触发 Bun 误判**：PM2 `ForkMode.js` 检查 `interpreter.includes('bun')`，而 `/home/ubuntu/...` 恰好含 `bun`，会用 `ProcessContainerForkBun.js` 启动 Python 报 `SyntaxError`！必须用短名 `python` + 独立 `PATH`。
2. **Linux `fork` vs Windows `spawn` + Polars 多线程**：父进程已使用 Polars 时，子进程池须关注 `spawn` 安全边界，不能为省几秒启动耗时盲目改 `fork` 引入死锁；此外 Windows `spawn` 导致 21 个现货杠杆规则与 Linux `fork` 分歧。
3. **旧版 `111` 因子就地改写输入首行 `quote_volume`**：无深拷贝隔离时会污染后续因子与新币回撤计算，现已由 `111_输入隔离` 隔离（数学本身仍保持旧版，切勿误称为“已因果化”）。
4. **Windows `conda run` 间歇性崩溃 & `pytest` 临时目录权限/260 字符限制**：始终直接调用 `C:/Users/Administrator/miniconda3/envs/alpha311/python.exe`，并给 pytest 传短路径 `--basetemp` 与 `-p no:cacheprovider`。
5. **GCC FMA contraction 导致 CciV3 与 MSVC 结果微差**：Linux 编译 `cci_build.py` 必须保留关闭 FMA contraction 的编译选项，否则跨平台不逐值一致。
6. **生产机硬链接残留阻塞空间回收**：删除上游 Arrow 后，只要失败初始化工作区里的硬链接还在，物理空间不释放；清理必须走 `lifecycle.py` 持锁流程。
