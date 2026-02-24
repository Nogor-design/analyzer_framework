from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Dict

import yaml

from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.registry import SECTION_REGISTRY

from ta_foundation.marketdata.store import MarketDataStore


@dataclass
class ReportConfig:
    title: str
    output_filename: str
    sections: list[dict[str, Any]]  # each has id + optional title + options
    raw: dict[str, Any]             # full YAML (merged with defaults)


DEFAULT_CONFIG: dict[str, Any] = {
    "report": {
        "title": "Strategy Comparison Report",
        "output_filename": "comparison_report.html",
        "embedded_images": True,
        "timezone": "America/Denver",
    },
    "sections": [
        {"id": "comparison_overview"},
        {"id": "equity_curve_comparison"},
        {"id": "run_kpi_cards"},
        {"id": "run_snapshot_clipboard"},
    ],
    # NOTE: leave room for top-level feature blocks like:
    # "pattern_engine": {...}
}


def _deepish_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Minimal merge semantics:
      - merge 'report' dict keys
      - replace 'sections' entirely if provided
      - preserve any other top-level keys from override (pattern_engine, etc.)
    """
    out = dict(base)

    # merge report
    base_report = dict(base.get("report", {}) or {})
    override_report = dict(override.get("report", {}) or {})
    base_report.update(override_report)
    out["report"] = base_report

    # sections replace if present
    if "sections" in override and override.get("sections") is not None:
        out["sections"] = override.get("sections")
    else:
        out["sections"] = base.get("sections")

    # preserve all other top-level keys from override
    for k, v in (override or {}).items():
        if k in ("report", "sections"):
            continue
        out[k] = v

    return out


def load_report_config(path: Optional[Path]) -> ReportConfig:
    cfg_raw: dict[str, Any] = dict(DEFAULT_CONFIG)

    if path is not None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw:
            cfg_raw = _deepish_merge(DEFAULT_CONFIG, raw)

    report = cfg_raw.get("report", {}) or {}
    sections = cfg_raw.get("sections", DEFAULT_CONFIG["sections"])

    return ReportConfig(
        title=str(report.get("title", DEFAULT_CONFIG["report"]["title"])),
        output_filename=str(report.get("output_filename", DEFAULT_CONFIG["report"]["output_filename"])),
        sections=list(sections) if isinstance(sections, list) else list(DEFAULT_CONFIG["sections"]),
        raw=cfg_raw,
    )


def build_report_from_config(packages, cfg: ReportConfig, market: Optional[MarketDataStore] = None):
    """
    Returns: (html_string, output_filename)

    IMPORTANT CONTEXT CONTRACT:
      - ctx["options"] is SECTION-LOCAL options (kept as-is for backwards compatibility)
      - ctx["all_options"] is the FULL merged YAML config (top-level blocks like pattern_engine live here)
    """
    sections: list[HtmlSection] = []

    # base ctx is whatever your builder/sections expect
    base_ctx: Dict[str, Any] = {
        "packages": packages,
        "market": market,
        "report_config": cfg,
        "all_options": cfg.raw,  # ✅ full YAML (includes pattern_engine)
    }

    # ---- Run Pattern Engine once BEFORE rendering (analysis phase) ----
    try:
        from ta_foundation.analysis.pattern_engine.orchestrator import compute_and_attach_pattern_engine

        pe_opts = (cfg.raw.get("pattern_engine") or {}) if isinstance(cfg.raw, dict) else {}
        compute_and_attach_pattern_engine(packages, market, options=pe_opts)
    except Exception as e:
        # Never crash report generation because pattern engine failed.
        # Store error on each package so the report can show it.
        for _, pkg in (packages or {}).items():
            md = getattr(pkg, "metadata", None)
            if md is None:
                pkg.metadata = {}
                md = pkg.metadata
            md.setdefault("derived", {})
            md["derived"]["pattern_engine"] = {
                "version": "pe_v1",
                "disabled": True,
                "reason": f"pattern_engine_exception: {type(e).__name__}: {e}",
            }

    # ---- Deduplicate section ids while preserving order ----
    seen: set[str] = set()
    sections_cfg: list[dict[str, Any]] = []
    duplicates: list[str] = []

    for s in cfg.sections:
        if not isinstance(s, dict):
            continue
        sid = (s.get("id") or "").strip()
        if not sid:
            continue
        if sid in seen:
            duplicates.append(sid)
            continue
        seen.add(sid)
        sections_cfg.append(s)

    if duplicates:
        print(f"[ta_foundation] WARNING: Duplicate section ids ignored: {duplicates}")

    # ---- Build HtmlSection list exactly once ----
    for s in sections_cfg:
        sid = s["id"]
        if sid not in SECTION_REGISTRY:
            raise KeyError(f"Unknown section id in report config: {sid!r}")

        reg = SECTION_REGISTRY[sid]
        section_options = s.get("options", {}) or {}

        sections.append(
            HtmlSection(
                id=sid,
                title=s.get("title") or reg.default_title,
                render_fn=reg.render_fn,
                options=section_options,  # ✅ section-local options
            )
        )

    builder = HtmlReportBuilder(report_title=cfg.title, sections=sections)
    html = builder.build(base_ctx)
    return html, cfg.output_filename