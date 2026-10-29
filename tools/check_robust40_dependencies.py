"""Read-only source comparison for the frozen 14 children; no configs imported."""
import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.robust40.offline import assignment


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);args=p.parse_args()
    project=Path(args.project);local=Path(__file__).resolve().parents[1]
    pool=assignment(project/'config_MODEL_ROBUST40_20m.py','strategy_pool')
    names=set()
    for parent in pool:
        if parent.get('re_timing'):names.add(('signals',parent['re_timing']['name']))
        for child in parent['strategy_list']:
            for key,value in child.items():
                if 'factor_list' in key or 'filter_list' in key:
                    names.update(('factors',v[0]) for v in value)
    for kind,name in sorted(names):
        a=local/kind/(name+'.py');b=project/kind/(name+'.py')
        if not b.exists():b=project/'sections'/(name+'.py')
        if not a.exists():a=local/'sections'/(name+'.py')
        if not a.exists() or not b.exists():
            print('MISSING',kind,name,a.exists(),b.exists());continue
        normalize=lambda path:ast.dump(ast.parse(path.read_text(encoding='utf-8-sig')),include_attributes=False)
        if normalize(a)!=normalize(b):print('DIFF',kind,name)
    print('checked',len(names),'modules')


if __name__ == '__main__':main()
