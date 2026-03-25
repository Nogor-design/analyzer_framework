from __future__ import annotations

from dataclasses import dataclass, field

from ta_foundation.reports.html.sections.anchor_interaction_diagnostics import render_anchor_interaction_diagnostics


@dataclass
class _Pkg:
    metadata: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)


def test_anchor_interaction_diagnostics_surfaces_analysis_failure_state() -> None:
    pkg = _Pkg(
        metadata={
            "derived": {
                "anchor_interaction": {
                    "engine": {"instrument": "NQ", "contract": "H25", "timeframe": "1m"},
                    "artifacts": {"anchors": {"type": "parquet", "path": None}},
                    "reason": "anchor_interaction_exception: AttributeError: 'tuple' object has no attribute 'empty'",
                    "diagnostics": {"n_segments": 0, "pct_censored": 0.0, "warnings": []},
                }
            }
        }
    )

    html = render_anchor_interaction_diagnostics({"packages": {"BronzeApolloGod": pkg}, "options": {}})

    assert "analysis_failed" in html
    assert "NQ / H25 / 1m" in html
    assert "tuple" in html
    assert "empty" in html
    assert "How to read failures" in html
