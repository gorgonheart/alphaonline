"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.utils.log_kit import logger

_BTC_CACHE: dict = {}
_BTC_CACHE_MAX = 8
_CAP_CACHE: dict = {}
_CAP_CACHE_MAX = 2048


def _to_naive_ns(series: pd.Series) -> np.ndarray:
    """Convert datetime Series to tz-naive int64 nanoseconds (matching pandas merge semantics).

    Fast path avoids `pd.to_datetime()` re-parsing (~5 ms/call in pandas 2.2) when
    the Series is already `datetime64` or `DatetimeTZDtype`.
    """
    dtype = series.dtype
    if isinstance(dtype, pd.DatetimeTZDtype):
        return series.dt.tz_localize(None).to_numpy(dtype='datetime64[ns]', copy=False).view(np.int64)
    if np.issubdtype(dtype, np.datetime64):
        return series.to_numpy(dtype='datetime64[ns]', copy=False).view(np.int64)
    dt = pd.to_datetime(series)
    if dt.dt.tz is not None:
        dt = dt.dt.tz_localize(None)
    return dt.astype('datetime64[ns]').to_numpy(dtype=np.int64, copy=False)


def _load_cap_file_cached(file_path: Path) -> Optional[pd.DataFrame]:
    """Read, deduplicate, and sort a coin_cap pickle once per (path, mtime_ns, size)."""
    try:
        st = file_path.stat()
    except OSError:
        return None
    cache_key = (str(file_path), st.st_mtime_ns, st.st_size)
    cached = _CAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        extra_df = pd.read_pickle(file_path)
        extra_df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)
        extra_df.sort_values('candle_begin_time', inplace=True)
    except BaseException:
        return None
    if len(_CAP_CACHE) >= _CAP_CACHE_MAX:
        _CAP_CACHE.clear()
    _CAP_CACHE[cache_key] = extra_df
    return extra_df


def _ffill_1d(arr: np.ndarray) -> np.ndarray:
    """Fast 1D forward-fill for float64 array (zero pandas inplace FutureWarning)."""
    out = arr.astype(np.float64, copy=True)
    if out.size == 0:
        return out
    mask = np.isnan(out)
    if not mask.any():
        return out
    idx = np.where(~mask, np.arange(out.size), 0)
    np.maximum.accumulate(idx, out=idx)
    valid_prefix = ~np.isnan(out[idx])
    out[mask & valid_prefix] = out[idx[mask & valid_prefix]]
    return out


def _mode_log10_scale(close_arr: np.ndarray, usd_price_arr: np.ndarray) -> float:
    """Vectorized exact equivalent of `times.map(lambda x: 10 ** np.log10(x).round(0)).mode().iloc[0]`."""
    with np.errstate(divide='ignore', invalid='ignore'):
        ratio = close_arr / usd_price_arr
        mapped = np.power(10.0, np.round(np.log10(ratio)))
    valid = mapped[~np.isnan(mapped)]
    if valid.size == 0:
        return np.nan
    # np.unique returns ascending sorted values; argmax selects the smallest mode on ties,
    # matching pandas Series.mode().iloc[0].
    uniq, counts = np.unique(valid, return_counts=True)
    return float(uniq[int(np.argmax(counts))])


