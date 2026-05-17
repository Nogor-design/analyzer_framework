from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web.discovery_summary import (
    SCHEMA_VERSION,
    TIER_HIGH_QUALITY,
    TIER_MARGINAL,
    TIER_MOST_ROBUST,
    TIER_REJECTED,
    TIER_SOLID,
    build_summary,
    classify_tier,
    read_summary,
    resolve_sidecar_path,
    sidecar_path_for_report,
    write_sidecar_for_run,
    write_summary,
)


# ---------------------------------------------------------------------------
# Tier classifier — every cell of the 4-tier table
# ---------------------------------------------------------------------------

def test_tier_most_robust_requires_pf_n_and_low_degradation():
    t = classify_tier(profit_factor=1.6, trade_count=40, is_oos_degradation=0.05)
    assert t.id == TIER_MOST_ROBUST
    assert "profit_factor >= 1.5" in " ".join(t.criteria_met)
    assert "Trade it" not in t.verdict
    assert "harden" in t.verdict.lower()


def test_tier_most_robust_drops_to_high_quality_when_degradation_too_high():
    t = classify_tier(profit_factor=1.6, trade_count=40, is_oos_degradation=0.20)
    assert t.id == TIER_HIGH_QUALITY


def test_tier_most_robust_drops_to_high_quality_when_n_too_low():
    t = classify_tier(profit_factor=1.6, trade_count=25, is_oos_degradation=0.05)
    assert t.id == TIER_HIGH_QUALITY


def test_tier_most_robust_requires_known_degradation():
    t = classify_tier(profit_factor=1.6, trade_count=40, is_oos_degradation=None)
    # No degradation measured → cannot certify as Most Robust; falls back.
    assert t.id == TIER_HIGH_QUALITY


def test_tier_high_quality_pf_threshold():
    assert classify_tier(1.30, 25, 0.20).id == TIER_HIGH_QUALITY
    assert classify_tier(1.29, 25, 0.20).id == TIER_SOLID


def test_tier_solid_pf_threshold():
    assert classify_tier(1.10, 18, None).id == TIER_SOLID
    assert classify_tier(1.09, 18, None).id == TIER_MARGINAL


def test_tier_marginal_when_pf_above_one_but_low_n():
    assert classify_tier(1.05, 5, None).id == TIER_MARGINAL


def test_tier_rejected_below_one():
    t = classify_tier(0.95, 100, 0.0)
    assert t.id == TIER_REJECTED


def test_tier_rejected_dominates_high_n_low_pf():
    assert classify_tier(0.5, 1000, 0.0).id == TIER_REJECTED


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------

def _candle_combo(
    *,
    pattern_id: str = "large_body",
    pf: float = 1.41,
    n: int = 87,
    win_rate: float = 0.52,
    deg: float = 0.08,
    tf: int = 1,
    tp: int = 20,
    sl: int = 10,
    direction: str = "long",
) -> dict:
    return {
        "tf": tf,
        "pattern_id": pattern_id,
        "params": {
            "body_multiplier": 2.0,
            "wick_to_body_max": 0.5,
            "lookback": 10,
            "tp_ticks": tp,
            "sl_ticks": sl,
        },
        "direction_mode": direction,
        "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_signals": n + 5,
        "n_trades": n,
        "fill_rate": 0.95,
        "metrics": {
            "profit_factor": pf,
            "win_rate": win_rate,
            "n_trades": n,
            "avg_trade": 1.83,
            "avg_winner": 19.2,
            "avg_loser": -9.8,
            "max_drawdown": -45.0,
            "sharpe_ratio": 1.12,
        },
        "is_oos_degradation": deg,
        "session_filter": {"hour_from": 7, "minute_from": 30, "hour_to": 16},
    }


def test_build_summary_returns_well_formed_payload():
    raw = {
        "candle": [
            _candle_combo(pattern_id="large_body", pf=1.41, n=87, deg=0.08),
            _candle_combo(pattern_id="inside_bar", pf=1.22, n=34, deg=0.18),
            _candle_combo(pattern_id="doji", pf=0.95, n=22, deg=0.40),
        ],
    }
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
        report_html_path="outputs/01_scan/01_quick_scan.html",
    )

    assert summary.schema_version == SCHEMA_VERSION
    assert summary.stage.id == "01_quick_scan"
    assert summary.instrument.symbol == "NQ"
    assert summary.instrument.tick_value == 5.00

    assert len(summary.rankings) == 3
    # Sorted by PF desc
    assert summary.rankings[0].metrics.profit_factor == pytest.approx(1.41)
    assert summary.rankings[2].metrics.profit_factor == pytest.approx(0.95)

    # Tier on the leader is High Quality (deg=0.08 + pf=1.41 -> high quality, not most robust because pf<1.5)
    assert summary.rankings[0].tier.id == TIER_HIGH_QUALITY
    # Last entry is rejected (pf < 1.0)
    assert summary.rankings[2].tier.id == TIER_REJECTED


