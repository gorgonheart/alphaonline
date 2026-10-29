"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import hashlib
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import List, Tuple

import numpy as np
import pandas as pd
import polars as pl
from numba import njit

from core.utils import ee_continuation
from core.utils.factor_hub import FactorHub
from core.utils.log_kit import logger
from core.utils.strategy_hub import DummyStrategy, PositionStrategyHub


def filter_series_by_range(series, range_str):
    # 提取运算符和数值
    operator = range_str[:2] if range_str[:2] in ['>=', '<=', '==', '!='] else range_str[0]
    value = float(range_str[len(operator):])

    match operator:
        case '>=':
            return series >= value
        case '<=':
            return series <= value
        case '==':
            return series == value
        case '!=':
            return series != value
        case '>':
            return series > value
        case '<':
            return series < value
        case _:
            raise ValueError(f"Unsupported operator: {operator}")


def filter_expr_by_range(expr: pl.Expr, range_str: str) -> pl.Expr:
    """
    根据范围字符串构建 Polars 过滤表达式
    """
    operator = range_str[:2] if range_str[:2] in ['>=', '<=', '==', '!='] else range_str[0]
    value = float(range_str[len(operator):])

    match operator:
        case '>=':
            return expr >= value
        case '<=':
            return expr <= value
        case '==':
            return expr == value
        case '!=':
            return expr != value
        case '>':
            return expr > value
        case '<':
            return expr < value
        case _:
            raise ValueError(f"Unsupported operator: {operator}")


# 自定义一个类来保持dict的使用方法，并保证其可哈希，且保证顺序
class HashableDict:
    def __init__(self, data: dict):
        # 将字典按键排序并转为tuple，保证顺序并可哈希
        self.data = tuple(sorted(data.items()))

    def __repr__(self):
        # 使其返回一个类似字典的表示方式
        if isinstance(self.data, tuple):
            return "{" + ", ".join(f"{k}: {v}" for k, v in self.data) + "}"
        return repr(self.data)

    def __eq__(self, other):
        return self.data == other.data

    def __hash__(self):
        return hash(self.data)

    # 支持通过 [] 方式访问
    def __getitem__(self, key):
        if isinstance(self.data, tuple):
            # 将tuple转换回一个dict来支持按键访问
            dict_data = dict(self.data)
            return dict_data[key]
        else:
            raise TypeError(f"Cannot subscript a {type(self.data)} object")


def parse_param(param) -> tuple | HashableDict | str | int | float | bool | None:
    # param的类型需要转换为hashable的状态
    if isinstance(param, list):
        param = tuple(param)
    elif isinstance(param, dict):
        param = HashableDict(param)
    elif isinstance(param, (str, int, float, tuple, bool)) or param is None:
        pass
    else:
        raise ValueError(f'不支持的参数类型：{type(param)}')
    return param


@dataclass(frozen=True)
class FactorConfig:
    name: str = 'Bias'  # 选币因子名称
    is_sort_asc: bool = True  # 是否正排序
    param: int = 3  # 选币因子参数
    weight: float = 1  # 选币因子权重
    is_cross: bool = False  # 是否截面因子

    @classmethod
    def parse_config_list(cls, config_list: List[tuple]):
        all_long_factor_weight = sum([factor[3] for factor in config_list])
        factor_list = []
        for factor_name, is_sort_asc, parameter_list, weight in config_list:
            new_weight = weight / all_long_factor_weight if all_long_factor_weight != 0 else 0
            factor = FactorHub.get_by_name(factor_name)
            param = parse_param(parameter_list)
            factor_list.append(cls(name=factor_name, is_sort_asc=is_sort_asc, param=param, weight=new_weight,
                                   is_cross=factor.is_cross))
        return factor_list

    @cached_property
    def col_name(self):
        return f'{self.name}_{str(self.param)}'

    def __repr__(self):
        return f'{self.col_name}{"↑" if self.is_sort_asc else "↓"}权重:{self.weight}'

    def to_tuple(self):
        return self.name, self.is_sort_asc, self.param, self.weight


@dataclass(frozen=True)
class FilterMethod:
    how: str = ''  # 过滤方式
    range: str = ''  # 过滤值

    def __repr__(self):
        match self.how:
            case 'rank':
                name = '排名'
            case 'pct':
                name = '百分比'
            case 'val':
                name = '数值'
            case _:
                raise ValueError(f'不支持的过滤方式：`{self.how}`')

        return f'{name}:{self.range}'

    def to_val(self):
        return f'{self.how}:{self.range}'


@dataclass(frozen=True)
class FilterFactorConfig:
    name: str = 'Bias'  # 选币因子名称
    param: int = 3  # 选币因子参数
    method: FilterMethod = None  # 过滤方式
    is_sort_asc: bool = True  # 是否正排序
    is_cross: bool = False  # 是否截面因子

    def __repr__(self):
        _repr = self.col_name
        if self.method:
            _repr += f'{"↑" if self.is_sort_asc else "↓"}{self.method}'
        return _repr

    @cached_property
    def col_name(self):
        return f'{self.name}_{str(self.param)}'

    @classmethod
    def init(cls, filter_factor: tuple):
        # 仔细看，结合class的默认值，这个和默认策略中使用的过滤是一模一样的
        config = dict(name=filter_factor[0], param=parse_param(filter_factor[1]))
        if len(filter_factor) > 2:
            # 可以自定义过滤方式
            _how, _range = re.sub(r'\s+', '', filter_factor[2]).split(':')
            cls.check_value(_range)
            config['method'] = FilterMethod(how=_how, range=_range)
        if len(filter_factor) > 3:
            # 可以自定义排序
            config['is_sort_asc'] = filter_factor[3]
        factor = FactorHub.get_by_name(filter_factor[0])
        config['is_cross'] = factor.is_cross
        return cls(**config)

    def to_tuple(self, full_mode=False):
        if full_mode:
            return self.name, self.param, self.method.to_val(), self.is_sort_asc
        else:
            return self.name, self.param

    @staticmethod
    def check_value(range_str):
        try:
            _operator = range_str[:2] if range_str[:2] in ['>=', '<=', '==', '!='] else range_str[0]
            _ = float(range_str[len(_operator):])
        except ValueError as e:
            logger.error(e)
            raise ValueError(f'过滤配置暂不支持表达式：`{range_str}`')


