"""Offline: normal module imports must reuse Hybrid's disk cache across workers."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_hybrid_import_does_not_load_framework():
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c',
        "import sys; from positions import HybridRotationDriftStrategy; "
        "assert 'config' not in sys.modules; assert 'core.model.strategy_config' not in sys.modules"],
        cwd=ROOT, capture_output=True, text=True, encoding='utf8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_model_uses_normal_hybrid_import():
    from positions import ModelRobust40Strategy as model
    from positions import HybridRotationDriftStrategy as hybrid
    assert model.calc_hybrid_ratio is hybrid.calc_ratio


def test_hybrid_fresh_process_cache_and_reference_parity(tmp_path):
    probe = tmp_path / 'probe.py'
    probe.write_text('''import json, time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from positions import HybridRotationDriftStrategy as h
from tools.robust40.offline import hybrid_function
t = pd.date_range('2025-01-01 00:40', periods=400, freq='h')
x = np.arange(len(t))
equities = [pd.DataFrame({'candle_begin_time':t, 'close':100*np.exp(np.sin(x/(7+i))*.2+x*.001),
                         'Bias_48':np.sin(x/(9+i)), 'Bias_144':np.cos(x/(13+i))}) for i in range(6)]
params = dict(cap_ratios=[.2,.3,.4,.2,.3,.1], direction_list=['long']*4+['short']*2,
    factor_list=[('Bias',False,48,1),('Bias',False,144,1)], rotation_indices=[0,1],
    dynamic_groups=[dict(indices=[2,3],direction='long',mode='both',period_hours=48,
                        trigger_threshold=.05,rebalance_duration_hours=7),
                    dict(indices=[4,5],direction='short',mode='both',period_hours=72,
                        trigger_threshold=.1,rebalance_duration_hours=3)],
    init_time='2025-01-02 00:40', rebalance_anchor_time='2021-01-01')
start=time.perf_counter(); actual=h.calc_ratio(equities,SimpleNamespace(params=params)); elapsed=time.perf_counter()-start
reference=hybrid_function(Path(h.__file__))(equities,SimpleNamespace(params=params))
np.testing.assert_array_equal(actual.to_numpy(),reference.to_numpy())
pd.testing.assert_index_equal(actual.index,reference.index)
pd.testing.assert_frame_equal(actual.attrs['hybrid_baseline'],reference.attrs['hybrid_baseline'])
assert actual.attrs['hybrid_dynamic_groups']==reference.attrs['hybrid_dynamic_groups']
core=h._drift_core_numba
print('RESULT '+json.dumps(dict(hits=sum(core.stats.cache_hits.values()),
    misses=sum(core.stats.cache_misses.values()),seconds=elapsed,values=actual.to_numpy().tolist())))
''', encoding='utf8')
    env = dict(os.environ, PYTHONPATH=str(ROOT), NUMBA_CACHE_DIR=str(tmp_path/'cache'),
               X3S_USE_FILE_LOGGING='0')
    results = []
    for _ in range(2):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(probe)],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Cannot cache' not in result.stderr
        results.append(json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT '))))
    assert results[0]['misses'] > 0
    assert results[1]['misses'] == 0 and results[1]['hits'] == results[0]['misses']
    assert results[0]['values'] == results[1]['values']
