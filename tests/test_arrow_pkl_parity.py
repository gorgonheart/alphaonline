"""真实 30m 数据：Arrow 快路径 vs pkl 路径，经 read_and_merge_bmac_data 后逐 symbol/列/行对账。"""
import glob
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from core.utils.arrow_reader import read_arrow_market_dict
from core.utils.datatools import read_and_merge_bmac_data

ARROW_30M = Path(r'd:/alpha/data/preprocess_1h_resample/30m')
TS = 1782563400
RUN_TIME = datetime.fromtimestamp(TS)

pytestmark = pytest.mark.skipif(
    not (ARROW_30M / f'swap_dict_{TS}.arrow').exists(),
    reason='需要真实 30m arrow 数据')


def _conf():
    conf = mock.Mock()
    conf.black_list = []
    conf.white_list = []
    conf.min_kline_num = 1
    conf.get_kline_num.return_value = 4050
    return conf


def _pkl_dict(symbol_type):
    merged = {}
    for p in glob.glob(str(ARROW_30M / f'{symbol_type}_dict_batch*.pkl')):
        merged.update(pd.read_pickle(p))
    return merged


@pytest.mark.parametrize('symbol_type', ['swap', 'spot'])
def test_arrow_pkl_merge_parity(symbol_type):
    conf = _conf()
    arrow_dict = read_arrow_market_dict(ARROW_30M / f'{symbol_type}_dict_{TS}.arrow')
    pkl_dict = _pkl_dict(symbol_type)

    def merge_all(d):
        out = {}
        for _df in d.values():
            if _df is None or _df.empty:
                continue
            sym, m = read_and_merge_bmac_data(conf, symbol_type, _df.copy(), RUN_TIME)
            if m is not None:
                out[sym] = m
        return out

    arrow_out = merge_all(arrow_dict)
    pkl_out = merge_all(pkl_dict)

    assert set(arrow_out.keys()) == set(pkl_out.keys()), \
        f'symbol 集合不一致: arrow-pkl={set(arrow_out)-set(pkl_out)}, pkl-arrow={set(pkl_out)-set(arrow_out)}'

    for sym in pkl_out:
        a, p = arrow_out[sym], pkl_out[sym]
        assert list(a.columns) == list(p.columns), f'{sym} 列顺序不一致'
        # 值必须逐列逐行完全一致；dtype 允许差异，但仅限"整数值列"
        # （pkl 侧 fill_gaps 把曾有缺口的 symbol 的 trade_num 等整数列提升为 float64，
        #  是 legacy 产物；单文件 Arrow 统一 schema 无法复现 per-symbol dtype。
        #  这里证明：凡 dtype 不同的列，两侧值相等且都是整数 → 仅此一种已知无害分歧）。
        pd.testing.assert_frame_equal(a, p, check_like=False, check_dtype=False,
                                      obj=f'symbol={sym}')
        for col in p.columns:
            if a[col].dtype != p[col].dtype:
                # dtype 分歧只允许出现在"两侧均为整数值"的数值列上
                a_num = pd.to_numeric(a[col], errors='coerce')
                p_num = pd.to_numeric(p[col], errors='coerce')
                assert a_num.notna().all() and p_num.notna().all(), \
                    f'{sym}.{col} dtype 分歧但存在非数值，疑似真实 bug'
                assert ((a_num % 1) == 0).all() and ((p_num % 1) == 0).all(), \
                    f'{sym}.{col} dtype 分歧且含小数，疑似真实精度损失 bug'