def calc_factor_common(df, factor_list: List[FactorConfig]):
    factor_val = np.zeros(df.shape[0])
    for factor_config in factor_list:
        # 权重为 0，跳过计算
        if factor_config.weight == 0:
            continue
        col_name = f'{factor_config.name}_{str(factor_config.param)}'
        # 计算单个因子的排名
        _rank = df.groupby('candle_begin_time')[col_name].rank(ascending=factor_config.is_sort_asc, method='min')
        # 将因子按照权重累加
        factor_val += _rank * factor_config.weight
    return factor_val


def calc_factor_expr_polars(factor_list: List[FactorConfig], reverse: bool = False) -> pl.Expr:
    """
    构建因子排名加权和表达式 (Polars 版本)

    reverse=True 时翻转每个因子的排序方向（用于 entry_exit 短边：选与长边相反的一端）。
    打分始终是为正的加权 min-rank（恒 >=1），不会破坏 numba 引擎 "score<0=缺席" 的约定。
    """
    rank_exprs = []
    for factor_config in factor_list:
        if factor_config.weight == 0:
            continue
        col_name = f'{factor_config.name}_{str(factor_config.param)}'
        descending = (not factor_config.is_sort_asc) != reverse
        rank_expr = (
            pl.col(col_name)
            .rank(method='min', descending=descending)
            .over('candle_begin_time')
            * factor_config.weight
        )
        rank_exprs.append(rank_expr)

    if not rank_exprs:
        return pl.lit(0.0)

    combined = rank_exprs[0]
    for expr in rank_exprs[1:]:
        combined = combined + expr

    return combined


def filter_common(df, filter_list):
    condition = pd.Series(True, index=df.index)

    for filter_config in filter_list:
        col_name = f'{filter_config.name}_{str(filter_config.param)}'
        match filter_config.method.how:
            case 'rank':
                rank = df.groupby('candle_begin_time')[col_name].rank(ascending=filter_config.is_sort_asc, pct=False)
                condition = condition & filter_series_by_range(rank, filter_config.method.range)
            case 'pct':
                rank = df.groupby('candle_begin_time')[col_name].rank(ascending=filter_config.is_sort_asc, pct=True)
                condition = condition & filter_series_by_range(rank, filter_config.method.range)
            case 'val':
                condition = condition & filter_series_by_range(df[col_name], filter_config.method.range)
            case _:
                raise ValueError(f'不支持的过滤方式：{filter_config.method.how}')

    return condition


def filter_common_polars(filter_list, rank_method='average', empty_value=True) -> pl.Expr:
    """
    根据过滤配置构建过滤条件 (Polars 版本)

    rank_method: rank/pct 的排名方式，默认 'average'（框架老行为）；
                 entry_exit 进出场模式传 'min'，对齐老截面因子的排名口径。
    empty_value: filter_list 为空时返回的常量。默认 True（无过滤=全保留）；
                 进出场模式的出场条件为空列表时需传 False（无出场=不出场）。
    """
    conditions = []

    for filter_config in filter_list:
        col_name = f'{filter_config.name}_{str(filter_config.param)}'
        descending = not filter_config.is_sort_asc

        match filter_config.method.how:
            case 'rank':
                rank_expr = pl.col(col_name).rank(method=rank_method, descending=descending).over('candle_begin_time')
                cond = filter_expr_by_range(rank_expr, filter_config.method.range)
                conditions.append(cond)
            case 'pct':
                rank_expr = pl.col(col_name).rank(method=rank_method, descending=descending).over('candle_begin_time')
                count_expr = pl.len().over('candle_begin_time').cast(pl.Float64)
                pct_expr = rank_expr.cast(pl.Float64) / count_expr
                cond = filter_expr_by_range(pct_expr, filter_config.method.range)
                conditions.append(cond)
            case 'val':
                cond = filter_expr_by_range(pl.col(col_name), filter_config.method.range)
                conditions.append(cond)
            case _:
                raise ValueError(f'不支持的过滤方式：{filter_config.method.how}')

    if not conditions:
        return pl.lit(empty_value)

    combined = conditions[0]
    for cond in conditions[1:]:
        combined = combined & cond

    return combined


@njit
def compute_entry_exit_numba(times, symbol_ids, scores, in_mask, out_mask,
                             target_counts, n_symbols, fill_lag,
                             exit_cooldown=0, max_hold_hours=0):
    """进出场选币引擎：逐截面 出场→补位。times 升序、同时间相邻；score 越小越优先(升序排名)。
    held 跨截面携带：① 持仓中触发 out_mask 或满 max_hold_hours 强制出场 ② 从 in_mask 非持仓按
    fill_lag 期前 score 补到 target，fill_lag>0 时滞后期缺席的币(score<0)排除；首截面冷启动按当期 score。
    入场门 in_mask 与补位排名同口径：fill_lag>0 时也取 fill_lag 期前的 in_mask 判定（避免“补位看
    滞后排名、入场门看当期”造成的时间错位）；冷启动按当期 in_mask。
    exit_cooldown>0：出场后拉黑该币 exit_cooldown 期(出场当期+随后期内不可补回)，0=当期可立即补回。
    max_hold_hours>0：单笔连续持有满该期数后强制出场，0=不限制。返回 0/1。"""
    n_rows = len(times)
    result = np.zeros(n_rows, dtype=np.int32)
    held = np.zeros(n_symbols, dtype=np.int8)
    keep = np.zeros(n_symbols, dtype=np.int8)
    exited = np.zeros(n_symbols, dtype=np.int8)
    hold_hours = np.zeros(n_symbols, dtype=np.int32)
    blacklist_remaining = np.zeros(n_symbols, dtype=np.int32)
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

            # 写入本期 score / 入场门 到环形缓冲（缺席的币 score=-1、入场门=0）
            if fill_lag > 0:
                for a in range(n_symbols):
                    rank_buf[buf_ptr, a] = -1.0
                    in_buf[buf_ptr, a] = 0
                for j in range(start, end):
                    rank_buf[buf_ptr, symbol_ids[j]] = scores[j]
                    in_buf[buf_ptr, symbol_ids[j]] = in_mask[j]
                buf_ptr = (buf_ptr + 1) % buf_size

            # 期末：拉黑计数 -1（必须在补位之后，确保出场当期仍被拉黑）
            for a in range(n_symbols):
                if blacklist_remaining[a] > 0:
                    blacklist_remaining[a] -= 1

            gi += 1
            start = i

    return result