def load_coin_cap(file_path: str, candle_df: pd.DataFrame, save_cols: list, symbol: str) -> Optional[pd.DataFrame]:
    """
    加载coinmarketcap数据
    :param file_path: 文件路径
    :param candle_df: 原始k线数据
    :param save_cols: 需要保存的列
    :param symbol: 币种
    :return: 一个 df 数据
    """
    data_path = Path(file_path)
    # 合并选币名称并剔除空字符串
    symbols = (
        {symbol} if symbol else
        {s for s in (list(candle_df['symbol_spot'].unique()) + list(candle_df['symbol_swap'].unique())) if s}
    )
    # 兼容 回测 & 实盘。
    # 回测币种带 -USDT，实盘币种带 USDT
    # bmac 那边存储的数据是 pkl，所以这里拼接 .pkl 结尾
    all_file_paths = [data_path / f'{s}.pkl' for s in symbols]

    # 读取数据（按 (path, mtime_ns, size) 进程内缓存，消除同轮 spot/swap 双侧重复读盘与去重排序）
    extra_df_list = []
    for p in all_file_paths:
        extra_df = _load_cap_file_cached(p)
        if extra_df is not None:
            extra_df_list.append(extra_df)

    if not extra_df_list:
        logger.warning(f'coin_cap 没有找到任何数据，symbol={symbols}')
        return None

    if len(candle_df) == 1:
        logger.warning(f'{candle_df["symbol"].iloc[0]}币种K线数据不足，跳过')
        return None

    # 预处理
    extra_df = extra_df_list[0] if len(extra_df_list) == 1 else pd.concat(extra_df_list, ignore_index=True, copy=False)
    time_diff = candle_df['candle_begin_time'].iloc[1] - candle_df['candle_begin_time'].iloc[0]
    hour_diff = time_diff.components.days * 24 + time_diff.components.hours
    hold_period_type = 'D' if hour_diff == 24 else 'H'

    extra_ts = _to_naive_ns(extra_df['candle_begin_time'])
    if hold_period_type == 'H':
        extra_ts = extra_ts + np.int64(23 * 3600 * 1_000_000_000)

    # Ensure extra_ts is ascending for searchsorted (merge_asof requires sorted right keys)
    if len(extra_df_list) > 1 or (extra_ts.size > 1 and np.any(extra_ts[1:] < extra_ts[:-1])):
        order = np.argsort(extra_ts, kind='mergesort')
        extra_ts = extra_ts[order]
        extra_df = extra_df.iloc[order]

    candle_ts = _to_naive_ns(candle_df['candle_begin_time'])
    pos = np.searchsorted(extra_ts, candle_ts, side='right') - 1
    valid_pos = pos >= 0
    safe_pos = np.where(valid_pos, pos, 0)

    # 处理 cmc 数据中的 usd_price与close 数据的不一致
    # 例：1000SATS usd_price = 0.0000001， 但是close = 0.0001
    close_arr = candle_df['close'].to_numpy(dtype=np.float64, copy=False)
    usd_raw = extra_df['usd_price'].to_numpy(dtype=np.float64, copy=False)
    usd_aligned = np.where(valid_pos, usd_raw[safe_pos], np.nan)
    scale = _mode_log10_scale(close_arr, usd_aligned)

    # 筛选指定列
    columns = list(save_cols) if save_cols else [c for c in extra_df.columns if c != 'candle_begin_time']
    out_data = {}
    for col in columns:
        if col == 'close':
            series = pd.Series(close_arr, dtype=np.float64).ffill()
            out_data[col] = series.to_numpy(copy=False)
            continue
        col_vals = extra_df[col].to_numpy(copy=False)
        if np.issubdtype(col_vals.dtype, np.number):
            aligned = np.where(valid_pos, col_vals.astype(np.float64, copy=False)[safe_pos], np.nan)
            if 'supply' in col:
                aligned = aligned / scale
            out_data[col] = _ffill_1d(aligned)
        else:
            aligned_s = pd.Series(col_vals[safe_pos])
            if not valid_pos.all():
                aligned_s = aligned_s.where(valid_pos, np.nan)
            out_data[col] = aligned_s.ffill().to_numpy(copy=False)

    return pd.DataFrame(out_data)


