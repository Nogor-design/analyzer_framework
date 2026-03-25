"""
MA Anchor / Anchor Interaction — Config Report (render-only)

STRICT:
- No disk IO
- No compute-heavy analytics
- Read only from:
  - ctx["report_config"]
  - ctx["options"] / legacy section options
  - pkg.assets["anchor_interaction"] (optional presence check only)
  - pkg.metadata["derived"]["anchor_interaction"] (optional presence check only)
"""

from __future__ import annotations

from typing import Any, Dict, List
import html

import pandas as pd


def _safe_str(x: Any) -> str:
    if x is None:
        return "—"
    s = str(x)
    return html.escape(s) if s.strip() else "—"


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


def _bool_label(x: Any) -> str:
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


def _anchor_presence_summary(packages: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for run_id, pkg in packages.items():
        assets = getattr(pkg, "assets", {}) or {}
        metadata = getattr(pkg, "metadata", {}) or {}

        ai_assets = assets.get("anchor_interaction") or {}
        ai_meta = (metadata.get("derived") or {}).get("anchor_interaction") or {}

        rows.append(
            {
                "run_id": run_id,
                "assets_attached": bool(ai_assets),
                "metadata_attached": bool(ai_meta),
                "asset_keys": ", ".join(sorted(ai_assets.keys())) if isinstance(ai_assets, dict) and ai_assets else "",
                "artifact_keys": ", ".join(sorted((ai_meta.get("artifacts") or {}).keys())) if isinstance(ai_meta, dict) else "",
            }
        )
    return pd.DataFrame(rows)


def render_anchor_interaction_config(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config") or {}

    # standardized pattern requested
    _ = market

    # read top-level engine config directly from report_config
    anchor_cfg = report_config.get("anchor_interaction") or {}

    if not anchor_cfg:
        return (
            "<h3>MA Anchor Configuration</h3>"
            "<div class='muted'>No top-level <code>anchor_interaction</code> block found in report configuration.</div>"
        )

    show_presence = bool(options.get("show_presence", True))
    show_anchor_table = bool(options.get("show_anchor_table", True))
    show_tp_sl = bool(options.get("show_tp_sl", True))
    show_validation = bool(options.get("show_validation", True))
    show_guardrails = bool(options.get("show_guardrails", True))

    anchors = anchor_cfg.get("anchors") or []
    tp_sl = anchor_cfg.get("tp_sl") or {}
    folds = tp_sl.get("folds") or {}

    html_out: List[str] = []
    html_out.append("<h3>MA Anchor Configuration</h3>")
    html_out.append(
        "<div class='muted'>"
        "This section is renderer-only. It reads MA Anchor parameters directly from "
        "<code>report.yaml</code> via <code>ctx['report_config']</code> and does not require pipeline changes."
        "</div>"
    )

    # Core engine summary
    engine_rows = [
        ["enabled", _bool_label(anchor_cfg.get("enabled", False))],
        ["instrument", _safe_str(anchor_cfg.get("instrument"))],
        ["contract", _safe_str(anchor_cfg.get("contract"))],
        ["timeframe", _safe_str(anchor_cfg.get("timeframe", "1m"))],
        ["cross_mode", _safe_str(anchor_cfg.get("cross_mode", "close"))],
        ["exit_mode", _safe_str(anchor_cfg.get("exit_mode", "close"))],
        ["recross_policy", _safe_str(anchor_cfg.get("recross_policy", "first_return"))],
        ["return_band_atr", _safe_float(anchor_cfg.get("return_band_atr", 0.0), 3)],
        ["return_band_ticks", _safe_float(anchor_cfg.get("return_band_ticks", 0.0), 3)],
        ["min_bars_after_entry", _safe_int(anchor_cfg.get("min_bars_after_entry", 1))],
        ["descriptive_sample_floor", _safe_int(anchor_cfg.get("descriptive_sample_floor", 100))],
        ["regime_sample_floor", _safe_int(anchor_cfg.get("regime_sample_floor", 75))],
    ]
    html_out.append("<h4 style='margin-top:14px'>Engine Summary</h4>")
    html_out.append(_table(["parameter", "value"], engine_rows))

    # Anchors
    if show_anchor_table:
        anchor_rows: List[List[str]] = []
        for i, a in enumerate(anchors, start=1):
            if not isinstance(a, dict):
                continue
            anchor_rows.append(
                [
                    str(i),
                    _safe_str(a.get("family")),
                    _safe_int(a.get("length")),
                    _safe_str(a.get("source", "close")),
                ]
            )

        html_out.append("<h4 style='margin-top:14px'>Anchor Set</h4>")
        if anchor_rows:
            html_out.append(_table(["#", "family", "length", "source"], anchor_rows))
        else:
            html_out.append("<div class='muted'>No anchors configured.</div>")

    # TP/SL
    if show_tp_sl:
        tp_grid = tp_sl.get("tp_grid") or []
        sl_grid = tp_sl.get("sl_grid") or []

        tp_rows = [
            ["enabled", _bool_label(tp_sl.get("enabled", False))],
            ["unit", _safe_str(tp_sl.get("unit", "atr"))],
            ["tp_grid", html.escape(", ".join(str(x) for x in tp_grid)) if tp_grid else "—"],
            ["sl_grid", html.escape(", ".join(str(x) for x in sl_grid)) if sl_grid else "—"],
        ]
        html_out.append("<h4 style='margin-top:14px'>TP / SL Grid</h4>")
        html_out.append(_table(["parameter", "value"], tp_rows))

    # Validation / folds
    if show_validation:
        validation_rows = [
            ["fold_mode", _safe_str(folds.get("mode", "—"))],
            ["min_train_segments", _safe_int(folds.get("min_train_segments"))],
            ["min_test_segments", _safe_int(folds.get("min_test_segments"))],
        ]
        html_out.append("<h4 style='margin-top:14px'>Validation Settings</h4>")
        html_out.append(_table(["parameter", "value"], validation_rows))

    # Guardrails
    if show_guardrails:
        guardrail_rows = [
            ["shared bars duplicated into package", "forbidden"],
            ["naive datetimes", "forbidden"],
            ["heavy analytics inside section", "forbidden"],
            ["report behavior when assets missing", "show config only / do not crash"],
        ]
        html_out.append("<h4 style='margin-top:14px'>Guardrails</h4>")
        html_out.append(_table(["rule", "status"], guardrail_rows))

    # Optional presence check of attached results
    if show_presence:
        html_out.append("<h4 style='margin-top:14px'>Attached Artifact Presence</h4>")
        pres = _anchor_presence_summary(packages)
        if pres.empty:
            html_out.append("<div class='muted'>No packages available.</div>")
        else:
            rows = []
            for _, r in pres.iterrows():
                rows.append(
                    [
                        html.escape(str(r.get("run_id", ""))),
                        _bool_label(r.get("assets_attached", False)),
                        _bool_label(r.get("metadata_attached", False)),
                        html.escape(str(r.get("asset_keys", "") or "—")),
                        html.escape(str(r.get("artifact_keys", "") or "—")),
                    ]
                )
            html_out.append(
                _table(
                    ["run_id", "assets_attached", "metadata_attached", "asset_keys", "artifact_keys"],
                    rows,
                )
            )

    return "\n".join(html_out)