def _entry_exit_target_expr(coin_num) -> pl.Expr:
    """把 select_coin_num 转成逐期目标持仓数表达式。entry_exit 用"目标持仓数"模型：
    int=绝对数，float(0~1)=占比(floor 后至少 1)。不支持 tuple 区间选币（区间/阈值请用 entry_list/exit_list 表达）。
    短边 'long_nums' 由调用方单独处理，不会进入本函数。"""
    if isinstance(coin_num, (tuple, list)):
        raise ValueError(f'entry_exit 选币模式不支持 tuple 区间选币数 {coin_num}；'
                         f'请用 int(绝对数)/float(占比)，短边可用 "long_nums"')
    num = float(coin_num)
    if 0 < num < 1:
        return (pl.len().over('candle_begin_time').cast(pl.Float64) * num).floor().cast(pl.Int64).clip(lower_bound=1)
    return pl.lit(int(num))


def _compute_holdings(df: pl.DataFrame, fill_lag: int,
                      exit_cooldown: int = 0, max_hold_hours: int = 0) -> np.ndarray:
    """把已带 _ee_score/_ee_in/_ee_out/_ee_target 的截面 df 交给 numba 引擎，返回逐行 0/1 选中数组。"""
    syms = df['symbol'].to_list()
    sym_to_id = {s: idx for idx, s in enumerate(sorted(set(syms)))}
    symbol_ids = np.array([sym_to_id[s] for s in syms], dtype=np.int64)
    times = df['candle_begin_time'].rank(method='dense').cast(pl.Int64).to_numpy()
    return compute_entry_exit_numba(
        times, symbol_ids,
        df['_ee_score'].cast(pl.Float64).to_numpy(),
        df['_ee_in'].to_numpy().astype(np.int8),
        df['_ee_out'].to_numpy().astype(np.int8),
        df['_ee_target'].cast(pl.Int64).to_numpy(),
        len(sym_to_id), int(fill_lag or 0), int(exit_cooldown or 0), int(max_hold_hours or 0),
    )


def _run_entry_exit_one_side(lf: pl.LazyFrame, factor_list, entry_list, exit_list,
                             target_expr: pl.Expr, fill_lag, exit_cooldown, max_hold_hours,
                             descending: bool, direction: int,
                             state_path=None, cfg_fp=None, meta=None,
                             n_floor=0, cap_cs=0) -> pl.LazyFrame:
    """单侧进出场引擎：逐截面 出场→留存→从非持仓按 fill_lag 期前 min-rank 补位到目标持仓数。

    descending=True（短边）时翻转排名方向取另一端（打分仍为正的加权 min-rank，不取负），
    对标标准 calc_select_factor_rank(ascending=False)。排名一律 method='min'（与老截面因子一致）。
    注：单因子（如 VolumeMaxBias）短边与长边严格相反端；多因子且边界跨因子并列时 tie-break
    以加权 reverse-rank 为准（与标准 rank<=N 含并列略有差异，属 entry_exit 定额取数的固有设计）。

    state_path 为 None（回测/异常）：从空仓全量算、不存盘（输出整段）。
    state_path 有值（实盘续算）：委托 ee_continuation.run_continuation（自包含累积缓存，byte-exact 见该模块）。
    """
    if not any(f.weight != 0 for f in factor_list):
        raise ValueError('entry_exit 选币模式需要在 factor_list 配置补位排序因子')

    # ① 把打分 / 进出场掩码 / 目标持仓数 写成截面内部列（min-rank 口径，打分恒为正）
    scored = lf.with_columns([
        calc_factor_expr_polars(factor_list, reverse=descending).alias('_ee_score'),
        filter_common_polars(entry_list, rank_method='min', empty_value=True).cast(pl.Int8).alias('_ee_in'),
        filter_common_polars(exit_list, rank_method='min', empty_value=False).cast(pl.Int8).alias('_ee_out'),
        target_expr.cast(pl.Int64).alias('_ee_target'),
    ]).sort('candle_begin_time')
    df = scored.collect()

    # ② 实盘续算：有缓存目录 -> 委托 ee_continuation 的自包含累积缓存（空窗保护/累积/裁剪/重放/落盘都在那里）
    if state_path is not None:
        return ee_continuation.run_continuation(df, fill_lag, exit_cooldown, max_hold_hours, direction,
                                                state_path, cfg_fp, meta, n_floor, cap_cs)

    # ③ 回测/异常：从空仓全量算，选中行 + 方向 + 等权(每侧 1/N)。此段与 dev2 _run_entry_exit_one_side 逐字节一致
    holdings = _compute_holdings(df, fill_lag, exit_cooldown, max_hold_hours)
    return df.with_columns(pl.Series('_ee_sel', holdings)).filter(pl.col('_ee_sel') == 1).with_columns([
        pl.lit(direction).alias('方向'),
        (pl.lit(1.0) / pl.len().over('candle_begin_time')).alias('target_alloc_ratio'),
    ]).lazy()


