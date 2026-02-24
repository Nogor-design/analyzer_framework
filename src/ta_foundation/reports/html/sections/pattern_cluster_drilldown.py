# src/ta_foundation/reports/html/sections/pattern_cluster_drilldown.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re

import pandas as pd

from ta_foundation.core.model import AnalysisPackage


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _matches(rx: Optional[str], text: str) -> bool:
    if not rx:
        return True
    try:
        return re.search(rx, text) is not None
    except Exception:
        return True


def _render_table(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<div class='muted'>No data.</div>"
    d = df.head(max_rows).copy()
    out = ["<table class='table'>"]
    out.append("<tr>" + "".join([f"<th>{_h(str(c))}</th>" for c in d.columns]) + "</tr>")
    for _, r in d.iterrows():
        out.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in d.columns]) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def _read_artifact(meta: Dict[str, Any], key: str) -> pd.DataFrame:
    art = (meta.get("artifacts") or {}).get(key) or {}
    if art.get("type") != "parquet":
        return pd.DataFrame()
    path = art.get("path")
    if not path:
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def _pick_pkg(packages: Dict[str, AnalysisPackage], run_id: Optional[str]) -> Optional[AnalysisPackage]:
    if not packages:
        return None
    if run_id and run_id in packages:
        return packages[run_id]
    return next(iter(packages.values()))


def _get_pe_meta(pkg: AnalysisPackage) -> Dict[str, Any]:
    md = getattr(pkg, "metadata", {}) or {}
    return (((md.get("derived") or {}).get("pattern_engine")) or {})