def test_summary_to_dict_is_json_safe():
    raw = {"candle": [_candle_combo()]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    json.dumps(summary.to_dict())


def test_top_n_truncates():
    combos = [_candle_combo(pf=1.0 + 0.01 * i, n=20) for i in range(50)]
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={"candle": combos},
        top_n=10,
    )
    assert len(summary.rankings) == 10


def test_diagnostics_count_total_and_passing():
    # One row missing metrics — should be counted in total but not passing
    bad = _candle_combo()
    bad.pop("metrics")
    raw = {"candle": [_candle_combo(), bad, _candle_combo()]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    assert summary.diagnostics.total_combos_tested == 3
    assert summary.diagnostics.combos_passing_min_trades == 2


def test_diagnostics_families_with_results_records_zero_for_silent_families():
    """Stage 1 enables 8 families; we pass results for only one.
    The remaining 7 must show up in the breakdown with count 0 so the UI
    can flag them as 'tested but produced nothing'."""
    raw = {"candle": [_candle_combo(pf=1.30, n=40)]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    fwr = summary.diagnostics.families_with_results
    # Stage 1 enables all 8 families.
    assert set(fwr.keys()) == {"candle", "ma", "orb", "bb", "lcr", "breakout", "pullback", "level"}
    assert fwr["candle"] == 1
    for fam in ("ma", "orb", "bb", "lcr", "breakout", "pullback", "level"):
        assert fwr[fam] == 0


def test_diagnostics_tier_breakdown_counts_each_tier():
    """tier_breakdown reflects the tiers that classify_tier actually
    assigned. We don't pin specific labels here — just that every
    ranking is accounted for and the rejected count surfaces."""
    raw = {"candle": [
        _candle_combo(pf=1.60, n=40, deg=0.05),
        _candle_combo(pf=1.25, n=40, deg=0.10),
        _candle_combo(pf=0.85, n=40, deg=0.10),
    ]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    tb = summary.diagnostics.tier_breakdown
    # Every ranking gets a tier slot; total counted == len(rankings).
    assert sum(tb.values()) == len(summary.rankings) == 3
    # The losing combo must land in rejected.
    assert tb.get(TIER_REJECTED) == 1


def test_empty_reason_unset_when_at_least_one_winner():
    raw = {"candle": [_candle_combo(pf=1.30, n=40)]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    assert summary.diagnostics.empty_reason is None


def test_empty_reason_when_no_rows_at_all():
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={},
    )
    assert summary.rankings == ()
    reason = summary.diagnostics.empty_reason or ""
    assert "no results" in reason.lower() or "no signals" in reason.lower() or "no usable" in reason.lower() or "produced no" in reason.lower()


def test_empty_reason_when_every_ranking_is_rejected():
    raw = {"candle": [
        _candle_combo(pf=0.82, n=56, deg=0.10),
        _candle_combo(pf=0.74, n=56, deg=0.20),
    ]}
    # Tiny bar count exercises the "small sample" suffix.
    from ta_foundation.web.discovery_summary import InputSummary
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
        input_summary=InputSummary(bar_count=12_000),
    )
    reason = summary.diagnostics.empty_reason or ""
    assert "no edge" in reason.lower() or "rejected" in reason.lower() or "failed" in reason.lower()
    # Sample-size hint kicked in.
    assert "12,000" in reason


def test_unknown_stage_raises():
    with pytest.raises(ValueError, match="Unknown stage id"):
        build_summary(stage_id="bogus", instrument_symbol="NQ", raw_results={})


def test_unknown_instrument_raises():
    with pytest.raises(ValueError, match="Unknown instrument"):
        build_summary(stage_id="01_quick_scan", instrument_symbol="ZZZ", raw_results={})


# ---------------------------------------------------------------------------
# Promote payload — per-family
# ---------------------------------------------------------------------------

def _summary_for(family_id: str, raw_combo: dict, *, stage_id: str = "01_quick_scan"):
    summary = build_summary(
        stage_id=stage_id,
        instrument_symbol="NQ",
        raw_results={family_id: [raw_combo]},
    )
    return summary.rankings[0]


def test_promote_payload_for_candle_targets_candle_discovery():
    entry = _summary_for("candle", _candle_combo(pattern_id="large_body", pf=1.5, n=40))
    overrides = entry.promote_payload.yaml_overrides
    assert "candle_discovery" in overrides
    candle = overrides["candle_discovery"]
    assert candle["enabled"] is True
    assert "large_body" in candle["patterns"]
    assert candle["patterns"]["large_body"]["enabled"] is True
    assert candle["patterns"]["large_body"]["body_multiplier"] == [2.0]
    assert candle["timeframes"] == [1]
    assert candle["outcome"]["ticks"]["take_profit"] == [20]
    assert candle["outcome"]["ticks"]["stop"] == [10]
    assert candle["entry_timing"]["next_open"]["enabled"] is True
    assert candle["entry_timing"]["break_extreme"]["enabled"] is False


def test_promote_payload_for_lcr_targets_lcr_discovery():
    raw = {
        "tf": 1,
        "signal_id": "retrace",
        "params": {
            "size_multiplier": 2.5,
            "lookback": 20,
            "zone_type": "body",
            "tp_ticks": 20,
            "sl_ticks": 10,
        },
        "direction_mode": "both",
        "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 42,
        "metrics": {"profit_factor": 1.34, "win_rate": 0.58, "n_trades": 42},
        "is_oos_degradation": 0.12,
    }
    entry = _summary_for("lcr", raw)
    overrides = entry.promote_payload.yaml_overrides
    assert "lcr_discovery" in overrides
    lcr = overrides["lcr_discovery"]
    assert lcr["signal_types"] == ["retrace"]
    assert lcr["size_multipliers"] == [2.5]
    assert lcr["lookbacks"] == [20]
    assert lcr["zone_types"] == ["body"]
    assert lcr["tp_ticks"] == [20]
    assert lcr["sl_ticks"] == [10]


def test_promote_payload_for_orb():
    raw = {
        "tf": 1,
        "signal_id": "orb",
        "params": {"orb_window_min": 15, "tp_ticks": 20, "sl_ticks": 10},
        "direction_mode": "both",
        "entry_timing": "break_extreme",
        "outcome_mode": "ticks",
        "n_trades": 28,
        "metrics": {"profit_factor": 1.31, "win_rate": 0.54, "n_trades": 28},
        "is_oos_degradation": 0.14,
    }
    entry = _summary_for("orb", raw)
    overrides = entry.promote_payload.yaml_overrides
    assert overrides["orb_discovery"]["orb"]["orb_minutes"] == [15]
    assert overrides["orb_discovery"]["entry_timing"]["break_extreme"]["enabled"] is True


def test_promote_payload_for_bb():
    raw = {
        "tf": 5,
        "signal_id": "bb_squeeze_breakout",
        "params": {"period": 20, "n_std": 2.0, "tp_ticks": 20, "sl_ticks": 10},
        "direction_mode": "both",
        "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 30,
        "metrics": {"profit_factor": 1.25, "win_rate": 0.48, "n_trades": 30},
        "is_oos_degradation": 0.20,
    }
    entry = _summary_for("bb", raw)
    overrides = entry.promote_payload.yaml_overrides
    bb = overrides["bb_discovery"]
    assert "bb_squeeze_breakout" in bb["signals"]
    assert bb["signals"]["bb_squeeze_breakout"]["period"] == [20]
    assert bb["signals"]["bb_squeeze_breakout"]["n_std"] == [2.0]
    assert bb["timeframes"] == [5]
    assert bb["entry_timing"]["next_open"]["enabled"] is True


def test_summary_prefers_hardened_degradation_and_keeps_metadata():
    raw = _candle_combo(pf=1.6, n=60, deg=0.01)
    raw["hardening"] = {
        "enabled": True,
        "passed": False,
        "validation": {
            "wf_results": {"oos_degradation": 0.42},
            "issues": ["hard gate failed: degradation"],
        },
        "slippage_stress": {"passed": False},
    }
    entry = _summary_for("candle", raw)
    assert entry.metrics.is_oos_degradation == pytest.approx(0.42)
    assert entry.tier.id == TIER_MARGINAL
    assert entry.hardening["validation"]["wf_results"]["oos_degradation"] == pytest.approx(0.42)


def test_promote_payload_for_ma():
    raw = {
        "tf": 1,
        "signal_id": "ma_cross",
        "params": {"fast_period": 9, "slow_period": 20, "ma_type": "ema", "tp_ticks": 20, "sl_ticks": 10},
        "direction_mode": "both",
        "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 30,
        "metrics": {"profit_factor": 1.18, "win_rate": 0.52, "n_trades": 30},
        "is_oos_degradation": 0.20,
    }
    entry = _summary_for("ma", raw)
    overrides = entry.promote_payload.yaml_overrides
    ma = overrides["ma_discovery"]
    assert ma["signals"]["ma_cross"]["enabled"] is True
    assert ma["signals"]["ma_cross"]["fast_period"] == [9]
    assert ma["signals"]["ma_cross"]["ma_type"] == ["ema"]


def test_promote_payload_for_breakout_pullback_level_share_pattern():
    for family, yaml_block in [
        ("breakout", "breakout_discovery"),
        ("pullback", "pullback_discovery"),
        ("level",    "level_discovery"),
    ]:
        raw = {
            "tf": 1,
            "signal_id": "n_bar_breakout" if family == "breakout" else "trend_pullback" if family == "pullback" else "swing_level",
            "params": {"lookback_bars": 10, "tp_ticks": 20, "sl_ticks": 10},
            "direction_mode": "both",
            "entry_timing": "next_open",
            "outcome_mode": "ticks",
            "n_trades": 25,
            "metrics": {"profit_factor": 1.18, "win_rate": 0.50, "n_trades": 25},
            "is_oos_degradation": 0.20,
        }
        entry = _summary_for(family, raw)
        overrides = entry.promote_payload.yaml_overrides
        assert yaml_block in overrides
        block = overrides[yaml_block]
        assert block["lookback_bars"] == [10]
        assert block["signals"][raw["signal_id"]]["lookback_bars"] == [10]
        assert block["timeframes"] == [1]


# ---------------------------------------------------------------------------
# Next stage recommendations
# ---------------------------------------------------------------------------

def test_recommends_next_stages_for_families_with_edge_in_stage_one():
    raw = {
        "candle": [_candle_combo(pattern_id="large_body", pf=1.41, n=87, deg=0.08)],
        "lcr": [
            {
                "tf": 1, "signal_id": "retrace",
                "params": {"size_multiplier": 2.5, "lookback": 20, "zone_type": "body",
                           "tp_ticks": 20, "sl_ticks": 10},
                "direction_mode": "both", "entry_timing": "next_open", "outcome_mode": "ticks",
                "n_trades": 42,
                "metrics": {"profit_factor": 1.34, "win_rate": 0.58, "n_trades": 42},
                "is_oos_degradation": 0.12,
            }
        ],
        "ma": [
            {
                "tf": 1, "signal_id": "ma_cross",
                "params": {"fast_period": 9, "slow_period": 20, "tp_ticks": 20, "sl_ticks": 10},
                "direction_mode": "both", "entry_timing": "next_open", "outcome_mode": "ticks",
                "n_trades": 11,  # too few — not promotable
                "metrics": {"profit_factor": 0.91, "win_rate": 0.4, "n_trades": 11},
                "is_oos_degradation": 0.50,
            }
        ],
    }
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    suggested = {rec.stage_id for rec in summary.next_stage_recommendations}
    assert "02_candle_patterns" in suggested
    assert "03_levels_regions" in suggested
    # MA had pf < 1.0 so it shouldn't appear
    assert "05_orb_momentum" not in suggested


def test_no_family_has_edge_returns_action_recommendations():
    """Pre-#8 this returned []. Now we surface 'try a different
    instrument' / 'try LCE' as structured action recs so the UI has
    something to show on a no-edge run."""
    raw = {"candle": [_candle_combo(pf=0.85, n=100, deg=0.5)]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
    )
    suggested = {rec.stage_id for rec in summary.next_stage_recommendations}
    # Sentinel action ids prefixed with `_action.` are how the backend
    # signals "this is a hint, not a real funnel stage".
    assert "_action.different_instrument" in suggested
    # LCE is recommended unless we're already on the LCE page.
    assert "large_candle_excursion" in suggested
    # No real funnel-stage suggestions on a no-edge run.
    assert "02_candle_patterns" not in suggested
    assert "06_validate" not in suggested


def test_empty_case_recommends_longer_history_when_dataset_is_small():
    from ta_foundation.web.discovery_summary import InputSummary
    raw = {"candle": [_candle_combo(pf=0.85, n=100, deg=0.5)]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
        input_summary=InputSummary(bar_count=12_000),
    )
    by_id = {rec.stage_id: rec for rec in summary.next_stage_recommendations}
    assert "_action.longer_history" in by_id
    assert "12,000" in by_id["_action.longer_history"].reason


def test_empty_case_skips_longer_history_when_dataset_is_large():
    from ta_foundation.web.discovery_summary import InputSummary
    raw = {"candle": [_candle_combo(pf=0.85, n=100, deg=0.5)]}
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results=raw,
        input_summary=InputSummary(bar_count=500_000),
    )
    suggested = {rec.stage_id for rec in summary.next_stage_recommendations}
    assert "_action.longer_history" not in suggested
    assert "_action.different_instrument" in suggested


# ---------------------------------------------------------------------------
# File I/O round-trip
# ---------------------------------------------------------------------------

def test_write_and_read_round_trip(tmp_path: Path):
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={"candle": [_candle_combo()]},
        report_html_path="outputs/foo.html",
    )
    target = tmp_path / "discovery_summary.json"
    write_summary(summary, target)

    assert target.exists()
    data = read_summary(target)
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["stage"]["id"] == "01_quick_scan"
    assert data["instrument"]["symbol"] == "NQ"
    assert data["report_html"] == "outputs/foo.html"
    assert data["rankings"]
    assert data["rankings"][0]["family"] == "candle"


