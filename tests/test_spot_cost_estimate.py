import json
from pathlib import Path
import pandas as pd
import pytest


def write_orders(root, rows):
    p=root/'订单记录'; p.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(p/'order_history_202611.csv',index=False,encoding='gbk')


def order(id, side, qty, quote, time='2026-11-06 12:00:00'):
    return dict(client_order_id=id,symbol_type='spot',symbol='ABCUSDT',side=side,
                executed_qty=qty,quote_qty=quote,exec_time=time,order_status='FILLED')


def estimate(root, qty, price=110):
    from tools.spot_cost_estimate import estimate_spot_costs
    return estimate_spot_costs(root, {'ABCUSDT':qty},{'ABCUSDT':price},'2026-11-06 13:00:00')['ABCUSDT']


def test_seed_ignores_old_records_and_survives_restart(tmp_path):
    write_orders(tmp_path,[order('old','BUY',10,500)])
    assert estimate(tmp_path,10,100)['avg_price']==100
    assert estimate(tmp_path,10,120)['avg_price']==100


def test_partial_sell_keeps_average_and_buy_weights_remaining_cost(tmp_path):
    estimate(tmp_path,10,100)
    rows=[order('s','SELL',4,480)]
    write_orders(tmp_path,rows)
    assert estimate(tmp_path,6)['avg_price']==100
    rows.append(order('b','BUY',4,560,'2026-11-06 12:01:00'))
    write_orders(tmp_path,rows)
    assert estimate(tmp_path,10)['avg_price']==116


def test_duplicate_and_cumulative_partial_fill_only_add_increment(tmp_path):
    estimate(tmp_path,10,100)
    rows=[order('b','BUY',2,240)]
    write_orders(tmp_path,rows)
    assert estimate(tmp_path,12)['avg_price']==pytest.approx(1240/12)
    assert estimate(tmp_path,12)['avg_price']==pytest.approx(1240/12)
    rows.extend([order('b','BUY',2,240),order('b','BUY',4,520)])
    write_orders(tmp_path,rows)
    assert estimate(tmp_path,14)['avg_price']==pytest.approx(1520/14)


def test_close_reopen_and_unexplained_quantity_reset(tmp_path):
    estimate(tmp_path,10,100)
    rows=[order('s','SELL',10,1200)]
    write_orders(tmp_path,rows); estimate(tmp_path,0)
    rows.append(order('b','BUY',2,180,'2026-11-06 12:02:00'))
    write_orders(tmp_path,rows)
    assert estimate(tmp_path,2)['avg_price']==90
    result=estimate(tmp_path,3,130)
    assert result['avg_price']==130
    assert result['cost_status']=='估算重置'


def test_bad_price_and_short_do_not_create_fake_cost(tmp_path):
    assert estimate(tmp_path,3,0)['avg_price'] is None
    assert estimate(tmp_path,-3)['avg_price'] is None


def test_missing_order_identity_resets_on_quantity_change(tmp_path):
    estimate(tmp_path,10,100)
    write_orders(tmp_path,[order('','BUY',2,240)])
    assert estimate(tmp_path,12,120)['cost_status']=='估算重置'


def test_corrupt_state_is_not_silently_reseeded(tmp_path):
    estimate(tmp_path,1,100)
    state=tmp_path/'账户信息/spot_cost_estimate.json'
    state.write_text('broken')
    with pytest.raises(ValueError): estimate(tmp_path,1)


def test_report_preserves_market_value_and_computes_estimated_pnl(tmp_path,monkeypatch):
    from core.utils import statis_func as report
    monkeypatch.setattr(report,'data_path',tmp_path)
    position=pd.DataFrame({'symbol':['ABCUSDT'],'当前持仓量':[10.]})
    first=report.calc_spot_position(position,'acct',{'ABCUSDT':100})
    assert first.loc['ABCUSDT','avg_price']==100
    second=report.calc_spot_position(position,'acct',{'ABCUSDT':110})
    assert second.loc['ABCUSDT','pos_u']==1100
    assert second.loc['ABCUSDT','pnl_u']==100
    assert second.loc['ABCUSDT','change']=='10.0%'
    assert position.columns.tolist()==['symbol','当前持仓量']


def test_report_cost_failure_does_not_discard_market_value(tmp_path,monkeypatch):
    from core.utils import statis_func as report
    from tools import spot_cost_estimate as cost
    monkeypatch.setattr(report,'data_path',tmp_path)
    def fail(*args): raise ValueError('corrupt')
    monkeypatch.setattr(cost,'estimate_spot_costs',fail)
    result=report.calc_spot_position(pd.DataFrame({'symbol':['ABCUSDT'],'当前持仓量':[2.]}),'acct',{'ABCUSDT':100})
    assert result.loc['ABCUSDT','pos_u']==200
    assert result.loc['ABCUSDT','cost_status']=='估算不可用'


def test_missing_receipt_time_falls_back_per_row_to_run_time(tmp_path):
    estimate(tmp_path,10,100)
    buy=order('b','BUY',10,2000,time='')
    buy['run_time']='2026-11-06 11:00:00'
    sell=order('s','SELL',10,2500,time='2026-11-06 12:00:00')
    sell['run_time']='2026-11-06 12:00:00'
    write_orders(tmp_path,[buy,sell])
    assert estimate(tmp_path,10)['avg_price']==150
