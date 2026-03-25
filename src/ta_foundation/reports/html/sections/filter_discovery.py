from __future__ import annotations

from typing import Any, Optional
import re
import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.reports.html.embed import fig_to_base64_png
from ta_foundation.analysis.trade_feature_store import FeatureStoreConfig, build_trade_feature_frame
from ta_foundation.analysis.recommendations import RecommendationConfig, build_recommendations

from ta_foundation.analysis.trade_entry_signal_store import (
    EntrySignalStoreConfig,
    build_trade_entry_signal_frame,
)


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _metric_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    x = df.copy()
    x["pnl"] = pd.to_numeric(x.get("pnl"), errors="coerce")
    g = x.groupby(group_col, dropna=False)
    out = pd.DataFrame(
        {
            "trades": g.size(),
            "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0),
            "net_pnl": g["pnl"].sum(min_count=1),
            "avg_pnl": g["pnl"].mean(),
        }
    ).reset_index()
    out = out.sort_values("net_pnl", ascending=False)
    return out


def _render_df_table(df: pd.DataFrame, max_rows: int = 12) -> str:
    df = df.head(max_rows).copy()
    cols = list(df.columns)
    out = ["<table class='table'>"]
    out.append("<tr>" + "".join([f"<th>{_h(str(c))}</th>" for c in cols]) + "</tr>")
    for _, r in df.iterrows():
        out.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in cols]) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def _matches(regex: Optional[str], text: str) -> bool:
    if not regex:
        return True
    try:
        return re.search(regex, text) is not None
    except Exception:
        return True


def _write_json(path: Path, payload: dict) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
        return True, ""
    except Exception as e:
        return False, str(e)


def _bucket_near_zero(x: pd.Series, edges: list[float], labels: list[str]) -> pd.Categorical:
    v = pd.to_numeric(x, errors="coerce").abs()
    bins = [-1e18] + edges + [1e18]
    return pd.cut(v, bins=bins, labels=labels)

def _build_actionable_insights(
    es: pd.DataFrame,
    bucket_cols: list[str],
    min_trades: int = 10,
    top_n: int = 10,
) -> pd.DataFrame:
    if es is None or es.empty:
        return pd.DataFrame()

    es = es.copy()
    es["pnl"] = pd.to_numeric(es.get("pnl"), errors="coerce")

    insights = []

    for run_id, run_df in es.groupby("run_id", dropna=False):
        run_df = run_df.copy()
        run_df = run_df.dropna(subset=["pnl"])
        if len(run_df) < min_trades:
            continue

        baseline_avg = run_df["pnl"].mean()

        for bucket_col in bucket_cols:
            if bucket_col not in run_df.columns:
                continue

            for bucket, sub in run_df.groupby(bucket_col, dropna=True):
                if bucket is None or len(sub) < min_trades:
                    continue

                avg_pnl = sub["pnl"].mean()
                win_rate = (sub["pnl"] > 0).mean()
                edge = avg_pnl - baseline_avg
                score = edge * (len(sub) ** 0.5)

                insights.append(
                    {
                        "run_id": run_id,
                        "feature": bucket_col.replace("_bucket", ""),
                        "condition": str(bucket),
                        "trades": len(sub),
                        "win_rate": round(win_rate, 3),
                        "avg_pnl": round(avg_pnl, 2),
                        "edge_vs_run": round(edge, 2),
                        "score": round(score, 2),
                    }
                )

    if not insights:
        return pd.DataFrame()

    df = pd.DataFrame(insights)
    df = df.sort_values("score", ascending=False).head(top_n)
    return df

