# src/ta_foundation/reports/html/sections/pattern_engine_overview.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
from collections import Counter

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png


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
    # fallback: first package
    return next(iter(packages.values()))


def _get_pe_meta(pkg: AnalysisPackage) -> Dict[str, Any]:
    md = getattr(pkg, "metadata", {}) or {}
    return (((md.get("derived") or {}).get("pattern_engine")) or {})


def _rank_clusters(
    *,
    clusters_df: pd.DataFrame,
    cluster_stats_df: pd.DataFrame,
    oos_stats_df: pd.DataFrame,
    mc_summary_df: pd.DataFrame,
    horizon: Optional[int],
) -> pd.DataFrame:
    """
    Return a "top clusters" table with best-available ranking:
      1) Monte Carlo prop_survival_score (cluster entity)
      2) OOS stability score (cluster entity)
      3) cluster_stats avg_ticks
    """
    # MC ranking
    if mc_summary_df is not None and not mc_summary_df.empty:
        m = mc_summary_df.copy()
        if "entity_type" in m.columns:
            m = m[m["entity_type"] == "cluster"].copy()
        if horizon is not None and "horizon" in m.columns:
            m = m[m["horizon"] == int(horizon)].copy()

        # keep only most relevant cols
        keep = [c for c in [
            "entity_id", "horizon", "prop_survival_score",
            "dd_p90", "trailing_dd_breach_prob", "daily_loss_breach_prob",
            "stress_slip_ticks", "n_paths"
        ] if c in m.columns]
        m = m[keep].copy()

        if "entity_id" in m.columns:
            m = m.rename(columns={"entity_id": "cluster_id"})
        # sort: higher prop score, lower breach probs
        sort_cols = [c for c in ["prop_survival_score", "trailing_dd_breach_prob", "daily_loss_breach_prob"] if c in m.columns]
        asc = [False, True, True][:len(sort_cols)]
        if sort_cols:
            m = m.sort_values(sort_cols, ascending=asc)
        # join cluster info if available
        if clusters_df is not None and not clusters_df.empty and "cluster_id" in clusters_df.columns:
            m = m.merge(
                clusters_df[["cluster_id", "n_members", "dispersion"]].copy(),
                on="cluster_id",
                how="left",
            )
        return m

    # OOS ranking
    if oos_stats_df is not None and not oos_stats_df.empty:
        o = oos_stats_df.copy()
        if "entity_type" in o.columns:
            o = o[o["entity_type"] == "cluster"].copy()
        if horizon is not None and "horizon" in o.columns:
            o = o[o["horizon"] == int(horizon)].copy()

        keep = [c for c in [
            "entity_id", "horizon", "stability_oos_score",
            "oos_avg_ticks", "oos_net_ticks", "oos_n",
            "fold_dispersion", "sign_consistency"
        ] if c in o.columns]
        o = o[keep].copy()
        if "entity_id" in o.columns:
            o = o.rename(columns={"entity_id": "cluster_id"})

        sort_cols = [c for c in ["stability_oos_score", "oos_avg_ticks"] if c in o.columns]
        if sort_cols:
            o = o.sort_values(sort_cols, ascending=[False, False][:len(sort_cols)])

        if clusters_df is not None and not clusters_df.empty and "cluster_id" in clusters_df.columns:
            o = o.merge(
                clusters_df[["cluster_id", "n_members", "dispersion"]].copy(),
                on="cluster_id",
                how="left",
            )
        return o

    # in-sample cluster stats
    if cluster_stats_df is not None and not cluster_stats_df.empty:
        cs = cluster_stats_df.copy()
        if horizon is not None and "horizon" in cs.columns:
            cs = cs[cs["horizon"] == int(horizon)].copy()

        # join cluster info if available
        if clusters_df is not None and not clusters_df.empty and "cluster_id" in clusters_df.columns:
            cs = cs.merge(
                clusters_df[["cluster_id", "n_members", "dispersion"]].copy(),
                on="cluster_id",
                how="left",
            )

        sort_cols = [c for c in ["avg_ticks", "n_signals"] if c in cs.columns]
        if sort_cols:
            cs = cs.sort_values(sort_cols, ascending=[False, False][:len(sort_cols)])
        return cs

    return pd.DataFrame()

