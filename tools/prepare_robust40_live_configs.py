"""Generate complete inactive account examples from the frozen research definitions."""
import argparse
import ast
import pprint
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.robust40.offline import assignment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--references', default='E:/quant')
    p.add_argument('--parent-config', required=True)
    p.add_argument('--output', default='examples/model_robust40')
    args = p.parse_args()
    parent = assignment(args.parent_config, 'strategy_config')['params']
    for minute in (20, 40, 55):
        source = Path(args.references)/f'position-mgmt_v2.3.0_{minute}m'/f'config_MODEL_ROBUST40_{minute}m.py'
        content = source.read_text(encoding='utf-8-sig');tree = ast.parse(content)
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'strategy_pool')
        pool = ast.get_source_segment(content, node)
        strategy = dict(name='ModelRobust40Strategy', hold_period='1H', params=dict(
            factor_list=parent['factor_list'], parent=parent, runtime=dict(minute=minute,
                bootstrap_path=f'research/model_robust40_v1/{minute}m/runtime_bootstrap.pkl',
                state_path=f'data/robust40_state/ACCOUNT_NAME_{minute}m.pkl')))
        account = dict(apiKey='', secret='', hour_offset=f'{minute}m', target_equity_mode='research',
            rebalance_mode=dict(mode='RebRobust40', params=dict(min_order_usdt_ratio=.4, execution_cap=2.45)),
            account_type='统一账户', buy_bnb_value=0, if_use_bnb_burn=False, if_transfer_bnb=False,
            order_spot_money_limit=10, order_swap_money_limit=5, filter_bstocks=True)
        text = ('"""Inactive MODEL_ROBUST40 v1 example. No credentials; not auto-discovered.\n'
                'Use one unique persistent state path per account; validate live overlap before activation.\n'
                'The portfolio rebalance mode is nested in account_config so children keep RebAlways.\n"""\n\n')
        for name, value in [('account_config', account), ('strategy_name', f'MODEL_ROBUST40_V1_{minute}m'),
                            ('strategy_config', strategy), ('simulator_config', assignment(source, 'simulator_config'))]:
            text += name+' = '+pprint.pformat(value, width=110, sort_dicts=False)+'\n\n'
        text += f"required_history_start = '2025-10-28 00:{minute:02d}:00'\n"
        text += "precompute_factor_configs = {'动量210401_双项异常隔离V2': [13608, 5421, 3509, 12600]}\n"
        text += 'get_kline_num = 5000\nmin_kline_num = 1\nleverage = 1\nblack_list = []\nwhite_list = []\n\n'+pool+'\n'
        target = Path(args.output)/f'MODEL_ROBUST40_V1_{minute}m.py'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():raise FileExistsError(target)
        target.write_text(text, encoding='utf-8')
        print(target)


if __name__ == '__main__':main()
