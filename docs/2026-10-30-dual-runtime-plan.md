# 双运行体实施计划：服务器 4050 根 / 离线 14050 根

> 后续方案已调整：初始化和交易均在实盘服务器执行，正常账户配置保持4050，首次初始化由独立配置覆盖。当前方案见 [服务器初始化与续算](2026-10-30-server-initialization-plan.md)。下文保留为历史v2草案，不作为当前开发指令；其中容差放宽等问题见 [审查记录](2026-10-30-dual-runtime-review.md)。

> 状态：v2 待实施 | 目标：同一份代码，两个入口，数值差异**定量可观测**而非追求逐位相同
> 前置结论见 `docs/2026-10-30-warmup-pipeline-decoupling.md`
>
> v2 变更：① §1.3 修正「必须逐位一致」的错误表述——重叠区只做体检、不进结果；
> ② 新增 §3.0 数值差异容忍设计（P1–P4）：`equity` OHLC 阈值放宽到 **1e-2**，
> `positions` 保持 1e-4 严格，`parity_policy` 三档响应 + JSONL 对账；
> ③ §5.2 验收标准按分档阈值重写；④ §6 拆为 P 批（先上）/ C 批。

---

## 1. 目标与边界

### 1.1 目标

| 运行体 | 位置 | K 线 | 职责 |
|---|---|---|---|
| **离线体** | 本地 Windows（D 盘） | **14050 根**（585 天） | 跑满 365+180 门槛，产出 `runtime_bootstrap.pkl` + `snapshot/` 预热包 |
| **服务器体** | Ubuntu 云主机 | **4050 根**（168.75 天） | 加载预热包 + arrow 快照，每小时增量 `advance`，下单 |

### 1.2 边界（明确不做）

- 不改模型公式、不改因子实现、不改 `RebRobust40` 执行逻辑
- **不改** `merge_context` 的「旧 state 权威」语义（重叠区仍取 `old`）；只调整容差分档与响应级别
- 不引入新的数据生产链路（arrow 仍由 `alpha` 发布）
- 不缩短 `14050` 根（`ILLQ rolling(6*2320)=13920` 是硬下界）

### 1.3 重叠校验的真实语义（原表述需修正）

`runtime.merge_context()` 在 `old`（state 里的 585 天）与 `new`（服务器 4050 根）之间做重叠校验：

```python
overlap = old['index'].intersection(new['index'][warmup:])[-168:]
if len(overlap) < 168: raise ValueError('Insufficient initialized overlap to continue')
_same(a[oi], b[ni], 'S{i+1} positions')      # :85
_same(a.loc[overlap], b.loc[overlap, a.columns], 'S{i+1} equity/factors')   # :97
```

**但重叠区只用于体检，不进入结果**：`:86` 与 `:114-119` 全部是 `pd.concat([a, b[ai]])`，`ai` 只含 `addition`（新行）。`new` 的重叠段数值无论差多少，都不会写进 state，也不会影响下单。

因此正确的表述是：

| 项 | 要求 | 理由 |
|---|---|---|
| 时间轴衔接 | `addition[0] == last + 1h`（`_same` 之外的硬约束） | 断档无法续算，必须炸 |
| `positions` 矩阵 | 需高度一致 | 当期因子决定，非递推；是「选币是否走对」的主守门员 |
| `equity` OHLC | **只需形状一致** | 递推量，被 `scale` 归一后仍含 lot 量子化噪声（实测 ~1.1e-3） |
| 价格数组 / `leverage` | 需一致 | 原始数据，无递推，差异即数据源错位 |

**结论：不是「逐位一致」，而是「持仓一致 + 净值形状一致」。** §5.2 原来写的「最大绝对差 ≤1e-12」是不可达的验收标准，需改为 §3.0 的分档阈值。

---

## 2. 差异清单

### 2.1 已知的 2 项（用户已列）

| # | 项 | 离线 | 服务器 |
|---|---|---|---|
| 1 | OS / 路径 | Windows，`d:/alpha/data` | Ubuntu，`/home/ubuntu/alpha2/data` |
| 2 | K 线数量 | 14050 | 4050 |

### 2.2 排查出的其余差异（共 6 类）

#### A. 配置层：已支持，只需填 JSON

