from __future__ import annotations

from typing import Any, Dict, List
import html

import pandas as pd


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _report_cfg_map(report_config: Any) -> Dict[str, Any]:
    """
    Supports either:
    - plain dict-like report config
    - object-style ReportConfig with attributes
    """
    if report_config is None:
        return {}
    if isinstance(report_config, dict):
        return report_config

    out: Dict[str, Any] = {}
    for k in dir(report_config):
        if k.startswith("_"):
            continue
        try:
            v = getattr(report_config, k)
        except Exception:
            continue
        if callable(v):
            continue
        out[k] = v
    return out


def _safe_str(x: Any) -> str:
    if x is None:
        return "—"
    s = str(x).strip()
    return html.escape(s) if s else "—"


def _safe_int(x: Any) -> str:
    try:
        if x is None or x == "":
            return "—"
        return str(int(x))
    except Exception:
        return html.escape(str(x))


def _safe_float(x: Any, digits: int = 2) -> str:
    try:
        if x is None or x == "":
            return "—"
        v = float(x)
        if pd.isna(v):
            return "—"
        return f"{v:.{digits}f}"
    except Exception:
        return html.escape(str(x))


def _safe_bool(x: Any) -> str:
    return "yes" if bool(x) else "no"


def _table(headers: List[str], rows: List[List[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = "".join(f"<td>{cell}</td>" for cell in row)
        body.append(f"<tr>{tds}</tr>")
    return (
        "<div style='overflow-x:auto'>"
        "<table class='table table-sm'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )


def _artifact_presence(packages: Dict[str, Any]) -> Dict[str, int]:
    n_runs = 0
    n_assets = 0
    n_meta = 0
    for _, pkg in packages.items():
        n_runs += 1
        assets = getattr(pkg, "assets", {}) or {}
        metadata = getattr(pkg, "metadata", {}) or {}
        if bool(assets.get("anchor_interaction")):
            n_assets += 1
        if bool((metadata.get("derived") or {}).get("anchor_interaction")):
            n_meta += 1
    return {
        "n_runs": n_runs,
        "n_assets": n_assets,
        "n_meta": n_meta,
    }


def render_anchor_interaction_overview(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")

    _ = market

    # ------------------------------------------------
    # CONFIG SOURCE
    # ------------------------------------------------
    # 1) section options
    # 2) report_config fallback
    # ------------------------------------------------

    cfg = options.get("anchor_interaction")

    if not cfg:
        report_cfg = _report_cfg_map(report_config)
        cfg = _cfg_get(report_cfg, "anchor_interaction", {}) or {}

    if not cfg:
        return (
            "<h3>MA Anchor Overview</h3>"
            "<div class='muted'>No top-level <code>anchor_interaction</code> block found in report configuration.</div>"
        )

    mode = str(_cfg_get(cfg, "mode", "market_only")).strip() or "market_only"
    anchors = _cfg_get(cfg, "anchors", []) or []
    tp_sl = _cfg_get(cfg, "tp_sl", {}) or {}
    presence = _artifact_presence(packages)

    show_presence = bool(options.get("show_presence", True))
    show_notes = bool(options.get("show_notes", True))

    html_out: List[str] = []
    html_out.append("<h3>MA Anchor Overview</h3>")
    html_out.append(
        "<div class='muted'>"
        "Renderer-only MA Anchor report shell. Parameters are read directly from "
        "<code>report.yaml</code> via <code>ctx['report_config']</code>."
        "</div>"
    )

    cards = [
        ("Enabled", _safe_bool(_cfg_get(cfg, "enabled", False))),
        ("Mode", _safe_str(mode)),
        ("Instrument", _safe_str(_cfg_get(cfg, "instrument"))),
        ("Contract", _safe_str(_cfg_get(cfg, "contract"))),
        ("Timeframe", _safe_str(_cfg_get(cfg, "timeframe", "1m"))),
        ("Anchors", _safe_int(len(anchors))),
        ("TP/SL Enabled", _safe_bool(_cfg_get(tp_sl, "enabled", False))),
        ("Runs Loaded", _safe_int(presence["n_runs"])),
    ]
    html_out.append("<div style='display:grid;grid-template-columns:repeat(4,minmax(180px,1fr));gap:10px;margin-top:12px;'>")
    for label, value in cards:
        html_out.append(
            "<div class='card' style='padding:10px;'>"
            f"<div class='muted' style='font-size:12px'>{html.escape(label)}</div>"
            f"<div style='font-size:18px;font-weight:600'>{value}</div>"
            "</div>"
        )
    html_out.append("</div>")

    rows = [
        ["cross_mode", _safe_str(_cfg_get(cfg, "cross_mode", "close"))],
        ["exit_mode", _safe_str(_cfg_get(cfg, "exit_mode", "close"))],
        ["recross_policy", _safe_str(_cfg_get(cfg, "recross_policy", "first_return"))],
        ["return_band_atr", _safe_float(_cfg_get(cfg, "return_band_atr", 0.0), 3)],
        ["return_band_ticks", _safe_float(_cfg_get(cfg, "return_band_ticks", 0.0), 3)],
        ["min_bars_after_entry", _safe_int(_cfg_get(cfg, "min_bars_after_entry", 1))],
        ["descriptive_sample_floor", _safe_int(_cfg_get(cfg, "descriptive_sample_floor", 100))],
        ["regime_sample_floor", _safe_int(_cfg_get(cfg, "regime_sample_floor", 75))],
    ]
    html_out.append("<h4 style='margin-top:16px'>Engine Controls</h4>")
    html_out.append(_table(["parameter", "value"], rows))

    if show_presence:
        rows = [[
            _safe_int(presence["n_runs"]),
            _safe_int(presence["n_assets"]),
            _safe_int(presence["n_meta"]),
        ]]
        html_out.append("<h4 style='margin-top:16px'>Attached Artifact Presence</h4>")
        html_out.append(_table(["runs_loaded", "runs_with_assets", "runs_with_metadata"], rows))

    if show_notes:
        html_out.append("<h4 style='margin-top:16px'>Interpretation</h4>")
        html_out.append(
            "<ul style='margin-left:18px'>"
            "<li><b>market_only</b> is sufficient for structural MA Anchor research.</li>"
            "<li><b>market_plus_runs</b> is only needed when mapping anchor behavior back to strategy results.</li>"
            "<li>This report shell will not fail if no anchor artifacts are attached yet.</li>"
            "</ul>"
        )

    return "\n".join(html_out)