def test_read_rejects_wrong_schema_version(tmp_path: Path):
    target = tmp_path / "bad.json"
    target.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        read_summary(target)


# ---------------------------------------------------------------------------
# Explain strings
# ---------------------------------------------------------------------------

def test_explain_strings_are_populated_and_human_readable():
    entry = _summary_for("candle", _candle_combo(pf=1.41, n=87))
    assert entry.explain.what_it_trades
    assert entry.explain.when_it_works
    assert entry.explain.risks
    # Sanity: the pattern label appears in what-it-trades
    assert "Large Body Bar" in entry.explain.what_it_trades


def test_explain_calls_out_low_sample_size():
    entry = _summary_for("candle", _candle_combo(pf=1.5, n=12, deg=0.05))
    assert "Sample size is small" in entry.explain.risks


def test_explain_calls_out_high_degradation():
    entry = _summary_for("candle", _candle_combo(pf=1.6, n=50, deg=0.45))
    assert "degradation is high" in entry.explain.risks


def test_explain_includes_disambiguating_params_for_orb():
    """Two ORB rows differing only by orb_minutes / min_range_ticks must
    produce explain.what_it_trades strings that differ. Pre-#7 the strings
    were identical because params lived only in the params dict."""
    common = {
        "tf": 1, "signal_id": "orb",
        "direction_mode": "both", "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 56,
        "metrics": {"profit_factor": 0.82, "win_rate": 0.55, "n_trades": 56},
    }
    a = _summary_for("orb", {
        **common,
        "params": {
            "orb_minutes": 15, "min_range_ticks": 4, "tp_ticks": 10, "sl_ticks": 8,
        },
    })
    b = _summary_for("orb", {
        **common,
        "params": {
            "orb_minutes": 30, "min_range_ticks": 8, "tp_ticks": 10, "sl_ticks": 8,
        },
    })
    assert "15m opening range" in a.explain.what_it_trades
    assert "30m opening range" in b.explain.what_it_trades
    assert ">=4t min range" in a.explain.what_it_trades
    assert ">=8t min range" in b.explain.what_it_trades
    # The two strings must not be identical.
    assert a.explain.what_it_trades != b.explain.what_it_trades
    # TP/SL is included for every family.
    assert "TP 10t / SL 8t" in a.explain.what_it_trades


