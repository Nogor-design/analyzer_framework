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


def _pe_meta(pkg: Any) -> Optional[Dict[str, Any]]:
    try:
        md = getattr(pkg, "metadata", None) or {}
        derived = md.get("derived", {}) or {}
        pe = derived.get("pattern_engine")
        return pe if isinstance(pe, dict) else None
    except Exception:
        return None


def _pe_assets(pkg: Any) -> Dict[str, Any]:
    try:
        assets = getattr(pkg, "assets", None) or {}
        pe = assets.get("pattern_engine", {}) or {}
        return pe if isinstance(pe, dict) else {}
    except Exception:
        return {}


def _df(pe_assets: Dict[str, Any], key: str) -> pd.DataFrame:
    v = pe_assets.get(key)
    return v if isinstance(v, pd.DataFrame) else pd.DataFrame()


def _html_table(df: pd.DataFrame, *, max_rows: int = 200, title: Optional[str] = None) -> str:
    if df is None or len(df) == 0:
        return "<div class='pe-muted'>No data.</div>"
    d2 = df.head(max_rows).copy()
    for c in d2.columns:
        if d2[c].dtype == "object":
            d2[c] = d2[c].astype(str).map(lambda s: s[:260] + ("…" if len(s) > 260 else ""))
    tbl = d2.to_html(index=False, escape=True, classes="pe-table")
    if title:
        return f"<h3>{html.escape(title)}</h3>\n{tbl}"
    return tbl


