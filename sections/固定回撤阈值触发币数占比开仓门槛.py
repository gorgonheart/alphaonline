import numpy as np
import polars as pl


# 已对拍：可独立按声明的依赖列计算，不修改其他截面的输入。
shared_input_safe = True


def get_state_input_columns(params):
    return ['candle_begin_time', '是否交易', 'is_spot', 'symbol_spot',
            'symbol_swap', f'当前回撤_{params[0]}']


def signal_block(frame, params, factor_name, state=None):
    """Count the full market at each timestamp, independent of adjacent blocks."""
    return signal_polars(frame.lazy(), params, factor_name).collect(), None


def signal(*args):
    df = args[0]
    window, threshold, max_pct = args[1]
    factor_name = args[2]

    col = f"当前回撤_{window}"
    df[factor_name] = 0

    domain_mask = (
        df[col].notna()
        & (df["是否交易"] == 1)
        & (df["is_spot"] == 0)
        & (df["symbol_spot"] != "")
        & (df["symbol_swap"] != "")
    )

    trigger_mask = domain_mask & (df[col] < threshold)
    group = df["candle_begin_time"]
    pool_count = domain_mask.groupby(group).transform("sum")
    trigger_count = trigger_mask.groupby(group).transform("sum")
    max_count = np.ceil(pool_count * max_pct)

    df.loc[trigger_mask & (trigger_count <= max_count), factor_name] = 1

    return df


def get_factor_list(param):
    window, _, _ = param
    return [("当前回撤", window)]


def signal_polars(lf: pl.LazyFrame, params, factor_name: str) -> pl.LazyFrame:
    """Polars 版本：固定回撤阈值触发币数占比开仓门槛。

    与 pandas signal 严格等价：domain 域内按 candle_begin_time 统计触发币数占比，
    仅当本截面触发数 <= ceil(池数 * max_pct) 时，触发币记 1，其余记 0（无 null）。
    入口 fill_nan(None) 使 is_not_null() 与 pandas notna() 语义一致。
    """
    window, threshold, max_pct = params
    col = f'当前回撤_{window}'
    lf = lf.with_columns(pl.col(col).fill_nan(None).alias('_dd'))
    domain = (
        pl.col('_dd').is_not_null()
        & (pl.col('是否交易') == 1)
        & (pl.col('is_spot') == 0)
        & (pl.col('symbol_spot') != '')
        & (pl.col('symbol_swap') != '')
    )
    trigger = domain & (pl.col('_dd') < threshold)
    lf = lf.with_columns([domain.alias('_domain'), trigger.alias('_trigger')])
    lf = lf.with_columns([
        pl.col('_domain').cast(pl.Int64).sum().over('candle_begin_time').alias('_pool'),
        pl.col('_trigger').cast(pl.Int64).sum().over('candle_begin_time').alias('_trig_cnt'),
    ])
    lf = lf.with_columns((pl.col('_pool') * max_pct).ceil().alias('_maxc'))
    lf = lf.with_columns(
        pl.when(pl.col('_trigger') & (pl.col('_trig_cnt') <= pl.col('_maxc')))
        .then(1).otherwise(0).cast(pl.Int64).alias(factor_name)
    )
    lf = lf.drop(['_dd', '_domain', '_trigger', '_pool', '_trig_cnt', '_maxc'])
    return lf