def test_dedupe_collapses_identical_metric_siblings():
    """Two ORB combos differing only in min_range_ticks but producing
    identical metrics must collapse to a single ranking row, with the
    differing param surfaced in `variants`."""
    common = {
        "tf": 1, "signal_id": "orb",
        "direction_mode": "both", "entry_timing": "next_open",
        "outcome_mode": "ticks_10_8",
        "n_trades": 56,
        "metrics": {
            "profit_factor": 0.8198006644518273,
            "win_rate": 0.5535714285714286,
            "n_trades": 56,
            "avg_trade": -4.358571428571429,
        },
        "is_oos_degradation": 0.13,
    }
    a = {**common, "params": {"orb_minutes": 15, "min_range_ticks": 4, "tp_ticks": 10, "sl_ticks": 8}}
    b = {**common, "params": {"orb_minutes": 15, "min_range_ticks": 8, "tp_ticks": 10, "sl_ticks": 8}}
    summary = build_summary(
        stage_id="04_ny_open",
        instrument_symbol="NQ",
        raw_results={"orb": [a, b]},
    )
    # Pre-#9 this would have been 2; after dedupe, exactly one row.
    assert len(summary.rankings) == 1
    kept = summary.rankings[0]
    # The retained row has 1 variant capturing the differing knob.
    assert len(kept.variants) == 1
    assert kept.variants[0]["params"] == {"min_range_ticks": 8}


