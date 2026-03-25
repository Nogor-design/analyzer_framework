from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import html

import pandas as pd


def _get_pkg(packages: Dict[str, Any], run_id: Optional[str]) -> Tuple[Optional[str], Optional[Any]]:
    if not packages:
        return None, None
    if run_id and run_id in packages:
        return run_id, packages[run_id]
    first = next(iter(packages.items()))
    return first[0], first[1]


def _ai_meta(pkg: Any) -> Optional[Dict[str, Any]]:
    try:
        md = getattr(pkg, "metadata", None) or {}
        derived = md.get("derived", {}) or {}
        ai = derived.get("anchor_interaction")
        return ai if isinstance(ai, dict) else None
    except Exception:
        return None


def _ai_assets(pkg: Any) -> Dict[str, Any]:
    try:
        assets = getattr(pkg, "assets", None) or {}
        ai = assets.get("anchor_interaction", {}) or {}
        return ai if isinstance(ai, dict) else {}
    except Exception:
        return {}


def _df(ai_assets: Dict[str, Any], key: str) -> pd.DataFrame:
    v = ai_assets.get(key)
    return v if isinstance(v, pd.DataFrame) else pd.DataFrame()


def _safe_str(x: Any) -> str:
    if x is None:
        return "—"
    s = str(x).strip()
    return html.escape(s) if s else "—"


def _safe_float(x: Any, digits: int = 3) -> str:
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

def _analysis_status(ai_meta: Optional[Dict[str, Any]], ai_assets: Dict[str, Any]) -> str:
    if not isinstance(ai_meta, dict):
        return "not_run"

    reason = str(ai_meta.get("reason") or ((ai_meta.get("diagnostics") or {}).get("reason")) or "").strip()
    if reason:
        return "analysis_failed"

    if ai_assets:
        return "ok"

    return "metadata_only"


def _engine_target(ai_meta: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ai_meta, dict):
        return "—"

    engine = ai_meta.get("engine") or {}
    instrument = str(engine.get("instrument") or "").strip()
    contract = str(engine.get("contract") or "").strip()
    timeframe = str(engine.get("timeframe") or "").strip()

    parts = [p for p in [instrument, contract, timeframe] if p]
    return html.escape(" / ".join(parts)) if parts else "—"

def _html_table(df: pd.DataFrame, *, max_rows: int = 200, title: Optional[str] = None) -> str:
    if df is None or len(df) == 0:
        return "<div class='ai-muted'>No data.</div>"

    d2 = df.head(max_rows).copy()
    for c in d2.columns:
        if d2[c].dtype == "object":
            d2[c] = d2[c].astype(str).map(lambda s: s[:260] + ("…" if len(s) > 260 else ""))

    tbl = d2.to_html(index=False, escape=True, classes="ai-table")
    if title:
        return f"<h3>{html.escape(title)}</h3>\n{tbl}"
    return tbl