@dataclass
class StrategyConfig:
    name: str = 'Strategy'
    strategy: str = 'Strategy'

    # 持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
    # 当持仓周期为D时，选币指标也是按照每天一根K线进行计算。
    # 当持仓周期为H时，选币指标也是按照每小时一根K线进行计算。
    hold_period: str = '1D'.replace('h', 'H').replace('d', 'D')

    # 配置offset
    offset: int = 0  # 策略配置的特定的offset
    offset_list: List[int] = (0,)

    # 是否使用现货
    is_use_spot: bool = False  # True：使用现货。False：不使用现货，只使用合约。

    # 选币市场范围 & 交易配置
    #   配置解释： 选币范围 + '_' + 优先交易币种类型
    #󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
    #   spot_spot: 在 '现货' 市场中进行选币。如果现货币种含有'合约'，优先交易 '现货'。
    #   swap_swap: 在 '合约' 市场中进行选币。如果现货币种含有'现货'，优先交易 '合约'。
    #   spot_swap: 在 '现货' 市场中进行选币。如果现货币种含有'合约',优先交易'合约'。
    #   mix_spot:  在 '现货与合约' 的市场中进行选币。如果两边市场都存在的币种，会保留'现货'，并优先下单'现货'。
    #   mix_swap:  在 '现货与合约' 的市场中进行选币。如果两边市场都存在的币种，会保留'合约'，并优先下单'合约'。
    market: str = 'swap_swap'

    # 多头选币数量。1 表示做多一个币; 0.1 表示做多10%的币
    long_select_coin_num: int | float | tuple = 0.1
    # 空头选币数量。1 表示做空一个币; 0.1 表示做空10%的币，'long_nums'表示和多头一样多的数量
    short_select_coin_num: int | float | tuple | str = 'long_nums'  # 注意：多头为0的时候，不能配置'long_nums'

    # 多头选币数量限制。实际选币数量 min(long_select_coin_num, long_select_coin_num_max)
    long_select_coin_num_max: int = None
    # 空头选币数量限制。实际选币数量 max(short_select_coin_num, short_select_coin_num_limit)
    short_select_coin_num_min: int = None

    # 选币范围控制，
    # 默认为'both'，表示 <= 和 >=；'left' 表示 >= 和 <；'right' 表示 > 和 <=。
    # 也支持多空分离，比如 ['both', 'left']，表示多头是 both 模式，空头是 left 模式
    select_inclusive: str | tuple = 'right'

    # 多头的选币因子列名。
    long_factor: str = '因子'  # 因子：表示使用复合因子，默认是 factor_list 里面的因子组合。需要修改 calc_factor 函数配合使用
    # 空头的选币因子列名。多头和空头可以使用不同的选币因子
    short_factor: str = '因子'

    # 策略空头和多头的权重，默认是 1:1
    long_cap_weight: float = field(default=1)
    short_cap_weight: float = field(default=1)

    # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
    factor_list: List[tuple] = ()  # 因子名（和factors文件中相同），排序方式，参数，权重。

    long_factor_list: List[FactorConfig] = ()  # 多头选币因子
    short_factor_list: List[FactorConfig] = ()  # 空头选币因子

    # 确认过滤因子及其参数，用于`2_选币_单offset.py`进行过滤
    filter_list: List[tuple] = ()  # 因子名（和factors文件中相同），参数

    long_filter_list: List[FilterFactorConfig] = ()  # 多头过滤因子
    short_filter_list: List[FilterFactorConfig] = ()  # 空头过滤因子

    # 后置过滤因子及其参数，用于`2_选币_单offset.py`进行过滤
    filter_list_post: List[tuple] = ()  # 因子名（和factors文件中相同），参数

    long_filter_list_post: List[FilterFactorConfig] = ()  # 多头后置过滤因子
    short_filter_list_post: List[FilterFactorConfig] = ()  # 空头后置过滤因子

    select_mode: str = 'standard'              # 'standard'(老) | 'entry_exit'(新)
    # 进出场目标持仓数复用 long/short_select_coin_num（int=绝对数, float=占比, 短边支持 'long_nums'）
    fill_lag: int = 0                          # 补位滞后期数(长边&共享默认; 0=当期排名)
    short_fill_lag: int | None = None          # 短边补位滞后; None=回退 fill_lag
    exit_cooldown: int = 0                      # 出场冷却期数(长边&共享默认; 0=当期可立即补回, N=拉黑N期)
    short_exit_cooldown: int | None = None      # 短边出场冷却; None=回退 exit_cooldown
    max_hold_hours: int = 0                     # 单笔最长持仓期数(长边&共享默认; 0=不限制)
    short_max_hold_hours: int | None = None     # 短边最长持仓; None=回退 max_hold_hours
    entry_list: List[tuple] = ()               # 进场条件(raw, 长短共享默认输入)
    exit_list: List[tuple] = ()                # 出场条件(raw, 长短共享默认输入)
    long_entry_list: List[FilterFactorConfig] = ()    # 进场(解析后, 引擎读取)
    short_entry_list: List[FilterFactorConfig] = ()
    long_exit_list: List[FilterFactorConfig] = ()     # 出场(解析后, 引擎读取)
    short_exit_list: List[FilterFactorConfig] = ()

    # 进出场续算的持仓状态存这个目录(=本组合的 snapshot 目录)，运行期由 BacktestConfig 赋值，None 表示不存(回测)。
    # init=False 不作为构造入参；compare/repr 排除，不影响相等判断和打印。
    ee_state_dir: str | None = field(default=None, init=False, compare=False, repr=False)

    use_custom_func: bool = True  # 是否使用自定义函数

    cap_weight: float = 1  # 策略权重

    md5_hash: str = ''  # 策略的md5值

    @cached_property
    def select_scope(self):
        return self.market.split('_')[0]

    @cached_property
    def order_first(self):
        return self.market.split('_')[1]

    @cached_property
    def is_day_period(self):
        return self.hold_period.endswith('D')

    @cached_property
    def is_hour_period(self):
        return self.hold_period.endswith('H')

    @cached_property
    def period_num(self) -> int:
        return int(self.hold_period.upper().replace('H', '').replace('D', ''))

    @cached_property
    def period_type(self) -> str:
        return self.hold_period[-1]

    @cached_property
    def factor_columns(self) -> List[str]:
        factor_columns = set()  # 去重

        # 针对当前策略的因子信息，整理之后的列名信息，并且缓存到全局
        for factor_config in set(self.long_factor_list + self.short_factor_list):
            # 策略因子最终在df中的列名
            factor_columns.add(factor_config.col_name)  # 添加到当前策略缓存信息中

        # 针对当前策略的过滤因子信息，整理之后的列名信息，并且缓存到全局
        for filter_factor in set(self.long_filter_list + self.short_filter_list):
            # 策略过滤因子最终在df中的列名
            factor_columns.add(filter_factor.col_name)  # 添加到当前策略缓存信息中

        # 针对当前策略的过滤因子信息，整理之后的列名信息，并且缓存到全局
        for filter_factor in set(self.long_filter_list_post + self.short_filter_list_post):
            # 策略过滤因子最终在df中的列名
            factor_columns.add(filter_factor.col_name)  # 添加到当前策略缓存信息中

        for filter_factor in set(self.long_entry_list + self.short_entry_list +
                                 self.long_exit_list + self.short_exit_list):
            factor_columns.add(filter_factor.col_name)

        return list(factor_columns)

    @cached_property
    def all_factors(self) -> set:
        return (set(self.long_factor_list + self.short_factor_list) |
                set(self.long_filter_list + self.short_filter_list) |
                set(self.long_filter_list_post + self.short_filter_list_post) |
                set(self.long_entry_list + self.short_entry_list +
                    self.long_exit_list + self.short_exit_list))

    @classmethod
    def init(cls, index: int, file: DummyStrategy = None, **config):
        # 自动补充因子列表
        long_select_num = config.get('long_select_coin_num', file.long_select_coin_num if file else 0.1)
        short_select_num = config.get('short_select_coin_num', file.short_select_coin_num if file else 'long_nums')

        # 初始化多空分离策略因子
        factor_list = [(factor_tp[0], factor_tp[1], parse_param(factor_tp[2]), factor_tp[3]) for factor_tp in
                       config.get('factor_list', file.factor_list if file else [])]
        if 'long_factor_list' in config or 'short_factor_list' in config:
            # 如果设置过的话，默认单边是挂空挡
            factor_list = []
        config['long_factor_list'] = FactorConfig.parse_config_list(config.get('long_factor_list', factor_list))
        config['short_factor_list'] = FactorConfig.parse_config_list(config.get('short_factor_list', factor_list))

        for f in config['long_factor_list'] + config['short_factor_list']:
            if not f.is_cross:
                continue
            factor = FactorHub.get_by_name(f.name)
            _factor_list = [(cross_sec[0], True, cross_sec[1], 0) for cross_sec in factor.get_factor_list(f.param)]
            if _factor_list:
                config['long_factor_list'].extend(FactorConfig.parse_config_list(_factor_list))
                config['short_factor_list'].extend(FactorConfig.parse_config_list(_factor_list))

        # 初始化多空分离过滤因子
        filter_list = [(factor_tp[0], parse_param(factor_tp[1]), *factor_tp[2:]) for factor_tp in
                       config.get('filter_list', file.filter_list if file else [])]
        if 'long_filter_list' in config or 'short_filter_list' in config:
            # 如果设置过的话，则默认单边是挂空挡
            filter_list = []

        # 检查配置是否合法
        if file.is_abstract:
            use_custom_func = False
        else:
            use_custom_func = config.get('use_custom_func', True)
        config['use_custom_func'] = use_custom_func  # 重置config，否则后面实例化配置还是错的，导致因子文件无法生成

        long_filter_list = config.get('long_filter_list', filter_list)
        short_filter_list = config.get('short_filter_list', filter_list)
        new_filter_param = [len(item) > 2 for item in set(long_filter_list) | set(short_filter_list)]
        if any(new_filter_param) and use_custom_func:
            logger.error('过滤因子配置自定义规则，需要配置 use_custom_func 为 False')
            exit()
        old_filter_param = [len(item) <= 2 for item in set(long_filter_list) | set(short_filter_list)]
        if any(old_filter_param) and not use_custom_func:
            logger.error('策略中包含老的因子，但没有配置filter过滤规则，请检查config中策略的 filter_list 参数')
            exit()

        config['long_filter_list'] = [FilterFactorConfig.init(item) for item in long_filter_list]
        config['short_filter_list'] = [FilterFactorConfig.init(item) for item in short_filter_list]

        for f in config['long_filter_list'] + config['short_filter_list']:
            if not f.is_cross:
                continue
            factor = FactorHub.get_by_name(f.name)
            _factor_list = [(cross_sec[0], cross_sec[1], 'pct:<2') for cross_sec in factor.get_factor_list(f.param)]
            if _factor_list:
                config['long_filter_list'].extend([FilterFactorConfig.init(item) for item in _factor_list])
                config['short_filter_list'].extend([FilterFactorConfig.init(item) for item in _factor_list])

        # 初始化后置过滤因子
        filter_list_post = config.get('filter_list_post', [])
        # 根据配置，初始化后置过滤
        if 'long_filter_list_post' in config or 'short_filter_list_post' in config:
            # 如果设置过的话，则默认单边是挂空挡
            filter_list_post = []
        config['long_filter_list_post'] = [FilterFactorConfig.init(item) for item in
                                           config.get('long_filter_list_post', filter_list_post)]
        config['short_filter_list_post'] = [FilterFactorConfig.init(item) for item in
                                            config.get('short_filter_list_post', filter_list_post)]

        for f in config['long_filter_list_post'] + config['short_filter_list_post']:
            if not f.is_cross:
                continue
            factor = FactorHub.get_by_name(f.name)
            _factor_list = [(cross_sec[0], cross_sec[1], 'pct:<2') for cross_sec in factor.get_factor_list(f.param)]
            if _factor_list:
                config['long_filter_list_post'].extend([FilterFactorConfig.init(item) for item in _factor_list])
                config['short_filter_list_post'].extend([FilterFactorConfig.init(item) for item in _factor_list])

        # 初始化进出场模式字段
        config['select_mode'] = config.get('select_mode', 'standard')
        config['fill_lag'] = config.get('fill_lag', 0)
        config['short_fill_lag'] = config.get('short_fill_lag', None)
        config['exit_cooldown'] = config.get('exit_cooldown', 0)
        config['short_exit_cooldown'] = config.get('short_exit_cooldown', None)
        config['max_hold_hours'] = config.get('max_hold_hours', 0)
        config['short_max_hold_hours'] = config.get('short_max_hold_hours', None)
        for _k in ('exit_cooldown', 'short_exit_cooldown', 'max_hold_hours', 'short_max_hold_hours'):
            if config[_k] is not None and config[_k] < 0:
                raise ValueError(f'{_k} 不能为负数，当前为 {config[_k]}')
        # 进/出场条件：entry_list/exit_list 为长短共享默认，long/short_* 可单独覆盖（复刻框架 filter_list 模式）
        ein = config.get('entry_list', [])
        if 'long_entry_list' in config or 'short_entry_list' in config:
            ein = []
        config['long_entry_list'] = [FilterFactorConfig.init(i) for i in config.get('long_entry_list', ein)]
        config['short_entry_list'] = [FilterFactorConfig.init(i) for i in config.get('short_entry_list', ein)]
        eout = config.get('exit_list', [])
        if 'long_exit_list' in config or 'short_exit_list' in config:
            eout = []
        config['long_exit_list'] = [FilterFactorConfig.init(i) for i in config.get('long_exit_list', eout)]
        config['short_exit_list'] = [FilterFactorConfig.init(i) for i in config.get('short_exit_list', eout)]

        # 多空分离因子字段
        if config['long_factor_list'] != config['short_factor_list']:
            config['long_factor'] = '多头因子'
            config['short_factor'] = '空头因子'

        # 检查配置是否合法
        if (len(config['long_factor_list']) == 0) and (config.get('long_select_coin_num', 0) != 0):
            raise ValueError('多空分离因子配置有误，多头因子不能为空')
        if (len(config['short_factor_list']) == 0) and (config.get('short_select_coin_num', 0) != 0):
            raise ValueError('多空分离因子配置有误，空头因子不能为空')

        # auto overwrite
        # 如果多头不配置资金，然后空头还要和多头选一样多的情况下，直接把参数overwrite
        if config.get('long_cap_weight', 1) == 0 and short_select_num == 'long_nums':
            config['short_select_coin_num'] = long_select_num

        all_long_short_cap_weight = config.get('long_cap_weight', 1) + config.get('short_cap_weight', 1)
        if all_long_short_cap_weight == 0:
            raise ValueError('多空分离因子配置有误，空头和多头的总权重不能都为0')
        config['long_cap_weight'] = config.get('long_cap_weight', 1) / all_long_short_cap_weight
        config['short_cap_weight'] = config.get('short_cap_weight', 1) / all_long_short_cap_weight

        # 如果没有配置 market，则继续使用 is_use_spot
        if 'is_use_spot' in config and 'market' not in config:
            config['market'] = 'spot_swap' if config['is_use_spot'] else 'swap_swap'

        # 开始初始化策略对象
        if file is None or file.is_abstract:
            config['name'] = f"#{index}.{config.get('strategy', 'strategy')}"
            return cls(**config)

        """
        兼容历史原因下使用的策略配置逻辑
        """
        _config = dict(
            name=f'#{index}.{file.name}',
            hold_period=file.hold_period.upper(),
            long_select_coin_num=file.long_select_coin_num,
            short_select_coin_num=file.short_select_coin_num,
            long_factor=file.long_factor,
            short_factor=file.short_factor,
            factor_list=file.factor_list,
            filter_list=file.filter_list,
            cap_weight=1,
            md5_hash=file.md5_hash  # ** 回测特有 ** 记录文件的md5，避免重复计算
        )
        # 兼容历史原因，回测的相关策略特有逻辑
        if hasattr(file, 'if_use_spot'):
            _config['is_use_spot'] = file.if_use_spot
            _config['offset_list'] = [file.offset]
            _config['offset'] = file.offset

        _config.update(config)
        stg_conf = cls(**_config)

        # ** 整合原生函数的功能 **
        # 重新组合一下after_merge_index
        stg_conf.after_merge_index = file.after_merge_index

        # 重新组合一下filter_list
        stg_conf.factor_list = list(dict.fromkeys(
            [factor_config.to_tuple() for factor_config in stg_conf.long_factor_list + stg_conf.short_factor_list]))
        stg_conf.filter_list = list(dict.fromkeys(
            [filter_factor.to_tuple() for filter_factor in stg_conf.long_filter_list + stg_conf.short_filter_list]))

        # 如果使用自定义函数，重新赋值
        if stg_conf.use_custom_func and not file.is_abstract:
            stg_conf.calc_factor = file.calc_factor
            stg_conf.before_filter = file.before_filter
        return stg_conf

    def get_fullname(self, as_folder_name=False):
        factor_desc_list = [f'{self.long_factor_list}', f'前滤{self.long_filter_list}',
                            f'后滤{self.long_filter_list_post}']
        long_factor_desc = '&'.join(factor_desc_list)

        factor_desc_list = [f'{self.short_factor_list}', f'前滤{self.short_filter_list}',
                            f'后滤{self.short_filter_list_post}']
        short_factor_desc = '&'.join(factor_desc_list)

        # ** 回测特有 ** 因为需要计算hash，因此包含的信息不同
        fullname = f"""{self.name}-{self.hold_period}-{self.market}-offset{self.offset_list}"""
        if self.long_cap_weight > 0:
            fullname += f"""-多|权重:{self.long_cap_weight:.2f},数量:{self.long_select_coin_num},因子{long_factor_desc}"""
        if self.short_cap_weight > 0:
            fullname += f"""-空|权重:{self.short_cap_weight:.2f},数量:{self.short_select_coin_num},因子{short_factor_desc}"""

        md5_hash = hashlib.md5(f'{fullname}-{self.offset_list}-{self.md5_hash}'.encode('utf-8')).hexdigest()
        return f'{self.name}-{md5_hash[:8]}' if as_folder_name else fullname

    def __repr__(self):
        return f"""
................................
{self.name} 配置信息：
- 持仓周期: {self.hold_period}
- offset: ({len(self.offset_list)}个) {self.offset_list}
- 选币范围: {self.select_scope}
- 优先下单: {self.order_first}
- 多空比例: {self.long_cap_weight}:{self.short_cap_weight}
- 多头选币设置(权重 {self.long_cap_weight * 100:.2f}%):
  * 选币数量: {self.long_select_coin_num}
  * 策略因子: {self.long_factor_list}
  * 前置过滤: {self.long_filter_list}
  * 后置过滤: {self.long_filter_list_post}
- 空头选币设置(权重 {self.short_cap_weight * 100:.2f}%):
  * 选币数量: {self.short_select_coin_num}
  * 策略因子: {self.short_factor_list}
  * 前置过滤: {self.short_filter_list}
  * 后置过滤: {self.short_filter_list_post}"""

    def calc_factor(self, df, **kwargs) -> pd.DataFrame:
        raise NotImplementedError

    def calc_select_factor(self, df) -> pd.DataFrame:
        # 如果没有通过新的配置启动的话，使用原来的 `strategy` 中定义的函数计算
        if self.use_custom_func:
            # 1.2.1 新增：调用自定义函数的时候，把conf的对象传递给函数获取详细配置
            return self.calc_factor(df, external_list=self.factor_list, conf=self)

        # ========= 以上代码是为了兼容历史代码而写的 ========
        # 计算多头因子
        new_cols = {self.long_factor: calc_factor_common(df, self.long_factor_list)}

        # 如果单独设置了空头过滤因子
        if self.short_factor != self.long_factor:
            new_cols[self.short_factor] = calc_factor_common(df, self.short_factor_list)

        return pd.DataFrame(new_cols, index=df.index)

    def before_filter(self, df, **kwargs) -> Tuple[pd.DataFrame, pd.DataFrame]:
        raise NotImplementedError

    def filter_before_select(self, df):
        if self.use_custom_func:
            # 1.2.1 新增：调用自定义函数的时候，把conf的对象传递给函数获取详细配置
            return self.before_filter(df, ex_filter_list=self.filter_list, conf=self)

        # ========= 以上代码是为了兼容历史代码而写的 ========
        # 过滤多空因子
        long_filter_condition = filter_common(df, self.long_filter_list)

        # 如果单独设置了空头过滤因子
        if self.long_filter_list != self.short_filter_list:
            short_filter_condition = filter_common(df, self.short_filter_list)
        else:
            short_filter_condition = long_filter_condition

        return df[long_filter_condition].copy(), df[short_filter_condition].copy()

    def filter_after_select(self, df):
        long_filter_condition = (df['方向'] == 1) & filter_common(df, self.long_filter_list_post)
        short_filter_condition = (df['方向'] == -1) & filter_common(df, self.short_filter_list_post)

        return df[long_filter_condition | short_filter_condition].copy()

    # ========= Polars 版本的方法 (支持 LazyFrame) =========
    def calc_select_factor_polars_lazy(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """
        计算选币因子 (Polars 版本)
        """
        if self.use_custom_func:
            raise NotImplementedError('自定义函数暂不支持 Polars 版本')

        long_factor_expr = calc_factor_expr_polars(self.long_factor_list)
        lf = lf.with_columns(long_factor_expr.alias(self.long_factor))

        if self.short_factor != self.long_factor:
            short_factor_expr = calc_factor_expr_polars(self.short_factor_list)
            lf = lf.with_columns(short_factor_expr.alias(self.short_factor))

        return lf

    def filter_before_select_polars_lazy(self, lf: pl.LazyFrame) -> Tuple[pl.LazyFrame, pl.LazyFrame]:
        """
        前置过滤 (Polars 版本)
        """
        if self.use_custom_func:
            raise NotImplementedError('自定义函数暂不支持 Polars 版本')

        long_filter_expr = filter_common_polars(self.long_filter_list)
        long_lf = lf.filter(long_filter_expr)

        if self.long_filter_list != self.short_filter_list:
            short_filter_expr = filter_common_polars(self.short_filter_list)
            short_lf = lf.filter(short_filter_expr)
        else:
            short_lf = long_lf

        return long_lf, short_lf

    def filter_after_select_polars_lazy(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """
        后置过滤 (Polars 版本)
        """
        long_filter_expr = filter_common_polars(self.long_filter_list_post)
        long_condition = (pl.col('方向') == 1) & long_filter_expr

        short_filter_expr = filter_common_polars(self.short_filter_list_post)
        short_condition = (pl.col('方向') == -1) & short_filter_expr

        return lf.filter(long_condition | short_condition)

    def run_entry_exit_selection_polars_lazy(self, long_lf: pl.LazyFrame,
                                             short_lf: pl.LazyFrame) -> pl.LazyFrame:
        """进出场选币模式（长短双边）。long_lf/short_lf 已是 mix scope 过滤后的截面（同老逻辑宇宙）。

        复现老 `VolumeMaxBias_进出场阈值分离`：逐截面 出场→留存→从非持仓按 fill_lag 期前 min-rank 补位到目标持仓数；
        无入场门时从全部非持仓补位。与标准 select_long_and_short_coin 同语义：按 long/short_cap_weight 分别跑两侧
        并 concat，输出 方向(±1)/target_alloc_ratio(每侧 1/N)，交由 select_coins_by_strategy 继续做后置过滤与多空权重调整。
        目标持仓数复用 long/short_select_coin_num；现货去重已由上游 market scope 过滤完成，本方法不处理。
        """
        parts = []
        long_part = None
        if self.long_cap_weight > 0:
            long_part = _run_entry_exit_one_side(
                long_lf, self.long_factor_list, self.long_entry_list, self.long_exit_list,
                _entry_exit_target_expr(self.long_select_coin_num), self.fill_lag,
                self.exit_cooldown, self.max_hold_hours, descending=False, direction=1,
                **self._ee_cont_kwargs('long', self.fill_lag, self.exit_cooldown, self.max_hold_hours,
                                       self.long_select_coin_num, self.long_factor_list,
                                       self.long_entry_list, self.long_exit_list,
                                       self.long_filter_list, self.long_filter_list_post))
            parts.append(long_part)

        if self.short_cap_weight > 0:
            short_input, short_target = self._entry_exit_short_target(short_lf, long_part)
            short_lag = self.short_fill_lag if self.short_fill_lag is not None else self.fill_lag
            short_exit_cd = self.short_exit_cooldown if self.short_exit_cooldown is not None else self.exit_cooldown
            short_max_hold = self.short_max_hold_hours if self.short_max_hold_hours is not None else self.max_hold_hours
            parts.append(_run_entry_exit_one_side(
                short_input, self.short_factor_list, self.short_entry_list, self.short_exit_list,
                short_target, short_lag, short_exit_cd, short_max_hold, descending=True, direction=-1,
                **self._ee_cont_kwargs('short', short_lag, short_exit_cd, short_max_hold,
                                       self.short_select_coin_num, self.short_factor_list,
                                       self.short_entry_list, self.short_exit_list,
                                       self.short_filter_list, self.short_filter_list_post)))

        if not parts:
            raise ValueError('entry_exit: long_cap_weight 与 short_cap_weight 不能同时为 0')
        return pl.concat(parts, how='diagonal')

    def _ee_cont_kwargs(self, side, fill_lag, exit_cooldown, max_hold_hours, select_coin_num,
                        factor_list, entry_list, exit_list, filter_list, filter_list_post) -> dict:
        """实盘续算要透传给 _run_entry_exit_one_side 的参数（状态文件路径/参数指纹/meta/兜底N/30天cap）。
        ee_state_dir 为 None（回测/异常）时返回空 dict，_run_entry_exit_one_side 即走冷路径（从空仓全量算、不存盘），
        此时本侧调用与 dev2 回测逐字节一致。

        cap_cs：续算缓存最多保留 30 天(EE_RETENTION_CAP_DAYS)对应的截面数；n_floor：兜底下限，单位统一为截面数(期)
        N = max(fill_lag, 1个持仓周期) + 1天冗余 = max(fill_lag,1)+ceil(24/hp_hours)（正常恒满足，<N 即数据异常，
        ee_continuation 内 raise）。恒保证 n_floor <= cap_cs（否则 hold_period 长周期下下限大于上限、稳态必 raise）。
        缓存自包含，与 lookback_days/df 窗口无关。"""
        if self.ee_state_dir is None:
            return {}
        hp_hours = max(1, int(np.ceil(pd.to_timedelta(self.hold_period.lower()).total_seconds() / 3600)))
        n_floor = max(int(fill_lag or 0), 1) + int(np.ceil(24 / hp_hours))
        fullname = self.get_fullname(as_folder_name=True)
        return dict(
            state_path=ee_continuation.ee_state_path(self.ee_state_dir, fullname, side),
            cfg_fp=ee_continuation.ee_cfg_fingerprint(side, fill_lag, exit_cooldown, max_hold_hours,
                                                      select_coin_num, factor_list, entry_list, exit_list),
            meta=ee_continuation.ee_build_meta(self.name, fullname, side, factor_list, entry_list, exit_list,
                                               filter_list, filter_list_post, fill_lag, exit_cooldown,
                                               max_hold_hours, select_coin_num, self.hold_period),
            n_floor=n_floor,
            cap_cs=max(int(np.ceil(ee_continuation.EE_RETENTION_CAP_DAYS * 24 / hp_hours)), n_floor),
        )

    def _entry_exit_short_target(self, short_lf: pl.LazyFrame, long_part):
        """解析短边目标持仓数，返回 (short_input_lf, target_expr)。
        short_select_coin_num=='long_nums' 时短边逐期目标=当期多头数量（来自已算出的 long_part，join 进 short_lf）；
        否则按 int/float 解析。'long_nums' 仅在 long_cap_weight>0 时到达此处（init 已处理 long_cap_weight==0 的替换）。"""
        if self.short_select_coin_num == 'long_nums':
            if long_part is None:
                raise ValueError("short_select_coin_num='long_nums' 需要 long_cap_weight>0")
            long_counts = long_part.group_by('candle_begin_time').agg(pl.len().alias('_ee_long_count'))
            short_lf = short_lf.join(long_counts, on='candle_begin_time', how='left').with_columns(
                pl.col('_ee_long_count').fill_null(0))
            return short_lf, pl.col('_ee_long_count')
        return short_lf, _entry_exit_target_expr(self.short_select_coin_num)

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def after_merge_index(self, candle_df, symbol, factor_dict, data_dict) -> Tuple[pd.DataFrame, dict, dict]:
        return candle_df, factor_dict, data_dict

    def select_by_coin_num(self, df, coin_num, max_limit=None, min_limit=None):
        select_range = coin_num if isinstance(coin_num, (tuple, list)) else (None, coin_num)
        select_inclusive = self.select_inclusive if isinstance(self.select_inclusive, (tuple, list)) else (
            self.select_inclusive, self.select_inclusive)

        def get_select_condition(side_select_num, inclusive, is_left):
            if side_select_num is not None:
                select_num = df['总币数'] * side_select_num if int(side_select_num) == 0 else side_select_num
                if max_limit:
                    select_num = select_num.clip(upper=max_limit) if int(side_select_num) == 0 else min(select_num, max_limit)
                if min_limit:
                    select_num = select_num.clip(lower=min_limit) if int(side_select_num) == 0 else max(select_num, min_limit)
                if is_left:
                    include_cond = (df['rank'] >= select_num) if inclusive != 'right' else (df['rank'] > select_num)
                else:
                    include_cond = (df['rank'] <= select_num) if inclusive != 'left' else (df['rank'] < select_num)
            else:
                include_cond = pd.Series([True] * len(df), index=df.index)
            return include_cond

        # Calculate conditions for left and right sides
        left_condition = get_select_condition(select_range[0], select_inclusive[0], is_left=True)
        right_condition = get_select_condition(select_range[1], select_inclusive[1], is_left=False)

        return df[left_condition & right_condition].copy(False)


@dataclass
class PosStrategyConfig:
    name: str  # 策略名称
    hold_period: str  # 持仓周期
    params: dict = field(default_factory=dict)  # 策略参数
    rebalance_cap_step: float = field(default=1.0)  # 每次调仓的最大比例，1.0 表示100%，每次全量切换
    factor_list: List[FactorConfig] = field(default_factory=list)  # 有些策略需要添加因子
    # symbol_ratio_limit
    # 参数说明:
    # -rate_limit:单币种最大权重限制(如0.1表示10%)
    # -fill_strategy:超出部分权重分配给哪个策略的选币结果
    # 'symbol_ratio_limit': {
    #         'long': {
    #             'rate_limit': 0.1,
    #             'fill_strategy': '纯多',
    #         },
    #         'short': {
    #             'rate_limit': 0.1,
    #             'fill_strategy': '黄果树',
    #         },
    #     }
    symbol_ratio_limit: dict = field(default_factory=dict)  # 币种权重显示

    strategy_cfg_list: list = field(default_factory=list)
    core_funcs: dict = field(default_factory=dict)  # 策略核心函数

    @cached_property
    def is_day_period(self):
        return self.hold_period.endswith('D')

    @cached_property
    def is_hour_period(self):
        return self.hold_period.endswith('H')

    @cached_property
    def period_num(self) -> int:
        return int(self.hold_period.upper().replace('H', '').replace('D', ''))

    @cached_property
    def period_type(self) -> str:
        return self.hold_period[-1]

    @cached_property
    def factor_columns(self) -> List[str]:
        factor_columns = set()  # 去重

        # 针对当前策略的因子信息，整理之后的列名信息，并且缓存到全局
        for factor_config in self.factor_list:
            # 策略因子最终在df中的列名
            factor_columns.add(factor_config.col_name)  # 添加到当前策略缓存信息中

        return list(factor_columns)

    def calc_ratio(self, df):
        raise NotImplementedError

    def load(self):
        funcs = PositionStrategyHub.get_funcs_by_name(self.name)
        self.core_funcs = funcs

        # 检查参数
        if self.rebalance_cap_step <= 0 or self.rebalance_cap_step > 1:
            raise ValueError('`rebalance_cap_step` 分步换仓比例必须是 (0, 1]，请检查设置！！！')

        # 自动转义因子
        if 'factor_list' in self.params:
            self.factor_list = FactorConfig.parse_config_list(self.params['factor_list'])

    def calc_ratios(self, equity_dfs):
        return self.core_funcs['calc_ratio'](equity_dfs, stg_conf=self)

    def prepare_live(self, me_conf, account, run_time):
        hook = self.core_funcs.get('prepare_live')
        if hook is not None:
            hook(me_conf, account, run_time)

    def transform_targets(self, spot, swap):
        hook = self.core_funcs.get('transform_targets')
        return (spot, swap) if hook is None else hook(spot, swap, stg_conf=self)

    def __repr__(self):
        return f'{self.name}-{self.hold_period}-{self.params}'