def render_filter_discovery(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    section = ctx.get("section") or {}
    opts = (section.get("options") or {}) if isinstance(section, dict) else {}

    bar_tf = str(opts.get("bar_tf", "5m"))
    htf_tf = str(opts.get("htf_tf", "15m"))
    ema_period = int(opts.get("ema_period", 50))
    atr_period = int(opts.get("atr_period", 14))
    include_micro = bool(opts.get("include_micro", True))

    top_n = int(opts.get("top_n_runs", 12))
    min_trades = int(opts.get("min_trades", 50))
    sort_by = str(opts.get("sort_by", "net_pnl")).strip().lower()
    include_regex = opts.get("include_run_id_regex")
    exclude_regex = opts.get("exclude_run_id_regex")

    rec_enable = bool(opts.get("write_recommendations_json", True))
    rec_min_trades_run = int(opts.get("rec_min_trades_run", max(100, min_trades)))
    rec_min_trades_bucket = int(opts.get("rec_min_trades_bucket", 25))
    rec_max_hour_buckets = int(opts.get("rec_max_hour_buckets", 4))
    rec_max_atr_quartiles = int(opts.get("rec_max_atr_quartiles", 3))
    rec_require_better_by = float(opts.get("rec_require_better_by", 0.0))
    out_dir = str(opts.get("out_dir"))

    # Entry Signal Discovery options
    entry_signal_enable = bool(opts.get("entry_signal_enable", False))
    entry_tick_size = opts.get("tick_size", 0.25)
    entry_prev_bars = int(opts.get("entry_prev_bars", 1))
    entry_swing_k = int(opts.get("entry_swing_k", 2))
    entry_near_edges_atr = opts.get("entry_near_edges_atr", [0.5, 1.0, 2.0])
    entry_near_labels = opts.get("entry_near_labels", ["<=0.5 ATR", "0.5–1 ATR", "1–2 ATR", ">2 ATR"])

    # Futures session controls (Denver time)
    entry_session_start = str(opts.get("session_start", "07:30"))
    entry_globex_start = str(opts.get("globex_start", "16:00"))
    entry_opening_range_minutes = int(opts.get("opening_range_minutes", 15))

    # NEW: per-run entry signal rendering controls
    entry_per_run = bool(opts.get("entry_signal_per_run", True))
    entry_per_run_top_n = int(opts.get("entry_signal_per_run_top_n", 12))
    entry_per_run_min_trades = int(opts.get("entry_signal_per_run_min_trades", 1))
    entry_show_run_coverage = bool(opts.get("entry_signal_show_run_coverage", True))

    cfg = FeatureStoreConfig(
        bar_tf=bar_tf,
        htf_tf=htf_tf,
        ema_period=ema_period,
        atr_period=atr_period,
        include_micro=include_micro,
    )

    cache = ctx.setdefault("_cache", {})
    cache_key = f"trade_features|{bar_tf}|{htf_tf}|ema{ema_period}|atr{atr_period}|micro{int(include_micro)}"
    feats = cache.get(cache_key)
    if feats is None:
        feats = build_trade_feature_frame(packages, market, cfg)
        cache[cache_key] = feats

    html: list[str] = []
    html.append("<div class='section'>")
    html.append("<div class='card'>")
    html.append("<h3>Filter Discovery — per bot (run_id)</h3>")

    if feats is None or feats.empty or "run_id" not in feats.columns:
        html.append("<div class='muted'>No feature data available. Ensure market bars are loaded and trades include Instrument.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    html.append(
        f"<div class='muted'>Features built from bars ({_h(bar_tf)} / {_h(htf_tf)})"
        + (" + ticks" if include_micro else "")
        + f". Total trades: <b>{len(feats):,}</b></div>"
    )
    html.append("</div>")  # header card

    # -----------------------
    # Entry Signal Discovery
    # -----------------------
    if entry_signal_enable:
        es_key = (
            f"entry_signals|{bar_tf}|atr{atr_period}|k{entry_swing_k}|prev{entry_prev_bars}"
            f"|tick{entry_tick_size}|ss{entry_session_start}|gs{entry_globex_start}|or{entry_opening_range_minutes}"
        )
        es = cache.get(es_key)
        if es is None:
            es_cfg = EntrySignalStoreConfig(
                bar_tf=bar_tf,
                atr_period=atr_period,
                swing_k=entry_swing_k,
                session_start=entry_session_start,
                globex_start=entry_globex_start,
                opening_range_minutes=entry_opening_range_minutes,
                tick_size=float(entry_tick_size) if entry_tick_size is not None else None,
                prev_bars=entry_prev_bars,
            )
            es = build_trade_entry_signal_frame(packages, market, es_cfg)
            cache[es_key] = es

        html.append("<div class='card'>")
        html.append("<h3>Entry Signal Discovery (context @ entry)</h3>")

        if es is None or es.empty:
            html.append("<div class='muted'>No entry-signal feature data available. Check that market bars are loaded for your instrument/timeframe.</div>")
            html.append("</div>")
        else:
            html.append(
                "<div class='muted'>"
                f"Trades with entry-context features: <b>{len(es):,}</b> • "
                f"Session start: <b>{_h(entry_session_start)}</b> • "
                f"Globex start: <b>{_h(entry_globex_start)}</b> • "
                f"OR: <b>{entry_opening_range_minutes}m</b>"
                "</div>"
            )

            es2 = es.copy()
            es2["pnl"] = pd.to_numeric(es2.get("pnl"), errors="coerce")

            # --- NEW: run coverage table (do we actually have multiple bots?) ---
            if entry_show_run_coverage and "run_id" in es2.columns:
                rs_cov = (
                    es2.groupby("run_id", dropna=False)
                    .apply(lambda g: pd.Series({
                        "trades": int(len(g)),
                        "net_pnl": float(pd.to_numeric(g.get("pnl"), errors="coerce").sum(min_count=1) or 0.0),
                        "avg_pnl": float(pd.to_numeric(g.get("pnl"), errors="coerce").mean() or 0.0),
                        "win_rate": float((pd.to_numeric(g.get("pnl"), errors="coerce") > 0).mean()) if len(g) else 0.0,
                    }))
                    .reset_index()
                    .sort_values("trades", ascending=False)
                    .head(entry_per_run_top_n)
                )
                html.append("<h4 style='margin-top:12px;'>Entry-signal run coverage</h4>")
                html.append(_render_df_table(rs_cov, max_rows=entry_per_run_top_n))

            # Bucket distance features in ATR units
            for col in (
                "d_pd_high_atr", "d_pd_low_atr", "d_pd_pp_atr",
                "d_or_high_atr", "d_or_low_atr",
                "d_on_high_atr", "d_on_low_atr",
            ):
                if col in es2.columns:
                    es2[col + "_bucket"] = _bucket_near_zero(
                        es2[col], edges=list(entry_near_edges_atr), labels=list(entry_near_labels)
                    )

            def _panel_all(title: str, bucket_col: str) -> None:
                if bucket_col in es2.columns:
                    html.append(f"<h4 style='margin-top:12px;'>{_h(title)} — ALL runs pooled</h4>")
                    t = _metric_table(es2.dropna(subset=[bucket_col]), bucket_col)
                    html.append(_render_df_table(t, max_rows=12))

            def _panel_per_run(title: str, bucket_col: str) -> None:
                if bucket_col not in es2.columns:
                    return

                # Choose runs by trade count in es2 (entry-signal-valid trades)
                rs = (
                    es2.groupby("run_id", dropna=False)
                    .size()
                    .reset_index(name="trades")
                    .sort_values("trades", ascending=False)
                )
                rs = rs[rs["trades"] >= entry_per_run_min_trades].head(entry_per_run_top_n)

                html.append(f"<h4 style='margin-top:12px;'>{_h(title)} — per run_id</h4>")
                if rs.empty:
                    html.append(
                        f"<div class='muted'>No runs have ≥ {entry_per_run_min_trades} entry-signal trades.</div>"
                    )
                    return

                for _, r in rs.iterrows():
                    rid = str(r["run_id"])
                    sub = es2[es2["run_id"].astype(str) == rid].dropna(subset=[bucket_col])
                    if sub.empty:
                        continue

                    html.append("<details style='margin-top:10px;'>")
                    html.append(
                        f"<summary><b>{_h(rid)}</b> "
                        f"<span class='muted'>({int(r['trades']):,} trades)</span></summary>"
                    )
                    t = _metric_table(sub, bucket_col)
                    html.append(_render_df_table(t, max_rows=12))
                    html.append("</details>")

            # Keep pooled view optionally (default: show it)
            show_pooled = bool(opts.get("entry_signal_show_pooled", True))

            if show_pooled:
                _panel_all("PnL by proximity to Prev-Day High (abs dist / ATR)", "d_pd_high_atr_bucket")

            if entry_per_run:
                _panel_per_run("PnL by proximity to Prev-Day High (abs dist / ATR)", "d_pd_high_atr_bucket")
                _panel_per_run("PnL by proximity to Prev-Day Low (abs dist / ATR)", "d_pd_low_atr_bucket")
                _panel_per_run("PnL by proximity to Prev-Day Pivot (PP) (abs dist / ATR)", "d_pd_pp_atr_bucket")
                _panel_per_run("PnL by proximity to Opening Range High (ORH) (abs dist / ATR)", "d_or_high_atr_bucket")
                _panel_per_run("PnL by proximity to Opening Range Low (ORL) (abs dist / ATR)", "d_or_low_atr_bucket")
                _panel_per_run("PnL by proximity to Overnight High (ONH) (abs dist / ATR)", "d_on_high_atr_bucket")
                _panel_per_run("PnL by proximity to Overnight Low (ONL) (abs dist / ATR)", "d_on_low_atr_bucket")
            else:
                # fallback: pooled only
                _panel_all("PnL by proximity to Prev-Day Low (abs dist / ATR)", "d_pd_low_atr_bucket")
                _panel_all("PnL by proximity to Prev-Day Pivot (PP) (abs dist / ATR)", "d_pd_pp_atr_bucket")
                _panel_all("PnL by proximity to Opening Range High (ORH) (abs dist / ATR)", "d_or_high_atr_bucket")
                _panel_all("PnL by proximity to Opening Range Low (ORL) (abs dist / ATR)", "d_or_low_atr_bucket")
                _panel_all("PnL by proximity to Overnight High (ONH) (abs dist / ATR)", "d_on_high_atr_bucket")
                _panel_all("PnL by proximity to Overnight Low (ONL) (abs dist / ATR)", "d_on_low_atr_bucket")

        html.append("</div>")  # card

    # --------- ACTIONABLE INSIGHTS ---------
    bucket_cols = [
        "d_pd_high_atr_bucket",
        "d_pd_low_atr_bucket",
        "d_pd_pp_atr_bucket",
        "d_or_high_atr_bucket",
        "d_or_low_atr_bucket",
        "d_on_high_atr_bucket",
        "d_on_low_atr_bucket",
    ]

    insights_df = _build_actionable_insights(
        es2,
        bucket_cols=bucket_cols,
        min_trades=int(opts.get("entry_signal_insight_min_trades", 10)),
        top_n=int(opts.get("entry_signal_insight_top_n", 10)),
    )

    if not insights_df.empty:
        html.append("<h3 style='margin-top:18px;'>🔥 Entry Signal Actionable Insights (Top Standouts)</h3>")
        html.append(_render_df_table(insights_df, max_rows=len(insights_df)))
    else:
        html.append(
            "<div class='muted' style='margin-top:12px;'>No actionable insights met minimum trade threshold.</div>")

    # -----------------------
    # Existing per-run cards (unchanged)
    # -----------------------
    f = feats.copy()
    f["pnl"] = pd.to_numeric(f.get("pnl"), errors="coerce")

    runs = []
    for rid in sorted(set([str(x) for x in f["run_id"].dropna().unique()])):
        if not _matches(include_regex, rid):
            continue
        if exclude_regex and _matches(exclude_regex, rid):
            continue
        runs.append(rid)

    if not runs:
        html.append("<div class='card'><div class='muted'>No runs match include/exclude filters.</div></div>")
        html.append("</div>")
        return "\n".join(html)

    g = f[f["run_id"].astype(str).isin(runs)].groupby("run_id")
    run_summary = pd.DataFrame(
        {
            "run_id": g.size().index,
            "trades": g.size().values,
            "net_pnl": g["pnl"].sum(min_count=1).values,
            "avg_pnl": g["pnl"].mean().values,
            "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0).values,
        }
    )

    run_summary = run_summary[run_summary["trades"] >= min_trades].copy()
    if run_summary.empty:
        html.append(f"<div class='card'><div class='muted'>No runs have at least {min_trades} trades after filtering.</div></div>")
        html.append("</div>")
        return "\n".join(html)

    if sort_by not in ("net_pnl", "trades", "win_rate"):
        sort_by = "net_pnl"
    run_summary = run_summary.sort_values(sort_by, ascending=False).head(top_n)

    for _, rs in run_summary.iterrows():
        rid = str(rs["run_id"])
        sub = f[f["run_id"].astype(str) == rid].copy()
        if sub.empty:
            continue

        sub["atr"] = pd.to_numeric(sub.get("atr"), errors="coerce")
        if sub["atr"].notna().sum() > 4:
            sub["atr_q"] = pd.qcut(sub["atr"], 4, labels=["Q1 (low)", "Q2", "Q3", "Q4 (high)"], duplicates="drop")
        else:
            sub["atr_q"] = pd.NA

        sub["htf_slope"] = pd.to_numeric(sub.get("htf_ema_slope"), errors="coerce")
        sub["htf_slope_sign"] = pd.cut(sub["htf_slope"], bins=[-1e18, 0, 1e18], labels=["<=0 (down/flat)", ">0 (up)"])

        sub["vwap_dist_atr"] = pd.to_numeric(sub.get("vwap_dist_atr"), errors="coerce")
        sub["vwap_side"] = pd.cut(sub["vwap_dist_atr"], bins=[-1e18, 0, 1e18], labels=["Below VWAP", "Above VWAP"])

        sub["entry_hour"] = pd.to_numeric(sub.get("entry_hour"), errors="coerce")
        sub["hour_bucket"] = pd.cut(
            sub["entry_hour"],
            bins=[-1, 3, 6, 9, 12, 15, 18, 21, 24],
            labels=["0-3", "4-6", "7-9", "10-12", "13-15", "16-18", "19-21", "22-24"],
        )

        html.append("<div class='card'>")
        html.append(f"<h3 style='margin-bottom:6px;'>{_h(rid)}</h3>")
        html.append(
            "<div class='muted'>"
            f"Trades: <b>{int(rs['trades']):,}</b> • "
            f"Net PnL: <b>{rs['net_pnl']}</b> • "
            f"Avg/Trade: <b>{rs['avg_pnl']}</b> • "
            f"Win rate: <b>{rs['win_rate']:.2%}</b>"
            "</div>"
        )

        html.append("<h4 style='margin-top:12px;'>PnL by ATR quartile</h4>")
        t_atr = _metric_table(sub.dropna(subset=["atr_q"]), "atr_q") if sub["atr_q"].notna().any() else pd.DataFrame()
        html.append(_render_df_table(t_atr, max_rows=10) if not t_atr.empty else "<div class='muted'>Not enough ATR data.</div>")

        html.append("<h4 style='margin-top:12px;'>PnL by HTF slope sign</h4>")
        t_slope = _metric_table(sub.dropna(subset=["htf_slope_sign"]), "htf_slope_sign")
        html.append(_render_df_table(t_slope, max_rows=10))

        html.append("<h4 style='margin-top:12px;'>PnL by VWAP side</h4>")
        t_vwap = _metric_table(sub.dropna(subset=["vwap_side"]), "vwap_side")
        html.append(_render_df_table(t_vwap, max_rows=10))

        html.append("<h4 style='margin-top:12px;'>PnL by time-of-day bucket</h4>")
        t_hour = _metric_table(sub.dropna(subset=["hour_bucket"]), "hour_bucket")
        html.append(_render_df_table(t_hour, max_rows=12))

        if not t_atr.empty:
            try:
                fig = plt.figure(figsize=(8, 3))
                ax = fig.add_subplot(111)
                x = t_atr.copy()
                order = {"Q1 (low)": 1, "Q2": 2, "Q3": 3, "Q4 (high)": 4}
                x["_o"] = x["atr_q"].astype(str).map(order).fillna(99)
                x = x.sort_values("_o")
                ax.bar([str(v) for v in x["atr_q"]], pd.to_numeric(x["net_pnl"], errors="coerce"))
                ax.set_title("Net PnL by ATR quartile")
                ax.set_xlabel("ATR regime")
                ax.set_ylabel("Net PnL")
                uri = fig_to_base64_png(fig)
                plt.close(fig)
                html.append("<div style='margin-top:12px;'>")
                html.append(f"<img src='{uri}' style='max-width:100%; border-radius:10px;'/>")
                html.append("</div>")
            except Exception as e:
                html.append(f"<div class='muted' style='margin-top:12px;'>Chart failed: {_h(str(e))}</div>")

        html.append("</div>")  # card

    html.append("</div>")  # section
    return "\n".join(html)