def test_dedupe_does_not_collapse_when_metrics_differ():
    """If PF or trade_count differ, the rows are NOT siblings even if
    all other dimensions match."""
    common = {
        "tf": 1, "signal_id": "orb",
        "direction_mode": "both", "entry_timing": "next_open",
        "outcome_mode": "ticks_10_8",
    }
    a = {
        **common,
        "params": {"orb_minutes": 15, "min_range_ticks": 4, "tp_ticks": 10, "sl_ticks": 8},
        "n_trades": 56,
        "metrics": {"profit_factor": 0.82, "win_rate": 0.55, "n_trades": 56, "avg_trade": -4.36},
    }
    b = {
        **common,
        "params": {"orb_minutes": 15, "min_range_ticks": 8, "tp_ticks": 10, "sl_ticks": 8},
        "n_trades": 40,  # different trade count - real difference
        "metrics": {"profit_factor": 0.82, "win_rate": 0.55, "n_trades": 40, "avg_trade": -4.36},
    }
    summary = build_summary(
        stage_id="04_ny_open",
        instrument_symbol="NQ",
        raw_results={"orb": [a, b]},
    )
    assert len(summary.rankings) == 2
    for r in summary.rankings:
        assert r.variants == ()


def test_dedupe_does_not_cross_family_or_signal_or_timeframe():
    """Sibling-detection is scoped: identical metrics across different
    families / signals / timeframes is coincidence, not redundancy."""
    common_metrics = {
        "n_trades": 50,
        "metrics": {"profit_factor": 1.05, "win_rate": 0.5, "n_trades": 50, "avg_trade": 1.0},
    }
    rows = [
        # candle 1m
        {**common_metrics, "tf": 1, "pattern_id": "large_body",
         "params": {"body_multiplier": 2.0, "tp_ticks": 10, "sl_ticks": 10},
         "direction_mode": "long", "entry_timing": "next_open", "outcome_mode": "ticks"},
        # candle 5m (different timeframe -> not a sibling)
        {**common_metrics, "tf": 5, "pattern_id": "large_body",
         "params": {"body_multiplier": 2.0, "tp_ticks": 10, "sl_ticks": 10},
         "direction_mode": "long", "entry_timing": "next_open", "outcome_mode": "ticks"},
    ]
    # Pre-#9 these would be two rows. They still are - dedupe only collapses
    # combos that share family/signal/tf/direction/timing.
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={"candle": rows},
    )
    assert len(summary.rankings) == 2