def render_anchor_interaction_diagnostics(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")

    _ = market

    top_n_runs = int(options.get("top_n_runs", 50) or 50)
    focus_run_id = (options.get("run_id") or "").strip() or None
    show_only_failures = bool(options.get("show_only_failures", False))
    include_asset_key_list = bool(options.get("include_asset_key_list", True))
    include_issue_list = bool(options.get("include_issue_list", True))
    include_focus_detail = bool(options.get("include_focus_detail", True))

    css = """
    <style>
      .ai-block{
        padding: 16px !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
        line-height: 1.35 !important;
        color: #111827 !important;
      }

      .ai-card,
      .ai-card *{
        color: #111827 !important;
        opacity: 1 !important;
        text-shadow: none !important;
      }

      .ai-card{
        background: #ffffff !important;
        border: 1px solid #d1d5db !important;
        border-radius: 12px !important;
        padding: 14px !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.06) !important;
        margin-bottom: 14px !important;
      }

      .ai-block h2{
        margin: 0 0 12px 0 !important;
        font-size: 20px !important;
        font-weight: 700 !important;
        color: #111827 !important;
      }

      .ai-card h3{
        margin: 0 0 10px 0 !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        color: #111827 !important;
      }

      .ai-muted{
        color: #4b5563 !important;
        font-size: 12px !important;
      }

      table.ai-table{
        width: 100% !important;
        border-collapse: collapse !important;
        font-size: 12px !important;
        background: #ffffff !important;
      }

      .ai-table th,
      .ai-table td{
        border: 1px solid #d1d5db !important;
        padding: 8px 8px !important;
        vertical-align: top !important;
        color: #111827 !important;
        opacity: 1 !important;
        word-break: break-word !important;
      }

      .ai-table th{
        background: #f3f4f6 !important;
        font-weight: 700 !important;
      }

      .ai-table tr:nth-child(even) td{
        background: #fafafa !important;
      }
    </style>
    """

    if not packages:
        return f"{css}<div class='ai-block'><h2>MA Anchor Diagnostics</h2><p>No packages loaded.</p></div>"

    run_items = list(packages.items())[: max(1, top_n_runs)]

    rows: List[Dict[str, Any]] = []
    for rid, pkg in run_items:
        if focus_run_id and rid != focus_run_id:
            continue

        ai_meta = _ai_meta(pkg)
        ai_assets = _ai_assets(pkg)

        diagnostics = (ai_meta.get("diagnostics") or {}) if isinstance(ai_meta, dict) else {}
        warnings = diagnostics.get("warnings", []) if isinstance(diagnostics, dict) else []
        reason = ""
        if isinstance(ai_meta, dict):
            reason = str(ai_meta.get("reason") or diagnostics.get("reason") or "").strip()
        warnings_s = "; ".join(str(x) for x in warnings) if include_issue_list and warnings else ""
        if include_issue_list and reason:
            warnings_s = f"{warnings_s}; {reason}" if warnings_s else reason

        expected_assets = [
            "anchors",
            "segments",
            "segment_path_stats",
            "summary_by_anchor",
            "summary_by_anchor_regime",
            "tp_sl_candidates",
            "recommendations",
            "validation_folds",
        ]
        missing_assets = [k for k in expected_assets if k not in ai_assets]

        assets_attached = bool(ai_assets)
        metadata_attached = isinstance(ai_meta, dict)

        if show_only_failures and metadata_attached and assets_attached and not missing_assets and not warnings_s:
            continue

        rows.append(
            {
                "run_id": rid,
                "analysis_status": _analysis_status(ai_meta, ai_assets),
                "engine_target": _engine_target(ai_meta),
                "ai_attached": "yes" if (assets_attached and metadata_attached) else "no",
                "assets_attached": _bool_label(assets_attached),
                "metadata_attached": _bool_label(metadata_attached),
                "n_anchors": len(_df(ai_assets, "anchors")),
                "n_segments": diagnostics.get("n_segments", len(_df(ai_assets, "segments"))),
                "pct_censored": _safe_float(diagnostics.get("pct_censored")),
                "missing_assets": ", ".join(missing_assets) if missing_assets else "",
                "warnings": warnings_s[:400],
                "asset_keys": ", ".join(sorted(ai_assets.keys())) if include_asset_key_list else "",
                "artifact_keys": (
                    ", ".join(sorted((ai_meta.get("artifacts") or {}).keys()))
                    if isinstance(ai_meta, dict) else ""
                ),
            }
        )

    df = pd.DataFrame(rows)

    focus_detail = ""
    if include_focus_detail and focus_run_id:
        rid, pkg = _get_pkg(packages, focus_run_id)
        ai_meta = _ai_meta(pkg) if pkg else None
        ai_assets = _ai_assets(pkg) if pkg else {}
        diagnostics = (ai_meta.get("diagnostics") or {}) if isinstance(ai_meta, dict) else {}
        warnings = diagnostics.get("warnings", []) if isinstance(diagnostics, dict) else []


        focus_detail = f"""
        <div class="ai-card">
          <h3>Focus run detail</h3>
          <div><b>run_id:</b> {html.escape(str(rid))}</div>
          <div class="ai-muted">
            In-memory sizes:
            anchors={len(_df(ai_assets, 'anchors'))},
            segments={len(_df(ai_assets, 'segments'))},
            segment_path_stats={len(_df(ai_assets, 'segment_path_stats'))},
            summary_by_anchor={len(_df(ai_assets, 'summary_by_anchor'))},
            summary_by_anchor_regime={len(_df(ai_assets, 'summary_by_anchor_regime'))},
            tp_sl_candidates={len(_df(ai_assets, 'tp_sl_candidates'))},
            recommendations={len(_df(ai_assets, 'recommendations'))},
            validation_folds={len(_df(ai_assets, 'validation_folds'))}
          </div>
          <div class="ai-muted">
            Metadata attached: {"yes" if isinstance(ai_meta, dict) else "no"}
          </div>
          <div class="ai-muted">
            Diagnostics:
            n_segments={html.escape(str(diagnostics.get("n_segments", "—")))},
            pct_censored={html.escape(str(diagnostics.get("pct_censored", "—")))},
            warnings={html.escape("; ".join(str(x) for x in warnings)) if warnings else "none"}
          </div>
        </div>
        """

    return f"""
    {css}
    <div class="ai-block">
      <h2>MA Anchor Diagnostics</h2>
      <div class="ai-card">
        <div class="ai-muted">
          This section is render-only. It safely inspects
          <code>pkg.metadata['derived']['anchor_interaction']</code> and
          <code>pkg.assets['anchor_interaction']</code> when present, and does not
          crash when some runs do not have MA Anchor artifacts attached.
        </div>
      </div>
      <div class="ai-card">
        {_html_table(df, max_rows=200, title="Run health")}
      </div>
      {focus_detail}
      <div class="ai-card">
        <h3>How to read failures</h3>
        <ul style="margin-left:18px">
          <li><b>analysis_status=ok</b>: analytics attached successfully and section is reading in-memory results.</li>
          <li><b>analysis_status=analysis_failed</b>: the analysis helper failed upstream; the <code>warnings</code> cell shows the captured exception.</li>
          <li><b>analysis_status=metadata_only</b>: metadata exists but no in-memory assets were attached for this run.</li>
          <li><b>analysis_status=not_run</b>: no anchor interaction metadata was attached to that package.</li>
        </ul>
      </div>
      <div class="ai-card">
        <h3>Guardrails</h3>
        <ul style="margin-left:18px">
          <li>Safe when no MA Anchor artifacts exist on some or all runs.</li>
          <li>No market bars are duplicated into run packages here.</li>
          <li>No heavy analytics are performed inside the report section.</li>
        </ul>
      </div>
    </div>
    """