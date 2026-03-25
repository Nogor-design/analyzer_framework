from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List

import html
import pandas as pd


def _pick_run_ids(packages: Dict[str, Any], top_n: int) -> List[str]:
    run_ids = list((packages or {}).keys())
    # stable order: whatever dict preserves; optionally sort for deterministic output
    # run_ids.sort()
    return run_ids[: max(0, int(top_n))]


def _get_pkg(packages: Dict[str, Any], run_id: Optional[str]) -> Tuple[Optional[str], Optional[Any]]:
    if not packages:
        return None, None
    if run_id and run_id in packages:
        return run_id, packages[run_id]
    # fallback first
    first = next(iter(packages.items()))
    return first[0], first[1]


def _pe_meta(pkg: Any) -> Optional[Dict[str, Any]]:
    try:
        md = getattr(pkg, "metadata", None) or {}
        derived = md.get("derived", {}) or {}
        pe = derived.get("pattern_engine")
        if isinstance(pe, dict):
            return pe
    except Exception:
        return None
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


def _fmt_int(x: Any) -> str:
    try:
        return f"{int(x):,}"
    except Exception:
        return "0"


def _fmt_float(x: Any, nd: int = 2) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "nan"


def _html_table(df: pd.DataFrame, *, max_rows: int = 25, title: Optional[str] = None) -> str:
    if df is None or len(df) == 0:
        return ""
    d2 = df.head(max_rows).copy()
    # avoid huge nested objects
    for c in d2.columns:
        if d2[c].dtype == "object":
            d2[c] = d2[c].astype(str).map(lambda s: s[:200] + ("…" if len(s) > 200 else ""))
    tbl = d2.to_html(index=False, escape=True, classes="pe-table")
    if title:
        return f"<h3>{html.escape(title)}</h3>\n{tbl}"
    return tbl


