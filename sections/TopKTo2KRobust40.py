"""Research 2.1 exit boundary, isolated from legacy pandas entry behavior."""
import polars as pl
from sections.TopKTo2K import signal_polars, get_factor_list


def signal(*args):
    df, params, factor_name = args[:3]
    result = signal_polars(pl.from_pandas(df).lazy(), params, factor_name).collect()
    df[factor_name] = result.get_column(factor_name).to_numpy()
    return df
