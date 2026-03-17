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


def render_anchor_interaction_anchor_matrix(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")

    _ = packages, market

    # ---------------------------------------
    # LOAD CONFIG
    # ---------------------------------------

    cfg = options.get("anchor_interaction")

    if not cfg:
        report_cfg = _report_cfg_map(report_config)
        cfg = _cfg_get(report_cfg, "anchor_interaction", {}) or {}

    anchors = cfg.get("anchors", []) or []

    if not anchors:
        return (
            "<h3>MA Anchor Matrix</h3>"
            "<div class='muted'>No anchors configured in <code>anchor_interaction.anchors</code>.</div>"
        )

    show_entry_exit = bool(options.get("show_entry_exit", True))
    show_role = bool(options.get("show_role", True))

    rows: List[List[str]] = []
    for i, a in enumerate(anchors, start=1):
        if not isinstance(a, dict):
            continue

        family = _cfg_get(a, "family")
        length = _cfg_get(a, "length")
        source = _cfg_get(a, "source", "close")

        role = "structure"
        try:
            length_i = int(length)
            if length_i <= 25:
                role = "fast / reactive"
            elif length_i <= 75:
                role = "intermediate"
            else:
                role = "slow / structural"
        except Exception:
            pass

        row = [str(i), _safe_str(family), _safe_int(length), _safe_str(source)]

        if show_role:
            row.append(_safe_str(role))
        if show_entry_exit:
            row.extend([
                _safe_str(_cfg_get(cfg, "cross_mode", "close")),
                _safe_str(_cfg_get(cfg, "exit_mode", "close")),
            ])

        rows.append(row)

    headers = ["#", "family", "length", "source"]
    if show_role:
        headers.append("intended_role")
    if show_entry_exit:
        headers.extend(["entry_logic", "exit_logic"])

    return "\n".join([
        "<h3>MA Anchor Matrix</h3>",
        "<div class='muted'>Configured anchor set from YAML. This is a structural preview, not computed analytics.</div>",
        _table(headers, rows),
    ])