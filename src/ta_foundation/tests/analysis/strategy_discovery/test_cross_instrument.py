from __future__ import annotations

from ta_foundation.analysis.strategy_discovery.cross_instrument import (
    evaluate_cross_instrument,
)


def test_robust_edge_passes_all_gates():
    # +20t x40, -20t x20 per instrument -> mean +6.7t, PF 2.0, 4/4 profitable,
    # pooled n=240 with a pooled t-stat well above 2.
    panel = {inst: [20.0] * 40 + [-20.0] * 20 for inst in ("NQ", "ES", "RTY", "YM")}
    v = evaluate_cross_instrument(panel)
    assert v.is_robust_edge is True
    assert v.pooled_trades == 240
    assert v.instruments_profitable == 4
    assert v.pooled_t_stat > 2.0
    assert v.pooled_profit_factor > 1.2


def test_single_instrument_edge_is_rejected():
    # profitable on NQ only; the other three lose -> not a structural edge,
    # exactly the false positive same-instrument IS/OOS validation missed.
    panel = {
        "NQ": [20.0] * 40 + [-20.0] * 20,
        "ES": [10.0] * 25 + [-10.0] * 35,
        "RTY": [10.0] * 25 + [-10.0] * 35,
        "YM": [10.0] * 25 + [-10.0] * 35,
    }
    v = evaluate_cross_instrument(panel)
    assert v.is_robust_edge is False
    assert v.instruments_profitable == 1
    assert any(r.startswith("FAIL") and "instruments_profitable" in r for r in v.reasons)


def test_insufficient_trades_is_rejected():
    panel = {"NQ": [20.0] * 20 + [-20.0] * 10,
             "ES": [20.0] * 20 + [-20.0] * 10}
    v = evaluate_cross_instrument(panel)
    assert v.is_robust_edge is False
    assert v.pooled_trades == 60
    assert any(r.startswith("FAIL") and "pooled_trades" in r for r in v.reasons)


def test_verdict_to_dict_exposes_per_instrument():
    panel = {inst: [20.0] * 40 + [-20.0] * 20 for inst in ("NQ", "ES", "RTY", "YM")}
    d = evaluate_cross_instrument(panel).to_dict()
    assert d["is_robust_edge"] is True
    assert set(d["per_instrument"]) == {"NQ", "ES", "RTY", "YM"}
    assert d["per_instrument"]["NQ"]["n"] == 60
