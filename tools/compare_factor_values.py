# -*- coding: utf-8 -*-
"""
因子数值对比工具
比较本地 signal_multi_params 计算结果 vs preprocess 中预计算的因子列

用法:
  单个 symbol 测试:
    python compare_factor_values.py --symbol BTCUSDT
    python compare_factor_values.py --symbol BTCUSDT --verbose
  全量对比:
    python compare_factor_values.py --all
  指定 batch 文件:
    python compare_factor_values.py --all --batch spot_dict_batch1.pkl

环境: alpha311
  alpha311\\python.exe tools\\compare_factor_values.py
"""
import sys
import os
import pickle
import argparse
import importlib.util

import numpy as np
import pandas as pd

sys.path.insert(0, r'D:\alphaonline')

# ─── 配置 ───────────────────────────────────────────────────────────────────
PKL_DIR = r'D:\alpha\data\preprocess_1h_resample\10m'  # overridden by --dir
OUTPUT_CSV = r'D:\alphaonline\data\factor_comparison_results.csv'

# 因子名 -> 要对比的参数列表（从 preprocess 实际列推断）
FACTORS = {
    'ILLQStdBbi45208':    [2856, 1344, 1512, 672, 168],
    'CciV3':              [1176, 10248, 5208],
    '动量因子210401修正':  [10248, 8568, 13608],
}
# ────────────────────────────────────────────────────────────────────────────


