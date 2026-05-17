from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.research_intake.author_from_intake import (
    load_author_json,
    run_author_from_intake,
)
from ta_foundation.research_intake import LdrImportOptions, import_ldr_report
from ta_foundation.research_intake.ldr import select_candidates, validate_candidates
from ta_foundation.research_ledger import get_repository


SAMPLE_REPORT = """
Local Deep Research result

The report discusses opening range breakout, ORB failure reclaim, overnight high
and overnight low sweep reclaim, prior high failed breakout, VWAP reclaim,
VWAP mean reversion, compression squeeze expansion, TradingView scripts, Reddit
threads, and a claimed >55% win rate. It also mentions large_candle_origin_retest
as a risky idea.
"""


def test_select_candidates_maps_report_terms_to_registry_families() -> None:
    candidates = select_candidates(SAMPLE_REPORT)
    families = {c.family for c in candidates}

    assert "orb_failure_reclaim" in families
    assert "overnight_high_low_sweep_reclaim" in families
    assert "prior_high_low_failed_breakout" in families
    assert "vwap_reclaim_continuation" in families
    assert "vwap_reject_fade" in families
    assert "compression_then_expansion" in families


def test_import_ldr_report_writes_review_artifacts(tmp_path: Path) -> None:
    report = tmp_path / "ldr_report.txt"
    out_md = tmp_path / "out" / "ldr_backlog.md"
    out_json = tmp_path / "out" / "ldr_backlog.json"
    author_json = tmp_path / "out" / "author_proposals.json"
    extracted = tmp_path / "out" / "extracted.txt"
    ledger_db = tmp_path / "ledger.db"
    get_repository(ledger_db).close()
    report.write_text(SAMPLE_REPORT, encoding="utf-8")

    bundle = import_ldr_report(
        LdrImportOptions(
            report_path=report,
            source_run_id="run_123",
            predecessor_run_id="run_old",
            output_markdown=out_md,
            output_json=out_json,
            author_json=author_json,
            extracted_text_path=extracted,
            ledger_db_path=ledger_db,
        )
    )

    assert bundle.source.tool == "local_deep_research"
    assert bundle.source.run_id == "run_123"
    assert bundle.source.predecessor_run_id == "run_old"
    assert len(bundle.candidates) >= 4
    assert bundle.validations
    assert all(v.valid for v in bundle.validations)
    assert any("performance language" in w for w in bundle.warnings)
    assert any("low-confidence" in w for w in bundle.warnings)
    assert any("large_candle_origin_retest" in w for w in bundle.warnings)

    md = out_md.read_text(encoding="utf-8")
    assert "External Research Intake - Local Deep Research" in md
    assert "ORB Failure Reclaim" in md
    assert "Registry Validation" in md
    assert "This file is a review artifact" in md

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["source"]["run_id"] == "run_123"
    assert payload["candidates"]
    assert payload["validations"]
    author_payload = json.loads(author_json.read_text(encoding="utf-8"))
    assert author_payload["proposals"]
    assert {
        "family",
        "instrument",
        "timeframe",
        "session_window",
        "direction",
        "params",
        "mechanism",
        "revival_reason",
    } <= set(author_payload["proposals"][0])
    assert extracted.read_text(encoding="utf-8") == SAMPLE_REPORT


def test_validate_candidates_blocks_unknown_family(tmp_path: Path) -> None:
    ledger_db = tmp_path / "ledger.db"
    get_repository(ledger_db).close()
    candidate = select_candidates("opening range ORB failure reclaim")[0]
    bad = candidate.__class__(
        **{
            **candidate.__dict__,
            "family": "not_a_family",
        }
    )

    validations = validate_candidates([bad], ledger_db)

    assert len(validations) == 1
    assert validations[0].valid is False
    assert "not in the registry" in validations[0].errors[0]


def test_validate_candidates_warns_when_ledger_path_is_not_sqlite(tmp_path: Path) -> None:
    bad_db = tmp_path / "experiments.duckdb"
    bad_db.write_text("not sqlite", encoding="utf-8")
    candidate = select_candidates("opening range ORB failure reclaim")[0]

    validations = validate_candidates([candidate], bad_db)

    assert len(validations) == 1
    assert validations[0].valid is True
    assert "Could not open ledger" in validations[0].warnings[0]


def test_author_from_intake_dry_run_does_not_write_real_ledger(tmp_path: Path) -> None:
    ledger_db = tmp_path / "ledger.db"
    get_repository(ledger_db).close()
    author_json = tmp_path / "author.json"
    author_json.write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "family": "orb_failure_reclaim",
                        "instrument": "NQ",
                        "timeframe": "5m",
                        "session_window": "ny_open_0730_0830_denver",
                        "direction": "both",
                        "params": {
                            "orb_minutes": 5,
                            "sweep_min_ticks": 4,
                            "reclaim_within_bars": 2,
                            "fill_mode": "body_midpoint",
                            "stop_ticks": 24,
                            "target_ticks": 80,
                        },
                        "mechanism": (
                            "An opening range breakout that cannot hold beyond "
                            "the range traps early breakout participants and "
                            "reclaim forces late entries to exit."
                        ),
                        "revival_reason": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_author_from_intake(
        author_json=author_json,
        db_path=ledger_db,
        max_proposals=1,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["report"]["accepted"] == 1
    repo = get_repository(ledger_db)
    try:
        assert repo.count_hypotheses_by_family() == {}
    finally:
        repo.close()


def test_load_author_json_accepts_utf8_bom(tmp_path: Path) -> None:
    author_json = tmp_path / "author.json"
    author_json.write_text(
        '\ufeff{"proposals": [{"family": "orb_failure_reclaim"}]}',
        encoding="utf-8",
    )

    loaded = json.loads(load_author_json(author_json))

    assert loaded["proposals"] == [{"family": "orb_failure_reclaim"}]


def test_author_from_intake_registers_when_not_dry_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    from ta_foundation.agent.roles import hypothesis_author
    from ta_foundation.agent.tools.write import author_probe

    monkeypatch.setattr(
        hypothesis_author,
        "INBOX_ROOT",
        tmp_path / "runs" / "inbox",
    )
    monkeypatch.setattr(
        author_probe,
        "GENERATED_PROBE_DIR",
        tmp_path / "discovery" / "generated",
    )
    ledger_db = tmp_path / "ledger.db"
    get_repository(ledger_db).close()
    author_json = tmp_path / "author.json"
    author_json.write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "family": "overnight_high_low_sweep_reclaim",
                        "instrument": "NQ",
                        "timeframe": "5m",
                        "session_window": "ny_open_0730_0830_denver",
                        "direction": "both",
                        "params": {
                            "level_type": "both",
                            "sweep_min_ticks": 6,
                            "reclaim_within_bars": 3,
                            "stop_ticks": 28,
                            "target_ticks": 90,
                        },
                        "mechanism": (
                            "Overnight highs and lows concentrate resting "
                            "liquidity before RTH open, and a sweep followed "
                            "by reclaim can trap breakout participants."
                        ),
                        "revival_reason": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_author_from_intake(
        author_json=author_json,
        db_path=ledger_db,
        max_proposals=1,
        dry_run=False,
    )

    assert result["dry_run"] is False
    assert result["report"]["accepted"] == 1
    repo = get_repository(ledger_db)
    try:
        counts = repo.count_hypotheses_by_family()
        assert counts == {"overnight_high_low_sweep_reclaim": 1}
    finally:
        repo.close()
