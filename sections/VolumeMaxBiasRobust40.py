"""Research zero mask for invalid sections, without changing legacy strategies."""
import polars as pl
from sections.VolumeMaxBias_进出场阈值分离官方 import signal as legacy_signal, get_factor_list


def signal(*args):
    # Let the legacy continuation merge run before introducing any output column.
    df = legacy_signal(*args)
    name = args[2]
    if name not in df:
        df[name] = 0
    elif len(args) < 4 or args[3] is None:
        df[name] = df[name].fillna(0)
    return df


def signal_polars(lf, params, factor_name):
    df = signal(lf.collect().to_pandas(), params, factor_name)
    return lf.with_columns(pl.from_pandas(df[factor_name].rename(factor_name)))