def test_dedupe_serializes_variants_in_to_dict():
    """variants must round-trip through to_dict so the UI can read them
    out of the sidecar JSON."""
    common = {
        "tf": 1, "signal_id": "orb",
        "direction_mode": "both", "entry_timing": "next_open",
        "outcome_mode": "ticks_10_8",
        "n_trades": 56,
        "metrics": {"profit_factor": 0.82, "win_rate": 0.55, "n_trades": 56, "avg_trade": -4.36},
    }
    a = {**common, "params": {"orb_minutes": 15, "min_range_ticks": 4, "tp_ticks": 10, "sl_ticks": 8}}
    b = {**common, "params": {"orb_minutes": 15, "min_range_ticks": 8, "tp_ticks": 10, "sl_ticks": 8}}
    summary = build_summary(
        stage_id="04_ny_open",
        instrument_symbol="NQ",
        raw_results={"orb": [a, b]},
    )
    payload = summary.to_dict()
    json.dumps(payload)  # JSON-safe
    assert payload["rankings"][0]["variants"] == [{"params": {"min_range_ticks": 8}}]


def test_explain_includes_session_filter_window_when_set():
    combo = {
        "tf": 1, "pattern_id": "large_body",
        "params": {"body_multiplier": 2.0, "tp_ticks": 20, "sl_ticks": 10},
        "direction_mode": "long", "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 40,
        "metrics": {"profit_factor": 1.4, "win_rate": 0.55, "n_trades": 40},
        "session_filter": {"hour_from": 6, "minute_from": 0, "hour_to": 10},
    }
    entry = _summary_for("candle", combo)
    assert "session 06:00-10:00" in entry.explain.what_it_trades


# ---------------------------------------------------------------------------
# CLI integration helper: write_sidecar_for_run
# ---------------------------------------------------------------------------

class _StubPackage:
    """Stand-in for AnalysisPackage — only the metadata.derived path is used."""

    def __init__(self, derived: dict) -> None:
        self.metadata = {"derived": derived}