def render_pattern_cluster_drilldown(ctx: Dict[str, Any]) -> str:
    """
    Cluster Drilldown for Pattern Engine.

    Options (recommended) under ctx["options"]["pattern_cluster_drilldown"]:
      run_id: optional
      cluster_id: optional (if omitted, choose best by MC then OOS then members)
      horizon: optional
      top_k_members: default 25
      include_run_id_regex / exclude_run_id_regex: optional (used only when run_id omitted)
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    sec = ctx.get("options") or {}  # section options
    all_opts = ctx.get("all_options") or {}  # full yaml
    pe_opts = all_opts.get("pattern_engine") or {}


    run_id = sec.get("run_id")
    include_regex = sec.get("include_run_id_regex", pe_opts.get("include_run_id_regex"))
    exclude_regex = sec.get("exclude_run_id_regex", pe_opts.get("exclude_run_id_regex"))

    cluster_id = str(sec.get("cluster_id") or "").strip()
    horizon = sec.get("horizon")
    horizon = int(horizon) if horizon is not None else None
    top_k_members = int(sec.get("top_k_members", 25))

    css = """
    <style>
      .tf-pe { display:flex; flex-direction:column; gap:14px; }
      .tf-card { border-radius:16px; padding:12px 14px; background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.06); }
      .tf-title { font-weight:900; font-size:1.02rem; margin-bottom:6px; }
      .tf-sub { opacity:0.82; font-size:0.86rem; }
      .muted { opacity:0.75; }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .table { border-collapse: collapse; width:100%; }
      .table th, .table td { border-bottom: 1px solid rgba(255,255,255,0.08); padding: 6px 8px; text-align:left; }
    </style>
    """

    html: List[str] = [css, "<div class='tf-pe'>"]
    html.append("<div class='tf-card'>")
    html.append("<div class='tf-title'>Pattern Engine — Cluster Drilldown</div>")

    # Choose package
    pkg: Optional[AnalysisPackage] = None
    if run_id:
        pkg = packages.get(run_id)
    else:
        # pick first run matching regexes with pattern_engine meta present
        for rid, p in (packages or {}).items():
            if not _matches(include_regex, rid):
                continue
            if exclude_regex and _matches(exclude_regex, rid):
                continue
            meta = _get_pe_meta(p)
            if meta and not meta.get("disabled", False):
                pkg = p
                run_id = rid
                break
        if pkg is None:
            pkg = _pick_pkg(packages, None)
            run_id = getattr(pkg, "run_id", None) if pkg else None

    if pkg is None:
        html.append("<div class='muted'>No packages.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    meta = _get_pe_meta(pkg)
    if not meta:
        html.append("<div class='muted'>No pattern_engine derived metadata found on selected run.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    if meta.get("disabled", False):
        html.append(f"<div class='muted'>Pattern engine disabled for this run: {_h(str(meta.get('reason','')))}</div>")
        html.append("</div></div>")
        return "\n".join(html)

    engine = meta.get("engine") or {}
    html.append(
        "<div class='tf-sub'>"
        f"Run: <b>{_h(str(run_id or ''))}</b> • "
        f"{_h(str(engine.get('instrument','')))} {_h(str(engine.get('contract','')))} • "
        f"bar_tf: {_h(str(engine.get('bar_tf','')))} • tick_size: {_h(str(engine.get('tick_size','')))}"
        "</div>"
    )
    if horizon is not None:
        html.append(f"<div class='tf-sub muted'>Horizon filter: {horizon}</div>")
    html.append("</div>")  # header card

    clusters = _read_artifact(meta, "clusters")
    members = _read_artifact(meta, "cluster_members")
    cluster_stats = _read_artifact(meta, "cluster_stats")
    pattern_stats = _read_artifact(meta, "pattern_stats")
    oos = _read_artifact(meta, "oos_stats")
    mc = _read_artifact(meta, "mc_summary")

    if clusters.empty or members.empty:
        html.append("<div class='tf-card'><div class='muted'>No cluster artifacts (clusters/cluster_members missing).</div></div>")
        html.append("</div>")
        return "\n".join(html)

    # If cluster_id not provided, choose best:
    if not cluster_id:
        # Prefer MC
        if not mc.empty:
            m = mc.copy()
            if "entity_type" in m.columns:
                m = m[m["entity_type"] == "cluster"].copy()
            if horizon is not None and "horizon" in m.columns:
                m = m[m["horizon"] == int(horizon)].copy()
            if "prop_survival_score" in m.columns and "entity_id" in m.columns and not m.empty:
                m = m.sort_values(["prop_survival_score"], ascending=[False])
                cluster_id = str(m["entity_id"].iloc[0])
        # Else OOS
        if not cluster_id and not oos.empty:
            oo = oos.copy()
            if "entity_type" in oo.columns:
                oo = oo[oo["entity_type"] == "cluster"].copy()
            if horizon is not None and "horizon" in oo.columns:
                oo = oo[oo["horizon"] == int(horizon)].copy()
            if "stability_oos_score" in oo.columns and "entity_id" in oo.columns and not oo.empty:
                oo = oo.sort_values(["stability_oos_score"], ascending=[False])
                cluster_id = str(oo["entity_id"].iloc[0])
        # Else largest
        if not cluster_id and "n_members" in clusters.columns and "cluster_id" in clusters.columns and not clusters.empty:
            cluster_id = str(clusters.sort_values("n_members", ascending=False)["cluster_id"].iloc[0])

    if not cluster_id:
        html.append("<div class='tf-card'><div class='muted'>Could not select a cluster_id.</div></div>")
        html.append("</div>")
        return "\n".join(html)

    # Cluster info
    html.append("<div class='tf-card'>")
    html.append(f"<div class='tf-title'>Cluster: {_h(cluster_id)}</div>")
    cinfo = clusters[clusters["cluster_id"] == cluster_id].copy() if "cluster_id" in clusters.columns else pd.DataFrame()
    html.append("<h4 style='margin:8px 0 6px 0;'>Cluster info</h4>")
    html.append(_render_table(cinfo, max_rows=5))

    # Cluster MC summary
    if not mc.empty:
        mcc = mc.copy()
        if "entity_type" in mcc.columns:
            mcc = mcc[mcc["entity_type"] == "cluster"].copy()
        if "entity_id" in mcc.columns:
            mcc = mcc[mcc["entity_id"] == cluster_id].copy()
        if horizon is not None and "horizon" in mcc.columns:
            mcc = mcc[mcc["horizon"] == int(horizon)].copy()
        if not mcc.empty:
            keep = [c for c in [
                "horizon", "prop_survival_score", "dd_p90",
                "trailing_dd_breach_prob", "daily_loss_breach_prob",
                "stress_slip_ticks", "n_paths"
            ] if c in mcc.columns]
            html.append("<h4 style='margin:10px 0 6px 0;'>Monte Carlo (prop)</h4>")
            html.append(_render_table(mcc[keep].round(4), max_rows=30))

    # Cluster stats (in-sample)
    if not cluster_stats.empty and "cluster_id" in cluster_stats.columns:
        cs = cluster_stats[cluster_stats["cluster_id"] == cluster_id].copy()
        if horizon is not None and "horizon" in cs.columns:
            cs = cs[cs["horizon"] == int(horizon)].copy()
        if not cs.empty:
            html.append("<h4 style='margin:10px 0 6px 0;'>Cluster stats (in-sample)</h4>")
            html.append(_render_table(cs.round(4), max_rows=30))

    # Members + reps
    cm = members[members["cluster_id"] == cluster_id].copy()
    if cm.empty:
        html.append("<div class='muted'>No members found for this cluster.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    # Sort reps first
    sort_cols = [c for c in ["is_representative", "rep_rank"] if c in cm.columns]
    if sort_cols:
        cm = cm.sort_values(sort_cols, ascending=[False, True][:len(sort_cols)])

    html.append("<h4 style='margin:10px 0 6px 0;'>Members</h4>")
    html.append(_render_table(cm.head(top_k_members), max_rows=top_k_members))

    # Member pattern stats
    if not pattern_stats.empty and "pattern_id" in pattern_stats.columns:
        ps = pattern_stats.merge(
            cm[[c for c in ["pattern_id", "is_representative", "rep_rank"] if c in cm.columns]],
            on="pattern_id",
            how="inner",
        )
        if horizon is not None and "horizon" in ps.columns:
            ps = ps[ps["horizon"] == int(horizon)].copy()

        sort_cols = [c for c in ["is_representative", "rank_score_raw", "avg_ticks", "n_signals"] if c in ps.columns]
        asc = [False, False, False, False][:len(sort_cols)]
        if sort_cols:
            ps = ps.sort_values(sort_cols, ascending=asc)

        keep = [c for c in [
            "pattern_id", "is_representative", "rep_rank",
            "horizon", "n_signals", "avg_ticks", "net_ticks", "win_rate",
            "p10", "p50", "p90", "rank_score_raw"
        ] if c in ps.columns]

        html.append("<h4 style='margin:10px 0 6px 0;'>Member stats (in-sample)</h4>")
        html.append(_render_table(ps[keep].round(4), max_rows=top_k_members))

    # Member OOS
    if not oos.empty:
        oo = oos.copy()

        # Some pipelines use entity_type/entity_id, others may use pattern_id directly.
        if "entity_type" in oo.columns:
            oo = oo[oo["entity_type"] == "pattern"].copy()

        # Normalize id column to pattern_id
        if "entity_id" in oo.columns:
            oo = oo.rename(columns={"entity_id": "pattern_id"})
        # If neither exists, bail gracefully
        if "pattern_id" not in oo.columns:
            html.append(
                "<div class='muted'>OOS stats present but missing id column (expected entity_id or pattern_id).</div>")
            html.append("</div>")  # card
            html.append("</div>")  # wrapper
            return "\n".join(html)

        # Filter to members in this cluster if we can
        if "pattern_id" in cm.columns:
            oo = oo[oo["pattern_id"].isin(cm["pattern_id"].tolist())].copy()

        # Optional horizon filter
        if horizon is not None and "horizon" in oo.columns:
            oo = oo[oo["horizon"] == int(horizon)].copy()

        if not oo.empty:
            # Sort by best available stability columns
            sort_cols = [c for c in ["stability_oos_score", "oos_avg_ticks", "sign_consistency"] if c in oo.columns]
            if sort_cols:
                oo = oo.sort_values(sort_cols, ascending=[False] * len(sort_cols))

            # Render only columns that actually exist
            preferred = [
                "pattern_id", "horizon",
                "oos_n", "oos_avg_ticks", "oos_net_ticks",
                "stability_oos_score", "fold_dispersion", "sign_consistency",
            ]
            keep = [c for c in preferred if c in oo.columns]

            html.append("<h4 style='margin:10px 0 6px 0;'>Member OOS stability</h4>")
            html.append(_render_table(oo[keep].round(4), max_rows=top_k_members))

    html.append("</div>")  # card
    html.append("</div>")  # wrapper

    # pe = pkg.metadata["derived"]["pattern_engine"]
    # arts = pe["artifacts"]

    # import pandas as pd
    #
    # def show(name):
    #     p = arts.get(name, {}).get("path")
    #     if not p:
    #         print(name, "MISSING")
    #         return
    #     df = pd.read_parquet(p)
    #     print(name, "rows", len(df), "cols", list(df.columns)[:20])
    #
    # for k in ["clusters", "cluster_members", "cluster_stats", "oos_stats", "mc_summary"]:
    #     show(k)
    return "\n".join(html)