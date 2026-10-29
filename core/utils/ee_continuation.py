"""
邢不行｜策略分享会
仓位管理实盘框架

entry_exit 进出场选币模式的 **实盘跨周期续算** 机制（回测不走这里）。

与回测的区别：回测一次性全历史单跑(strategy_config._run_entry_exit_one_side 的 state_path=None 分支)；
实盘每小时只拿到最近一小段窗口，靠这里的 **自包含累积缓存** 维持持仓状态：
  - 缓存自己存住最近 N..30天 的引擎输入(inputs) + active 起点处的引擎状态(seed)；
  - 每周期把新截面追加进缓存、裁到最多 30 天(裁掉的部分推进 seed)、从缓存第一根整段重放；
  - 锚点(seed)永远落在缓存内，**与 lookback_days / df 窗口大小彻底解耦**(窗口塌成 1H 也不影响)；
  - byte-exact 由 seed 携带 hold_hours 等保证，与 max_hold 多大无关。
输出仅覆盖本轮 df 窗口(全列)，由引擎在 30 天缓存上算出的选中结果映射回 scored 得到。
"""
import os
import pickle
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from numba import njit

from core.utils.log_kit import logger

# 状态文件格式版本号。不对外，改格式时手动清空 ee_state 重跑即可，保持 '1' 不再递增。
_EE_ENGINE_VERSION = '1'
EE_RETENTION_CAP_DAYS = 30  # 缓存最多保留天数(上限)；下限是 N=max(fill_lag,ceil(hp小时))+24，由调用方算好传入

_INPUT_COLS = ['candle_begin_time', 'symbol', '_ee_score', '_ee_in', '_ee_out', '_ee_target']


def ee_state_path(snapshot_dir, fullname: str, side: str) -> Path:
    return Path(snapshot_dir) / 'ee_state' / f'{fullname}__{side}.pkl'


def _ee_obj_sig(items) -> tuple:
    """把 factor/entry/exit 配置列表压成可哈希的稳定签名（用于配置指纹）。"""
    sig = []
    for it in items or ():
        method = getattr(it, 'method', None)
        sig.append((
            str(getattr(it, 'col_name', None)), str(getattr(it, 'weight', None)),
            str(getattr(it, 'is_sort_asc', None)),
            str(getattr(method, 'how', None) if method is not None else None),
            str(getattr(method, 'range', None) if method is not None else None),
        ))
    return tuple(sig)


def ee_cfg_fingerprint(side, fill_lag, exit_cooldown, max_hold_hours, select_coin_num,
                        factor_list, entry_list, exit_list) -> str:
    """根据影响选币的参数(进出场/补位/目标数/因子)算个指纹，参数一变旧状态文件就失效、重新全量算。"""
    payload = (side, str(fill_lag), str(exit_cooldown), str(max_hold_hours), str(select_coin_num),
               _ee_obj_sig(factor_list), _ee_obj_sig(entry_list), _ee_obj_sig(exit_list), _EE_ENGINE_VERSION)
    return hashlib.md5(repr(payload).encode('utf-8')).hexdigest()


def ee_build_meta(name, fullname, side, factor_list, entry_list, exit_list,
                   filter_list, filter_list_post, fill_lag, exit_cooldown,
                   max_hold_hours, select_coin_num, hold_period) -> dict:
    """组装续算缓存的策略配置快照——纯供人工打开 pkl 时定位归属，不参与续算逻辑。"""
    return {'name': name, 'fullname': fullname, 'side': side,
            'factor_list': str(factor_list), 'entry_list': str(entry_list), 'exit_list': str(exit_list),
            'filter_list': str(filter_list), 'filter_list_post': str(filter_list_post),
            'fill_lag': fill_lag, 'exit_cooldown': exit_cooldown, 'max_hold_hours': max_hold_hours,
            'select_coin_num': select_coin_num, 'hold_period': hold_period}


