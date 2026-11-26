"""align_spot_swap_mapping_fast 必须与原 align_spot_swap_mapping 输出逐行一致。

原函数（groupby.cumcount 路径）是 oracle：fast 版只是把 groupby 向量化，
语义必须 byte-identical。仅 Arrow 快路径使用 fast 版，pkl fallback 仍用原版。
用 alpha311 跑：tests 见 [[python-env]]。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.utils.datatools import align_spot_swap_mapping, align_spot_swap_mapping_fast


def _make_df(swap_col, spot_col):
    return pd.DataFrame({'symbol_swap': list(swap_col), 'symbol_spot': list(spot_col)})


def _assert_same(swap_col, spot_col, column_name, n):
    df = _make_df(swap_col, spot_col)
    expected = align_spot_swap_mapping(df.copy(), column_name, n)
    actual = align_spot_swap_mapping_fast(df.copy(), column_name, n)
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True), expected.reset_index(drop=True),
        check_dtype=False, obj=f'col={column_name} n={n} swap={swap_col} spot={spot_col}')


# ---- spot path: column_name == 'symbol_swap'? No. spot path aligns symbol_spot,
# but the n=对齐列 is the *other* type's symbol. Cover both columns as align target. ----

@pytest.mark.parametrize('n', [1, 2, 3, 5])
def test_continuous_listing(n):
    _assert_same(['A'] * 6, ['A'] * 6, 'symbol_spot', n)


@pytest.mark.parametrize('n', [1, 2, 3])
def test_leading_empty(n):
    _assert_same(['', '', 'A', 'A', 'A'], ['', '', 'A', 'A', 'A'], 'symbol_spot', n)


@pytest.mark.parametrize('n', [1, 2])
def test_one_relisting(n):
    col = ['A', 'A', '', '', 'A', 'A', 'A']
    _assert_same(col, col, 'symbol_spot', n)


@pytest.mark.parametrize('n', [1, 2])
def test_multi_relisting(n):
    col = ['A', '', 'A', '', 'A', 'A']
    _assert_same(col, col, 'symbol_spot', n)


def test_all_empty():
    _assert_same(['', '', '', ''], ['', '', '', ''], 'symbol_spot', 2)


def test_n_larger_than_group():
    _assert_same(['A', 'A'], ['A', 'A'], 'symbol_spot', 5)


def test_swap_path_mismatch_column():
    # v2.4.1: align_spot_swap_mapping masks empty values on `column_name` itself,
    # independent of `symbol_swap == ''`.
    df = _make_df(['', 'A', 'A', '', 'A'], ['A', 'A', 'A', 'A', 'A'])
    expected = align_spot_swap_mapping(df.copy(), 'symbol_spot', 2)
    actual = align_spot_swap_mapping_fast(df.copy(), 'symbol_spot', 2)
    assert expected['symbol_spot'].tolist() == ['', '', 'A', 'A', 'A']
    assert actual['symbol_spot'].tolist() == ['', '', 'A', 'A', 'A']


def test_swap_table_single_coin_relisting_preserves_align():
    # Simulated swap table where symbol_swap is constant 'BTCUSDT' and symbol_spot relists.
    swap_col = ['BTCUSDT'] * 9
    spot_col = ['BTCUSDT', 'BTCUSDT', 'BTCUSDT', '', '', 'BTCUSDT', 'BTCUSDT', 'BTCUSDT', 'BTCUSDT']
    df = _make_df(swap_col, spot_col)
    expected = align_spot_swap_mapping(df.copy(), 'symbol_spot', 2)
    actual = align_spot_swap_mapping_fast(df.copy(), 'symbol_spot', 2)
    assert expected['symbol_spot'].tolist() == ['', '', 'BTCUSDT', '', '', '', '', 'BTCUSDT', 'BTCUSDT']
    assert actual['symbol_spot'].tolist() == expected['symbol_spot'].tolist()


def test_align_symbol_swap_column():
    # the other direction: column_name == 'symbol_swap' (mask col == align col)
    col = ['A', 'A', '', 'A', 'A', 'A']
    _assert_same(col, ['A'] * 6, 'symbol_swap', 2)


@pytest.mark.parametrize('seed', range(200))
def test_fuzz_random_sequences(seed):
    rng = np.random.default_rng(seed)
    length = int(rng.integers(1, 120))
    swap = [rng.choice(['', 'A']) for _ in range(length)]
    spot = [rng.choice(['', 'A']) for _ in range(length)]
    n = int(rng.integers(1, 10))
    column_name = rng.choice(['symbol_spot', 'symbol_swap'])
    _assert_same(swap, spot, column_name, n)
