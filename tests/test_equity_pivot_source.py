"""equity 必须通过 2 参数 pkl 契约读取 market pivot（read_bmac_market_pivot(hour_offset, symbol_type)），
不得走 Arrow 现场重建（run_time=/required_index= kwargs）。

背景：生产者 d:/alpha 每轮发布 market_pivot_{type}_{year}.pkl（save_ready('market_pivot_{type}') 门控），
是稳定的跨项目契约。upgrade/core-2.2.1 上一度加了从 Arrow market-dict 现场重建 pivot 的冗余路径；
其输出与 pkl 逐格相同（见已删除的 test_arrow_pivot_parity.py），故只能从"调用契约"观测：
2 参数 pkl == 期望，4 参数（含 run_time/required_index）== 冗余重建。

在 calc_equity 集成点断言调用形态，命中后抛哨兵异常在 Simulator/numba 启动前短路。"""
from datetime import datetime

import pandas as pd
import pytest

import core.equity as equity


class _StopBeforeSim(Exception):
    """哨兵：观测到 pivot 调用后立即中止 calc_equity，不启动 Simulator。"""


def _write_ratio_pkls(folder):
    # tz-naive index —— calc_equity 在 account_name='' 分支会 tz_localize('UTC')。
    idx = pd.to_datetime(['2026-01-01 00:00:00', '2026-01-01 01:00:00', '2026-01-01 02:00:00'])
    df_spot = pd.DataFrame({'BTCUSDT': [0.1, 0.2, 0.3], 'ETHUSDT': [0.0, 0.1, 0.2]}, index=idx)
    df_swap = pd.DataFrame({'BTCUSDT': [0.1, 0.2, 0.3], 'ETHUSDT': [0.0, 0.1, 0.2]}, index=idx)
    df_spot.to_pickle(folder / 'df_spot_ratio.pkl')
    df_swap.to_pickle(folder / 'df_swap_ratio.pkl')


class _Conf:
    def __init__(self, folder):
        self._folder = folder

    def get_result_folder(self):
        return self._folder


def test_calc_equity_loads_pivot_via_2arg_pkl_contract(tmp_path, monkeypatch):
    _write_ratio_pkls(tmp_path)
    conf = _Conf(tmp_path)
    run_time = datetime(2026, 6, 27, 20, 30)

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise _StopBeforeSim

    monkeypatch.setattr(equity, 'read_bmac_market_pivot', _spy)

    with pytest.raises(_StopBeforeSim):
        equity.calc_equity(conf, run_time)

    assert calls, 'read_bmac_market_pivot 未被调用'
    args, kwargs = calls[0]
    # 2 参数 pkl 契约：(hour_offset, symbol_type)，无 Arrow 重建 kwargs。
    assert kwargs == {}, f'不应传 Arrow 重建 kwargs，实际 kwargs={kwargs}'
    assert len(args) == 2, f'应为 2 位置参数 (hour_offset, symbol_type)，实际 args={args}'
    assert args[0] == '30m', f'hour_offset 应为 30m，实际 {args[0]}'
    assert args[1] == 'spot', f'首个 symbol_type 应为 spot，实际 {args[1]}'
