"""验证 Arrow 缺失时 load_bmac_data 仍走 pkl（fallback 不回归）。
真实 30m 对账见 test_arrow_pkl_parity.py。"""
from datetime import datetime
from unittest import mock

import pandas as pd

import core.utils.datatools as dt


def _fake_conf():
    conf = mock.Mock()
    conf.black_list = []
    conf.white_list = []
    conf.precompute_factor_configs = {}
    conf.min_kline_num = 1
    conf.get_kline_num.return_value = 4050
    return conf


def test_falls_back_to_pkl_when_no_arrow(tmp_path, monkeypatch):
    # bmac_data_path 指向空目录 → 无 arrow、无 pkl
    monkeypatch.setattr(dt, 'bmac_data_path', tmp_path)
    rt = datetime(2026, 6, 27, 20, 30)
    (tmp_path / '30m').mkdir()
    with mock.patch.object(dt, 'get_dummy_client') as mc:
        mc.return_value.get_market_info.return_value = {'symbol_list': []}
        with mock.patch.object(dt, 'read_arrow_market_dict_for_run_time', return_value=None) as m_arrow:
            result = dt.load_bmac_data('swap', rt, _fake_conf())
    m_arrow.assert_called_once()
    assert result == {}


def test_uses_arrow_when_present(monkeypatch):
    rt = datetime(2026, 6, 27, 20, 30)
    fake_df = pd.DataFrame({
        'candle_begin_time': pd.to_datetime(['2026-01-01']),
        'first_candle_time': pd.to_datetime(['2026-01-01']),
        'last_candle_time': pd.to_datetime(['2026-01-01']),
        'symbol': ['ZZZUSDT'], 'symbol_swap': ['ZZZUSDT'], 'symbol_spot': [''],
    })
    with mock.patch.object(dt, 'read_arrow_market_dict_for_run_time',
                           return_value={'ZZZUSDT': fake_df}) as m_arrow:
        with mock.patch.object(dt, 'get_dummy_client') as mc:
            mc.return_value.get_market_info.return_value = {'symbol_list': ['ZZZUSDT']}
            result = dt.load_bmac_data('swap', rt, _fake_conf())
    m_arrow.assert_called_once()
    assert 'ZZZUSDT' in result
