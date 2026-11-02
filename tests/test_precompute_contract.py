"""数据框架预计算列经真实 Arrow 发布器传递至交易框架，缺列可回退。"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from core.select_coin import calc_factors_by_candle
from core.utils.arrow_reader import read_arrow_market_dict


@pytest.mark.parametrize('declared,present,reuse', [(True, True, True), (True, False, False), (False, True, False)])
def test_published_factors_are_reused_or_recomputed(tmp_path, monkeypatch, declared, present, reuse, producer_root):
    import core.select_coin as select
    path = producer_root/'core/arrow_publish.py'
    if not path.exists():
        pytest.skip('需要数据框架 Arrow 发布器')
    spec = importlib.util.spec_from_file_location('producer_arrow_publish', path)
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    times = pd.date_range('2026-10-01', periods=4, freq='h')
    candle = pd.DataFrame({
        'candle_begin_time': times, 'first_candle_time': times[0],
        'symbol': 'BTCUSDT', 'is_spot': 0, 'close': [1., 2., 3., 4.],
        'symbol_spot': '', 'symbol_swap': 'BTCUSDT', '是否交易': 1,
    })
    values = np.array([np.nan, 0.1, 0.2, 0.3])
    if present:
        candle['CciV3_2'] = values
    output = tmp_path / 'swap.arrow'
    producer.publish_arrow_snapshot({'BTCUSDT': candle}, output, 4)
    loaded = read_arrow_market_dict(output)['BTCUSDT']
    conf = SimpleNamespace(name='test', min_kline_num=0, factor_params_dict={'CciV3': [2]},
                           precompute_factor_configs={'CciV3': [2]} if declared else {})
    monkeypatch.setattr(select, 'get_snapshot_time', lambda _: None)
    fallback = Mock(return_value={'CciV3_2': values})
    monkeypatch.setattr(select, 'calc_factor_vals', fallback)
    result = calc_factors_by_candle(loaded, conf, ['CciV3_2'])
    np.testing.assert_array_equal(result['CciV3_2'], values)
    assert fallback.call_count == (0 if reuse else 1)


def test_c_factors_survive_publication_and_consumer_selection(tmp_path, monkeypatch, producer_root):
    """隔离进程加载生产者，避免两个项目同名 core/factors/config 包互相污染。"""
    import core.select_coin as select
    if not (producer_root / 'core/arrow_publish.py').exists():
        pytest.skip('需要本地数据框架及其 C 扩展')
    script = r'''
import importlib, sys
from pathlib import Path
import numpy as np
import pandas as pd
from core.preprocess import apply_precomputed_factors
from core.arrow_publish import publish_arrow_snapshot
out = Path(sys.argv[1])
times = pd.date_range('2024-01-01', periods=18000, freq='h')
x = np.arange(len(times), dtype=float)
close = 100 + x / 1000 + np.sin(x / 30)
df = pd.DataFrame({'candle_begin_time': times, 'first_candle_time': times[0],
                   'symbol': 'BTCUSDT', 'is_spot': 0, 'close': close, 'open': close + .1,
                   'high': close + 1, 'low': close - 1, 'quote_volume': 1000 + x,
                   'symbol_spot': '', 'symbol_swap': 'BTCUSDT', '是否交易': 1})
names = ['动量因子210401修正', '动量210401_双项异常隔离V2', 'CciV3', 'ILLQStdBbi45208']
modules = [(importlib.import_module('factors.' + name), name, [168]) for name in names]
df.to_pickle(out / 'input.pkl')
computed = apply_precomputed_factors(df, modules)
computed.tail(5000).reset_index(drop=True).to_pickle(out / 'expected.pkl')
publish_arrow_snapshot({'BTCUSDT': computed}, out / 'factors.arrow', 5000)
'''
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=producer_root,
                               capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
    expected = pd.read_pickle(tmp_path / 'expected.pkl')
    # 独立交易端 Python oracle 校验 C 预计算，而非只比较 C 输出的往返传输。
    import importlib
    full_input = pd.read_pickle(tmp_path / 'input.pkl')
    oracle = importlib.import_module('factors.动量210401_双项异常隔离V2')
    python_values = oracle.signal_multi_params(full_input, [168])['168'].tail(5000).to_numpy()
    np.testing.assert_allclose(expected['动量210401_双项异常隔离V2_168'], python_values,
                               rtol=2e-11, atol=2e-11)
    loaded = read_arrow_market_dict(tmp_path / 'factors.arrow')['BTCUSDT']
    factors = {'动量因子210401修正': [168], '动量210401_双项异常隔离V2': [168],
               'CciV3': [168], 'ILLQStdBbi45208': [168]}
    columns = [f'{name}_168' for name in factors]
    conf = SimpleNamespace(name='test', min_kline_num=0, factor_params_dict=factors,
                           precompute_factor_configs=factors)
    monkeypatch.setattr(select, 'get_snapshot_time', lambda _: None)
    fallback = Mock(side_effect=AssertionError('完整预计算列不得在交易端重复计算'))
    monkeypatch.setattr(select, 'calc_factor_vals', fallback)
    result = calc_factors_by_candle(loaded, conf, columns)
    pd.testing.assert_frame_equal(result[columns], expected[columns], check_exact=True)
    assert result[columns].notna().any().all()
    fallback.assert_not_called()
