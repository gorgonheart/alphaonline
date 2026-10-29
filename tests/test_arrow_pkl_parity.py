"""真实 30m 数据：Arrow 快路径 vs pkl 路径，经 read_and_merge_bmac_data 后逐 symbol/列/行对账。

Arrow 侧走向量化 align（align_spot_swap_mapping_fast），pkl 侧走原版 align，
故本测试同时验证：(1) 两条读取路径数据一致；(2) fast align 在真实数据上与原版逐行等价。
ts 动态发现最新快照（快照每轮轮换，不可写死）。"""
import glob
import ast
import re
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from core.utils.arrow_reader import read_arrow_market_dict
from core.utils.datatools import (read_and_merge_bmac_data, align_spot_swap_mapping,
                                   align_spot_swap_mapping_fast)

ARROW_30M = Path(r'd:/alpha/data/preprocess_1h_resample/30m')


def _latest_ts():
    tss = []
    for p in glob.glob(str(ARROW_30M / 'swap_dict_*.arrow')):
        m = re.search(r'swap_dict_(\d+)\.arrow$', Path(p).name)
        if m:
            tss.append(int(m.group(1)))
    return max(tss) if tss else None


TS = _latest_ts()


def _producer_factor_columns():
    # 只读取生产者配置中的字面量，不导入另一项目的 config/core，也不执行配置副作用。
    tree = ast.parse((ARROW_30M.parents[2] / 'config.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'precompute_factor_configs' for t in node.targets):
            configured = ast.literal_eval(node.value)
            # 升级不重写既有真实行情快照；历史旧动量列只用于此行情对账，不可作为 V2 复用。
            if '动量210401_双项异常隔离V2' in configured:
                configured['动量因子210401修正'] = configured['动量210401_双项异常隔离V2']
            return {f'{name}_{param}' for name, params in configured.items() for param in params}
    raise AssertionError('生产者缺少预计算因子声明，不能放宽列契约')

pytestmark = pytest.mark.skipif(TS is None, reason='需要真实 30m arrow 数据')


def _conf():
    conf = mock.Mock()
    conf.black_list = []
    conf.white_list = []
    conf.precompute_factor_configs = {}
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
    run_time = datetime.fromtimestamp(TS)
    arrow_dict = read_arrow_market_dict(ARROW_30M / f'{symbol_type}_dict_{TS}.arrow')
    pkl_dict = _pkl_dict(symbol_type)

    def merge_all(d, align_fn):
        out = {}
        for _df in d.values():
            if _df is None or _df.empty:
                continue
            sym, m = read_and_merge_bmac_data(conf, symbol_type, _df.copy(), run_time, align_fn=align_fn)
            if m is not None:
                out[sym] = m
        return out

    # Arrow 侧用 fast align（与生产路径一致），pkl 侧用原版 align（oracle）
    arrow_out = merge_all(arrow_dict, align_spot_swap_mapping_fast)
    pkl_out = merge_all(pkl_dict, align_spot_swap_mapping)

    assert set(arrow_out.keys()) == set(pkl_out.keys()), \
        f'symbol 集合不一致: arrow-pkl={set(arrow_out)-set(pkl_out)}, pkl-arrow={set(pkl_out)-set(arrow_out)}'

    declared_columns = _producer_factor_columns()
    for sym in pkl_out:
        a, p = arrow_out[sym], pkl_out[sym]
        # 生产者有意只向 Arrow 嵌入预计算因子，持久化 PKL 保留原始行情。
        # 只允许明确配置的附加列；行情缺列、未知附加列、顺序和数值差异仍失败。
        extra = set(a.columns) - set(p.columns)
        assert extra <= declared_columns, f'{sym} 未声明附加列: {extra}'
        a = a.drop(columns=list(extra))
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