def _candle_derived() -> dict:
    return {
        "candle_discovery": {
            "sweep_results": [
                _candle_combo(pattern_id="large_body", pf=1.41, n=87, deg=0.08),
                _candle_combo(pattern_id="inside_bar", pf=1.22, n=34, deg=0.18),
            ],
            "n_results": 2,
        }
    }


def test_write_sidecar_for_run_writes_next_to_html(tmp_path: Path):
    pkg = _StubPackage(_candle_derived())
    report_html = tmp_path / "01_quick_scan.html"
    report_html.write_text("<html></html>", encoding="utf-8")

    sidecar = write_sidecar_for_run(
        packages={"run_a": pkg},
        discovery_block={"stage": "01_quick_scan", "instrument": "NQ", "contract": "H25"},
        report_html_path=report_html,
        runtime_seconds=180,
    )

    assert sidecar is not None
    # Per-stage sidecar is named after the report HTML stem so successive
    # stage runs in the same output folder don't clobber each other.
    assert sidecar == tmp_path / "01_quick_scan_summary.json"
    data = read_summary(sidecar)
    assert data["stage"]["id"] == "01_quick_scan"
    assert data["instrument"]["symbol"] == "NQ"
    assert data["instrument"]["contract"] == "H25"
    assert data["diagnostics"]["runtime_seconds"] == 180
    assert data["report_html"].endswith("01_quick_scan.html")
    assert data["rankings"][0]["family"] == "candle"
    assert data["rankings"][0]["signal"] == "large_body"


def test_write_sidecar_handles_lcr_results_key(tmp_path: Path):
    """LCR sweep stores rows under 'results' rather than 'sweep_results'."""
    lcr_row = {
        "tf": 1,
        "signal_id": "retrace",
        "params": {"size_multiplier": 2.5, "lookback": 20, "zone_type": "body",
                   "tp_ticks": 20, "sl_ticks": 10},
        "direction_mode": "both",
        "entry_timing": "next_open",
        "outcome_mode": "ticks",
        "n_trades": 42,
        "metrics": {"profit_factor": 1.34, "win_rate": 0.58, "n_trades": 42},
        "is_oos_degradation": 0.12,
    }
    pkg = _StubPackage({"lcr_discovery": {"results": [lcr_row], "n_results": 1}})
    report_html = tmp_path / "03_levels_regions.html"
    report_html.write_text("<html></html>", encoding="utf-8")

    sidecar = write_sidecar_for_run(
        packages={"run_a": pkg},
        discovery_block={"stage": "03_levels_regions", "instrument": "NQ"},
        report_html_path=report_html,
    )

    assert sidecar is not None
    data = read_summary(sidecar)
    assert data["rankings"][0]["family"] == "lcr"
    assert data["rankings"][0]["signal"] == "retrace"


def test_write_sidecar_returns_none_when_block_missing_keys(tmp_path: Path):
    pkg = _StubPackage(_candle_derived())
    report_html = tmp_path / "report.html"
    report_html.write_text("<html></html>", encoding="utf-8")

    # Missing instrument
    assert write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "01_quick_scan"},
        report_html_path=report_html,
    ) is None

    # Missing stage
    assert write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"instrument": "NQ"},
        report_html_path=report_html,
    ) is None

    # Unknown stage id is treated as opt-out, not an error
    assert write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "bogus_stage", "instrument": "NQ"},
        report_html_path=report_html,
    ) is None

    # No sidecar written under either the per-stage name or the legacy name.
    assert not (tmp_path / "report_summary.json").exists()
    assert not (tmp_path / "discovery_summary.json").exists()


def test_sidecar_path_for_report_uses_html_stem(tmp_path: Path):
    p = sidecar_path_for_report(tmp_path / "01_quick_scan.html")
    assert p == tmp_path / "01_quick_scan_summary.json"


def test_resolve_sidecar_path_prefers_per_stage_then_legacy(tmp_path: Path):
    report = tmp_path / "01_quick_scan.html"
    report.write_text("", encoding="utf-8")
    # Neither file exists -> None.
    assert resolve_sidecar_path(report) is None

    # Only legacy exists -> falls back to legacy.
    legacy = tmp_path / "discovery_summary.json"
    legacy.write_text("{}", encoding="utf-8")
    assert resolve_sidecar_path(report) == legacy

    # Per-stage exists -> wins over legacy.
    canonical = tmp_path / "01_quick_scan_summary.json"
    canonical.write_text("{}", encoding="utf-8")
    assert resolve_sidecar_path(report) == canonical