def _available_horizons(df: pd.DataFrame) -> list[int]:
    if df is None or df.empty or "horizon" not in df.columns:
        return []
    try:
        return sorted([int(x) for x in df["horizon"].dropna().unique().tolist()])
    except Exception:
        return []

def render_pattern_engine_overview(ctx: Dict[str, Any]) -> str:
    """
    Pattern Engine Overview across runs.

    Contracts:
      - options from ctx["options"]
      - packages are AnalysisPackage objects (no reloads)
      - derived artifacts must be under pkg.metadata["derived"]["pattern_engine"]["artifacts"]
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    sec = ctx.get("options") or {}  # section options
    all_opts = ctx.get("all_options") or {}  # full yaml
    pe_opts = all_opts.get("pattern_engine") or {}

    # from ta_foundation.analysis.pattern_engine.orchestrator import compute_and_attach_pattern_engine
    # compute_and_attach_pattern_engine(ctx["packages"], ctx.get("market"), options=pe_opts)
    # print(pe_opts)
    # print("HAS_PATTERN_ENGINE_TOPLEVEL:", "pattern_engine" in (ctx.get("all_options") or {}))
    # run selection
    top_n = int(sec.get("top_n_runs", pe_opts.get("top_n_runs", 12)))
    include_regex = sec.get("include_run_id_regex", pe_opts.get("include_run_id_regex"))
    exclude_regex = sec.get("exclude_run_id_regex", pe_opts.get("exclude_run_id_regex"))
    run_id_focus = sec.get("run_id")  # optional: render only one run

    # cluster ranking options
    horizon = sec.get("horizon")  # optional: filter to one horizon
    horizon = int(horizon) if horizon is not None else None
    top_k_clusters = int(sec.get("top_k_clusters", 20))
    top_k_patterns = int(sec.get("top_k_patterns", 20))

    css = """
    <style>
      .tf-pe { display:flex; flex-direction:column; gap:14px; }
      .tf-card { border-radius:16px; padding:12px 14px; background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.06); }
      .tf-title { font-weight:900; font-size:1.02rem; margin-bottom:6px; }
      .tf-sub { opacity:0.82; font-size:0.86rem; }
      .tf-grid { display:grid; grid-template-columns: 1fr; gap:12px; margin-top:10px; }
      .tf-img { width:100%; height:auto; display:block; border-radius:10px; }
      .tf-kv { display:grid; grid-template-columns: 240px 1fr; gap:6px 12px; font-size:0.9rem; margin-top:8px; }
      .muted { opacity:0.75; }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .table { border-collapse: collapse; width:100%; }
      .table th, .table td { border-bottom: 1px solid rgba(255,255,255,0.08); padding: 6px 8px; text-align:left; }
    </style>
    """

    html: List[str] = [css, "<div class='tf-pe'>"]
    html.append("<div class='tf-card'>")
    html.append("<div class='tf-title'>Pattern Engine — Overview</div>")





    # now render sections using SECTION_REGISTRY
    # Decide which runs to render
    run_ids = list(packages.keys())
    if run_id_focus:
        run_ids = [run_id_focus] if run_id_focus in packages else []
    else:
        # filter by regex; then prefer runs with PE meta present
        filtered = []
        for rid in run_ids:
            if not _matches(include_regex, rid):
                continue
            if exclude_regex and _matches(exclude_regex, rid):
                continue
            filtered.append(rid)
        run_ids = filtered

    if not run_ids:
        html.append("<div class='muted'>No runs selected (check run_id or include/exclude regex).</div>")
        html.append("</div></div>")
        return "\n".join(html)

    # Score runs by "presence" and counts for picking top_n
    scored = []
    for rid in run_ids:
        pkg = packages[rid]
        meta = _get_pe_meta(pkg)
        counts = ((meta.get("diagnostics") or {}).get("counts") or {})
        # prefer runs where pattern engine ran and produced signals/outcomes
        score = 0
        if meta and not meta.get("disabled", False):
            score += 100
        score += int(counts.get("n_signals", 0) or 0) // 1000  # small bump
        scored.append((rid, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    chosen = [rid for rid, _ in scored[:max(1, top_n)]]



    # header kv
    html.append("<div class='tf-kv'>")
    html.append(f"<div class='muted'>Packages (runs)</div><div>{len(packages):,}</div>")
    html.append(f"<div class='muted'>Selected runs</div><div>{len(chosen):,} (top_n={top_n})</div>")
    if include_regex:
        html.append(f"<div class='muted'>include regex</div><div><code>{_h(str(include_regex))}</code></div>")
    if exclude_regex:
        html.append(f"<div class='muted'>exclude regex</div><div><code>{_h(str(exclude_regex))}</code></div>")
    if horizon is not None:
        html.append(f"<div class='muted'>Horizon filter</div><div>{horizon}</div>")
    html.append("</div>")
    html.append("</div>")  # header card

    # Per-run cards + collect cross-run cluster scores
    cross_rows = []
    skip = Counter()

    for rid in chosen:
        pkg = packages[rid]
        meta = _get_pe_meta(pkg)
        if not meta:
            skip["NO_PATTERN_ENGINE_META"] += 1
            continue
        if meta.get("disabled", False):
            skip["PATTERN_ENGINE_DISABLED"] += 1
            continue

        engine = meta.get("engine") or {}
        diag = meta.get("diagnostics") or {}
        counts = diag.get("counts") or {}
        issues = (diag.get("validation") or {}).get("issues") or []

        patterns = _read_artifact(meta, "patterns")
        pattern_stats = _read_artifact(meta, "pattern_stats")
        clusters = _read_artifact(meta, "clusters")
        cluster_stats = _read_artifact(meta, "cluster_stats")
        oos = _read_artifact(meta, "oos_stats")
        mc = _read_artifact(meta, "mc_summary")

        # rank clusters
        top_clusters = _rank_clusters(
            clusters_df=clusters,
            cluster_stats_df=cluster_stats,
            oos_stats_df=oos,
            mc_summary_df=mc,
            horizon=horizon,
        ).head(top_k_clusters)

        if top_clusters.empty and horizon is not None:
            # Try again without horizon filter so report always shows something.
            top_clusters = _rank_clusters(
                clusters_df=clusters,
                cluster_stats_df=cluster_stats,
                oos_stats_df=oos,
                mc_summary_df=mc,
                horizon=None,
            ).head(top_k_clusters)

        avail = {
            "cluster_stats": _available_horizons(cluster_stats),
            "oos_stats": _available_horizons(oos),
            "mc_summary": _available_horizons(mc),
        }

        # pick top patterns (raw)
        top_patterns = pd.DataFrame()
        if pattern_stats is not None and not pattern_stats.empty:
            ps = pattern_stats.copy()
            if horizon is not None and "horizon" in ps.columns:
                ps = ps[ps["horizon"] == int(horizon)].copy()
            sort_cols = [c for c in ["rank_score_raw", "avg_ticks", "n_signals"] if c in ps.columns]
            if sort_cols:
                ps = ps.sort_values(sort_cols, ascending=[False] * len(sort_cols))
            top_patterns = ps.head(top_k_patterns)

        # build chart: if we have cluster table with a score column
        chart_uri = None
        try:
            chart_df = top_clusters.copy()
            score_col = None
            for c in ["prop_survival_score", "stability_oos_score", "avg_ticks"]:
                if c in chart_df.columns:
                    score_col = c
                    break
            if score_col and "cluster_id" in chart_df.columns and not chart_df.empty:
                fig = plt.figure(figsize=(9.0, 2.8))
                ax = fig.add_subplot(111)
                ax.bar(chart_df["cluster_id"].tolist(), chart_df[score_col].astype(float).tolist())
                ax.axhline(0, linewidth=1)
                ax.set_title(f"{rid} — Top clusters by {score_col}")
                ax.set_ylabel(score_col)
                fig.tight_layout()
                chart_uri = fig_to_base64_png(fig)
                plt.close(fig)
        except Exception:
            chart_uri = None

        html.append("<div class='tf-card'>")
        html.append(f"<div class='tf-title'>{_h(rid)}</div>")
        html.append(
            "<div class='tf-sub muted'>"
            f"Instrument: {_h(str(engine.get('instrument','')))} {_h(str(engine.get('contract','')))} • "
            f"bar_tf: {_h(str(engine.get('bar_tf','')))} • tick_size: {_h(str(engine.get('tick_size','')))} • "
            f"patterns: {int(len(patterns)) if patterns is not None else 0:,} • "
            f"signals: {int(counts.get('n_signals', 0) or 0):,} • "
            f"clusters: {int(counts.get('n_clusters', 0) or 0):,}"
             f"Available horizons — cluster_stats: {avail['cluster_stats']} • "
            f"oos_stats: {avail['oos_stats']} • "
            f"mc_summary: {avail['mc_summary']}"
            "</div>"
        )

        # issues
        if issues:
            html.append("<div class='muted' style='margin-top:6px;'>Validation issues:</div>")
            df_issues = pd.DataFrame([{"issue": str(x)} for x in issues[:10]])
            html.append(_render_table(df_issues, max_rows=10))

        html.append("<div class='tf-grid'>")
        html.append("<div>")
        html.append("<h4 style='margin:8px 0 6px 0;'>Top clusters</h4>")
        html.append(_render_table(top_clusters.round(4) if not top_clusters.empty else top_clusters, max_rows=top_k_clusters))
        html.append("</div>")

        if chart_uri:
            html.append("<div>")
            html.append(f"<img class='tf-img' src='{chart_uri}' alt='top clusters chart' />")
            html.append("</div>")

        html.append("<div>")
        html.append("<h4 style='margin:10px 0 6px 0;'>Top patterns (raw)</h4>")
        html.append(_render_table(top_patterns.round(4) if not top_patterns.empty else top_patterns, max_rows=top_k_patterns))
        html.append("</div>")

        html.append("</div>")  # grid
        html.append("</div>")  # card

        # cross-run aggregation payload: try to normalize into (run_id, cluster_id, score)
        try:
            if top_clusters is not None and not top_clusters.empty:
                tmp = top_clusters.copy()
                tmp["run_id"] = rid
                # determine score col
                score_col = None
                for c in ["prop_survival_score", "stability_oos_score", "avg_ticks"]:
                    if c in tmp.columns:
                        score_col = c
                        break
                if score_col and "cluster_id" in tmp.columns:
                    cross_rows.append(tmp[["run_id", "cluster_id", score_col]].rename(columns={score_col: "score"}))
        except Exception:
            pass

    # Skip summary card if needed
    if skip:
        html.append("<div class='tf-card'>")
        html.append("<div class='tf-title'>Skipped runs</div>")
        df_skip = pd.DataFrame([{"reason": k, "count": int(v)} for k, v in skip.most_common()])
        html.append(_render_table(df_skip, max_rows=50))
        html.append("</div>")

    # Cross-run card
    if cross_rows:
        try:
            big = pd.concat(cross_rows, axis=0, ignore_index=True)
            # wide table: runs x cluster_id
            piv = big.pivot(index="run_id", columns="cluster_id", values="score").fillna(0.0)
            piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
            # overall cluster mean score
            mean_score = big.groupby("cluster_id")["score"].mean().sort_values(ascending=False).reset_index()
            mean_score = mean_score.rename(columns={"score": "mean_score"})

            html.append("<div class='tf-card'>")
            html.append("<div class='tf-title'>Cross-run cluster comparison</div>")
            html.append("<div class='tf-sub muted'>Aggregated across the displayed runs above (top clusters per run).</div>")

            html.append("<h4 style='margin:8px 0 6px 0;'>Clusters — mean score across runs</h4>")
            html.append(_render_table(mean_score.round(4), max_rows=50))

            html.append("<h4 style='margin:10px 0 6px 0;'>Runs × clusters (score)</h4>")
            html.append(_render_table(piv.round(4).reset_index(), max_rows=top_n))
            html.append("</div>")
        except Exception:
            pass

    html.append("</div>")  # wrapper
    return "\n".join(html)