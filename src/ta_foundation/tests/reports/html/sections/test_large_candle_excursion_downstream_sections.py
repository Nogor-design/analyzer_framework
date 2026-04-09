from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_summary import render_large_candle_excursion_discovery_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_executive_summary import render_large_candle_excursion_findings_executive_summary


def test_findings_section_renders_payload() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "enabled": True,
                    "has_source": True,
                    "executive_summary": ["Best reverse setup found."],
                }
            }
        },
    )
    html = render_large_candle_excursion_findings_executive_summary({"packages": {"run1": pkg}, "options": {}})
    assert "Executive Summary" in html
    assert "Best reverse setup found." in html


def test_discovery_section_truthful_missing_source_state() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_discovery": {
                    "enabled": True,
                    "has_source": False,
                    "message": "source analytics missing",
                }
            }
        },
    )
    html = render_large_candle_excursion_discovery_summary({"packages": {"run1": pkg}, "options": {}})
    assert "source analytics missing" in html
