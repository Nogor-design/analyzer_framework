from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_causal_learnability import (
    CausalLearnabilityError,
    build_causal_learnability_panel,
    run_causal_learnability_audit,
    write_causal_learnability_audit,
)


DENVER = "America/Denver"


def _families(count: int = 24) -> list[tuple[int, int, float, str]]:
    values = []
    for timeframe in (1, 2, 3, 5):
        for lookback in (5, 10, 20):
            for multiplier in (1.25, 1.5, 2.0):
                for mode in ("continuation", "reversion"):
                    values.append((timeframe, lookback, multiplier, mode))
    return values[:count]


def _rows(
    *,
    sequence: str = "NQ 09-26",
    session_count: int = 30,
    family_count: int = 24,
) -> list[dict]:
    rows = []
    families = _families(family_count)
    center = (len(families) - 1) / 2.0
    sessions = pd.date_range("2026-01-02", periods=session_count, freq="B")
    for session_index, day in enumerate(sessions):
        session = day.strftime("%Y-%m-%d")
        for family_index, (timeframe, lookback, multiplier, mode) in enumerate(
            families
        ):
            signal = pd.Timestamp(f"{session} 10:00", tz=DENVER)
            quality = 2.0 * (family_index - center)
            pnl = quality + (1.0 if session_index % 2 == 0 else -1.0)
            rows.append(
                {
                    "sequence": sequence,
                    "session_id": session,
                    "timeframe": timeframe,
                    "lookback": lookback,
                    "multiplier": multiplier,
                    "mode": mode,
                    "signal_dt": signal,
                    "exit_known_dt": signal + pd.Timedelta(minutes=6),
                    "net_pnl_ticks": pnl,
                    "capacity_eligible": True,
                }
            )
    return rows


def test_causal_rank_predicts_stable_family_quality_without_future_evidence():
    result = run_causal_learnability_audit(_rows())

    assert result["summary"]["evaluated_sessions"] >= 20
    assert result["summary"]["mean_rank_ic"] == pytest.approx(1.0)
    assert result["summary"]["mean_top_quintile_uplift_ticks"] > 0.0
    assert all(
        pd.Timestamp(row["evidence_through"])
        < pd.Timestamp(row["decision_asof"])
        for row in result["rank_rows"]
    )
    assert all(row["prior_family_sessions"] <= 5 for row in result["rank_rows"])


def test_exit_known_exactly_at_boundary_is_not_prior_evidence():
    rows = _rows(session_count=8)
    sessions = sorted({row["session_id"] for row in rows})
    boundary = pd.Timestamp(f"{sessions[3]} 10:00", tz=DENVER)
    for row in rows:
        if row["session_id"] == sessions[2]:
            row["exit_known_dt"] = boundary

    result = run_causal_learnability_audit(rows)
    first_ranked_session = min(
        row["session_index"] for row in result["rank_rows"]
    )

    assert first_ranked_session == 4


def test_appending_future_sessions_does_not_change_prior_scores_or_metrics():
    prefix = run_causal_learnability_audit(_rows(session_count=25))
    full = run_causal_learnability_audit(_rows(session_count=30))

    assert full["rank_rows"][: len(prefix["rank_rows"])] == prefix["rank_rows"]
    assert full["session_rows"][: len(prefix["session_rows"])] == (
        prefix["session_rows"]
    )


def test_panel_applies_frozen_cross_sequence_gates():
    sequences = (
        "NQ 03-26",
        "NQ 06-26",
        "NQ 09-26",
        "ES 03-26",
        "RTY 03-26",
        "YM 03-26",
        "YM 06-26",
    )
    results = [
        run_causal_learnability_audit(_rows(sequence=sequence))
        for sequence in sequences
    ]
    panel = build_causal_learnability_panel(results)

    assert panel["result_label"] == "CAUSALLY_LEARNABLE"
    assert panel["gates"]["passed"] is True
    assert panel["panel"]["positive_rank_ic_sequences"] == 7


def test_writer_hashes_complete_panel_bundle(tmp_path: Path):
    sequences = (
        "NQ 03-26",
        "NQ 06-26",
        "NQ 09-26",
        "ES 03-26",
        "RTY 03-26",
        "YM 03-26",
        "YM 06-26",
    )
    results = [
        run_causal_learnability_audit(_rows(sequence=sequence))
        for sequence in sequences
    ]
    panel = build_causal_learnability_panel(results)
    manifest = {
        "schema_version": "dynamic_causal_learnability.v1",
        "research_phase": "dynamic_phase_4c",
        "children": [
            result["manifest"]["manifest_sha256"] for result in results
        ],
    }
    bundle = {
        "manifest": manifest,
        "summary": panel,
        "sequence_results": results,
    }
    paths = write_causal_learnability_audit(tmp_path / "audit", bundle)
    written = json.loads(
        Path(paths["manifest"]).read_text(encoding="utf-8")
    )

    assert set(written["artifacts"]) == {
        "summary",
        "family_session_ledger",
        "rank_ledger",
        "session_ledger",
    }
    assert all(
        len(record["sha256"]) == 64
        for record in written["artifacts"].values()
    )


def test_configuration_and_gate_tuning_is_rejected():
    with pytest.raises(CausalLearnabilityError, match="freezes"):
        run_causal_learnability_audit(
            _rows(),
            config={"lookback_sessions": 10},
        )
    results = [
        run_causal_learnability_audit(_rows(sequence=f"sequence-{index}"))
        for index in range(7)
    ]
    with pytest.raises(CausalLearnabilityError, match="freezes"):
        build_causal_learnability_panel(
            results,
            gates={"minimum_pair_weighted_rank_ic": 0.0},
        )
