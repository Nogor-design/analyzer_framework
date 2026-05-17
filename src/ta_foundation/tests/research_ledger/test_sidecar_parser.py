"""Tests for the discovery sidecar parser.

Uses in-memory body fixtures to exercise the parser without depending on the
contents of the live `outputs/` tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.research_ledger.sidecar_parser import (
    infer_family,
    parse_summary_body,
    parse_summary_sidecar,
)


def _hardening_passed_body() -> dict:
    return {
        "schema_version": 1,
        "stage": {"id": "03_levels_regions", "label": "Levels", "ordinal": 3, "kind": "funnel"},
        "instrument": {"symbol": "NQ", "tick_size": 0.25},
        "rankings": [
            {
                "rank": 1,
                "family": "level",
                "signal": "vwap_reclaim_reject",
                "direction": "long",
                "timeframe": "1m",
                "session_filter": {"hour_from": 0, "minute_from": 0,
                                    "hour_to": 6, "minute_to": 0},
                "params": {"min_dist_ticks": 8, "max_dist_ticks": 32},
                "metrics": {"trade_count": 120, "profit_factor": 2.1,
                             "win_rate": 0.55, "expectancy_ticks": 6.4},
                "hardening": {
                    "enabled": True, "passed": True,
                    "validation": {"passed": True, "gates": [
                        {"name": "min_counts", "passed": True},
                        {"name": "degradation", "passed": True},
                        {"name": "t_test", "passed": True},
                    ]},
                    "evaluation_oos": {"trade_count": 40, "profit_factor": 1.85,
                                        "expectancy_ticks": 5.1},
                    "slippage_stress": {"passed": True, "expectancy_loss_pct": 18.2,
                                         "max_expectancy_loss_pct": 40.0},
                },
                "tier": {"id": "qualified", "label": "Qualified",
                         "verdict": "Genuine edge candidate.", "criteria_met": []},
                "outcome": {"mode": "ticks_24_8"},
                "entry_timing": "next_open",
            }
        ],
    }


def _hardening_failed_body() -> dict:
    body = _hardening_passed_body()
    r = body["rankings"][0]
    r["hardening"]["passed"] = False
    r["hardening"]["validation"]["passed"] = False
    r["hardening"]["validation"]["gates"][2] = {"name": "t_test", "passed": False,
                                                 "value": 1.4, "threshold": 3.0}
    r["tier"] = {"id": "marginal", "label": "Marginal", "verdict": "Likely noise.",
                 "criteria_met": ["hardening gates failed"]}
    return body


def _fast_probe_body() -> dict:
    return {
        "schema_version": 1,
        "stage": {"id": "04_ny_open", "label": "NY Open", "ordinal": 4, "kind": "funnel"},
        "instrument": {"symbol": "NQ", "tick_size": 0.25},
        "rankings": [
            {
                "rank": 1, "family": "orb", "signal": "orb_break_close",
                "direction": "both", "timeframe": "5m",
                "params": {"orb_minutes": 15, "stop_ticks": 8, "target_ticks": 24},
                "metrics": {"trade_count": 47, "profit_factor": 1.62, "win_rate": 0.4,
                             "expectancy_ticks": 4.2},
                "hardening": {"enabled": False},
                "tier": {"id": "reject", "label": "Reject",
                         "verdict": "Below thresholds.", "criteria_met": []},
            }
        ],
    }


# ---------- Schema parsing --------------------------------------------------


def test_parse_hardening_passed_body() -> None:
    out = parse_summary_body(_hardening_passed_body(), raw_path="x.json")
    assert out.schema_version == 1
    assert out.stage_id == "03_levels_regions"
    assert out.instrument_symbol == "NQ"
    assert out.timeframe == "1m"
    assert out.n_rankings == 1
    c = out.candidates[0]
    assert c["rank_in_run"] == 1
    assert c["gate_verdict"] == "survivor"
    assert c["pf_dev"] == 2.1
    assert c["n_trades_dev"] == 120
    assert c["pf_oos"] == 1.85
    assert c["slippage_stress_pass"] is True


def test_parse_hardening_failed_body() -> None:
    out = parse_summary_body(_hardening_failed_body(), raw_path="x.json")
    c = out.candidates[0]
    assert c["gate_verdict"] == "rejected"
    reasons = c["gate_reasons"]
    assert reasons and any(r["gate"] == "t_test" and r["passed"] is False for r in reasons)


def test_parse_fast_probe_no_hardening() -> None:
    out = parse_summary_body(_fast_probe_body(), raw_path="x.json")
    c = out.candidates[0]
    # tier id 'reject' → gate_verdict 'rejected' even without hardening.
    assert c["gate_verdict"] == "rejected"
    assert c["slippage_stress_pass"] is None
    assert c["pf_oos"] is None


def test_parse_preserves_notes_metadata() -> None:
    out = parse_summary_body(_hardening_passed_body(), raw_path="x.json")
    notes = out.candidates[0]["notes"]
    assert notes["signal"] == "vwap_reclaim_reject"
    assert notes["discovery_family"] == "level"
    assert notes["tier_id"] == "qualified"
    assert notes["instrument_symbol"] == "NQ"


def test_parse_handles_missing_rankings() -> None:
    body = {"schema_version": 1, "stage": {"id": "x", "label": "x", "ordinal": 1, "kind": "f"},
            "instrument": {"symbol": "ES"}}
    out = parse_summary_body(body, raw_path="x.json")
    assert out.n_rankings == 0
    assert out.candidates == []


def test_parse_empty_body() -> None:
    out = parse_summary_body({}, raw_path="x.json")
    assert out.schema_version == 0
    assert out.n_rankings == 0


def test_parse_handles_malformed_ranking_entries() -> None:
    body = _hardening_passed_body()
    # Insert garbage that the parser should silently skip.
    body["rankings"].insert(0, "not a dict")
    body["rankings"].append(None)
    out = parse_summary_body(body, raw_path="x.json")
    assert out.n_rankings == 1


def test_parse_summary_sidecar_reads_disk(tmp_path: Path) -> None:
    body = _hardening_passed_body()
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    out = parse_summary_sidecar(p)
    assert out.n_rankings == 1
    assert out.raw_path == str(p)


def test_coerces_floats_safely() -> None:
    body = _hardening_passed_body()
    body["rankings"][0]["metrics"]["profit_factor"] = float("nan")
    out = parse_summary_body(body, raw_path="x.json")
    assert out.candidates[0]["pf_dev"] is None


# ---------- Family inference ------------------------------------------------


def test_infer_family_known_signals() -> None:
    assert infer_family("03_vwap_london_reject_fade.yaml", "vwap_reclaim_reject") == "vwap_reject_fade"
    assert infer_family("04_nq_ny_open_orb_failure_reclaim_probe.yaml") == "orb_failure_reclaim"
    assert infer_family("04_nq_ny_open_orb_5m_large_move_hardened.yaml") == "orb_breakout"
    assert infer_family("03_nq_london_liquidity_sweep_fast_probe.yaml") == "overnight_high_low_sweep_reclaim"
    assert infer_family("03_nq_prior_overnight_fast_probe.yaml") == "prior_high_low_failed_breakout"
    assert infer_family("large_candle_excursion.yaml") == "large_candle_origin_retest"


def test_infer_family_unknown_falls_back_to_legacy() -> None:
    assert infer_family("some_random_file.yaml", "unknown_signal") == "legacy_imported"
    assert infer_family("06_validate.yaml") == "legacy_imported"


def test_infer_family_signal_match_overrides_name() -> None:
    # Filename gives no clue but signal does.
    assert infer_family("opaque_name.yaml", "vwap_reclaim_continuation") == "vwap_reclaim_continuation"