| # | 配置项 | 离线 | 服务器 | 覆盖通道 |
|---|---|---|---|---|
| A1 | `realtime_data_path` | `d:/alpha/data` | `/home/ubuntu/alpha2/data` | `config.json`（白名单已有） |
| A2 | `is_simulate` | `dry_run` | `none` | `config.json`（白名单已有） |
| A3 | `error_webhook_url` | `''`（避免误报） | 保留 | `config.json`（白名单已有） |
| A4 | `factor_col_limit` | 80 | 64（8GB 机器建议下调） | `config.json`（白名单已有） |
| A5 | `get_kline_num` | 14050 | 4050 | 账户文件（`config.py:94` 白名单已有） |

#### B. 配置层：**当前无法覆盖，必须改代码**

| # | 配置项 | 离线 | 服务器 | 问题 |
|---|---|---|---|---|
| B1 | `utc_offset` | 8 | 0 或 8 | `config.py:203` 在 JSON 块（`:119-134`）**之后**重新赋值，JSON 覆盖被冲掉 |
| B2 | `proxy` | `127.0.0.1:7897` | `None` | `config.py:210` 同样在 JSON 块之后，且不在白名单 |
| B3 | `clean_start` | 无意义 | `True` | 不在 `config.py:125` 白名单 |

`utc_offset` 影响面（差 1 就会错位）：

```
core/utils/datatools.py:251     df['candle_begin_time'] + utc_offset < run_time
core/backtest_config.py:791     end_time = run_time - utc_offset
tools/robust40/live.py:90       expected = expected_bar(run_time, minute, utc_offset)
core/binance/base_client.py:348 K 线过滤
```

**若服务器是 UTC 时区而本地是 UTC+8，`utc_offset` 差 8，尾行直接差 8 根，`update_state` 的 `expected` 校验会失败。**

#### C. 行为层：新增配置项

| # | 新增项 | 语义 | 默认 |
|---|---|---|---|
| C1 | `kline_source` | `'pkl'` 读全历史 / `'arrow'` 读发布快照 | `'pkl'`（保持现状） |
| C2 | `arrow_min_rows` | arrow 视为可用的最小行数，须 `>= get_kline_num` | `None`（跟随 `get_kline_num`） |

`required_history_start` 语义按 `kline_source` 分叉：

| 场景 | 校验方式 |
|---|---|
| `kline_source='pkl'`（离线） | 现状：首行 ≤ `2025-10-28 00:40:00` |
| `kline_source='arrow'`（服务器） | 改为相对校验：**首行 ≤ 末行 − `get_kline_num` 小时**，且 12 个因子列齐全 |

#### D. 代码层：`live.py` 有独立的第二次 pkl 读盘

`tools/robust40/live.py:141-153` **不经过 `datatools`**，直接 glob pkl：

```python
for path in sorted((Path(bmac_data_path)/account.hour_offset).rglob('swap_dict_batch*.pkl')):
    for symbol, frame in pd.read_pickle(path).items():
        raw[symbol] = frame.loc[frame.candle_begin_time <= expected]
```

即使 `datatools` 改成读 arrow，这里仍然读 14060 根 pkl。**必须一并改，否则 4050 方案不成立。**

#### E. 平台层：代码分支，不是配置

| # | 差异 | 现状 | 处置 |
|---|---|---|---|
| E1 | 多进程启动方式 | Windows `spawn` / Linux `fork` | `prewarm_robust40.py:82-83,153-156` 已用 subprocess 重入 + 环境变量隔离，**无需改** |
| E2 | numba 编译 | `@njit(cache=False)`，两平台各自编译 | 结果遵循 IEEE754，差异在 1e-16 量级；`_same(atol=1e-10)` 可容 |
| E3 | 控制台编码 | `startup.py:52-55` 仅 Windows 分支 reconfigure | Linux 默认 UTF-8，**无需改**；离线入口需沿用同一段 |
| E4 | 文件锁 | `local_risk_model.lock()` 用 `O_CREAT\|O_EXCL` | 跨平台可用；崩溃残留 `.lock` 会死锁，需人工清理（记为已知风险） |
| E5 | pickle 跨平台 | pandas/numpy/sklearn pickle | **两机 Python 3.11 + numpy/sklearn 版本必须一致**，否则 `pd.read_pickle` 可能告警或失败 |
| E6 | 路径大小写 | Linux 区分，Windows 不区分 | 代码内路径全小写，**无需改**；账户文件名保持原样 |

