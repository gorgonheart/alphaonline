"""Release-oracle parity and the Python execution boundary (no account/network)."""
import importlib.util
import ast
import json
import os
import subprocess
import sys
from pathlib import Path
import numpy as np
import pytest
from positions import ModelRobust40Strategy as model


def oracle():
    spec = importlib.util.spec_from_file_location('frozen_execution', Path(__file__).parent/'fixtures/robust40_execution_7177978.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RebRobust40


def python_oracle():
    # Keep the frozen mathematics; only remove Numba decorators. The old
    # jitclass can misdispatch mixed contiguous/strided arguments on Numba 0.60.
    path = Path(__file__).parent/'fixtures/robust40_execution_7177978.py'
    tree = ast.parse(path.read_text(encoding='utf8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            node.decorator_list = []
    namespace = {}
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace['RebRobust40']


def test_live_does_not_construct_uncached_jitclass(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Live execution still constructs a process-local jitclass')
    monkeypatch.setattr(model, 'RebRobust40', fail)
    a = np.array([.01])
    reb = model.LiveRebRobust40(a, a)
    reb.calc_lots(1000., np.array([10.]), np.array([0]), np.array([.2]),
                  np.array([5.]), np.array([0]), np.array([-.2]))


def test_release_oracle_exact_and_inputs_unchanged():
    old = oracle()
    plain = python_oracle()
    rng = np.random.default_rng(937)
    for case in range(28):
        ns, nu = ((0, 0) if case == 0 else (3, 0) if case == 1 else (5, 17))
        sizes = [np.full(n, .001) for n in (ns, nu)]
        prices = [rng.uniform(.01, 100., n*2)[::2] for n in (ns, nu)]
        lots = [rng.integers(-10000, 10001, n, dtype=np.int64) for n in (ns, nu)]
        ratios = [rng.uniform(-.5, .5, n) for n in (ns, nu)]
        if case % 4 == 0:
            ratios = [np.zeros(n) for n in (ns, nu)]
        if case % 4 == 1:
            lots = [np.zeros(n, dtype=np.int64) for n in (ns, nu)]
        threshold = (.0, .4, 1.)[case % 3]
        cap = (.5, 2.45, 9.)[case % 3]
        args = (10000., prices[0], lots[0], ratios[0], prices[1], lots[1], ratios[1])
        before = [a.copy() for a in args[1:]]
        contiguous = (args[0], *(np.ascontiguousarray(a) for a in args[1:]))
        expected = plain(*sizes, threshold, cap).calc_lots(*args)
        old_result = old(*sizes, threshold, cap).calc_lots(*contiguous)
        for x, y in zip(old_result, expected):
            np.testing.assert_array_equal(x, y)
        actual = model.LiveRebRobust40(*sizes, threshold, cap).calc_lots(*args)
        for x, y in zip(actual, expected):
            np.testing.assert_array_equal(x, y)
        for x, y in zip(model.LiveRebRobust40(*sizes, threshold, cap).calc_lots(*contiguous), expected):
            np.testing.assert_array_equal(x, y)
        for x, y in zip(args[1:], before):
            np.testing.assert_array_equal(x, y)


@pytest.mark.parametrize('target,expected', [(6, 6), (7, 10), (14, 14), (13, 10), (0, 0), (-10, -10)])
def test_deadzone_boundary(target, expected):
    sizes = np.ones(1)
    empty = np.empty(0)
    reb = model.LiveRebRobust40(sizes, empty, .4, 100.)
    # Long-only budget is 97.  Price=97 makes integer target exact.
    s, _ = reb.calc_lots(100., np.array([97.]), np.array([10]), np.array([float(target)]),
                         empty, np.empty(0, dtype=np.int64), empty)
    assert s.tolist() == [expected]


def test_execution_cache_in_two_new_processes(tmp_path):
    probe = tmp_path/'probe.py'
    probe.write_text('''import json,time
import numpy as np
from positions import ModelRobust40Strategy as m
a=np.ones(3);u=np.ones(126);zero=lambda n:np.zeros(n,dtype=np.int64)
t=time.perf_counter()
r=m.LiveRebRobust40(a,u).calc_lots(10000.,a*100,zero(3),a*.02,u*10,zero(126),u*-.005)
print(json.dumps(dict(seconds=time.perf_counter()-t, hits=sum(m.execution_lots.stats.cache_hits.values()),
 misses=sum(m.execution_lots.stats.cache_misses.values()), output=[x.tolist() for x in r])))
''', encoding='utf8')
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ, PYTHONPATH=str(root), NUMBA_CACHE_DIR=str(tmp_path/'cache'), X3S_USE_FILE_LOGGING='0')
    outputs=[]
    for _ in range(2):
        result=subprocess.run([sys.executable,'-B',str(probe)],cwd=root,env=env,
                              capture_output=True,text=True,timeout=180)
        assert result.returncode == 0, result.stderr
        assert 'Cannot cache' not in result.stderr
        outputs.append(json.loads(result.stdout.splitlines()[-1]))
    assert outputs[0]['misses'] == 1
    assert outputs[1]['hits'] == 1 and outputs[1]['misses'] == 0
    assert outputs[0]['output'] == outputs[1]['output']
    print('EXECUTION_CACHE '+json.dumps(outputs))
