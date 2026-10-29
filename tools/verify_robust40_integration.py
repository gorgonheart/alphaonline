"""Offline real-data acceptance through the actual live aggregation and lot mapper."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def verify(root):
    from core.model.backtest_config import MultiEquityBacktestConfig
    from core.model.strategy_config import PosStrategyConfig
    from core.model.rebalance_mode import RebalanceMode
    import core.position as position
    from tools.robust40.live import parent_ratios, transform_targets, build_context
    from tools.robust40.engine import slice_context
    from tools.robust40.runtime import merge_context
    from positions.ModelRobust40Strategy import calc_ratio
    bundle = pd.read_pickle(root/'runtime_bootstrap.pkl')
    c = bundle['context'];r = bundle['result'];minute = c['minute']
    formal = pd.read_csv(root/f'formal_portfolio/ROBUST40_V1_{minute}m/资金曲线.csv', parse_dates=['candle_begin_time']).set_index('candle_begin_time')
    fields = ['equity','turnover','fee','funding_fee','long_pos_value','short_pos_value']
    actual = r['account'].reindex(formal.index)[fields]
    np.testing.assert_allclose(formal[fields], actual, rtol=1e-12, atol=1e-8)
    report = dict(minute=minute, formal_bars=len(formal), formal_max_error=float(abs(formal[fields]-actual).max().max()),
                  final_equity=float(formal.equity.iloc[-1]), max_drawdown=float((formal.equity/formal.equity.cummax()-1).min()))
    incoming = slice_context(c, c['index'][-2000:])
    index = incoming['index'].tz_localize('UTC')
    ratios = []
    symbols = {k: [v.split(':',1)[1] for v in c['columns'] if v.startswith(k+':')] for k in ('spot','swap')}
    for matrix in incoming['matrices']:
        pair = []
        for kind in ('spot','swap'):
            loc = [i for i, v in enumerate(c['columns']) if v.startswith(kind+':')]
            pair.append(pd.DataFrame(matrix[:,loc].toarray(),index=index,columns=symbols[kind]))
        ratios.append(tuple(pair))
    equities = [d.assign(candle_begin_time=index) for d in incoming['equities']]
    pivots = {};sizes = {}
    for kind in ('spot','swap'):
        pivots[kind] = {key: pd.DataFrame(incoming['simulation'][kind+'_'+key+'_p'],index=index,columns=symbols[kind]) for key in ('open','close','vwap1m')}
        if kind == 'swap':pivots[kind]['funding_rate'] = pd.DataFrame(incoming['simulation']['funding_rates'],index=index,columns=symbols[kind])
        sizes[kind] = pd.Series(incoming['simulation'][kind+'_lot_sizes'],index=symbols[kind])
    bridged = build_context(equities,ratios,incoming['leverage'].set_axis(index),pivots,sizes,minute,bundle['simulator_config'])
    merged = merge_context(c, bridged)
    for a,b in zip(merged['matrices'],c['matrices']):assert (a != b).nnz == 0
    with tempfile.TemporaryDirectory(prefix='acceptance-',dir=root) as folder:
        folder = Path(folder)
        stg = PosStrategyConfig('ModelRobust40Strategy','1H')
        stg.core_funcs = {'calc_ratio':calc_ratio};stg._robust40_result = r
        me = MultiEquityBacktestConfig.__new__(MultiEquityBacktestConfig)
        me.strategy = stg;me.equity_dfs = equities;me.ratio_dfs = ratios
        me.start_time = index[0];me.end_time = index[-1]+pd.Timedelta(hours=1)
        configs = []
        for i in range(14):
            path = folder/f'S{i+1}';path.mkdir()
            lev = incoming['leverage'][i].set_axis(index).rename('动态杠杆');lev.index.name = 'candle_begin_time'
            lev.to_csv(path/'再择时动态杠杆.csv')
            configs.append(SimpleNamespace(name=f'S{i+1}',get_result_folder=lambda path=path:path))
        me.factory = SimpleNamespace(result_folder=folder,config_list=configs)
        parent = me.calc_ratios()
        np.testing.assert_allclose(parent,r['parent'].loc[incoming['index']].to_numpy()*incoming['leverage'].to_numpy(),rtol=1e-12,atol=1e-12)
        spot,swap = me.agg_pos_ratio(parent)
        spot,swap = transform_targets(spot,swap,stg)
        errors = []
        for kind,frame in [('spot',spot),('swap',swap)]:
            target = r['targets'][kind].loc[incoming['index'],frame.columns].to_numpy()
            np.testing.assert_allclose(frame,target,rtol=1e-12,atol=1e-12)
            errors.append(float(np.max(abs(frame.to_numpy()-target))))
            frame.to_pickle(folder/f'df_{kind}_ratio.pkl')
        state = r['account'].attrs['simulation_state']
        bn = SimpleNamespace(market_info={})
        current = {}
        for kind,s in zip(('spot','swap'),state):
            valid = np.isfinite(c['simulation'][kind+'_close_p'][-1]) & (c['simulation'][kind+'_close_p'][-1] > 0)
            bn.market_info[kind] = dict(symbol_list=list(np.array(symbols[kind])[valid]),min_qty=dict(zip(symbols[kind],-np.log10(sizes[kind]))))
            current[kind] = pd.DataFrame({'当前持仓量': s[1]*sizes[kind]},index=pd.Index(symbols[kind],name='symbol'))
            price = pd.Series(c['simulation'][kind+'_close_p'][-1],index=pd.Index(symbols[kind],name='symbol'),name='price')
            setattr(bn,'get_'+kind+'_ticker_price_series',lambda price=price:price)
        acct = SimpleNamespace(spot_position=current['spot'],swap_position=current['swap'],bn=bn,
            spot_equity=state[0][0],swap_equity=state[1][0],leverage=1.,buy_bnb_value=0.,
            rebalance_mode=RebalanceMode.init(dict(mode='RebRobust40',params=dict(min_order_usdt_ratio=.4,execution_cap=2.45))))
        old = position.runtime_folder
        try:
            position.runtime_folder = folder
            lots = position.calc_target_position(acct,is_debug=True)
        finally:position.runtime_folder = old
        for kind,s in zip(('spot','swap'),state):
            calculated = lots[lots.symbol_type.eq(kind)].set_index('symbol').target_lots.reindex(symbols[kind],fill_value=0)
            np.testing.assert_array_equal(calculated.to_numpy(),s[2])
        report.update(bridge_bars=len(index),target_max_error=max(errors),target_lot_mismatches=0)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='research/model_robust40_v1')
    args=p.parse_args();root=Path(args.root)
    report=[verify(root/f'{m}m') for m in (20,40,55)]
    text=json.dumps(report,ensure_ascii=False,indent=2)
    (root/'integration_report.json').write_text(text,encoding='utf-8');print(text)


if __name__ == '__main__':main()