def render_pattern_engine_diagnostics(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer diagnostics for Pattern Engine.

    Reads only:
      - pkg.metadata["derived"]["pattern_engine"]["diagnostics"]
      - pkg.assets["pattern_engine"] (in-memory DFs)

    Section options (ctx["options"]):
      top_n_runs: int (default 50)
      run_id: optional (if set, only show that run + detail)
      show_only_failures: bool (default false)
      include_asset_key_list: bool (default true)
      include_issue_list: bool (default true)
    """
    packages = (ctx.get("packages") or {})
    sec = (ctx.get("options") or {})

    top_n_runs = int(sec.get("top_n_runs", 50))
    focus_run_id = (sec.get("run_id") or "").strip() or None
    show_only_failures = bool(sec.get("show_only_failures", False))
    include_asset_key_list = bool(sec.get("include_asset_key_list", True))
    include_issue_list = bool(sec.get("include_issue_list", True))

    css = """
    <style>
      /* ==== Pattern Engine scoped readability overrides ==== */
    
      .pe-block{
        padding: 16px !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
        line-height: 1.35 !important;
        color: #111827 !important;
      }
    
      /* Force contrast inside cards even if global theme sets light/transparent text */
      .pe-card,
      .pe-card *{
        color: #111827 !important;
        opacity: 1 !important;
        text-shadow: none !important;
      }
    
      .pe-card{
        background: #ffffff !important;
        border: 1px solid #d1d5db !important;
        border-radius: 12px !important;
        padding: 14px !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.06) !important;
        margin-bottom: 14px !important;
      }
    
      .pe-block h2{
        margin: 0 0 12px 0 !important;
        font-size: 20px !important;
        font-weight: 700 !important;
        color: #111827 !important;
      }
    
      .pe-card h3{
        margin: 0 0 10px 0 !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        color: #111827 !important;
      }
    
      .pe-muted{
        color: #4b5563 !important; /* readable gray */
        font-size: 12px !important;
      }
    
      /* Lists readable */
      .pe-card ul{
        margin: 6px 0 0 18px !important;
      }
      .pe-card li{
        margin: 3px 0 !important;
      }
    
      /* Tables: readable sizing + contrast */
      table.pe-table{
        width: 100% !important;
        border-collapse: collapse !important;
        font-size: 12px !important;
        background: #ffffff !important;
      }
      .pe-table th,
      .pe-table td{
        border: 1px solid #d1d5db !important;
        padding: 8px 8px !important;
        vertical-align: top !important;
        color: #111827 !important;
        opacity: 1 !important;
        word-break: break-word !important;
      }
      .pe-table th{
        background: #f3f4f6 !important;
        font-weight: 700 !important;
      }
      .pe-table tr:nth-child(even) td{
        background: #fafafa !important;
      }
    
      /* Links */
      .pe-card a,
      .pe-card a:visited{
        color: #1d4ed8 !important;
        text-decoration: underline !important;
      }
    
      /* Optional: if your report has a dark page background, ensure cards stand out */
      .pe-block{
        background: transparent !important;
      }
    </style>
    """

    if not packages:
        return f"{css}<div class='pe-block'><h2>Pattern Engine — Diagnostics</h2><p>No packages loaded.</p></div>"

    run_items = list(packages.items())
    # stable order; cap for readability
    run_items = run_items[: max(1, top_n_runs)]

    rows: List[Dict[str, Any]] = []
    for rid, pkg in run_items:
        if focus_run_id and rid != focus_run_id:
            continue

        meta = _pe_meta(pkg)
        pe_assets = _pe_assets(pkg)

        if not isinstance(meta, dict):
            rows.append(
                {
                    "run_id": rid,
                    "pe_attached": "no",
                    "ok": "no",
                    "n_patterns": len(_df(pe_assets, "patterns")),
                    "n_signals": len(_df(pe_assets, "signals")),
                    "n_outcomes": len(_df(pe_assets, "outcomes")),
                    "n_clusters": len(_df(pe_assets, "clusters")),
                    "issues": "missing pkg.metadata['derived']['pattern_engine']",
                    "asset_keys": ", ".join(sorted(pe_assets.keys())) if include_asset_key_list else "",
                }
            )
            continue

        diag = (meta.get("diagnostics") or {}) if isinstance(meta, dict) else {}
        validation = (diag.get("validation") or {}) if isinstance(diag, dict) else {}
        counts = (diag.get("counts") or {}) if isinstance(diag, dict) else {}

        ok = bool(validation.get("ok", False)) if isinstance(validation, dict) else False
        issues = validation.get("issues", []) if isinstance(validation, dict) else []
        issues_s = "; ".join([str(x) for x in issues]) if include_issue_list else ""

        # counts: prefer diag counts, fall back to actual in-memory frames
        n_patterns = counts.get("n_patterns", len(_df(pe_assets, "patterns")))
        n_signals = counts.get("n_signals", len(_df(pe_assets, "signals")))
        n_outcomes = counts.get("n_outcomes", len(_df(pe_assets, "outcomes")))
        n_clusters = counts.get("n_clusters", len(_df(pe_assets, "clusters")))

        # detect missing expected in-memory assets (dual storage should populate these)
        expected = [
            "patterns",
            "signals",
            "outcomes",
            "pattern_stats",
            "clusters",
            "cluster_members",
            "cluster_stats",
            "oos_stats",
            "mc_summary",
        ]
        missing_assets = [k for k in expected if k not in pe_assets]

        if show_only_failures and ok and not missing_assets:
            continue

        rows.append(
            {
                "run_id": rid,
                "pe_attached": "yes",
                "ok": "yes" if ok else "no",
                "n_patterns": int(n_patterns) if str(n_patterns).isdigit() else n_patterns,
                "n_signals": int(n_signals) if str(n_signals).isdigit() else n_signals,
                "n_outcomes": int(n_outcomes) if str(n_outcomes).isdigit() else n_outcomes,
                "n_clusters": int(n_clusters) if str(n_clusters).isdigit() else n_clusters,
                "missing_assets": ", ".join(missing_assets) if missing_assets else "",
                "issues": issues_s[:400],
                "asset_keys": ", ".join(sorted(pe_assets.keys())) if include_asset_key_list else "",
            }
        )

    df = pd.DataFrame(rows)

    focus_detail = ""
    if focus_run_id:
        rid, pkg = _get_pkg(packages, focus_run_id)
        meta = _pe_meta(pkg) if pkg else None
        pe_assets = _pe_assets(pkg) if pkg else {}
        focus_detail = f"""
        <div class="pe-card">
          <h3>Focus run detail</h3>
          <div><b>run_id:</b> {html.escape(str(rid))}</div>
          <div class="pe-muted">
            In-memory sizes:
            patterns={len(_df(pe_assets,'patterns'))},
            signals={len(_df(pe_assets,'signals'))},
            outcomes={len(_df(pe_assets,'outcomes'))},
            pattern_stats={len(_df(pe_assets,'pattern_stats'))},
            clusters={len(_df(pe_assets,'clusters'))},
            cluster_members={len(_df(pe_assets,'cluster_members'))},
            cluster_stats={len(_df(pe_assets,'cluster_stats'))},
            oos_stats={len(_df(pe_assets,'oos_stats'))},
            mc_summary={len(_df(pe_assets,'mc_summary'))}
          </div>
          <div class="pe-muted">
            Metadata attached: {"yes" if isinstance(meta, dict) else "no"} (diagnostics lives here)
          </div>
        </div>
        """

    return f"""
    {css}
    <div class="pe-block">
      <h2>Pattern Engine — Diagnostics</h2>
      <div class="pe-card">
        <div class="pe-muted">
          This section is render-only and does not read parquet. It verifies that the orchestrator populated
          pkg.metadata['derived']['pattern_engine'] and pkg.assets['pattern_engine'] (dual storage).
        </div>
      </div>
      <div class="pe-card">
        {_html_table(df, max_rows=200, title="Run health")}
      </div>
      {focus_detail}
    </div>
    """