"""截面 section 的 signal_polars 与 pandas signal（oracle）严格等价对账。

纯合成、确定性输入：不依赖数据中心快照。合成帧先落地为 parquet 再用
pl.scan_parquet 读入，复现生产路径（calc_cross_sections_polars）里 NaN→null 的
表示，把「依赖列到底是 nan 还是 null」这一 parity 风险钉死。oracle 口径：以
pandas signal 为准。"""
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

# tests 自插 repo root（仓库无 conftest.py / pytest.ini）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_section(mod_name):
    return importlib.import_module(f'sections.{mod_name}')


def _run_parity(module, params, factor_name, df, tmp_path):
    """df: 含所有依赖列的 pandas 帧。落地 parquet→两路计算→对账 factor 列。"""
    pq = tmp_path / 'base.parquet'
    df.to_parquet(pq)

    # oracle：pandas signal 在独立副本上计算（df_pd 从同一 parquet 读回，保证输入一致）
    df_pd = pd.read_parquet(pq)
    out_pd = module.signal(df_pd.copy(), params, factor_name)
    expected = out_pd[factor_name].reset_index(drop=True)

    # fast path：signal_polars 在 scan_parquet lazy 上计算
    lf = pl.scan_parquet(pq)
    out_pl = module.signal_polars(lf, params, factor_name).collect()
    got = out_pl[factor_name].to_pandas()

    # 数值列对齐 dtype 后逐格比较；NaN 位置须一致，inf 一致
    exp = expected.astype('float64').to_numpy()
    gv = got.astype('float64').to_numpy()
    en, gn = np.isnan(exp), np.isnan(gv)
    assert np.array_equal(en, gn), (
        f'{factor_name} NaN 位置不一致 mismatch={int((en ^ gn).sum())}')
    ev = np.where(en, 0.0, exp)
    gg = np.where(gn, 0.0, gv)
    assert np.allclose(ev, gg, equal_nan=False, rtol=0, atol=1e-12), (
        f'{factor_name} 值不一致 max|diff|={np.abs(ev - gg).max():.3e}')


def _zscore_frame():
    """两个截面：t0 正常离散、t1 全相等（std=0→z 分母 0，pandas 得 NaN→fillna 0）。
    含一个 LUNA 式 NaN 依赖值，触发 nan→null 归一路径。"""
    rows = []
    # t0：4 个 symbol，当前回撤_24 离散
    for sym, dd in [('A', -0.10), ('B', -0.20), ('C', -0.05), ('D', np.nan)]:
        rows.append(('2024-01-01 00:00:00', sym, dd))
    # t1：3 个 symbol，当前回撤_24 全相等（std=0）
    for sym in ['A', 'B', 'C']:
        rows.append(('2024-01-01 00:30:00', sym, -0.15))
    df = pd.DataFrame(rows, columns=['candle_begin_time', 'symbol', '当前回撤_24'])
    df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'])
    df['is_spot'] = 0
    return df


def test_动态回撤阈值_parity(tmp_path):
    mod = _load_section('动态回撤阈值')
    df = _zscore_frame()
    _run_parity(mod, 24, '动态回撤阈值_24', df, tmp_path)


def _breadth_frame():
    """两个截面，含现货域/交易域过滤 + 触发数占比门槛边界。
    max_pct=0.5：t0 domain 池=4，max_count=ceil(4*0.5)=2；触发 2 只 <=2 → 命中。
    t1 domain 池=4，触发 3 只 >2 → 全 0。含 is_spot=1 与空 symbol 行验证被剔除。"""
    rows = []
    # (time, symbol, dd, 是否交易, is_spot, symbol_spot, symbol_swap)
    t0 = '2024-01-01 00:00:00'
    rows += [
        (t0, 'A', -0.30, 1, 0, 'A-SPOT', 'A-SWAP'),   # 触发 (<-0.2)
        (t0, 'B', -0.25, 1, 0, 'B-SPOT', 'B-SWAP'),   # 触发
        (t0, 'C', -0.10, 1, 0, 'C-SPOT', 'C-SWAP'),   # domain 内，不触发
        (t0, 'D', -0.05, 1, 0, 'D-SPOT', 'D-SWAP'),   # domain 内，不触发
        (t0, 'E', -0.99, 1, 1, 'E-SPOT', 'E-SWAP'),   # is_spot=1，被剔除
        (t0, 'F', -0.99, 1, 0, '', ''),               # 空 symbol，被剔除
    ]
    t1 = '2024-01-01 00:30:00'
    rows += [
        (t1, 'A', -0.30, 1, 0, 'A-SPOT', 'A-SWAP'),   # 触发
        (t1, 'B', -0.25, 1, 0, 'B-SPOT', 'B-SWAP'),   # 触发
        (t1, 'C', -0.28, 1, 0, 'C-SPOT', 'C-SWAP'),   # 触发 → 共3只>2 → 全0
        (t1, 'D', -0.05, 1, 0, 'D-SPOT', 'D-SWAP'),   # domain 内，不触发
    ]
    df = pd.DataFrame(rows, columns=[
        'candle_begin_time', 'symbol', '当前回撤_24',
        '是否交易', 'is_spot', 'symbol_spot', 'symbol_swap'])
    df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'])
    return df


def test_固定回撤阈值触发币数占比开仓门槛_parity(tmp_path):
    mod = _load_section('固定回撤阈值触发币数占比开仓门槛')
    df = _breadth_frame()
    _run_parity(mod, (24, -0.2, 0.5), '固定回撤阈值触发币数占比开仓门槛_(24, -0.2, 0.5)',
                df, tmp_path)