def render_pattern_engine_overview(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer.
    Reads PatternEngine results from:
      - pkg.metadata["derived"]["pattern_engine"] (diagnostics + artifact refs)
      - pkg.assets["pattern_engine"] (in-memory DataFrames, populated in builder/compute phase)

    Section options (ctx["options"]):
      top_n_runs: int (default 12)
      run_id: optional (if provided, show detail panels for that run)
      top_n_patterns: int (default 20)
    """
    packages = (ctx.get("packages") or {})
    sec = (ctx.get("options") or {})

    top_n_runs = int(sec.get("top_n_runs", 12))
    top_n_patterns = int(sec.get("top_n_patterns", 20))
    focus_run_id = sec.get("run_id")

    if not packages:
        return "<div class='pe-block'><h2>Pattern Engine Overview</h2><p>No packages loaded.</p></div>"

    run_ids = _pick_run_ids(packages, top_n_runs)

    # summary rows per run
    rows = []
    for rid in run_ids:
        pkg = packages.get(rid)
        pe = _pe_meta(pkg) if pkg is not None else None
        pe_a = _pe_assets(pkg) if pkg is not None else {}

        diag = (pe or {}).get("diagnostics", {}) if isinstance(pe, dict) else {}
        validation = (diag.get("validation", {}) or {}) if isinstance(diag, dict) else {}
        counts = (diag.get("counts", {}) or {}) if isinstance(diag, dict) else {}

        ok = bool(validation.get("ok", False)) if validation else False
        issues = validation.get("issues", []) if isinstance(validation, dict) else []
        issues_s = "; ".join([str(x) for x in issues])[:240]

        # Use diagnostics counts if present; fall back to in-memory DFs if missing
        n_patterns = counts.get("n_patterns", None)
        n_signals = counts.get("n_signals", None)
        n_outcomes = counts.get("n_outcomes", None)
        n_clusters = counts.get("n_clusters", None)

        if n_patterns is None:
            n_patterns = len(_df(pe_a, "patterns"))
        if n_signals is None:
            n_signals = len(_df(pe_a, "signals"))
        if n_outcomes is None:
            n_outcomes = len(_df(pe_a, "outcomes"))
        if n_clusters is None:
            n_clusters = len(_df(pe_a, "clusters"))

        rows.append(
            {
                "run_id": rid,
                "pe_attached": "yes" if isinstance(pe, dict) else "no",
                "ok": "yes" if ok else "no",
                "n_patterns": int(n_patterns) if str(n_patterns).isdigit() else n_patterns,
                "n_signals": int(n_signals) if str(n_signals).isdigit() else n_signals,
                "n_outcomes": int(n_outcomes) if str(n_outcomes).isdigit() else n_outcomes,
                "n_clusters": int(n_clusters) if str(n_clusters).isdigit() else n_clusters,
                "issues": issues_s,
            }
        )

    summary_df = pd.DataFrame(rows)

    # focus run panels
    frid, fpkg = _get_pkg(packages, focus_run_id)
    fmeta = _pe_meta(fpkg) if fpkg is not None else None
    fassets = _pe_assets(fpkg) if fpkg is not None else {}

    patterns = _df(fassets, "patterns")
    signals = _df(fassets, "signals")
    outcomes = _df(fassets, "outcomes")
    pattern_stats = _df(fassets, "pattern_stats")
    clusters = _df(fassets, "clusters")
    cluster_stats = _df(fassets, "cluster_stats")
    oos_stats = _df(fassets, "oos_stats")
    mc_summary = _df(fassets, "mc_summary")

    # Build a top patterns table if present
    top_patterns_tbl = ""
    if len(pattern_stats) > 0:
        # prefer stability / oos if present, else rank_score_raw, else avg_ticks * sqrt(n)
        sort_cols = []
        ps = pattern_stats.copy()

        if "stability_oos_score" in ps.columns:
            sort_cols = ["stability_oos_score"]
        elif "rank_score_raw" in ps.columns:
            sort_cols = ["rank_score_raw"]
        elif "avg_ticks" in ps.columns and "n_signals" in ps.columns:
            # Compute a temporary rank score
            try:
                ps["rank_score_tmp"] = ps["avg_ticks"].astype(float) * (ps["n_signals"].astype(float) ** 0.5)
                sort_cols = ["rank_score_tmp"]
            except Exception:
                sort_cols = []

        # Keep columns for display
        cols_keep = [
            c for c in [
                "pattern_id", "horizon", "n_signals", "avg_ticks", "win_rate",
                "p10", "p50", "p90", "rank_score_raw", "stability_oos_score"
            ]
            if c in ps.columns
        ]

        # IMPORTANT: if we're sorting by rank_score_tmp, keep it during sort, then drop it
        sort_cols = [c for c in sort_cols if c in ps.columns]

        d2 = ps[cols_keep + [c for c in sort_cols if c not in cols_keep]].copy() if cols_keep else ps.copy()

        if sort_cols:
            existing = [c for c in sort_cols if c in d2.columns]
            if existing:
                d2 = d2.sort_values(existing, ascending=False)

        # Drop tmp column from display if present
        if "rank_score_tmp" in d2.columns and "rank_score_tmp" not in cols_keep:
            d2 = d2.drop(columns=["rank_score_tmp"], errors="ignore")

        top_patterns_tbl = _html_table(d2, max_rows=top_n_patterns, title=f"Top patterns (run {frid})")

    # Simple diagnostics block
    diag_html = "<p>No PatternEngine metadata attached for this run.</p>"
    if isinstance(fmeta, dict):
        diag = fmeta.get("diagnostics", {}) or {}
        validation = (diag.get("validation", {}) or {}) if isinstance(diag, dict) else {}
        ok = bool(validation.get("ok", False)) if isinstance(validation, dict) else False
        issues = validation.get("issues", []) if isinstance(validation, dict) else []
        issues_li = "".join(f"<li>{html.escape(str(x))}</li>" for x in issues) if issues else "<li>(none)</li>"
        diag_html = f"""
        <div class="pe-diagnostics">
          <div><b>Status:</b> {"OK" if ok else "NOT OK"}</div>
          <div><b>Issues:</b><ul>{issues_li}</ul></div>
        </div>
        """

    # Render
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

    html_out = f"""
    {css}
    <div class="pe-block">
      <h2>Pattern Engine Overview</h2>
      <div class="pe-grid">
        <div class="pe-card">
          <h3>Runs (first {html.escape(str(len(run_ids)))})</h3>
          {_html_table(summary_df, max_rows=top_n_runs)}
          <div class="pe-muted">
            Note: This section reads from in-memory artifacts (pkg.assets["pattern_engine"]). No parquet IO is performed during render.
          </div>
        </div>

        <div class="pe-card">
          <h3>Focus run</h3>
          <div><b>run_id:</b> {html.escape(str(frid))}</div>
          {diag_html}
          <div class="pe-muted">
            In-memory artifacts found:
            patterns={_fmt_int(len(patterns))},
            signals={_fmt_int(len(signals))},
            outcomes={_fmt_int(len(outcomes))},
            pattern_stats={_fmt_int(len(pattern_stats))},
            clusters={_fmt_int(len(clusters))},
            cluster_stats={_fmt_int(len(cluster_stats))},
            oos_stats={_fmt_int(len(oos_stats))},
            mc_summary={_fmt_int(len(mc_summary))}
          </div>
        </div>

        <div class="pe-card">
          {top_patterns_tbl if top_patterns_tbl else "<p>No pattern_stats available in memory for this run.</p>"}
        </div>
      </div>
    </div>
    """
    return html_out