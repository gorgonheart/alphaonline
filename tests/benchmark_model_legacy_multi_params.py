"""Offline, bounded old/new complete dispatch benchmark; no services or network.

Run from the repository root with alpha311, -X utf8 -B. The output explicitly
identifies synthetic auxiliary fields; this is not a whole-account benchmark.
"""
import argparse
import ast
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pyarrow as pa

from core.factor import calc_factor_vals
from core.utils.factor_hub import FactorHub
from test_model_legacy_multi_params import model_parameters


def legacy_dispatch(df, name, params, shift=0):
    factor = FactorHub.get_by_name(name)
    copied = df.copy()
    result = {}
    for param in params:
        column = f'{name}_{param}'
        copied = factor.signal(copied, param, column)
        result[column] = copied[column].shift(shift).values
    return result


def check_unchanged_legacy_signals(names):
    for name in names:
        path = Path('factors') / f'{name}.py'
        original = subprocess.check_output(['git', 'show', f'cee71bc5a4417f1069c397f83a15a9b417741860:{path.as_posix()}'], cwd=ROOT).decode('utf-8-sig')
        current = (ROOT / path).read_text(encoding='utf-8-sig')

        def signal(source):
            return next(n for n in ast.parse(source).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'signal')

        assert ast.dump(signal(original)) == ast.dump(signal(current)), name


def samples(producer):
    path = producer / 'data/binance_swap_1h_resample/40m/BTCUSDT.pkl'
    full = pd.read_pickle(path).tail(14050).reset_index(drop=True)
    full['symbol'] = full['symbol_spot'] = full['symbol_swap'] = 'BTCUSDT'
    # This PKL contains OHLCV only. Auxiliary values are declared synthetic.
    full['btc_close'] = full['close']
    full['circulating_supply'] = 19_000_000.
    full['funding_fee'] = np.resize([0., -.0001, 0., .0002], len(full))
    yield full, dict(kind='real_ohlcv_pkl', path=str(path),
                     synthetic=['symbol mappings', 'circulating_supply', 'funding_fee'],
                     btc_close='same BTC OHLCV close')

    paths = sorted((producer / 'data/preprocess_1h_resample/40m').glob('swap*.arrow'))
    path = paths[-1]
    with pa.memory_map(str(path), 'r') as source:
        reader = pa.ipc.open_file(source)
        symbols = json.loads(reader.schema.metadata[b'symbols'])
        batch = reader.get_batch(symbols.index('BTCUSDT'))
        short = batch.slice(max(0, batch.num_rows - 4050)).to_pandas().reset_index(drop=True)
    short['btc_close'] = short['close']
    short['circulating_supply'] = 19_000_000.
    yield short, dict(kind='real_arrow', path=str(path),
                      synthetic=['circulating_supply'], btc_close='same BTC Arrow close')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--producer-root', type=Path, default=Path('D:/alpha'))
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path, default=ROOT / 'research/model_legacy_multi_params/benchmark.json')
    args = parser.parse_args()
    warnings.simplefilter('ignore', FutureWarning)
    params = model_parameters()
    check_unchanged_legacy_signals(params)
    # Sources are already present in the sample. Keep real core dispatch while
    # avoiding unrelated data-source I/O in both timed paths.
    for name in params:
        FactorHub.get_by_name(name).extra_data_dict = {}
    report = dict(repeats=args.repeats, timing='whole per-factor dispatch, including copy/shift/values',
                  old_signal_ast_unchanged=len(params), parameters=params, samples=[])
    for df, metadata in samples(args.producer_root):
        before = df.copy(deep=True)
        entry = dict(rows=len(df), source=metadata, factors=[])
        for name, values in params.items():
            old = lambda: legacy_dispatch(df, name, values)
            new = lambda: calc_factor_vals(df, name, values)
            expected, actual = old(), new()
            for column in expected:
                np.testing.assert_array_equal(actual[column], expected[column])
            times = {'old': [], 'new': []}
            for repeat in range(args.repeats):
                for label, call in ([('old', old), ('new', new)] if repeat % 2 == 0
                                    else [('new', new), ('old', old)]):
                    start = time.perf_counter()
                    call()
                    times[label].append((time.perf_counter() - start) * 1000)
            old_ms, new_ms = (statistics.median(times[label]) for label in ['old', 'new'])
            entry['factors'].append(dict(name=name, old_ms=old_ms, new_ms=new_ms,
                                         speedup=old_ms / new_ms))
        pd.testing.assert_frame_equal(df, before, check_exact=True)
        entry['sum_median_old_ms'] = sum(item['old_ms'] for item in entry['factors'])
        entry['sum_median_new_ms'] = sum(item['new_ms'] for item in entry['factors'])
        report['samples'].append(entry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['repeats', 'old_signal_ast_unchanged']}, ensure_ascii=False))
    for sample in report['samples']:
        print(sample['rows'], sample['source']['kind'],
              f"old={sample['sum_median_old_ms']:.3f}ms new={sample['sum_median_new_ms']:.3f}ms")
    print(args.output)


if __name__ == '__main__':
    main()