#### F. 网络隔离：离线体不得触网

| # | 触网点 | 位置 | 处置 |
|---|---|---|---|
| F1 | `check_accounts()`（杠杆/持仓模式/联合保证金） | `startup.py:167-177` | 离线入口不调用 |
| F2 | `reset_max_leverage` / `update_account_info` | `core/account_exec.py:57-58` | 离线 `is_debug=True` 时跳过 |
| F3 | `run_statistics_async` 子进程 | `startup.py:74-83` | 离线入口不调用 |
| F4 | `proxy` | `config.py:210` | 离线置 `None`（见 B2） |

### 2.3 数值差异（不是配置差异，交给阈值）

配置归零之后仍会剩下的差异，分三类，处置见 **§3.0**：

| 类 | 来源 | 量级 | 处置 |
|---|---|---|---|
| G2 浮点环境 | numba 机器码、BLAS 归约顺序、sklearn 版本 | 1e-16 → 累积 1e-12 | 固定线程数 + 同版本，靠阈值吸收 |
| G3 lot 量子化 | 两条 equity 起点不同，整数 lot 舍入路径不同 | **实测 1.1e-3** | 阈值放宽到 1e-2 |
| G4 重叠段本身 | `incoming` 的重叠行 | 任意 | 不进结果，只进体检 |

**这三类都不需要「解决」，只需要「定量 + 可观测」。**

---

## 3. 代码改动清单

共 10 处，约 100 行改动。其中 **3.0 的 P1–P4 是「接受细微差异」的落地**，C-1–C-6 是「双运行体」的落地，两批独立可分批上线。

### 3.0　数值差异容忍设计（P1–P4）

#### 3.0.1 差异源三分：哪些能归零

