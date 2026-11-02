from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from positions.ModelRobust40Strategy import expected_bar, merge_context, expand_result
from positions.ModelRobust40Strategy import build_context, parent_ratios, validate_targets as transform_targets


@pytest.mark.parametrize('minute', [0, 20, 40, 55])
def test_closed_bar_uses_account_clock_and_timezone(minute):
    local = pd.Timestamp(f'2026-10-29 08:{minute:02d}')
    want = pd.Timestamp(f'2026-10-28 23:{minute:02d}')
    assert expected_bar(local, minute, 8) == want
    assert expected_bar(local.tz_localize('Asia/Hong_Kong'), minute, 8) == want
    with pytest.raises(ValueError):expected_bar(local+pd.Timedelta(minutes=1), minute, 8)


def context(start, periods, extra=False):
    index = pd.date_range(start, periods=periods, freq='h')
    cols = ['spot:S', 'swap:U']+(['swap:V'] if extra else [])
    values = np.tile([.1, -.1]+([0] if extra else []), (periods, 1))
    equities = [pd.DataFrame(dict(candle_begin_time=index, open=100., high=100., low=100., close=100., Bias_48=1.)) for _ in range(14)]
    sim = dict(init_capital=10000.,leverages=np.ones(periods),require_rebalance=np.ones(periods),capital_flow_arr=np.zeros(periods))
    for kind in ('spot', 'swap'):
        n = sum(c.startswith(kind+':') for c in cols)
        for field in ('open', 'close', 'vwap1m'):sim[f'{kind}_{field}_p'] = np.full((periods, n), 100.)
        sim[kind+'_lot_sizes'] = np.ones(n)
    sim['funding_rates'] = np.zeros((periods, 2 if extra else 1))
    return dict(index=index,minute=index.minute[0],columns=cols,leverage=pd.DataFrame(1.,index=index,columns=range(14)),
        matrices=[sparse.csr_matrix(values) for _ in range(14)],equities=equities,simulation=sim,close=pd.DataFrame(100.,index=index,columns=cols))


def test_merge_preserves_history_expands_symbols_and_rejects_revision():
    old = context('2026-10-01 00:40', 5)
    new = context('2026-10-01 02:40', 5, extra=True)
    for d in new['equities']:d.loc[:, ['open','high','low','close']] *= 3
    merged = merge_context(old, new, minimum_overlap=3, warmup=0)
    assert len(merged['index']) == 7
    assert merged['columns'] == ['spot:S', 'swap:U', 'swap:V']
    assert merged['equities'][0].close.eq(100).all()
    np.testing.assert_array_equal(merged['matrices'][0][:5, :2].toarray(), old['matrices'][0].toarray())
    new['leverage'].iloc[1, 4] = .5
    with pytest.raises(ValueError, match='retiming'):merge_context(old, new, minimum_overlap=3, warmup=0)
    assert old['leverage'][4].eq(1).all()


def test_price_revision_names_time_symbol_values_and_preserves_saved_state(tmp_path):
    from positions.ModelRobust40Strategy import update_state
    old = context('2026-10-01 00:40', 1300)
    new = context('2026-10-01 00:40', 1301)
    new['simulation']['swap_close_p'][-2, 0] = 101.
    path = tmp_path/'state.pkl'
    pd.to_pickle(dict(version=1, minute=40, account_identity='account', context=old,
                      result={'parent': pd.DataFrame(index=old['index'])}), path)
    original = path.read_bytes()
    with pytest.raises(ValueError) as exc:
        update_state(path, tmp_path/'bootstrap.pkl', 'account', 40, new, {}, new['index'][-1])
    message = str(exc.value)
    for part in ('swap_close_p', str(old['index'][-1]), 'swap:U', 'saved=100.0', 'incoming=101.0'):
        assert part in message
    assert path.read_bytes() == original


def test_revision_diagnostics_include_missing_value_and_series_instrument():
    from positions.ModelRobust40Strategy import _same
    old = pd.Series([1., np.nan], index=['spot:A', 'spot:B'])
    new = pd.Series([1., 2.], index=old.index)
    with pytest.raises(ValueError, match='spot:B.*saved=nan.*incoming=2.0'):
        _same(old, new, 'spot instrument units')


