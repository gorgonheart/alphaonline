"""Materialize a full standalone config and copied child cache for a formal backtest."""
import argparse
import ast
import json
import shutil
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.robust40.offline import canonical


def replace_assignment(text, name, expression):
    t = ast.parse(text)
    n = next(n for n in t.body if isinstance(n, ast.Assign) and
             isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:n.lineno-1])+name+' = '+expression+'\n'+''.join(lines[n.end_lineno:])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True);p.add_argument('--reference', required=True)
    p.add_argument('--minute', required=True, type=int)
    args = p.parse_args();project = Path(args.project);reference = Path(args.reference)
    offset = f'{args.minute}m';name = f'OV1028B_{offset}_ORIGINAL'
    source = project/'results/children'/name
    dest = reference/'formal_children'/name
    if dest.exists():raise FileExistsError(dest)
    shutil.copytree(source, dest, copy_function=shutil.copy2)
    # Existing CacheGuard requires absolute output paths. Rebase only those paths
    # in the copied cache record; preserve fingerprints, source/data metadata,
    # row ranges, bytes and copy2 timestamps. No new hash protocol is introduced.
    path = dest/'.substrategy_cache_manifest.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    for item in record.get('outputs', []):
        relative = Path(item['path']).relative_to(source)
        item['path'] = str(dest/relative)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    native = {}
    for folder in source.glob('S*'):
        if folder.is_dir():
            for symbol in pd.read_pickle(folder/'df_swap_ratio.pkl').columns:
                key = canonical(symbol)
                if key in native and native[key] != symbol:raise ValueError('Symbol alias collision')
                native[key] = symbol
    columns = sorted(native.values())
    d = pd.read_pickle(reference/'delta.pkl').reindex(columns=[canonical(c) for c in columns])
    if d.isna().any().any():raise ValueError('Replay delta columns missing')
    d.columns = columns;d.to_pickle(reference/'delta_replay.pkl')
    text = (project/f'config_MODEL_ROBUST40_{offset}.py').read_text(encoding='utf-8-sig')
    text = replace_assignment(text, 'backtest_name', repr(f'ROBUST40_V1_{offset}'))
    text = replace_assignment(text, 'backtest_path', f'Path({(reference/"formal_portfolio").as_posix()!r})')
    text = replace_assignment(text, 'backtest_iter_path', f'Path({(reference/"formal_children").as_posix()!r})')
    text = text.replace((project/'planned'/f'MODEL_ROBUST40_{offset}.pkl').as_posix(), (reference/'parent.pkl').as_posix())
    text = text.replace((project/'deltas'/f'MODEL_ROBUST40_{offset}.pkl').as_posix(), (reference/'delta_replay.pkl').as_posix())
    old_doc = ast.parse(text).body[0]
    text = text.replace(ast.get_source_segment(text, old_doc),
        f'"""MODEL_ROBUST40 v1 {offset}: independent offset parent and quarterly model.\nFull standalone config; frozen 14-child definitions; explicit new replay artifacts.\nGenerated for formal backtest; source references and historical results remain untouched.\n"""', 1)
    compile(text, '<standalone config>', 'exec')
    target = reference/f'config_MODEL_ROBUST40_V1_{offset}.py'
    target.write_text(text, encoding='utf-8')
    print(target)


if __name__ == '__main__':main()
