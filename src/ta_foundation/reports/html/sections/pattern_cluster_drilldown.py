from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List

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


def _html_table(df: pd.DataFrame, *, max_rows: int = 50, title: Optional[str] = None) -> str:
    if df is None or len(df) == 0:
        return ""
    d2 = df.head(max_rows).copy()
    for c in d2.columns:
        if d2[c].dtype == "object":
            d2[c] = d2[c].astype(str).map(lambda s: s[:240] + ("…" if len(s) > 240 else ""))
    tbl = d2.to_html(index=False, escape=True, classes="pe-table")
    if title:
        return f"<h3>{html.escape(title)}</h3>\n{tbl}"
    return tbl


def render_pattern_cluster_drilldown(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer.

    Reads in-memory artifacts from:
      pkg.assets["pattern_engine"]  (preferred)
    Reads diagnostics + config snapshot from:
      pkg.metadata["derived"]["pattern_engine"]

    Section options (ctx["options"]):
      run_id: str (optional)
      cluster_id: str (optional)
      top_n_members: int (default 25)
      horizon: int (optional) for filtering cluster_stats/oos_stats
    """
    packages = (ctx.get("packages") or {})
    sec = (ctx.get("options") or {})

    if not packages:
        return "<div class='pe-block'><h2>Pattern Cluster Drilldown</h2><p>No packages loaded.</p></div>"

    run_id = sec.get("run_id")
    cluster_id = sec.get("cluster_id")
    top_n_members = int(sec.get("top_n_members", 25))
    horizon_opt = sec.get("horizon", None)
    horizon = int(horizon_opt) if horizon_opt is not None and str(horizon_opt).strip() != "" else None

    rid, pkg = _get_pkg(packages, run_id)
    pe = _pe_meta(pkg) if pkg is not None else None
    pe_a = _pe_assets(pkg) if pkg is not None else {}

    clusters = _df(pe_a, "clusters")
    members = _df(pe_a, "cluster_members")
    cluster_stats = _df(pe_a, "cluster_stats")
    oos_stats = _df(pe_a, "oos_stats")
    mc_summary = _df(pe_a, "mc_summary")
    pattern_stats = _df(pe_a, "pattern_stats")

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

    if not isinstance(pe, dict):
        return f"""{css}<div class="pe-block"><h2>Pattern Cluster Drilldown</h2>
        <div class="pe-card"><p>No PatternEngine metadata attached for run <b>{html.escape(str(rid))}</b>.</p></div></div>"""

    # Pick a default cluster_id if not specified
    chosen_cluster = cluster_id
    if not chosen_cluster:
        if len(clusters) > 0 and "cluster_id" in clusters.columns:
            # Prefer largest cluster
            if "n_members" in clusters.columns:
                chosen_cluster = str(clusters.sort_values("n_members", ascending=False)["cluster_id"].iloc[0])
            else:
                chosen_cluster = str(clusters["cluster_id"].iloc[0])

    # Build cluster header
    header_bits = []
    header_bits.append(f"<div><b>run_id:</b> {html.escape(str(rid))}</div>")
    header_bits.append(f"<div><b>cluster_id:</b> {html.escape(str(chosen_cluster))}</div>")
    if horizon is not None:
        header_bits.append(f"<div><b>horizon filter:</b> {html.escape(str(horizon))}</div>")

    # Cluster summary row
    cluster_row_tbl = ""
    if chosen_cluster and len(clusters) > 0 and "cluster_id" in clusters.columns:
        c2 = clusters[clusters["cluster_id"].astype(str) == str(chosen_cluster)].copy()
        if len(c2) > 0:
            cluster_row_tbl = _html_table(c2, max_rows=5, title="Cluster summary")

    # Members table
    members_tbl = ""
    if chosen_cluster and len(members) > 0 and "cluster_id" in members.columns:
        m2 = members[members["cluster_id"].astype(str) == str(chosen_cluster)].copy()
        # Join representative stats if available
        if len(m2) > 0 and len(pattern_stats) > 0 and "pattern_id" in m2.columns and "pattern_id" in pattern_stats.columns:
            ps = pattern_stats.copy()
            if horizon is not None and "horizon" in ps.columns:
                ps = ps[ps["horizon"] == horizon]
            keep = [c for c in ["pattern_id", "horizon", "n_signals", "avg_ticks", "win_rate", "p50", "rank_score_raw", "stability_oos_score"] if c in ps.columns]
            if keep:
                ps = ps[keep]
                m2 = m2.merge(ps, on="pattern_id", how="left")

        # Prefer reps first, then rep_rank, then member_weight
        sort_cols = [c for c in ["is_representative", "rep_rank", "member_weight"] if c in m2.columns]
        if sort_cols:
            asc = [False] + [True] * (len(sort_cols) - 1)  # reps True should come first if boolean; but False/True order differs
            # normalize is_representative to int so sort works
            if "is_representative" in m2.columns:
                m2["is_representative"] = m2["is_representative"].astype(bool).astype(int)
            m2 = m2.sort_values(sort_cols, ascending=asc)

        members_tbl = _html_table(m2, max_rows=top_n_members, title=f"Cluster members (top {top_n_members})")

    # Cluster stats filtered
    cstats_tbl = ""
    if chosen_cluster and len(cluster_stats) > 0 and "cluster_id" in cluster_stats.columns:
        cs = cluster_stats[cluster_stats["cluster_id"].astype(str) == str(chosen_cluster)].copy()
        if horizon is not None and "horizon" in cs.columns:
            cs = cs[cs["horizon"] == horizon]
        cstats_tbl = _html_table(cs, max_rows=50, title="Cluster stats")

    # OOS stats table (if your CV produces per-cluster in future, this will show; otherwise mostly pattern-level)
    oos_tbl = ""
    if len(oos_stats) > 0:
        o2 = oos_stats.copy()
        if horizon is not None and "horizon" in o2.columns:
            o2 = o2[o2["horizon"] == horizon]
        # If this is pattern-level OOS, show only patterns in this cluster when members exist
        if chosen_cluster and len(members) > 0 and "pattern_id" in members.columns and "entity_id" in o2.columns:
            pat_ids = set(members[members["cluster_id"].astype(str) == str(chosen_cluster)]["pattern_id"].astype(str).tolist())
            o2 = o2[o2["entity_id"].astype(str).isin(pat_ids)]
        oos_tbl = _html_table(o2, max_rows=50, title="OOS stats (filtered)")

    # MC summary filtered similarly
    mc_tbl = ""
    if len(mc_summary) > 0:
        mcs = mc_summary.copy()
        if horizon is not None and "horizon" in mcs.columns and (mcs["horizon"] != -1).any():
            mcs = mcs[mcs["horizon"] == horizon]
        # If pattern-level MC, restrict to members in this cluster
        if chosen_cluster and len(members) > 0 and "pattern_id" in members.columns and "entity_id" in mcs.columns:
            pat_ids = set(members[members["cluster_id"].astype(str) == str(chosen_cluster)]["pattern_id"].astype(str).tolist())
            mcs = mcs[mcs["entity_id"].astype(str).isin(pat_ids)]
        mc_tbl = _html_table(mcs, max_rows=50, title="Monte Carlo summary (filtered)")

    # Diagnostics
    diag = pe.get("diagnostics", {}) or {}
    validation = (diag.get("validation", {}) or {}) if isinstance(diag, dict) else {}
    ok = bool(validation.get("ok", False)) if isinstance(validation, dict) else False
    issues = validation.get("issues", []) if isinstance(validation, dict) else []
    issues_li = "".join(f"<li>{html.escape(str(x))}</li>" for x in issues) if issues else "<li>(none)</li>"

    # Cache health
    cache_bits = []
    cache_bits.append(f"clusters={len(clusters)}")
    cache_bits.append(f"cluster_members={len(members)}")
    cache_bits.append(f"cluster_stats={len(cluster_stats)}")
    cache_bits.append(f"pattern_stats={len(pattern_stats)}")
    cache_bits.append(f"oos_stats={len(oos_stats)}")
    cache_bits.append(f"mc_summary={len(mc_summary)}")

    return f"""
    {css}
    <div class="pe-block">
      <h2>Pattern Cluster Drilldown</h2>

      <div class="pe-card">
        {''.join(header_bits)}
        <div class="pe-muted"><b>Render mode:</b> in-memory only (pkg.assets["pattern_engine"]). No parquet IO during render.</div>
        <div class="pe-muted"><b>Cache:</b> {html.escape(', '.join(cache_bits))}</div>
      </div>

      <div class="pe-card">
        <h3>Diagnostics</h3>
        <div><b>Status:</b> {"OK" if ok else "NOT OK"}</div>
        <div><b>Issues:</b><ul>{issues_li}</ul></div>
      </div>

      <div class="pe-card">
        {cluster_row_tbl if cluster_row_tbl else "<p>No cluster summary available (missing in-memory clusters).</p>"}
      </div>

      <div class="pe-card">
        {members_tbl if members_tbl else "<p>No cluster members available (missing in-memory cluster_members).</p>"}
      </div>

      <div class="pe-card">
        {cstats_tbl if cstats_tbl else "<p>No cluster_stats available.</p>"}
      </div>

      <div class="pe-card">
        {oos_tbl if oos_tbl else "<p>No OOS stats available for this selection.</p>"}
      </div>

      <div class="pe-card">
        {mc_tbl if mc_tbl else "<p>No Monte Carlo summary available for this selection.</p>"}
      </div>
    </div>
    """