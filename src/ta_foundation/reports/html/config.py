from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.registry import SECTION_REGISTRY


@dataclass
class ReportConfig:
    title: str
    output_filename: str
    sections: list[dict[str, Any]]  # each has id + optional title


DEFAULT_CONFIG = {
    "report": {
        "title": "Strategy Comparison Report",
        "output_filename": "comparison_report.html",
        "embedded_images": True,
        "timezone": "America/Denver",
    },
    "sections": [
        {"id": "comparison_overview"},
        {"id": "equity_curve_comparison"},
        {"id": "run_metadata_cards"},
        {"id": "run_kpi_cards"},
        {"id": "drawdown_curve"},
    ],
}


def load_report_config(path: Optional[Path]) -> ReportConfig:
    cfg = DEFAULT_CONFIG
    if path is not None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw:
            # shallow merge on keys we care about
            merged = dict(DEFAULT_CONFIG)
            merged_report = dict(DEFAULT_CONFIG.get("report", {}))
            merged_report.update(raw.get("report", {}) or {})
            merged["report"] = merged_report
            merged["sections"] = raw.get("sections", DEFAULT_CONFIG["sections"])
            cfg = merged

    report = cfg["report"]
    sections = cfg["sections"]

    return ReportConfig(
        title=str(report.get("title", DEFAULT_CONFIG["report"]["title"])),
        output_filename=str(report.get("output_filename", DEFAULT_CONFIG["report"]["output_filename"])),
        sections=list(sections),
    )


def build_report_from_config(packages: dict, cfg: ReportConfig) -> tuple[str, str]:
    """
    Returns: (html_string, output_filename)
    """
    sections: list[HtmlSection] = []

    for s in cfg.sections:
        sid = s.get("id")
        if not sid:
            continue
        if sid not in SECTION_REGISTRY:
            raise KeyError(f"Unknown section id in report config: {sid!r}")

        reg = SECTION_REGISTRY[sid]

        sections.append(
            HtmlSection(
                id=sid,
                title=s.get("title") or reg.default_title,
                render_fn=reg.render_fn,
                options=s.get("options") or {},   # ✅ CRITICAL FIX
            )
        )

    builder = HtmlReportBuilder(report_title=cfg.title, sections=sections)
    html = builder.build({"packages": packages})
    return html, cfg.output_filename