def load_factor_module(name: str):
    path = os.path.join(r'D:\alphaonline\factors', f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compute_local(df_full: pd.DataFrame, factor_name: str, params: list, module) -> dict:
    """在 df_full 的 OHLCV 副本上运行 signal_multi_params，返回 {str(param): Series(positional index)}"""
    df_copy = df_full.copy()
    result = module.signal_multi_params(df_copy, params)
    out = {}
    for param in params:
        key = str(param)
        s = result.get(key)
        if s is None:
            continue
        # 统一转为 positional-index Series（动量因子内部用 ignore_index 重置过）
        arr = s.values if hasattr(s, 'values') else np.array(s)
        out[key] = arr
    return out


def compare_symbol(symbol: str, df: pd.DataFrame, modules: dict, verbose: bool = False) -> list:
    rows = []
    n_rows = len(df)

    for factor_name, params in FACTORS.items():
        module = modules[factor_name]
        local_result = compute_local(df, factor_name, params, module)

        for param in params:
            col_name = f'{factor_name}_{param}'
            if col_name not in df.columns:
                continue

            preprocess_arr = df[col_name].values.astype(float)
            local_arr = local_result.get(str(param))
            if local_arr is None:
                continue

            # 长度对齐（本地结果可能比 preprocess 短，头部补 NaN）
            if len(local_arr) < len(preprocess_arr):
                pad = np.full(len(preprocess_arr) - len(local_arr), np.nan)
                local_arr = np.concatenate([pad, local_arr])
            elif len(local_arr) > len(preprocess_arr):
                local_arr = local_arr[-len(preprocess_arr):]

            valid = ~(np.isnan(preprocess_arr) | np.isnan(local_arr))
            n_valid = valid.sum()
            if n_valid < 2:
                continue

            diff = preprocess_arr[valid] - local_arr[valid]
            mae = np.abs(diff).mean()
            max_err = np.abs(diff).max()
            corr = np.corrcoef(preprocess_arr[valid], local_arr[valid])[0, 1]

            # 早期（前 1/2）vs 晚期（后 1/2）
            valid_idx = np.where(valid)[0]
            mid = len(valid_idx) // 2
            early_diff = preprocess_arr[valid_idx[:mid]] - local_arr[valid_idx[:mid]]
            late_diff  = preprocess_arr[valid_idx[mid:]] - local_arr[valid_idx[mid:]]
            early_mae = np.abs(early_diff).mean() if len(early_diff) else np.nan
            late_mae  = np.abs(late_diff).mean()  if len(late_diff)  else np.nan

            row = dict(
                symbol=symbol, factor=col_name,
                n_rows=n_rows, n_valid=n_valid,
                corr=round(corr, 8),
                mae=round(mae, 8), max_err=round(max_err, 8),
                early_mae=round(early_mae, 8), late_mae=round(late_mae, 8),
            )
            rows.append(row)

            if verbose:
                match = "OK" if late_mae < 1e-6 else "!!"
                print(f"  [{match}] {col_name:<40} corr={corr:.6f}  mae={mae:.4e}  "
                      f"early={early_mae:.4e}  late={late_mae:.4e}")
    return rows


def load_batch_files(batch_filter: str = None) -> list:
    files = [f for f in os.listdir(PKL_DIR)
             if f.startswith('spot_dict_batch') and f.endswith('.pkl')]
    if batch_filter:
        files = [f for f in files if batch_filter in f]
    return [os.path.join(PKL_DIR, f) for f in sorted(files)]


def run_single(symbol: str, verbose: bool = True):
    modules = {name: load_factor_module(name) for name in FACTORS}

    for fpath in load_batch_files():
        with open(fpath, 'rb') as f:
            data_dict = pickle.load(f)
        if symbol in data_dict:
            df = data_dict[symbol]
            print(f"\n=== {symbol}  rows={len(df)} ===")
            rows = compare_symbol(symbol, df, modules, verbose=verbose)
            if rows:
                result_df = pd.DataFrame(rows)[
                    ['factor', 'n_rows', 'n_valid', 'corr', 'mae', 'max_err', 'early_mae', 'late_mae']
                ]
                print(result_df.to_string(index=False))
            return

    print(f"Symbol {symbol} not found in any batch file.")


def run_all(batch_filter: str = None):
    modules = {name: load_factor_module(name) for name in FACTORS}
    all_rows = []

    for fpath in load_batch_files(batch_filter):
        print(f"Processing {os.path.basename(fpath)} ...")
        with open(fpath, 'rb') as f:
            data_dict = pickle.load(f)
        for symbol, df in data_dict.items():
            all_rows.extend(compare_symbol(symbol, df, modules, verbose=False))

    if not all_rows:
        print("No results.")
        return

    results_df = pd.DataFrame(all_rows)

    summary = results_df.groupby('factor').agg(
        n_symbols=('symbol', 'count'),
        mean_corr=('corr', 'mean'),
        min_corr=('corr', 'min'),
        mean_mae=('mae', 'mean'),
        max_mae=('mae', 'max'),
        mean_max_err=('max_err', 'mean'),
        avg_early_mae=('early_mae', 'mean'),
        avg_late_mae=('late_mae', 'mean'),
    ).round(8)

    print("\n=== 因子对比汇总（按因子列聚合） ===")
    print(summary.to_string())

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    results_df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"\n详细结果已保存至 {OUTPUT_CSV}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='因子数值对比工具')
    parser.add_argument('--symbol', type=str, help='单个 symbol 测试模式，例如 BTCUSDT')
    parser.add_argument('--all', action='store_true', help='全量对比所有 symbol')
    parser.add_argument('--batch', type=str, default=None, help='只处理指定 batch 文件名（部分匹配）')
    parser.add_argument('--verbose', action='store_true', help='逐行打印对比结果')
    parser.add_argument('--dir', type=str, default=None, help='preprocess 子目录，例如 30m（覆盖默认 10m）')
    args = parser.parse_args()

    if args.dir:
        PKL_DIR = os.path.join(r'D:\alpha\data\preprocess_1h_resample', args.dir)

    if args.symbol:
        run_single(args.symbol, verbose=True)
    elif args.all:
        run_all(batch_filter=args.batch)
    else:
        # 默认：找第一个 symbol 测试
        with open(load_batch_files()[0], 'rb') as f:
            d = pickle.load(f)
        first_sym = list(d.keys())[0]
        print(f"未指定参数，使用第一个 symbol: {first_sym}")
        run_single(first_sym, verbose=True)
