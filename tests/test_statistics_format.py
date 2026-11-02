"""Display formatting must not reduce stored statistical precision."""
from datetime import datetime
from unittest.mock import Mock
from types import SimpleNamespace

import pandas as pd
import pytest

from tools.statistics_format import format_number, format_percent, format_time, format_weights, format_amount, format_price


@pytest.mark.parametrize('value,expected', [
    (1515.307196646,'1520'),(12.3456,'12.3'),(.0123456,'0.0123'),
    (1.,'1.00'),(.1,'0.100'),(-.000123456,'-0.000123'),
    (999.9,'1000'),(0.,'0'),(float('nan'),'未知'),(float('inf'),'未知'),
])
def test_three_significant_digits(value,expected):
    assert format_number(value)==expected


@pytest.mark.parametrize('value,amount,price',[(1515.307,'1515','1515.31'),(.0123456,'0','0.01235'),(-.4,'0','-0.4000'),(12.345,'12','12.35'),(float('nan'),'未知','未知')])
def test_integer_amount_and_adaptive_price(value,amount,price):
    assert format_amount(value)==amount
    assert format_price(value)==price


def test_time_percent_and_weight_lines():
    from core.utils.statis_func import format_volume_message
    assert format_time(datetime(2026,11,1,9,8,7,123456))=='11-01 09:08:07'
    assert format_percent(.457983193277)=='45.8%'
    assert format_percent(.000123456)=='0.0123%'
    assert format_weights(pd.Series([.2,.8,0.],index=['1','0','2']),
                          {'0':'多头策略','1':'空头策略'})=='空头策略: 20.0%\n多头策略: 80.0%\n2: 0%'
    assert format_volume_message(12.,.3456)=='最近1h成交额:12 (spot:12, swap:0)'


def test_dataframe_only_changes_export_copy(tmp_path,monkeypatch):
    import core.utils.statis_func as sf
    source=pd.DataFrame({'pos_u':[1515.307196646], 'cur_price':[.0123456789],
                         'pos_amount':[.6294843], 'candle_begin_time':[datetime(2026,11,1,9,8,7)]})
    before=source.copy(deep=True)
    export=Mock()
    monkeypatch.setattr(sf,'data_path',tmp_path)
    monkeypatch.setattr(sf.dfi,'export',export)
    monkeypatch.setattr(sf,'send_wechat_work_img',Mock())
    sf.send_img_for_dataframe(source,'fake')
    display=export.call_args.args[0]
    assert display.pos_u.iloc[0]=='1515'
    assert display.cur_price.iloc[0]=='0.01235'
    assert display.candle_begin_time.iloc[0]=='11-01 09:08:07'
    pd.testing.assert_frame_equal(source,before)


def test_equity_message_formats_only_display(tmp_path,monkeypatch):
    import core.utils.statistics as st
    path=tmp_path/'equity.csv'
    monkeypatch.setattr(st,'get_file_path',lambda *args,**kwargs:path)
    bn=Mock()
    bn.get_unimmr.return_value=8.251234
    account=SimpleNamespace(name='format',bn=bn)
    observed=datetime(2026,11,1,9,8,7,123456)
    msg=st.save_and_send_equity_info(account,None,pd.DataFrame(),pd.DataFrame(),0.,1515.307196646,
                                   .0123456,0.,run_time=observed,observed_at=observed)
    assert '观察时间：11-01 09:08:07\n' in msg
    assert '账户净值：1515\n' in msg
    assert '现货USDT余额：0\n' in msg
    assert '账户UniMMR：8.25\n' in msg
    stored=pd.read_csv(path,encoding='gbk')
    assert stored['账户总净值'].iloc[0]==1515.307196646
    assert stored.observed_at.iloc[0]=='2026-11-01 09:08:07.123456'


def test_price_impact_report_drops_fixed_warnings_keeps_data_status(tmp_path,monkeypatch):
    import core.utils.statis_func as sf
    def path(*parts,**kwargs):
        target=tmp_path/str(parts[-1])
        return target
    monkeypatch.setattr(sf,'get_file_path',path)
    sent=Mock()
    monkeypatch.setattr(sf,'send_wechat_work_msg',sent)
    account=SimpleNamespace(name='format',bn=Mock(),wechat_webhook_url='fake')
    sf.calculate_and_send_pnl_report(account,pd.DataFrame(),pd.DataFrame(),pd.Series(dtype=float),
                                    observed_at=datetime(2026,11,1,9,8,7),
                                    current_frames=(pd.DataFrame(),pd.DataFrame()))
    message=sent.call_args.args[0]
    assert '价格影响估算' in message
    assert '历史不足' in message
    assert all(text not in message for text in ('假设','不代表','不含费用','参考值'))


@pytest.mark.parametrize('value,expected',[
    (2407.22,'2407.22'),(123456.789,'123456.79'),(.123456,'0.1235'),
    (.00123456,'0.001235'),(.0000123456,'0.00001235'),
    (.00000000123456,'0.000000001235'),(1.23456e-15,'1.235e-15'),
    (1.,'1.00'),(0.,'0'),(float('inf'),'未知'),
])
def test_adaptive_price_examples_without_grouping(value,expected):
    assert format_price(value)==expected
    assert ',' not in format_price(value)


def test_price_pairs_share_precision_and_reveal_close_values_without_mutation():
    from tools.statistics_format import format_frame
    source=pd.DataFrame({'avg_price':[2407.221,.00123456,float('nan'),1.234567e-15],
                         'cur_price':[2407.224,.00124,.0123456,1.234891e-15],
                         'historical_price':[2407.221,.00123456,float('nan'),1.234567e-15],
                         'current_price':[2407.224,.00124,.0123456,1.234891e-15]})
    before=source.copy(deep=True)
    result=format_frame(source)
    assert result.avg_price.tolist()==['2407.221','0.001235','未知','1.2346e-15']
    assert result.cur_price.tolist()==['2407.224','0.001240','0.01235','1.2349e-15']
    assert result.historical_price.tolist()==result.avg_price.tolist()
    assert result.current_price.tolist()==result.cur_price.tolist()
    pd.testing.assert_frame_equal(source,before)
    empty=format_frame(source.iloc[:0])
    assert empty.empty and empty.columns.tolist()==source.columns.tolist()