def test_nontraded_benchmark_survives_context_and_continuation():
    c = context('2026-10-01 00:20', 5)
    idx = c['index']
    ratios = [(pd.DataFrame({'S': .1}, index=idx), pd.DataFrame({'U': -.1}, index=idx))]*14
    pivots = {k: {field: pd.DataFrame(100., index=idx, columns=cols)
                  for field in ('open','close','vwap1m','funding_rate')}
              for k, cols in [('spot',['S']), ('swap',['U','BTCUSDT'])]}
    simulator = dict(initial_usdt=10000, spot_c_rate=.001, swap_c_rate=.0006,
                     spot_min_order_limit=10, swap_min_order_limit=5, margin_rate=.05)
    built = build_context(c['equities'], ratios, c['leverage'], pivots,
                          {'spot':pd.Series({'S':1.}), 'swap':pd.Series({'U':1.})}, 20, simulator)
    assert 'swap:BTCUSDT' not in built['columns']
    assert built['close']['swap:BTCUSDT'].eq(100).all()
    result = merge_context(built, built, minimum_overlap=3, warmup=0)
    pd.testing.assert_frame_equal(result['close'], built['close'])
    changed = dict(built, close=built['close'].copy())
    changed['close'].iloc[-1, changed['close'].columns.get_loc('swap:BTCUSDT')] = 101
    with pytest.raises(ValueError, match='reference prices'):
        merge_context(built, changed, minimum_overlap=3, warmup=0)


def test_merge_rejects_unit_changes_before_using_saved_lots():
    old=context('2026-10-01 00:40',5)
    new=context('2026-10-01 02:40',5)
    new['simulation']['swap_lot_sizes'][0]=10.
    with pytest.raises(ValueError,match='instrument units'):
        merge_context(old,new,minimum_overlap=3,warmup=0)
    assert old['simulation']['swap_lot_sizes'][0]==1.


def test_saved_lots_map_by_symbol_including_pending_orders():
    frame = pd.DataFrame({'equity': [100.]})
    frame.attrs['simulation_state'] = ((100., np.array([1]), np.array([2]), np.array([10.]), True),
                                     (0., np.array([-3]), np.array([-4]), np.array([20.]), True))
    result = dict(virtual_accounts=[frame]*9, account=frame,
        targets={'spot': pd.DataFrame({'S': [.1]}), 'swap': pd.DataFrame({'Z': [-.1]})},delta=pd.DataFrame({'Z': [0.]}))
    out = expand_result(result, ['spot:S','swap:Z'], ['spot:S','swap:A','swap:Z'])
    state = out['account'].attrs['simulation_state'][1]
    np.testing.assert_array_equal(state[1], [0,-3]);np.testing.assert_array_equal(state[2], [0,-4])
    np.testing.assert_array_equal(frame.attrs['simulation_state'][1][1], [-3])


def test_execution_registration_preserves_built_in_modes():
    from core import rebalance
    from core.model.rebalance_mode import RebalanceMode
    from positions.ModelRobust40Strategy import register_execution
    old = {k: v for k, v in vars(rebalance).items() if k.startswith('Reb') and k != 'RebRobust40'}
    register_execution();register_execution()
    assert all(getattr(rebalance,k) is v for k,v in old.items())
    reb = RebalanceMode.init(dict(mode='RebRobust40',params=dict(min_order_usdt_ratio=.4,execution_cap=2.45)))
    obj = reb.create(np.ones(1), np.ones(1))
    assert obj.use_full_equity and obj.calculator.min_order_usdt_ratio == .4 and obj.calculator.execution_cap == 2.45


