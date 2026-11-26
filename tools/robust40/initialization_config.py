"""Functional resource/input settings only; all strategy parameters stay in accounts."""
# 仅控制MODEL运行资源与历史覆盖；收益/权重/仓管策略参数仍在各账户文件。
# 保留原入口的16币批次；新编排已离线验证的组合为data_pipeline=True、32币。
# 仅改变功能配置，不改变账户参数；列分片配置仍供手工PKL预热使用。
INITIALIZATION = dict(
    # 新编排已完成本地全量对拍/计时，但选币内存峰值上升。
    # Ubuntu内存/swap隔离验证前保留旧入口；启用时同时把下面批次改为32。
    # 普通4050续算不使用此开关。
    data_pipeline=False,
    minimum_history_bars=14000, factor_col_limit=16, cross_section_chunk_size=1,
    factor_symbol_batch_size=16,
    # 0回退原初始化选币；正数表示包含完整市场的选币时间块。
    # False入口沿用Parquet→共享IPC；True入口由时序直接生成统一IPC。
    # 该值不是内存上限；普通4050续算不使用。容量与耗时见本轮复核文档。
    selection_block_hours=2016,
)
# 唯一MODEL总并发上限；初始化/续算/手工预热共同使用，传统策略不受影响。
# 时序因子最多4进程；大表选币/聚合/模拟再受下面的阶段上限约束。
# 初始化阶段1、续算阶段3；默认选币内部1线程，启用共享列缓存时最多2线程。
# 共享缓存转换/读取上限为MODEL_JOB_NUM，阶段不重叠；数值库仍1线程。
MODEL_JOB_NUM = 4
# 时间窗口仅供观测。冻结输入后按实际压力准入，低优先级填充空闲CPU。
# MemoryHigh固定为4.5GiB，避免每5分钟强行回收活跃大表；不承诺忙时瞬降2GiB。
# 预算包含64MiB监督进程预留。仅Linux自动初始化启用资源组，手工预热不启用。
RESOURCE_POLICY = dict(
    period_seconds=300, busy_head_seconds=30, busy_tail_seconds=30,
    poll_seconds=2, busy_memory_bytes=2*1024**3,
    exclusive_memory_bytes=int(4.5*1024**3), max_memory_bytes=5*1024**3,
    supervisor_reserve_bytes=64*1024**2,
    initialization_nice=10, trading_nice=3, initialization_cpu_weight=10,
    initialization_backtest_workers=1, trading_backtest_workers=3,
    swap_max_bytes=12*1024**3,
    admission_min_available_bytes=256*1024**2,
    pressure_available_bytes=1024**3, swap_reserve_bytes=512*1024**2,
    memory_full_limit=25., io_full_limit=50.,
    critical_available_bytes=128*1024**2, critical_seconds=30,
    telemetry_seconds=10,
    # Phase 2 dynamic BURST <-> YIELD CPU/IO governance (never shrinks MemoryHigh).
    # Keep False by default in repo (enabled per host like data_pipeline / ARROW_DEMAND_ENABLED).
    dynamic_burst_enabled=False,
    burst_cpu_weight=100,
    burst_io_weight=100,
    burst_cpu_quota='',
    yield_cpu_weight=5,
    yield_io_weight=10,
    yield_cpu_quota='20%',
    yield_preempt_seconds=10,
    burst_resume_hold_seconds=6,
    burst_min_available_bytes=int(1.5*1024**3),
    yield_gate_available_bytes=int(1.2*1024**3),
    yield_gate_memory_full=10.,
    yield_gate_io_full=15.,
    foreground_max_age_seconds=3600,
)
LIVE_BARS = 4050
# Enable with alpha arrow_demand_enabled + an audited arrow_demand_min_rows.
# Short publication keeps max(5000, LIVE_BARS + min_kline_num + 1) mapping warmup;
# source PKL/factors/pivots stay full. Failure retains the full-history request.
ARROW_DEMAND_ENABLED = False
ARROW_CONSUMER_NAME = None  # Defaults to installation folder name; unique per shared producer.
# Bounds waiting for missing input only; valid calculation has no time deadline.
DATA_READY_WAIT_SECONDS = 240
PREWARM_LEAD_SECONDS = 10
# Include cutoff-24h and its strictly earlier restore point, as well as cutoff.
CHECKPOINT_POINTS = 26
REVISION_HISTORY_HOURS = 168

# Failure payloads are disposable; keep bounded flat account diagnostics.
DIAGNOSTIC_LOG_COUNT = 24