def save_ee_state(path: Path, seed_time, seed_rows, inputs, last_we, cfg_fp: str, meta=None) -> None:
    """把续算缓存存到文件(原子写；失败只告警，下轮从空仓算)。
    seed_time: active 边界，重放时 time>seed_time 的截面为 active，<=的为预热。
    seed_rows: active 起点处引擎状态 list[(symbol, 是否持有, 已持有期数, 冷却剩余期数)]，下轮作种子。
    inputs: 保留的引擎输入截面(pandas，含前置 fill_lag 个预热截面)，下轮整段重放。
    last_we: 本轮最新处理截面时间，防 stutter 重复覆盖。
    meta: 策略配置快照，纯供人工查找；load 时用 fullname 做防呆。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'seed_time': pd.Timestamp(seed_time), 'seed_rows': seed_rows,
                   'inputs': inputs, 'last_we': pd.Timestamp(last_we),
                   'cfg_fp': cfg_fp, 'engine_version': _EE_ENGINE_VERSION, 'meta': meta}
        tmp = path.with_name(path.name + '.tmp')
        with open(tmp, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f'[entry_exit] 状态落盘失败({getattr(path, "name", path)})，下周期将冷启动: {e}')


def load_ee_state(path: Path, cfg_fp: str, expect_fullname=None):
    """读取缓存；文件缺失/损坏、参数指纹/格式版本对不上、或 meta.fullname 与当前策略不符 -> 返回 None(冷启动)。"""
    try:
        if not path.exists():
            return None
        with open(path, 'rb') as f:
            payload = pickle.load(f)
        if payload.get('cfg_fp') != cfg_fp or payload.get('engine_version') != _EE_ENGINE_VERSION:
            return None
        stored_fullname = (payload.get('meta') or {}).get('fullname')
        if expect_fullname is not None and stored_fullname is not None and stored_fullname != expect_fullname:
            return None      # 缓存归属的策略与当前不符(文件名意外撞车) -> 当失效、重建
        return payload
    except Exception as e:
        logger.warning(f'[entry_exit] 状态读取失败({getattr(path, "name", path)})，冷启动: {e}')
        return None


def _cast_inputs(df_pl: pl.DataFrame, ref: pl.DataFrame) -> pl.DataFrame:
    """把缓存里读出的 inputs(pandas->polars) 列类型对齐到本轮 ref，便于 concat。"""
    return df_pl.with_columns([
        pl.col('candle_begin_time').cast(ref.schema['candle_begin_time']),
        pl.col('symbol').cast(ref.schema['symbol']),
        pl.col('_ee_score').cast(pl.Float64),
        pl.col('_ee_in').cast(pl.Int8),
        pl.col('_ee_out').cast(pl.Int8),
        pl.col('_ee_target').cast(pl.Int64),
    ])


@njit
def compute_entry_exit_numba_seeded(times, symbol_ids, scores, in_mask, out_mask,
                                    target_counts, n_symbols, fill_lag,
                                    exit_cooldown, max_hold_hours,
                                    init_held, init_hold_hours, init_blacklist,
                                    active_start_time_id):
    """进出场选币引擎（可种子 + warmup 通用底座；compute_entry_exit_numba 是它的零种子薄包装）。

    held/hold_hours/blacklist_remaining 用 init_* 入参做初值（跨周期续算的种子）。
    active_start_time_id：time < 该值的截面为 **warmup**——只刷新 fill_lag 环形缓冲与 gi，
    **绝不写 result、绝不改 held/hold_hours/blacklist**（对齐老 VolumeMaxBias_进出场阈值分离 的
    active_start_time_id 语义）；time >= 该值的截面为 **active**，跑完整 出场→留存→补位。
    返回 (result, held, hold_hours, blacklist_remaining)——末截面状态用于落盘续算。

    逐截面：① 持仓触发 out_mask 或满 max_hold_hours 强制出场 ② 从 in_mask 非持仓按 fill_lag 期前
    score 补到 target，fill_lag>0 时滞后期缺席币(score<0)排除、入场门也取 fill_lag 期前判定；冷启动按当期。
    exit_cooldown>0：出场后拉黑 exit_cooldown 期。max_hold_hours>0：连续持满该期数强制出场。"""
    n_rows = len(times)
    result = np.zeros(n_rows, dtype=np.int32)
    held = init_held.copy()
    keep = np.zeros(n_symbols, dtype=np.int8)
    exited = np.zeros(n_symbols, dtype=np.int8)
    hold_hours = init_hold_hours.copy()
    blacklist_remaining = init_blacklist.copy()
    cand_idx = np.empty(n_symbols, dtype=np.int64)
    cand_score = np.empty(n_symbols, dtype=np.float64)

    buf_size = fill_lag if fill_lag > 0 else 1
    rank_buf = np.full((buf_size, n_symbols), -1.0, dtype=np.float64)
    in_buf = np.zeros((buf_size, n_symbols), dtype=np.int8)   # 入场门掩码环形缓冲（与 rank_buf 同口径滞后）
    buf_ptr = 0
    gi = 0

    start = 0
    for i in range(n_rows + 1):
        if i == n_rows or (i > 0 and times[i] != times[start]):
            end = i
            is_active = times[start] >= active_start_time_id

            if is_active:
                target = target_counts[start]

                for j in range(start, end):
                    sid = symbol_ids[j]
                    keep[sid] = 0
                    exited[sid] = 0

                # ① 出场 / 留存（出场条件 或 满 max_hold_hours 强制出场；出场则拉黑 exit_cooldown 期、hold_hours 清零）
                keep_count = 0
                for j in range(start, end):
                    sid = symbol_ids[j]
                    if held[sid] == 1:
                        next_hh = hold_hours[sid] + 1
                        force_exit = max_hold_hours > 0 and next_hh > max_hold_hours
                        if out_mask[j] == 1 or force_exit:
                            exited[sid] = 1
                            blacklist_remaining[sid] = exit_cooldown
                            hold_hours[sid] = 0
                        else:
                            keep[sid] = 1
                            result[j] = 1
                            hold_hours[sid] = next_hh
                            keep_count += 1

                # ② 补位（无入场门=in_mask 全 True；fill_lag>0 用滞后排名+滞后入场门并排除缺席；
                #    exit_cooldown>0 时跳过本期刚出场/仍在拉黑期的币）
                need = target - keep_count
                if need > 0:
                    m = 0
                    use_lag = fill_lag > 0 and gi > 0
                    for j in range(start, end):
                        sid = symbol_ids[j]
                        if keep[sid] == 1:
                            continue
                        if exited[sid] == 1 and exit_cooldown > 0:
                            continue
                        if blacklist_remaining[sid] > 0:
                            continue
                        if use_lag:
                            fr = rank_buf[buf_ptr, sid]
                            if fr < 0:
                                continue
                            if in_buf[buf_ptr, sid] != 1:   # 入场门也用 fill_lag 期前的判定，与补位排名同口径
                                continue
                        else:
                            if in_mask[j] != 1:             # 冷启动/无滞后：用当期入场门
                                continue
                            fr = scores[j]
                        cand_idx[m] = j
                        cand_score[m] = fr
                        m += 1
                    if m > 0:
                        order = np.argsort(cand_score[:m])
                        take = need if need < m else m
                        for k in range(take):
                            j = cand_idx[order[k]]
                            sid = symbol_ids[j]
                            result[j] = 1
                            keep[sid] = 1
                            hold_hours[sid] = 1

                # 重建 held（缺席的持仓币自动丢弃）
                held[:] = 0
                for j in range(start, end):
                    sid = symbol_ids[j]
                    if keep[sid] == 1:
                        held[sid] = 1

            # 写入本期 score / 入场门 到环形缓冲（warmup + active 都刷新；缺席币 score=-1、入场门=0）
            if fill_lag > 0:
                for a in range(n_symbols):
                    rank_buf[buf_ptr, a] = -1.0
                    in_buf[buf_ptr, a] = 0
                for j in range(start, end):
                    rank_buf[buf_ptr, symbol_ids[j]] = scores[j]
                    in_buf[buf_ptr, symbol_ids[j]] = in_mask[j]
                buf_ptr = (buf_ptr + 1) % buf_size

            # 期末：拉黑计数 -1（仅 active；必须在补位之后，确保出场当期仍被拉黑）
            if is_active:
                for a in range(n_symbols):
                    if blacklist_remaining[a] > 0:
                        blacklist_remaining[a] -= 1

            gi += 1
            start = i

    return result, held, hold_hours, blacklist_remaining


def _compute_holdings_seeded(df: pl.DataFrame, fill_lag: int, exit_cooldown: int = 0,
                      max_hold_hours: int = 0, seed=None, return_state: bool = False):
    """把已带打分/进出场标记/目标数的截面 df 交给 numba 引擎，返回逐行 0/1 选中数组。

    seed=None：从空仓开始算（全量）。
    seed={'rows','last_time'}：从上一轮持仓接着算——last_time 之前的截面只做预热(填补位用的滞后排名)，之后的截面正常选币。
    return_state=True：额外返回末截面持仓状态 list[(symbol, 是否持有, 已持有期数, 冷却剩余期数)]。"""
    syms = df['symbol'].to_list()
    sym_to_id = {s: idx for idx, s in enumerate(sorted(set(syms)))}
    n_symbols = len(sym_to_id)
    # 截面里没有任何币种时直接返回空，避免空数组进 numba 引擎崩溃
    if n_symbols == 0:
        empty = np.zeros(df.height, dtype=np.int32)
        return (empty, []) if return_state else empty
    symbol_ids = np.array([sym_to_id[s] for s in syms], dtype=np.int64)
    times = df['candle_begin_time'].rank(method='dense').cast(pl.Int64).to_numpy()
    scores = df['_ee_score'].cast(pl.Float64).to_numpy()
    in_m = df['_ee_in'].to_numpy().astype(np.int8)
    out_m = df['_ee_out'].to_numpy().astype(np.int8)
    targets = df['_ee_target'].cast(pl.Int64).to_numpy()
    fl, ec, mh = int(fill_lag or 0), int(exit_cooldown or 0), int(max_hold_hours or 0)

    init_held = np.zeros(n_symbols, dtype=np.int8)
    init_hh = np.zeros(n_symbols, dtype=np.int32)
    init_bl = np.zeros(n_symbols, dtype=np.int32)
    active_start = int(times.min())                                   # 默认从空仓全量算

    if seed is not None:
        last_np = np.datetime64(pd.Timestamp(seed['last_time']))
        active_mask = df['candle_begin_time'].to_numpy() > last_np
        cand_active_start = int(times[active_mask].min()) if active_mask.any() else int(times.max()) + 1
        warmup_count = cand_active_start - int(times.min())           # 续算前用于预热的截面数
        # fill_lag>0 时补位要读 fill_lag 期前的排名，至少得有 fill_lag 个预热截面才能算准；
        # 不够就退回从空仓全量算这一轮，避免排名不全导致补位选错。
        if fl > 0 and warmup_count < fl:
            logger.warning(f'[entry_exit] 续算预热截面数({warmup_count}) < fill_lag({fl})，本轮改为从空仓全量计算；'
                           f'请把回放窗口加长到至少 fill_lag+1 个截面。')
        else:
            active_start = cand_active_start
            for sym, h, hh, bl in seed['rows']:
                sid = sym_to_id.get(sym)
                if sid is None:                                       # 上一轮持有的币本轮不在宇宙里 -> 跳过
                    continue
                init_held[sid] = np.int8(1 if h else 0)
                init_hh[sid] = np.int32(hh)
                init_bl[sid] = np.int32(bl)

    result, held, hold_hours, blk = compute_entry_exit_numba_seeded(
        times, symbol_ids, scores, in_m, out_m, targets, n_symbols, fl, ec, mh,
        init_held, init_hh, init_bl, active_start)

    if not return_state:
        return result
    id_to_sym = {idx: s for s, idx in sym_to_id.items()}
    rows = [(id_to_sym[sid], int(held[sid]), int(hold_hours[sid]), int(blk[sid]))
            for sid in range(n_symbols) if held[sid] == 1 or blk[sid] > 0]
    return result, rows


def run_continuation(scored: pl.DataFrame, fill_lag, exit_cooldown, max_hold_hours, direction: int,
                     state_path: Path, cfg_fp: str, meta, n_floor: int, cap_cs: int) -> pl.LazyFrame:
    """实盘单侧续算：自包含累积缓存。scored 已带 _ee_*/全列。返回选中行(全列)+方向+等权。

    n_floor = max(fill_lag, ceil(hp小时)) + 24（兜底最小留存，正常运行恒满足，不足即数据异常报错）；
    cap_cs  = 最多保留截面数(30 天对应)。
    """
    fl = int(fill_lag or 0)

    if scored.height == 0:                                  # 空窗保护
        return scored.head(0).with_columns([
            pl.lit(direction).alias('方向'), pl.lit(1.0).alias('target_alloc_ratio')]).lazy()

    new_inputs = scored.select(_INPUT_COLS)
    state = load_ee_state(state_path, cfg_fp, expect_fullname=(meta or {}).get('fullname'))

    if state is not None:                                  # 续算：缓存 + 本轮新截面
        cache_inputs = _cast_inputs(pl.from_pandas(state['inputs']), new_inputs)
        # 本轮批次(new_inputs = 实盘 df 窗口)对其覆盖区间是权威的：晚闭合K线/上游修数会在此被重算修正。
        # 旧缓存只保留严格早于本轮批次起点(new_min)的历史(已沉淀、掉出窗口，不可变)；重叠区一律采用 new_inputs，
        # 不再冻结首次写入时可能未闭合的脏值——否则有状态引擎里脏值会污染 held/hold_hours 向后永久传播、偏离回测。
        # 与截面因子路径 concat_select_results 的 snapshot 合并(core/select_coin.py, min_select_time 语义)一致。
        new_min = pd.Timestamp(new_inputs['candle_begin_time'].min())
        immutable = cache_inputs.filter(pl.col('candle_begin_time') < new_min)
        inputs = (pl.concat([immutable, new_inputs], how='vertical').sort('candle_begin_time')
                  if immutable.height else new_inputs.sort('candle_begin_time'))
        seed = {'rows': state['seed_rows'], 'last_time': pd.Timestamp(state['seed_time'])}
        prev_we = pd.Timestamp(state['last_we'])
    else:                                                  # bootstrap：本轮全量 df 从空仓冷算
        inputs = new_inputs.sort('candle_begin_time')
        seed = None
        prev_we = None

    times = inputs['candle_begin_time'].unique().sort()
    n_cs = times.len()
    # 不变式：首轮全量 ≫ N，之后增量恒 ≥ N。一旦 < N 说明数据中途变更/损坏 -> 报错，不静默降级。
    if n_cs < n_floor:
        raise ValueError(
            f'[entry_exit] 续算缓存截面数 {n_cs} < 兜底 N={n_floor}，疑似数据中途变更/损坏；'
            f'请清空 ee_state 重新全量计算并排查上游数据。')

    we = pd.Timestamp(times[-1])
    diffs = times.diff().drop_nulls()
    period = pd.Timedelta(diffs.min()) if diffs.len() > 0 else pd.Timedelta(hours=1)

    # ① 整段重放(seed 续算/bootstrap 冷算)，取选中 (time,symbol) 映射回 scored(全列)输出本轮 df 窗口
    holdings = _compute_holdings_seeded(inputs, fl, exit_cooldown, max_hold_hours, seed=seed)
    sel = (inputs.with_columns(pl.Series('_ee_sel', holdings))
           .filter(pl.col('_ee_sel') == 1).select(['candle_begin_time', 'symbol']))
    out = scored.join(sel, on=['candle_begin_time', 'symbol'], how='semi').with_columns([
        pl.lit(direction).alias('方向'),
        (pl.lit(1.0) / pl.len().over('candle_begin_time')).alias('target_alloc_ratio'),
    ]).lazy()

    # ② 推进缓存并落盘：仅当真的推进到更新截面(防 stutter 重复覆盖)
    if (prev_we is None) or (we > prev_we):
        keep_from = max(0, n_cs - cap_cs)
        if keep_from == 0:                                 # 未到 30 天上限：缓存=整段，seed 不动(bootstrap 用零种子@首根前)
            if state is None:
                new_seed_time = pd.Timestamp(times[0]) - period
                new_seed_rows = []
            else:
                new_seed_time = pd.Timestamp(state['seed_time'])
                new_seed_rows = state['seed_rows']
            new_cache = inputs
        else:                                              # 裁到最近 cap_cs 根；前 fl 根做预热；seed 推进到新 active 起点前
            new_seed_idx = keep_from + fl - 1
            new_seed_time = pd.Timestamp(times[new_seed_idx])
            adv = inputs.filter(pl.col('candle_begin_time') <= new_seed_time)
            _, new_seed_rows = _compute_holdings_seeded(adv, fl, exit_cooldown, max_hold_hours,
                                                 seed=seed, return_state=True)
            new_cache = inputs.filter(pl.col('candle_begin_time') >= pd.Timestamp(times[keep_from]))
        save_ee_state(state_path, new_seed_time, new_seed_rows, new_cache.to_pandas(), we, cfg_fp, meta)

    return out