def test_write_sidecar_propagates_session_filter_from_family_config(tmp_path: Path):
    """The sweep orchestrators slice bars by session_filter upstream but
    don't echo it back onto each row. The sidecar writer must pull it from
    the per-family YAML config so the UI can display the actual tested window.
    """
    # Build a candle row with NO session_filter on the row itself.
    row = _candle_combo(pattern_id="large_body", pf=1.40, n=40, deg=0.05)
    row["session_filter"] = {}
    pkg = _StubPackage({"candle_discovery": {"sweep_results": [row], "n_results": 1}})
    report_html = tmp_path / "04_ny_open.html"
    report_html.write_text("", encoding="utf-8")

    sidecar = write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "04_ny_open", "instrument": "NQ"},
        report_html_path=report_html,
        family_configs={
            "candle": {
                "session_filter": {"hour_from": 6, "minute_from": 0, "hour_to": 10},
            },
        },
    )
    assert sidecar is not None
    data = read_summary(sidecar)
    # Row-level filter is now populated.
    assert data["rankings"][0]["session_filter"] == {"hour_from": 6, "minute_from": 0, "hour_to": 10}
    # Top-level input_summary reflects the same window.
    assert data["input_summary"]["session_filter"] == {"hour_from": 6, "minute_from": 0, "hour_to": 10}


def test_write_sidecar_does_not_overwrite_explicit_row_session_filter(tmp_path: Path):
    """If a row already carries a session_filter (some sweeps emit one),
    the family-level fallback must not clobber it."""
    row = _candle_combo(pattern_id="large_body", pf=1.40, n=40, deg=0.05)
    row["session_filter"] = {"hour_from": 9, "minute_from": 30, "hour_to": 16}
    pkg = _StubPackage({"candle_discovery": {"sweep_results": [row], "n_results": 1}})
    report_html = tmp_path / "01_quick_scan.html"
    report_html.write_text("", encoding="utf-8")

    sidecar = write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "01_quick_scan", "instrument": "NQ"},
        report_html_path=report_html,
        family_configs={
            "candle": {"session_filter": {"hour_from": 6, "minute_from": 0, "hour_to": 10}},
        },
    )
    assert sidecar is not None
    data = read_summary(sidecar)
    # Row-specific filter wins.
    assert data["rankings"][0]["session_filter"] == {"hour_from": 9, "minute_from": 30, "hour_to": 16}


def test_two_stage_runs_in_same_folder_do_not_overwrite(tmp_path: Path):
    """The original bug: two stages in the same output folder overwrote
    each other's sidecars. Both should now coexist under per-stage names.
    """
    pkg = _StubPackage(_candle_derived())

    quick = tmp_path / "01_quick_scan.html"
    quick.write_text("", encoding="utf-8")
    candle = tmp_path / "02_candle_patterns.html"
    candle.write_text("", encoding="utf-8")

    a = write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "01_quick_scan", "instrument": "NQ"},
        report_html_path=quick,
    )
    b = write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block={"stage": "02_candle_patterns", "instrument": "NQ"},
        report_html_path=candle,
    )

    assert a is not None and a.exists()
    assert b is not None and b.exists()
    assert a != b
    assert read_summary(a)["stage"]["id"] == "01_quick_scan"
    assert read_summary(b)["stage"]["id"] == "02_candle_patterns"


def test_write_sidecar_returns_none_when_block_not_a_dict(tmp_path: Path):
    pkg = _StubPackage(_candle_derived())
    report_html = tmp_path / "report.html"
    report_html.write_text("<html></html>", encoding="utf-8")

    assert write_sidecar_for_run(
        packages={"r": pkg},
        discovery_block=None,  # type: ignore[arg-type]
        report_html_path=report_html,
    ) is None


def test_write_sidecar_dedupes_across_packages(tmp_path: Path):
    """The CLI assigns the same sweep dict to every package — the sidecar
    should not multi-count rankings."""
    derived = _candle_derived()
    packages = {
        "run_a": _StubPackage(derived),
        "run_b": _StubPackage(derived),
        "run_c": _StubPackage(derived),
    }
    report_html = tmp_path / "report.html"
    report_html.write_text("<html></html>", encoding="utf-8")

    sidecar = write_sidecar_for_run(
        packages=packages,
        discovery_block={"stage": "01_quick_scan", "instrument": "NQ"},
        report_html_path=report_html,
    )
    data = read_summary(sidecar)
    # Two rows in the sweep — should still be two ranked entries, not six.
    assert len(data["rankings"]) == 2