| 类 | 来源 | 能否归零 | 归零手段 |
|---|---|---|---|
| **G1 可归零** | `utc_offset` 差 8 / 币种列表不同 / arrow 缺列 | ✅ 完全 | C-1、C-3 的硬校验，见 §2.2-B/C |
| **G2 不可归零** | numba 机器码、BLAS 归约顺序、sklearn 版本 | ❌ 只能压到 1e-12 | 固定 `NUMBA_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、两机同版本 |
| **G3 量子化噪声** | `RebRobust40` 的整数 lot 舍入，两条 equity 起点不同 | ❌ 必然存在，实测 **1.1e-3** | 靠阈值容纳，不试图消除 |
| **G4 业务无关** | `incoming` 重叠段本身 | — | 它不进结果，只进体检 |

G3 的量化依据（`equity≈1e4`，168 根重叠，~30% 调仓率，10 币）：

```
单笔 lot 舍入 ≤0.5 → 504 次独立扰动随机游走
累积 ≈ 0.5×√504 ≈ 11.2 USDT  →  11.2/1e4 = 1.1e-3
```

**1e-2 的阈值对 G3 有 9 倍余量，对 G2 有 1e10 倍余量。这是「以防万一」的安全边际。**

#### 3.0.2 阈值分档（核心表）

| 校验项 | 位置 | 现状 | **新阈值** | warn 档 | fail 档 |
|---|---|---|---|---|---|
| `equity` **OHLC** | `:97` | `atol=1e-8, rtol=1e-6` | **`atol=1e-4, rtol=1e-2`** | 1e-2 | 1e-2 |
| `equity` **因子列**（非 OHLC） | `:97` | 同上一起比 | `atol=1e-8, rtol=1e-6`（拆出，不变） | 1e-6 | 1e-6 |
| `S1..S14 positions` | `:85` | `atol=1e-10, rtol=1e-8` | `atol=1e-6, rtol=1e-4` | **1e-4** | **1e-2** |
| `leverage` | `:87` | `atol=1e-10` | 不变 | 1e-10 | 1e-10 |
| `simulation` 价格数组 | `:117` | `atol=1e-10` | 不变 | 1e-10 | 1e-10 |
| 参考价（BTC） | `:129` | `atol=1e-10` | 不变 | 1e-10 | 1e-10 |

`positions` 的 warn 档（1e-4）故意紧于 `equity`（1e-2）：它是「选币是否走偏」的直接证据，正常值应当是 0；`fail` 档才放宽到 1e-2，避免个别币跨机器浮点造成的边界翻转直接阻断下单。

**为什么 `equity` 放宽到 1e-2 不损失检出能力**——因为主守门员不是它：

| 错误场景 | `positions` 差异 | `equity` 相对差 | 谁先拦 |
|---|---|---|---|
| 预计算全部未命中 | O(1) 大面积 | O(1) | `positions` + `equity` |
| ILLQ 未命中（4050<13920） | O(1) 大面积 | ~1e-1 | `positions` + `equity` |
| CciV3 未命中（权重 1/5） | 个别币整列翻 | 2e-2~5e-2 | **`positions`（1e-4 档必然触发）** |
| 单币数据缺失 | 单列 O(1) | 1e-4~1e-3 | **`positions`** |
| 纯浮点环境差异 | 1e-16 | 1e-12 | 不触发 |

`positions` 是当期因子直接决定的、非递推的、稀疏的（大部分为 0），正常差异应当是 **0**。把它卡在 1e-4，任何「选币走偏」都会被打出来。`equity` 只是副证。

#### 3.0.3 三档响应策略

`update_state` 增加 `parity_policy` 参数：

| policy | 差异 < warn | warn ≤ 差异 < fail | 差异 ≥ fail |
|---|---|---|---|
| `strict`（现状，保留给 CI/回归） | 通过 | **抛错** | 抛错 |
| `warn`（**服务器默认**） | 通过 | 打印 + 落 JSONL，**继续更新 state** | 抛错，停在旧 state |
| `off`（离线首次迁移，可选） | 通过 | 通过 | 抛错 |

选择 `warn` 作默认的理由：重叠区差异不进入结果（§1.3），阻断更新没有收益，只会让服务器停在旧 state 上错过行情。**真正的保护是 `fail` 档 —— 它只在数据源错位/因子未命中时才触发。**

#### 3.0.4 对账指标落盘

每次 `update_state` 追加一行 `logs/parity_<account>.jsonl`：

```json
{"ts":"2026-10-30T05:40:03Z","account":"40m_robust_4h","expected":"2026-10-30T04:40:00",
 "overlap":168,"policy":"warn",
 "checks":[{"label":"S5 positions","max_abs":1.2e-09,"max_rel":3.1e-08,"level":"pass"},
           {"label":"S5 equity OHLC","max_abs":8.7,"max_rel":9.1e-04,"level":"warn"},
           {"label":"swap_close_p","max_abs":0.0,"max_rel":0.0,"level":"pass"}],
 "worst":"S5 equity OHLC"}
```

这条记录是「差异被接受」的唯一凭证。没有它，`warn` 模式就是静默吞错。**保留 90 天**，与 `cleanup_old_snapshots` 同期清理。

#### 3.0.5 代码改动（P1–P4）

| # | 文件:行 | 改动 |
|---|---|---|
| **P1** | `runtime.py:56-58` | `_same()` 拆为 `_deviation()`（返回 `label/max_abs/max_rel`）+ `_same()`（保留抛错，向后兼容 `tests/test_robust40_runtime.py:47,69` 的 `pytest.raises`） |
| **P2** | `runtime.py:83-129` | 6 处调用改为 `_deviation()` + `_verdict()`；`positions`/`equity` 的阈值按 §3.0.2 分档 |
| **P3** | `runtime.py:97` | 拆两段：因子列（`rtol=1e-6`）+ OHLC（`atol=1e-4/rtol=1e-2`，先 `scale` 归一再比） |
| **P4** | `runtime.py:160-182` | `update_state(..., parity_policy='warn')`；收集 checks → 落 JSONL → 按档位裁决 |

**P4 附带修一处**：`:169` 的 `if incoming['index'][-1] != expected: raise` 改为——

```python
tail = incoming['index'][-1]
if tail > expected:                       # 取到未闭合 bar：截断而非报错
    incoming = _truncate(incoming, expected)
elif tail < expected:                     # 真缺数据：保留报错
    raise ValueError('Incoming data does not end at the expected closed bar')