def test_target_adapter_aligns_timezone_and_columns_and_detects_double_timing():
    from positions.ModelRobust40Strategy import apply_s5_delta
    idx = pd.date_range('2026-10-01 00:55', periods=2, freq='h')
    net = pd.DataFrame([[.2,-.1]]*2,index=idx,columns=['B','A'])
    delta = pd.DataFrame([[-.1,0]]*2,index=idx,columns=net.columns)
    spot = pd.DataFrame(.1,index=idx,columns=['S'])
    result = dict(parent=pd.DataFrame(.2,index=idx,columns=range(14)),delta=delta,
                  targets=dict(spot=spot,swap=apply_s5_delta(net,delta)))
    strategy = SimpleNamespace(_robust40_result=result)
    aware = idx.tz_localize('UTC')
    parent = parent_ratios([pd.DataFrame({'candle_begin_time':aware})],strategy)
    assert parent.index.equals(aware)
    a,b = transform_targets(spot.set_axis(aware),net.set_axis(aware),strategy)
    np.testing.assert_allclose(b.to_numpy(), [[.1,-.1]]*2)
    with pytest.raises(ValueError,match='differs'):
        transform_targets(spot.set_axis(aware),net.set_axis(aware)*.5,strategy)


def test_failed_state_update_preserves_saved_bytes_and_releases_lock(tmp_path):
    from positions.ModelRobust40Strategy import update_state
    path=tmp_path/'account.pkl';bootstrap=tmp_path/'bootstrap.pkl'
    incoming=context('2026-10-01 00:20',5)
    bundle=dict(version=1,minute=20,account_identity='account-A')
    pd.to_pickle(bundle,path);before=path.read_bytes()
    with pytest.raises(ValueError,match='another account'):
        update_state(path,bootstrap,'account-B',20,incoming,{},incoming['index'][-1])
    assert path.read_bytes()==before
    assert not Path(str(path)+'.lock').exists()
    with pytest.raises(ValueError,match='expected closed bar'):
        update_state(path,bootstrap,'account-A',20,incoming,{},incoming['index'][-1]+pd.Timedelta(hours=1))
    assert path.read_bytes()==before


def test_missing_active_live_price_fails_before_generating_lots(tmp_path,monkeypatch):
    import core.position as position
    from core.model.rebalance_mode import RebalanceMode
    from positions.ModelRobust40Strategy import register_execution
    register_execution()
    index=pd.date_range('2026-10-01 00:20',periods=1)
    pd.DataFrame(index=index).to_pickle(tmp_path/'df_spot_ratio.pkl')
    pd.DataFrame({'U':[.2]},index=index).to_pickle(tmp_path/'df_swap_ratio.pkl')
    bn=SimpleNamespace(market_info={'spot':dict(symbol_list=[],min_qty={}),
        'swap':dict(symbol_list=['U'],min_qty={'U':1})},
        get_spot_ticker_price_series=lambda:pd.Series(dtype=float),
        get_swap_ticker_price_series=lambda:pd.Series({'U':np.nan}))
    acct=SimpleNamespace(bn=bn,spot_position=pd.DataFrame(),swap_position=pd.DataFrame(),
        spot_equity=0,swap_equity=1000,leverage=1,buy_bnb_value=0,
        rebalance_mode=RebalanceMode.init(dict(mode='RebRobust40',params={})))
    monkeypatch.setattr(position,'runtime_folder',tmp_path)
    with pytest.raises(ValueError,match='Missing active instrument price'):
        position.calc_target_position(acct,is_debug=True)


def test_fixed_history_opt_in_preserves_origin_and_rejects_short_input(monkeypatch):
    import core.utils.datatools as dt
    conf=SimpleNamespace(black_list=[],white_list=[],min_kline_num=1,precompute_factor_configs={},
        required_history_start='2026-01-01',get_kline_num=lambda:2)
    idx=pd.date_range('2026-01-01',periods=4,freq='h',tz='UTC')
    frame=pd.DataFrame(dict(symbol='ZZZUSDT',symbol_spot='',symbol_swap='ZZZUSDT',
        candle_begin_time=idx,first_candle_time=pd.Timestamp('2025-12-01',tz='UTC'),last_candle_time=idx))
    monkeypatch.setattr(dt,'utc_offset',0)
    def run(frame):
        return dt.read_and_merge_bmac_data(conf,'swap',frame,pd.Timestamp('2026-01-02'),align_fn=lambda df,*args:df)[1]
    assert len(run(frame.copy()))==4
    with pytest.raises(ValueError,match='retained history'):run(frame.iloc[1:].copy())
    conf.required_history_start=None
    assert len(run(frame.copy()))==2
