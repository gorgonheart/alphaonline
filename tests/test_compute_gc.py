"""GC changes must preserve ownership and leave unrelated caller state alone."""
import gc
import weakref

import pandas as pd
import polars as pl
import pytest


class Cycle:
    def __init__(self):
        self.owner = self


def test_temporary_gc_restores_disabled_state_after_body_enables_it():
    from core.utils.factor_data import temporary_gc
    previous = gc.isenabled()
    gc.disable()
    try:
        with temporary_gc():
            gc.enable()
        assert not gc.isenabled()
    finally:
        (gc.enable if previous else gc.disable)()


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_temporary_gc_collects_young_cycles_only_and_restores_caller(enabled, fail):
    from core.utils.factor_data import temporary_gc
    previous = gc.isenabled()
    old = Cycle()
    old_ref = weakref.ref(old)
    gc.collect()
    del old
    (gc.enable if enabled else gc.disable)()
    try:
        try:
            with temporary_gc():
                assert not gc.isenabled()
                young = Cycle()
                young_ref = weakref.ref(young)
                del young
                if fail:
                    raise RuntimeError('conversion failed')
        except RuntimeError as exc:
            assert fail and str(exc) == 'conversion failed'
        assert gc.isenabled() == enabled
        assert young_ref() is None
        assert old_ref() is not None, 'local cleanup traversed unrelated old generations'
    finally:
        (gc.enable if previous else gc.disable)()
        gc.collect()


def test_mapping_releases_mmaps_without_full_heap_collection(tmp_path, monkeypatch):
    from core.utils.factor_data import FactorBlocks
    from tools.initialization_pipeline import _swap_mapping
    from test_shared_io_optimization import make_frame
    frame = make_frame()
    store = FactorBlocks(tmp_path/'blocks', hours=4, minute=5)
    for i, part in enumerate(frame.iter_slices(17)):
        store.append(part, i)
    expected = store.read_history(['candle_begin_time','symbol','is_spot','symbol_spot','close','next_close'])
    expected = expected.filter((pl.col('is_spot') == 0) & (pl.col('symbol_spot') != '')).to_pandas().copy()
    gc.collect()
    original = gc.collect
    generations = []
    def collect(generation=2):
        generations.append(generation)
        return original(generation)
    monkeypatch.setattr(gc, 'collect', collect)
    actual = _swap_mapping(store)
    store.close()
    assert not store.directory.exists()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert generations == [0]