```

「取数时刻不同」导致的差异绝大多数属于 `tail > expected`（离线/服务器取数时刻差几分钟，bn 已吐出下一根未闭合 bar），截断是最干净的处置。

---

### 3.1　双运行体改动（C-1 – C-6）

### C-1　`config.py`：把 JSON 覆盖移到文件末尾

**位置**：`config.py:119-135`

**问题**：`utc_offset`（`:203`）和 `proxy`（`:210`）在 JSON 块之后赋值，覆盖无效。

**改法**：删除 `:119-135` 的 JSON 块，在 `exchange_basic_config` 定义之后（约 `:218`）插入：

```python
# JSON 覆盖必须在所有默认值定义之后
_json = get_file_path('config.json', as_path_type=True)
if _json.exists():
    _data = json.loads(_json.read_text(encoding='utf-8'))
    _allowed = ('realtime_data_path', 'error_webhook_url', 'is_simulate',
                'factor_col_limit', 'lookback_days', 'incremental_lookback_hours',
                'utc_offset', 'proxy', 'clean_start', 'kline_source', 'arrow_min_rows')
    for _k in _allowed:
        if _k in _data:
            globals()[_k] = _data[_k]
    if 'proxy' in _data:
        exchange_basic_config['proxies'] = _data['proxy']
```

注意 `exchange_basic_config['proxies']` 需同步，否则改 `proxy` 不影响交易所客户端。

### C-2　`datatools.py:306-315`：arrow 启用条件

**现状**：
```python
fixed_history = isinstance(required_start, (str, pd.Timestamp))
arrow_dict = None if fixed_history else read_arrow_market_dict_for_run_time(...)
```

**改为**：
```python
from config import kline_source, arrow_min_rows
fixed_history = isinstance(required_start, (str, pd.Timestamp))
use_arrow = kline_source == 'arrow' or not fixed_history
if use_arrow and arrow_min_rows and ...:      # 行数不足则回退
    use_arrow = False
arrow_dict = read_arrow_market_dict_for_run_time(...) if use_arrow else None
```

### C-3　`datatools.py:270-280`：校验与裁切分叉

**现状**：`required_history_start` 有值时保留全历史、绝对起点断言。

**改为**：
```python
required_start = getattr(conf, 'required_history_start', None)
came_from_arrow = <C-2 的 use_arrow 结果>   # 需传参进来
if came_from_arrow:
    need = conf.get_kline_num()
    if len(df) < need:
        raise ValueError(f'{symbol}: arrow rows {len(df)} < {need}')
    df = df[-need:]
elif isinstance(required_start, (str, pd.Timestamp)):
    ... 现状不变 ...
else:
    df = df[-conf.get_kline_num():]
