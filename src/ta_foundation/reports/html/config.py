from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

import yaml

from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.pattern_engine.orchestrator import compute_and_attach_pattern_engine
from ta_foundation.analysis.ma_structure.orchestrator import run_anchor_interaction_analysis

@dataclass
class ReportConfig:
    title: str
    output_filename: str
    sections: list[dict[str, Any]]  # each has id + optional title + options
    raw: dict[str, Any]             # merged YAML (defaults + overrides)


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
    # room for other top-level blocks (pattern_engine, etc.)
}


def _deepish_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Minimal merge semantics:
      - merge 'report' dict keys
      - replace 'sections' entirely if provided
      - preserve any other top-level keys from override (pattern_engine, etc.)
    """
    out = dict(base)

    base_report = dict(base.get("report", {}) or {})
    override_report = dict((override or {}).get("report", {}) or {})
    base_report.update(override_report)
    out["report"] = base_report

    if isinstance(override, dict) and "sections" in override and override.get("sections") is not None:
        out["sections"] = override.get("sections")
    else:
        out["sections"] = base.get("sections")

    for k, v in (override or {}).items():
        if k in ("report", "sections"):
            continue
        out[k] = v

    return out


def _normalize_multi_report_root(raw_root: dict[str, Any]) -> Tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Multi-report YAML supported shapes:

    A) preferred:
      reports:
        - report: {...}
          sections: [...]
        - report: {...}
          sections: [...]

    B) your current myReports.yaml nesting:
      reports:
        - report:
            title: ...
            output_filename: ...
            sections: [...]   # <-- nested
        - report:
            ...

    Returns:
      shared_override: applied to each report item (top-level blocks aside from reports)
      normalized_items: each item normalized so sections are top-level
    """
    shared_override = dict(raw_root or {})
    report_items = shared_override.pop("reports", None)

    if not isinstance(report_items, list):
        return {}, []

    shared_override = {k: v for k, v in shared_override.items() if v is not None}

    normalized_items: list[dict[str, Any]] = []
    for item in report_items:
        if not isinstance(item, dict):
            continue

        it = dict(item)
        rep = it.get("report")

        # Lift nested report.sections -> top-level sections
        if isinstance(rep, dict):
            rep = dict(rep)
            if "sections" not in it and isinstance(rep.get("sections"), list):
                it["sections"] = rep.pop("sections")
            it["report"] = rep

        normalized_items.append(it)

    return shared_override, normalized_items


def load_report_configs(path: Optional[Path]) -> List[ReportConfig]:
    """
    Auto-detect single vs multi YAML:
      - single: top-level report/sections => returns [ReportConfig]
      - multi: top-level reports: [...]  => returns N ReportConfigs
    """
    if path is None:
        return [load_report_config(None)]

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        return [load_report_config(None)]

    if isinstance(raw.get("reports"), list):
        shared_override, report_items = _normalize_multi_report_root(raw)
        configs: list[ReportConfig] = []

        for item in report_items:
            merged_override = dict(shared_override)
            merged_override.update(item)

            cfg_raw = _deepish_merge(DEFAULT_CONFIG, merged_override)
            report = cfg_raw.get("report", {}) or {}
            sections = cfg_raw.get("sections", DEFAULT_CONFIG["sections"])

            configs.append(
                ReportConfig(
                    title=str(report.get("title", DEFAULT_CONFIG["report"]["title"])),
                    output_filename=str(report.get("output_filename", DEFAULT_CONFIG["report"]["output_filename"])),
                    sections=list(sections) if isinstance(sections, list) else list(DEFAULT_CONFIG["sections"]),
                    raw=cfg_raw,
                )
            )

        # If someone provided reports: [] accidentally
        return configs if configs else [load_report_config(None)]

    # Single-report legacy
    return [load_report_config(path)]


def load_report_config(path: Optional[Path]) -> ReportConfig:
    """
    Legacy single-report loader (kept for backward compatibility).
    """
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


def build_report_from_config(
    packages: Dict[str, Any],
    cfg: ReportConfig,
    market: Optional[MarketDataStore] = None,
):
    """
    Returns: (html_string, output_filename)

    CONTEXT CONTRACT:
      - ctx["options"] is SECTION-LOCAL options (set by HtmlReportBuilder per section)
      - ctx["all_options"] is the FULL merged YAML config (top-level blocks like pattern_engine live here)
    """
    base_ctx: Dict[str, Any] = {
        "packages": packages,
        "market": market,
        "report_config": cfg,
        "all_options": cfg.raw,  # full merged config
    }

    # Pattern engine compute (analysis phase). Never crash report generation.
    pe_opts = (cfg.raw.get("pattern_engine") or {}) if isinstance(cfg.raw, dict) else {}
    # --------------------------------------------------------
    # MA Anchor interaction engine (analysis phase)
    # --------------------------------------------------------

    ai_opts: Dict[str, Any] = {}
    if isinstance(cfg.raw, dict):
        top_level = cfg.raw.get("anchor_interaction")
        if isinstance(top_level, dict):
            ai_opts = top_level

    if not ai_opts.get("enabled"):
        for s in cfg.sections or []:
            if not isinstance(s, dict):
                continue
            opts = s.get("options") or {}
            if not isinstance(opts, dict):
                continue
            sec_ai = opts.get("anchor_interaction")
            if isinstance(sec_ai, dict) and sec_ai.get("enabled"):
                ai_opts = sec_ai
                break

    if ai_opts.get("enabled"):

        try:

            for run_id, pkg in (packages or {}).items():
                run_anchor_interaction_analysis(
                    pkg=pkg,
                    market=market,
                    options=ai_opts,
                )

        except Exception as e:

            reason = f"anchor_interaction_exception: {type(e).__name__}: {e}"

            for _, pkg in (packages or {}).items():

                md = getattr(pkg, "metadata", None)

                if md is None:
                    pkg.metadata = {}
                    md = pkg.metadata

                if "derived" not in md or md["derived"] is None:
                    md["derived"] = {}

                md["derived"]["anchor_interaction"] = {
                    "version": "ai_v1",
                    "disabled": True,
                    "reason": reason,
                    "diagnostics": {"ok": False, "reason": reason},
                    "artifacts": {},
                }
    try:
        compute_and_attach_pattern_engine(packages=packages, market=market, options=pe_opts)
    except Exception as e:
        reason = f"pattern_engine_exception: {type(e).__name__}: {e}"
        for _, pkg in (packages or {}).items():
            md = getattr(pkg, "metadata", None)
            if md is None:
                pkg.metadata = {}
                md = pkg.metadata
            if "derived" not in md or md["derived"] is None:
                md["derived"] = {}
            md["derived"]["pattern_engine"] = {
                "version": "pe_v1",
                "disabled": True,
                "reason": reason,
                "diagnostics": {"ok": False, "reason": reason},
                "artifacts": {},
            }

    # Deduplicate section ids preserving order
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

    # Build HtmlSection list
    sections: list[HtmlSection] = []
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
                options=section_options,  # section-local options
            )
        )

    builder = HtmlReportBuilder(report_title=cfg.title, sections=sections)
    html = builder.build(base_ctx)
    return html, cfg.output_filename