"""Virtual units follow available market metadata, not account tradability."""
from unittest.mock import Mock
import numpy as np
import pandas as pd
import pytest
from positions import ModelRobust40Strategy as live


@pytest.mark.parametrize('minute',range(0,60,5))
def test_gap_new_non_margin_symbol_uses_child_market_unit(minute):
    index=pd.date_range(f'2026-10-31 00:{minute:02d}',periods=8,freq='h')
    ratios=[(pd.DataFrame({'OLDUSDT':.1,'AI-USDT':[0.]*6+[1/36]*2},index=index),pd.DataFrame(index=index))]*14
    saved={'context':{'columns':['spot:OLDUSDT'],'simulation':{'spot_lot_sizes':np.array([.01]),'swap_lot_sizes':np.array([])}}}
    market={'spot':{'min_qty':{'OLDUSDT':1}},'swap':{'min_qty':{}}}
    read=Mock(return_value=pd.DataFrame({'symbol':['AIUSDT','OLDUSDT'],'lot_size':['0.10000000','0.01']}))
    result=live.virtual_lot_sizes(saved,ratios,read)
    assert result['spot']['AIUSDT']==.1
    assert result['spot']['OLDUSDT']==.01  # historical lots never get repriced
    read.assert_called_once_with('spot')
    assert 'AIUSDT' not in market['spot']['min_qty']  # does not grant trading permission


def test_simulation_uses_step_size_not_account_minimum_quantity():
    ratios=[(pd.DataFrame(),pd.DataFrame(columns=['ALLUSDT']))]*14
    read=Mock(return_value=pd.DataFrame({'symbol':['ALLUSDT'],'lot_size':[1.]}))
    sizes=live.virtual_lot_sizes(None,ratios,read)
    assert sizes['swap']['ALLUSDT']==1.
    read.assert_called_once_with('swap')  # one small read, not once per child


def test_changed_unit_rejects_old_lots_instead_of_repricing():
    saved={'context':{'columns':['swap:ALLUSDT'],'simulation':{'spot_lot_sizes':[], 'swap_lot_sizes':[10.]}}}
    ratios=[(pd.DataFrame(),pd.DataFrame(columns=['ALLUSDT']))]
    read=Mock(return_value=pd.DataFrame({'symbol':['ALLUSDT'],'lot_size':[1.]}))
    with pytest.raises(ValueError,match='unit changed.*swap:ALLUSDT'):
        live.virtual_lot_sizes(saved,ratios,read)
    assert saved['context']['simulation']['swap_lot_sizes']==[10.]


@pytest.mark.parametrize('units',[[0.],[-1.],[np.nan],[np.inf]])
def test_metadata_invalid_units_fail_before_simulation(units):
    read=Mock(return_value=pd.DataFrame({'symbol':['ALLUSDT'],'lot_size':units}))
    with pytest.raises(ValueError,match='swap:ALLUSDT'):
        live.virtual_lot_sizes(None,[(pd.DataFrame(),pd.DataFrame(columns=['ALLUSDT']))],read)


def test_metadata_canonical_duplicates_are_rejected():
    read=Mock(return_value=pd.DataFrame({'symbol':['ALLUSDT','ALL-USDT'],'lot_size':[1.,1.]}))
    with pytest.raises(ValueError,match='collide'):
        live.virtual_lot_sizes(None,[(pd.DataFrame(),pd.DataFrame(columns=['ALLUSDT']))],read)


@pytest.mark.parametrize('unit',[None,0.,-1.,np.inf])
def test_unknown_or_invalid_units_still_fail_with_symbol(unit):
    index=pd.date_range('2026-10-31 00:20',periods=2,freq='h')
    equities=[pd.DataFrame({'candle_begin_time':index})]*14
    ratios=[(pd.DataFrame({'AIUSDT':.1},index=index),pd.DataFrame(index=index))]*14
    pivots={kind:{key:pd.DataFrame(1.,index=index,columns=['AIUSDT','BTCUSDT'])
                 for key in ('open','close','vwap1m','funding_rate')} for kind in ('spot','swap')}
    simulator=dict(initial_usdt=1000.,spot_c_rate=.001,swap_c_rate=.001,spot_min_order_limit=5.,swap_min_order_limit=5.,margin_rate=.05)
    with pytest.raises(ValueError,match='spot:AIUSDT'):
        live.build_context(equities,ratios,pd.DataFrame(1.,index=index,columns=range(14)),pivots,
            {'spot':pd.Series({'AIUSDT':unit}), 'swap':pd.Series(dtype=float)},20,simulator)