```

并把 12 个预计算列的存在性校验放在 `came_from_arrow` 分支里（缺列直接 raise，不允许静默重算）。

### C-4　`live.py:141-153`：raw 复用同一数据源

**现状**：独立 glob pkl。

**改为**：从 `datatools` 抽一个 `load_raw_swap(conf, expected)`，内部按 `kline_source` 决定读 arrow 还是 pkl。`live.py` 改为调用它。

抽出的函数放在 `core/utils/datatools.py`，两处共用，保证"选币用的 K 线"与"observation 用的 K 线"是同一份。

### C-5　`accounts/*.py`：显式声明运行体

在 `strategy_config.params.runtime` 旁增加：

```python
kline_source = 'arrow'      # 服务器；离线写 'pkl'
arrow_min_rows = 4050       # 服务器；离线不写
get_kline_num = 4050        # 服务器；离线 14050
required_history_start = None   # 服务器走 arrow 时置 None
```

离线账户保持 `required_history_start = '2025-10-28 00:xx:00'`。

### C-6　离线入口：新增 `tools/prewarm_full.py`（或复用 prewarm_robust40.py）

现 `tools/prewarm_robust40.py` 已实现：冻结输入 → 隔离 workspace → runpy 同一套 `MultiEquityBacktestConfig` → `rebuild_robust40_live_baseline` → export package。

需要补充的只有两点：
1. 强制 `kline_source='pkl'`、`get_kline_num=14050`，并在启动时断言 `len(df) >= 14050`
2. 产出后打印 `snapshot_time.txt`、`parent.index[-1]`、`predictions.time.nunique()`（必须 ≥ 365），作为迁移前的自检条

---

## 4. 两个入口

| | 离线 | 服务器 |
|---|---|---|
| 入口 | `python tools/prewarm_robust40.py --account accounts/40m_robust_4h.py --workspace ... --output ...` | `python startup.py`（现有） |
| 循环 | 一次性 | `while True` 每 5 分钟，仅 offset 分钟触发 |
| 账户配置 | `get_kline_num=14050`，`kline_source='pkl'`，`required_history_start='2025-10-28'` | `get_kline_num=4050`，`kline_source='arrow'`，`required_history_start=None` |
| 产出 | `warmup_packages/<ts>/`（`runtime_bootstrap.pkl` + `snapshot/`） | `data/robust40_state/<account>.pkl` 自增 |
| 触网 | 不允许 | 允许（下单 + 行情） |

迁移动作（一次性）：

```
1. 离线产出 warmup_packages/40m_<ts>/
2. 整包 rsync 到服务器 research/warmup_packages/40m_<ts>/
3. 服务器账户文件 runtime.warmup_path 指向该包
4. 服务器 startup.py 启动，prepare_startup 校验并 restore
5. 之后每轮 update_state 自增，state 独立于离线
```

---

## 5. 验收标准

### 5.1 迁移前（离线自检）

| 检查项 | 阈值 |
|---|---|
| `len(raw[symbol])` 中位数 | ≥ 14050 |
| `predictions.time.nunique()` | ≥ 365 |
| `virtual_accounts[0].index` 长度 | ≥ 365 |
| 12 个预计算列存在性 | 100% |
| `weekly_mix` 是否激活过（出现过非 `[1,0,...]` 行） | 必须为是 |

### 5.2 迁移后（服务器首轮）

| 检查项 | 方法 | 阈值 |
|---|---|---|
| 重叠段 `positions` | `_deviation` 的 `max_rel` | < 1e-4（正常应为 0，非零即有币种边界翻转） |
| 重叠段 `equity` OHLC | `_deviation` 的 `max_rel` | < 1e-2（理论噪声 1.1e-3，超 1e-2 说明链路坏了） |
| 重叠段因子列 / 价格数组 | `_deviation` 的 `max_rel` | < 1e-6 |
| `parent` / `delta` / `targets` 与离线同日对比 | 独立脚本 `tests/test_dual_runtime_parity.py` | `max_abs ≤ 1e-9`（这两项**不允许**放宽到 1e-2，它们是下单依据，不是体检项） |
| S5 是否生效 | `delta` 非全零 | 必须为是 |
| 单轮耗时 | `advance` 前后时间戳 | < 40s |
| 对账记录 | `logs/parity_40m_robust_4h.jsonl` 末行 | 存在且 `worst` 不为 `fail` |

**注意两套阈值的分工**：重叠区体检用 1e-2（容噪声），`parent`/`delta`/`targets` 用 1e-9（不许噪声）。因为后者是真正下单的数字，而前者只是历史副本的形状校验。

### 5.3 回归对照脚本

新增 `tests/test_dual_runtime_parity.py`：

```python
# 同一 run_time，分别用 kline_source='pkl'(14050) 与 'arrow'(4050)
# 打印/断言：parent、targets['swap']、delta 的最大绝对差
```

---

## 6. 落地顺序

分两批，**P 批先上线**（差异容忍是双运行体的前提，且可独立验证），C 批随后。

### 6.1 P 批：数值差异容忍（先做）

| 步 | 动作 | 依赖 | 风险 |
|---|---|---|---|
| P0 | 备份 `runtime.py`，执行现有 `tests/test_robust40_runtime.py` 记为基线 | 无 | 无 |
| P1 | `_same` → `_deviation` + `_same` 双函数（**行为完全不变**） | — | 零 |
| P2 | 6 处校验改用 `_deviation`，阈值按 §3.0.2 分档 | P1 | 低（仅在差异 >1e-6 时行为不同） |
| P3 | `:97` 拆因子列 / OHLC 两段，OHLC 走 `atol=1e-4/rtol=1e-2` | P2 | 低 |
| P4 | `update_state(parity_policy='warn')` + JSONL 落盘 + `tail>expected` 截断 | P2 | 中（改 update 入口签名，需同步 `live.py:143` 与 `research/check_warmup_live_adapter.py:45`） |
| P5 | 新增 `tests/test_parity_tolerance.py`：构造 1e-4 量子化偏差，断言 `warn` 通过 / `strict` 抛错 | P4 | — |

**P 批回退**：`parity_policy='strict'` 单开关回到现状，不需要回滚代码。

### 6.2 C 批：双运行体

| 步 | 动作 | 依赖 | 风险 |
|---|---|---|---|
| C-1 | `config.py` JSON 块移到文件末尾 | P5 | 低，但影响面广（`utc_offset`/`proxy` 全局） |
| C-2 | `datatools.py` arrow 启用条件改看 `kline_source` | C-1 | 中，需回归 pkl 路径不变 |
| C-3 | `datatools.py` 校验与裁切分叉 | C-2 | 中 |
| C-4 | `live.py` raw 复用同一数据源 | C-2 | 中 |
| C-5 | 写 `tests/test_dual_runtime_parity.py` 并跑通 | C-2..C-4 | — |
| C-6 | 两套账户配置 | C-5 | 低 |
| C-7 | 离线入口自检条 | C-6 | 低 |
| C-8 | 离线跑满 → 迁移 → 服务器首轮 | C-7 | 高（首次真跑） |

**C 批回退**：`kline_source='pkl'` 单开关回退到现状。

### 6.3 服务器首周观察项

首 5 个交易日必须每天看 `logs/parity_<account>.jsonl`：

| 现象 | 判读 | 动作 |
|---|---|---|
| `S* positions` 恒为 0 | 预计算全命中，链路健康 | 无 |
| `S* equity OHLC` 稳定在 1e-4~2e-3 | 正常量子化噪声 | 无 |
| 某天 `equity` 突跳到 >5e-3 | 该日有数据修订或未闭合 bar 混入 | 查该日 `expected` 与 arrow 末行 |
| `positions` 出现 1e-4 以上非零 | 有币种在选币边界翻转 | 查该币当日因子列，判断是否跨机器浮点导致 |
| 任一项进 `fail` | state 停在旧值，未下单 | 立即 `kline_source='pkl'` 全量重建 |

---

## 7. 风险登记

| # | 风险 | 触发条件 | 缓解 |
|---|---|---|---|
| R1 | 两机 numpy/sklearn 版本不同 | 迁移后 `read_pickle` 告警/失败 | 迁移前 `pip freeze` 比对，锁定版本 |
| R2 | 服务器时区与本地不同 | `utc_offset` 差 8，尾行错位 | C-1 落地后 JSON 显式设置，并在启动日志打印 `utc_offset` |
| R3 | arrow 缺列 | 因子列被裁或发布失败 | C-3 分支内硬 raise，不静默重算 |
| R4 | `.lock` 残留 | 进程被 kill | 启动时检测 `.lock` 且进程不存在则清理（需新增 3 行） |
| R5 | state 与 arrow 时间不衔接 | 迁移包与首轮数据差 >1h | `update_state` 已有 `expected` 校验，失败即停在旧 state，安全 |
| R6 | 新上币无因子列 | 上市晚于 arrow 发布 | `tail()` 保证该币自身全历史都在 arrow 里，因子值正确 |
| R7 | `warn` 模式静默吞掉真错误 | 阈值过宽 + 无对账 | **必须 P4 的 JSONL 同时上线**，否则 `warn` 等于关闭校验；首周每日人工过一遍 §6.3 |
| R8 | 服务器取到未闭合 bar | bn 在 offset 分钟前后吐新 bar | P4 的 `tail>expected` 截断；`expected` 由 `expected_bar(run_time, minute, utc_offset)` 唯一确定，不做模糊匹配 |
| R9 | 两机浮点环境差异放大 | 585 天递推累积 | 固定 `OMP_NUM_THREADS=1`/`NUMBA_NUM_THREADS=1`；实测噪声 1.1e-3 远小于 1e-2 阈值 |
| R10 | 因子列比对误伤未命中 | `equity` 因子列仍用 1e-6 | 这是**故意**的：因子是预计算是否生效的直接证据，不允许放宽 |
