from __future__ import annotations

from typing import Any, Dict, List
import html


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _report_cfg_map(report_config: Any) -> Dict[str, Any]:
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


def render_anchor_interaction_tp_sl_spec(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")

    _ = packages, options, market

    # ---------------------------------------
    # LOAD CONFIG
    # ---------------------------------------


    cfg = options.get("anchor_interaction")

    if not cfg:
        report_cfg = _report_cfg_map(report_config)
        cfg = _cfg_get(report_cfg, "anchor_interaction", {}) or {}

    tp_sl = cfg.get("tp_sl", {}) or {}
    folds = tp_sl.get("folds", {}) or {}
    derived_validation: Dict[str, Any] = {}
    if packages:
        first_pkg = next(iter(packages.values()))
        try:
            md = getattr(first_pkg, "metadata", {}) or {}
            derived = md.get("derived", {}) or {}
            ai = derived.get("anchor_interaction", {}) or {}
            diagnostics = ai.get("diagnostics", {}) or {}
            derived_validation = diagnostics.get("validation", {}) or {}
        except Exception:
            derived_validation = {}
    html_out: List[str] = ["<h3>MA Anchor TP/SL Specification</h3>"]

    if not tp_sl:
        html_out.append("<div class='muted'>No <code>anchor_interaction.tp_sl</code> block configured.</div>")
        return "\n".join(html_out)

    rows = [
        ["enabled", "yes" if bool(_cfg_get(tp_sl, "enabled", False)) else "no"],
        ["unit", _safe_str(_cfg_get(tp_sl, "unit", "atr"))],
        ["tp_grid", _safe_str(", ".join(str(x) for x in (_cfg_get(tp_sl, "tp_grid", []) or [])))],
        ["sl_grid", _safe_str(", ".join(str(x) for x in (_cfg_get(tp_sl, "sl_grid", []) or [])))],
    ]
    html_out.append(_table(["parameter", "value"], rows))

    val_rows = [
        [
            "fold_mode",
            _safe_str(_cfg_get(derived_validation, "fold_mode", _cfg_get(folds, "mode", "blocked_kfold"))),
        ],
        [
            "min_train_segments",
            _safe_int(_cfg_get(derived_validation, "min_train_segments", _cfg_get(folds, "min_train_segments", 120))),
        ],
        [
            "min_test_segments",
            _safe_int(_cfg_get(derived_validation, "min_test_segments", _cfg_get(folds, "min_test_segments", 40))),
        ],
    ]
    html_out.append("<h4 style='margin-top:16px'>Validation Controls</h4>")
    html_out.append(_table(["parameter", "value"], val_rows))

    html_out.append("<h4 style='margin-top:16px'>Interpretation</h4>")
    html_out.append(
        "<ul style='margin-left:18px'>"
        "<li>These values define the candidate search space for future structural TP/SL analysis.</li>"
        "<li>This section is declarative only; it does not score the grid in the renderer.</li>"
        "</ul>"
    )

    return "\n".join(html_out)