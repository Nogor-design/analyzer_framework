from __future__ import annotations

from pathlib import Path

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.deployment_board import (
    build_deployment_board_insight,
    parse_deployment_board_text,
)
from ta_foundation.reports.html.sections.deployment_board_insight import (
    render_deployment_board_insight,
)
from ta_foundation.reports.html.sections.deployment_board_gods import (
    render_deployment_board_gods,
)
from ta_foundation.reports.html.sections.deployment_board_poster import (
    render_deployment_board_poster,
)
from ta_foundation.reports.text.export_deployment_board_text import (
    export_deployment_board_text,
)


def _pkg(run_id: str, day_pnls: dict[str, float], *, start_hour: int, duration_hour: int = 2, image_uri: str | None = None) -> AnalysisPackage:
    ordered_days = sorted(day_pnls.keys())
    pkg = AnalysisPackage(
        run_id=run_id,
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(ordered_days),
                "net_profit": [day_pnls[d] for d in ordered_days],
                "trade_count": [1 if day_pnls[d] != 0 else 0 for d in ordered_days],
            }
        ),
        settings=pd.DataFrame(
            [
                {"item": "Start_Time_(HH)", "value": start_hour},
                {"item": "Start_Time_(mm)", "value": 0},
                {"item": "Duration_Time_(HH)", "value": duration_hour},
                {"item": "Duration_Time_(mm)", "value": 0},
            ]
        ),
    )
    if image_uri:
        pkg.metadata = {"derived": {"run_image_uri": image_uri}}
    return pkg


def _board_text() -> str:
    return """Team,

Current regime is Breakout 61 percent and Regression 39 percent. That makes this a Gods-first board. Deployment remains controlled, with account posture at 8 out of 20 accounts.

Primary Bots

RiseAlpha-NQ | 12:00 AM-2:00 AM CO | Trigger Odds 62% | Success Odds 74% | R/R 1:1
Reason: Early London lead.

PrimingBeta-NQ | 6:00 AM-8:00 AM CO | Trigger Odds 58% | Success Odds 76% | R/R 0.4:1
Reason: Strong confirmation.

Secondary Bots

CoilingGamma-NQ | 8:00 AM-10:00 AM CO | Trigger Odds 49% | Success Odds 70% | R/R 0.4:1
Reason: Stable support.

Reserve

ClosingDelta-NQ | 2:00 PM-4:00 PM CO | Trigger Odds 31% | Success Odds 67% | R/R 0.4:1
Reason: Afternoon fallback.

Deployment Law

- Keep adds controlled.
- Reduce pressure if price falls back.

Summary

This is still a bullish board with controlled adds.

Hermes
"""


def test_parse_deployment_board_text_extracts_sections() -> None:
    parsed = parse_deployment_board_text(_board_text())

    assert parsed["breakout_pct"] == 61.0
    assert parsed["regression_pct"] == 39.0
    assert parsed["account_posture"] == {"active": 8, "capacity": 20}
    assert len(parsed["sections"]["primary"]) == 2
    assert parsed["sections"]["secondary"][0]["run_id"] == "CoilingGamma-NQ"
    assert parsed["deployment_law"][0] == "Keep adds controlled."
    assert parsed["signer"] == "Hermes"


def test_build_deployment_board_insight_enriches_rows(tmp_path: Path) -> None:
    board_path = tmp_path / "board.txt"
    board_path.write_text(_board_text(), encoding="utf-8")

    packages = {
        "RiseAlpha-NQ": _pkg("RiseAlpha-NQ", {"2026-04-09": 200.0, "2026-04-10": 300.0, "2026-04-13": 500.0, "2026-04-14": 700.0, "2026-04-15": 100.0}, start_hour=0),
        "PrimingBeta-NQ": _pkg("PrimingBeta-NQ", {"2026-04-09": 100.0, "2026-04-10": 150.0, "2026-04-13": 250.0, "2026-04-14": 350.0, "2026-04-15": 80.0}, start_hour=6),
        "CoilingGamma-NQ": _pkg("CoilingGamma-NQ", {"2026-04-09": 50.0, "2026-04-10": 70.0, "2026-04-13": 90.0, "2026-04-14": 110.0, "2026-04-15": 60.0}, start_hour=8),
    }

    board = build_deployment_board_insight(
        packages,
        board_text_path=str(board_path),
        as_of=pd.Timestamp("2026-04-15").date(),
        strip_days=5,
    )

    assert board["summary"]["top_pick"]["run_id"] == "RiseAlpha-NQ"
    assert board["summary"]["strongest_support"]["run_id"] == "RiseAlpha-NQ"
    assert board["rows"][0]["recent10"] is not None
    assert board["rows"][0]["today_profit"] == 100.0
    assert board["rows"][0]["today_status"] == "WIN"
    assert board["rows"][0]["stackability"]["status"] == "unknown"
    assert any(row["stackability"]["status"] == "safe" for row in board["rows"][1:])