def _load_btc_cached(candidate_path: Path, is_csv: bool) -> Optional[tuple]:
    """Read and cache sorted tz-naive (ts_ns, btc_close) keyed by (path, mtime_ns, size)."""
    try:
        st = candidate_path.stat()
    except OSError:
        return None
    cache_key = (str(candidate_path.resolve()), st.st_mtime_ns, st.st_size)
    cached = _BTC_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        if is_csv:
            extra_df = pd.read_csv(candidate_path, skiprows=1, encoding='gbk', parse_dates=['candle_begin_time'])
        else:
            extra_df = pd.read_pickle(candidate_path)
        extra_df = extra_df[['candle_begin_time', 'close']].copy()
    except BaseException as e:
        if is_csv:
            logger.warning(f'coin_BTC 回测模式读取失败：{e}')
        return None
    if extra_df is None or extra_df.empty:
        return None
    extra_df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)
    extra_df.sort_values('candle_begin_time', inplace=True)
    ts_ns = _to_naive_ns(extra_df['candle_begin_time'])
    close_arr = extra_df['close'].to_numpy(dtype=np.float64, copy=True)
    if len(_BTC_CACHE) >= _BTC_CACHE_MAX:
        _BTC_CACHE.clear()
    result = (ts_ns, close_arr)
    _BTC_CACHE[cache_key] = result
    return result


def load_coin_btc(file_path: str, candle_df: pd.DataFrame, save_cols: list, symbol: str) -> Optional[pd.DataFrame]:
    """
    加载BTC现货K线数据
    优先尝试回测模式（读取 BTC-USDT.csv），失败则尝试实盘模式（按分钟偏移读取 pkl）
    :param file_path: 数据目录路径
    :param candle_df: 原始k线数据
    :param save_cols: 需要保存的列（如 ['btc_close']）
    :param symbol: 币种（未使用，保留接口一致性）
    :return: 一个 df 数据
    """
    if candle_df is None or candle_df.empty:
        logger.warning('coin_BTC 未收到K线数据，跳过')
        return None

    if len(candle_df) == 1:
        logger.warning(f'{candle_df["symbol"].iloc[0]}币种K线数据不足，跳过')
        return None

    data_path = Path(file_path)
    btc_pair = None

    # === 模式1：回测模式，直接读取 BTC-USDT.csv ===
    csv_path = data_path / 'BTC-USDT.csv'
    if csv_path.exists():
        btc_pair = _load_btc_cached(csv_path, is_csv=True)

    # === 模式2：实盘模式，按分钟偏移读取 pkl ===
    if btc_pair is None:
        minute_offset = int(candle_df['candle_begin_time'].iloc[-1].minute)
        data_folder = data_path / f'{minute_offset}m'
        if data_folder.exists():
            for symbol_name in ('BTCUSDT', 'BTC-USDT'):
                candidate_path = data_folder / f'{symbol_name}.pkl'
                if candidate_path.exists():
                    btc_pair = _load_btc_cached(candidate_path, is_csv=False)
                    if btc_pair is not None:
                        break

    if btc_pair is None:
        logger.warning(f'coin_BTC 没有找到任何数据，路径={data_path}')
        return None

    extra_ts, extra_close = btc_pair
    candle_ts = _to_naive_ns(candle_df['candle_begin_time'])
    if candle_ts.size > 1 and np.any(candle_ts[1:] < candle_ts[:-1]):
        candle_ts = np.sort(candle_ts)

    pos = np.searchsorted(extra_ts, candle_ts, side='left')
    in_bounds = pos < extra_ts.size
    safe_pos = np.where(in_bounds, pos, 0)
    matched = in_bounds & (extra_ts[safe_pos] == candle_ts)
    btc_close = np.where(matched, extra_close[safe_pos], np.nan)
    btc_close = _ffill_1d(btc_close)

    columns = list(save_cols) if save_cols else ['candle_begin_time', 'btc_close']
    available = {}
    for col in columns:
        if col == 'btc_close':
            available[col] = btc_close
        elif col == 'candle_begin_time':
            available[col] = pd.to_datetime(candle_ts)

    if not available:
        logger.warning(f'coin_BTC 没有可用的列：{columns}')
        return None

    return pd.DataFrame(available)
