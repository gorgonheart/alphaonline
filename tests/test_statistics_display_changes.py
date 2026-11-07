import pandas as pd
from core.utils.statis_func import price_impact_top, filter_small_price_impact


def test_rank_by_direction_and_absolute_impact_across_markets():
    rows=[dict(symbol=f'L{i}',side=1,total_pnl=(-1 if i%2 else 1)*i,type='spot') for i in range(1,8)]
    rows += [dict(symbol=f'S{i}',side=-1,total_pnl=(-1 if i%2 else 1)*i,type='swap') for i in range(1,8)]
    result=price_impact_top(rows)
    assert result.loc[result.side>0].symbol.tolist()==['L7','L6','L5','L4','L3']
    assert result.loc[result.side<0].symbol.tolist()==['S7','S6','S5','S4','S3']


def test_min_quantity_uses_absolute_opening_position_and_keeps_boundary():
    baseline=pd.DataFrame(dict(symbol=['dust','short','equal'],pos_amount=[.09,-.2,.1]))
    frame=pd.DataFrame(dict(symbol=['dust','short','equal'],total_pnl=[999.,-30.,20.]))
    result=filter_small_price_impact(frame,baseline,{'min_qty':dict(dust=1,short=1,equal=1)})
    assert result.symbol.tolist()==['short','equal']
    assert filter_small_price_impact(frame,baseline,{}).symbol.tolist()==['dust','short','equal']