def test_render_and_text_export_include_board_content(tmp_path: Path) -> None:
    board_path = tmp_path / "board.txt"
    board_path.write_text(_board_text(), encoding="utf-8")

    packages = {
        "RiseAlpha-NQ": _pkg("RiseAlpha-NQ", {"2026-04-09": 200.0, "2026-04-10": 300.0, "2026-04-13": 500.0, "2026-04-14": 700.0, "2026-04-15": 100.0}, start_hour=0),
        "PrimingBeta-NQ": _pkg("PrimingBeta-NQ", {"2026-04-09": 100.0, "2026-04-10": 150.0, "2026-04-13": 250.0, "2026-04-14": 350.0, "2026-04-15": 80.0}, start_hour=6),
    }

    html = render_deployment_board_insight(
        {
            "packages": packages,
            "options": {
                "board_text_path": str(board_path),
                "as_of_date": "2026-04-15",
                "strip_days": 5,
            },
        }
    )

    assert "Deployment Board Insight" in html
    assert "Per-Bot Reasons" in html
    assert "Original Board Text" in html
    assert "RiseAlpha-NQ" in html
    assert "<th>Today</th>" in html
    assert "<th>Trigger</th>" not in html
    assert "<th>Success</th>" not in html
    assert "<th>Stackability</th>" not in html
    assert "Success 74%" in html

    out_path = tmp_path / "deployment-board.txt"
    export_deployment_board_text(
        packages,
        out_path,
        options={"board_text_path": str(board_path), "as_of_date": "2026-04-15"},
    )

    text = out_path.read_text(encoding="utf-8")
    assert "Top Pick:" in text
    assert "DEPLOYMENT LAW" in text
    assert "ORIGINAL BOARD TEXT" in text
    assert "RiseAlpha-NQ" in text
    assert "Today +100" in text


def test_render_deployment_board_gods_includes_image_forward_cards(tmp_path: Path) -> None:
    board_path = tmp_path / "board.txt"
    board_path.write_text(_board_text(), encoding="utf-8")

    packages = {
        "RiseAlpha-NQ": _pkg(
            "RiseAlpha-NQ",
            {"2026-04-09": 200.0, "2026-04-10": 300.0, "2026-04-13": 500.0, "2026-04-14": 700.0, "2026-04-15": 100.0},
            start_hour=0,
            image_uri="data:image/png;base64,AAAA",
        ),
        "PrimingBeta-NQ": _pkg(
            "PrimingBeta-NQ",
            {"2026-04-09": 100.0, "2026-04-10": 150.0, "2026-04-13": 250.0, "2026-04-14": 350.0, "2026-04-15": 80.0},
            start_hour=6,
        ),
    }

    html = render_deployment_board_gods(
        {
            "packages": packages,
            "options": {
                "board_text_path": str(board_path),
                "as_of_date": "2026-04-15",
                "strip_days": 5,
            },
        }
    )

    assert "Deployment Board Pantheon" in html
    assert "Primary Gods" in html
    assert 'src="data:image/png;base64,AAAA"' in html
    assert "Today +100" in html or "Today WIN" in html


def test_render_deployment_board_poster_includes_poster_sections(tmp_path: Path) -> None:
    board_path = tmp_path / "board.txt"
    board_path.write_text(_board_text(), encoding="utf-8")

    packages = {
        "RiseAlpha-NQ": _pkg(
            "RiseAlpha-NQ",
            {"2026-04-09": 200.0, "2026-04-10": 300.0, "2026-04-13": 500.0, "2026-04-14": 700.0, "2026-04-15": 100.0},
            start_hour=0,
        ),
        "PrimingBeta-NQ": _pkg(
            "PrimingBeta-NQ",
            {"2026-04-09": 100.0, "2026-04-10": 150.0, "2026-04-13": 250.0, "2026-04-14": 350.0, "2026-04-15": 80.0},
            start_hour=6,
        ),
        "CoilingGamma-NQ": _pkg(
            "CoilingGamma-NQ",
            {"2026-04-09": 50.0, "2026-04-10": 70.0, "2026-04-13": 90.0, "2026-04-14": 110.0, "2026-04-15": 60.0},
            start_hour=8,
        ),
        "ClosingDelta-NQ": _pkg(
            "ClosingDelta-NQ",
            {"2026-04-09": -10.0, "2026-04-10": 40.0, "2026-04-13": 10.0, "2026-04-14": 20.0, "2026-04-15": 5.0},
            start_hour=14,
        ),
    }

    html = render_deployment_board_poster(
        {
            "packages": packages,
            "options": {
                "board_text_path": str(board_path),
                "as_of_date": "2026-04-15",
                "strip_days": 5,
                "timeframe_label": "1-MINUTE",
            },
        }
    )

    assert "ARES DEPLOYMENT CARD" in html
    assert "POST-MEETING RECOMMENDED BOT DEPLOYMENT" in html
    assert "PRIMARY BOTS (2)" in html
    assert "SECONDARY BOTS (1)" in html
    assert "RESERVE BOTS (1)" in html
    assert "DEPLOYMENT LAW" in html.upper()
    assert "SUMMARY" in html.upper()
    assert "RiseAlpha-NQ".upper() in html
    assert "PrimingBeta-NQ".upper() in html
