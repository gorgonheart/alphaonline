import json
import pandas as pd
import pytest
from tools.robust40 import handoff, telemetry


def _capture(monkeypatch):
    msgs = []
    monkeypatch.setattr(telemetry, "_write", msgs.append)
    return msgs


def _event(msgs):
    return json.loads(msgs[-1].split("MODEL_REPLAY ", 1)[1])


def test_pivot_removed_unheld_column_stays_incremental(monkeypatch):
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=168, freq="h")
    old = {
        "pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0, "ANTHROPICUSDT": 50.0}, index=idx),
        "pivot/spot/open": pd.DataFrame({"BTCUSDT": 100.0, "ANTHROPICUSDT": 50.0}, index=idx),
    }
    new = {
        "pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0}, index=idx),
        "pivot/spot/open": pd.DataFrame({"BTCUSDT": 100.0}, index=idx),
    }
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols={("spot", "BTCUSDT")})
    assert rev is None
    ev = _event(msgs)
    assert ev["removed_columns"] == 2
    assert "removed_held_symbols" not in ev and "circuit_breaker" not in ev


def test_pivot_removed_held_column_canonical_alias_triggers_conservative_revision(monkeypatch):
    """Review Focus #5: alias 'BTC-USDT' vs 'BTCUSDT' must match held set and trigger revision."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=168, freq="h")
    old = {"pivot/swap/close": pd.DataFrame({"BTC-USDT": 100.0, "ETHUSDT": 20.0}, index=idx)}
    new = {"pivot/swap/close": pd.DataFrame({"ETHUSDT": 20.0}, index=idx)}
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols={("swap", "BTCUSDT")})
    assert rev == idx[0]
    assert "swap:BTCUSDT" in _event(msgs)["removed_held_symbols"]


def test_pivot_removed_held_set_alias_also_canonicalized(monkeypatch):
    """Review Focus #5: held_symbols containing 'BTC-USDT' also matches removed column 'BTCUSDT'."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=168, freq="h")
    old = {"pivot/swap/close": pd.DataFrame({"BTCUSDT": 100.0, "ETHUSDT": 20.0}, index=idx)}
    new = {"pivot/swap/close": pd.DataFrame({"ETHUSDT": 20.0}, index=idx)}
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols={("swap", "BTC-USDT")})
    assert rev == idx[0]
    assert "swap:BTCUSDT" in _event(msgs)["removed_held_symbols"]


def test_pivot_removed_column_without_held_symbols_stays_conservative():
    """When held_symbols is None (unreadable/unspecified), pivot removed_columns stays conservative."""
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    old = {"pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0, "FOOUSDT": 1.0}, index=idx)}
    new = {"pivot/spot/close": pd.DataFrame({"BTCUSDT": 100.0}, index=idx)}
    assert handoff.first_revision(old, new, held_symbols=None) == idx[0]


def test_pivot_removed_columns_surge_trips_circuit_breaker_even_without_close_table(monkeypatch):
    """Review Focus #5: missing pivot/spot/close still computes safe non-zero denominator and trips breaker."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    cols = [f"C{i}USDT" for i in range(40)]
    old = {"pivot/spot/open": pd.DataFrame({c: 1.0 for c in cols}, index=idx)}
    new = {"pivot/spot/open": pd.DataFrame({"C0USDT": 1.0}, index=idx)}  # 39 removed > limit 30
    with telemetry.round_log("25m-test", 1795137900):
        with pytest.raises(handoff.UpstreamUniverseDropError, match="pivot"):
            handoff.first_revision(old, new, held_symbols=set())
    assert _event(msgs)["circuit_breaker"] == "removed_pivot_columns_surge"


def test_pivot_removed_columns_union_deduplicates_across_fields(monkeypatch):
    """20 symbols removed across 4 pivot fields counts as 20 unique symbols (<= 30), not 80."""
    msgs = _capture(monkeypatch)
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    cols = [f"C{i}USDT" for i in range(40)]
    keep = cols[20:]
    old = {
        f"pivot/spot/{f}": pd.DataFrame({c: 1.0 for c in cols}, index=idx)
        for f in ("open", "close", "vwap1m", "funding_rate")
    }
    new = {
        f"pivot/spot/{f}": pd.DataFrame({c: 1.0 for c in keep}, index=idx)
        for f in ("open", "close", "vwap1m", "funding_rate")
    }
    with telemetry.round_log("25m-test", 1795137900):
        rev = handoff.first_revision(old, new, held_symbols=set())
    assert rev is None
    ev = _event(msgs)
    assert ev["removed_columns"] == 80
    assert "circuit_breaker" not in ev


def test_non_pivot_removed_column_stays_conservative():
    idx = pd.date_range("2026-11-16 00:25", periods=4, freq="h")
    old = {"raw/spot/BTCUSDT": pd.DataFrame({"open": 1.0, "close": 1.0}, index=idx)}
    new = {"raw/spot/BTCUSDT": pd.DataFrame({"close": 1.0}, index=idx)}
    assert handoff.first_revision(old, new, held_symbols=set()) == idx[0]
