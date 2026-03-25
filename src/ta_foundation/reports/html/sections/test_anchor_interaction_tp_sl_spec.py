from __future__ import annotations

from dataclasses import dataclass, field

from ta_foundation.reports.html.sections.anchor_interaction_tp_sl_spec import render_anchor_interaction_tp_sl_spec


@dataclass
class _ReportConfig:
    raw: dict = field(default_factory=dict)


def test_anchor_interaction_tp_sl_spec_reads_top_level_anchor_config_from_report_config_raw() -> None:
    html = render_anchor_interaction_tp_sl_spec(
        {
            "packages": {},
            "options": {},
            "report_config": _ReportConfig(
                raw={
                    "anchor_interaction": {
                        "tp_sl": {
                            "enabled": True,
                            "unit": "atr",
                            "tp_grid": [0.8, 1.0, 1.3],
                            "sl_grid": [0.6, 0.8],
                            "folds": {"mode": "anchored_walk_forward", "min_train_segments": 150, "min_test_segments": 50},
                        }
                    }
                }
            ),
        }
    )

    assert "MA Anchor TP/SL Specification" in html
    assert "0.8, 1.0, 1.3" in html
    assert "0.6, 0.8" in html
    assert "anchored_walk_forward" in html
    assert "No <code>anchor_interaction.tp_sl</code> block configured." not in html
