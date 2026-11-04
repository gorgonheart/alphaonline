"""Projected factor blocks own no consumer frames or uncommitted columns."""
import gc
import weakref

import numpy as np
import pandas as pd
import polars as pl
import pytest


def make_store(tmp_path, minute=20):
    from core.utils.factor_data import FactorBlocks
    frame = pl.DataFrame(dict(
        candle_begin_time=list(pd.date_range(f'2026-01-01 00:{minute:02}', periods=4, freq='h'))*2,
        symbol=['B']*4+['A']*4, is_spot=[0]*4+[1]*4,
        score=[2., 2., None, 3.]*2, unused=list(range(8))))
    store = FactorBlocks(tmp_path/'blocks', hours=2, minute=minute)
    store.append(frame, 0)
    return store, frame.with_row_index('_row')


def test_reader_releases_unsorted_owner_before_selection(tmp_path, monkeypatch):
    store, expected = make_store(tmp_path)
    original = store._read
    references = []
    def read(*args):
        frame = original(*args)
        references.append(weakref.ref(frame))
        return frame
    monkeypatch.setattr(store, '_read', read)
    key = store.block_keys()[0]
    order = store.row_order(key)
    result = store.read_block(key, ['_row', 'score'], order=order)
    gc.collect()
    assert all(ref() is None for ref in references)
    assert result.height == 4
    assert result['_row'].to_list() == [4, 0, 5, 1]
    del result
    store.close()


def test_read_block_projects_only_requested_columns(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path)
    original = store._read
    seen = []
    def read(value, columns):
        seen.append(list(columns))
        return original(value, columns)
    monkeypatch.setattr(store, '_read', read)
    block = store.read_block(store.block_keys()[0], ['score'])
    assert block.columns == ['score']
    assert seen == [['score']]
    del block
    store.close()


def test_iterator_does_not_keep_yielded_frame(tmp_path):
    store, _ = make_store(tmp_path)
    iterator = store.iter_blocks(['score'], sort_time=False)
    frame = next(iterator)
    ref = weakref.ref(frame)
    del frame
    gc.collect()
    assert ref() is None
    iterator.close()
    store.close()


@pytest.mark.parametrize('minute', range(0, 60, 5))
def test_narrow_order_matches_full_sort_and_original_rows(tmp_path, minute):
    store, frame = make_store(tmp_path, minute)
    blocks = []
    for key in store.block_keys():
        block = store.read_block(key, frame.columns, order=store.row_order(key))
        blocks.append(block)
    actual = pl.concat(blocks)
    assert actual.equals(frame.sort(['candle_begin_time', 'symbol']))
    del block, actual, blocks
    store.close()


def test_section_install_failure_keeps_previous_columns(tmp_path, monkeypatch):
    store, frame = make_store(tmp_path)
    parts = {key: store.read_block(key, ['_row']).with_columns(pl.lit(1).alias('first'))
             for key in store.block_keys()}
    store.add_section('first', parts)
    before = store.read_history(['_row', 'first'])
    bad = {key: value.rename({'first': 'second'}) for key, value in parts.items()}
    last = store.block_keys()[-1]
    bad[last] = bad[last].reverse()
    with pytest.raises(ValueError, match='row'):
        store.add_section('second', bad)
    assert store.section_columns == ['first']
    assert store.read_history(['_row', 'first']).equals(before)
    assert 'second' not in store.section_columns
    del parts, bad, before
    store.close()


def test_append_schema_error_does_not_publish_shard(tmp_path):
    store, frame = make_store(tmp_path)
    old_rows = store.rows
    with pytest.raises(ValueError, match='columns'):
        store.append(frame.drop('_row', 'unused'), 1)
    assert store.rows == old_rows
    assert 1 not in store.shards
    store.close()
