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


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _metric_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    x = df.copy()
    x["pnl"] = pd.to_numeric(x.get("pnl"), errors="coerce")
    g = x.groupby(group_col, dropna=False)
    out = pd.DataFrame({
        "trades": g.size(),
        "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0),
        "net_pnl": g["pnl"].sum(min_count=1),
        "avg_pnl": g["pnl"].mean(),
    }).reset_index()
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


def _find_output_dir(ctx: dict[str, Any]) -> Optional[Path]:
    """
    Best-effort: different pipelines expose this differently.
    We try a few common keys and normalize to Path.
    """
    candidates = [
        ctx.get("output_dir"),
        ctx.get("output_folder"),
        ctx.get("out_dir"),
        (ctx.get("options") or {}).get("output_dir"),
        # (ctx.get("report_config") or {}).get("output_dir"),
        # (ctx.get("report_config") or {}).get("output_folder"),
    ]
    for c in candidates:
        if not c:
            continue
        try:
            p = Path(str(c))
            return p
        except Exception:
            continue
    return None


def _write_json(path: Path, payload: dict) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
        return True, ""
    except Exception as e:
        return False, str(e)


def render_filter_discovery(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    section = ctx.get("section") or {}
    opts = (section.get("options") or {}) if isinstance(section, dict) else {}

    # Feature build options
    bar_tf = str(opts.get("bar_tf", "5m"))
    htf_tf = str(opts.get("htf_tf", "15m"))
    ema_period = int(opts.get("ema_period", 50))
    atr_period = int(opts.get("atr_period", 14))
    include_micro = bool(opts.get("include_micro", True))

    # Per-run rendering controls
    top_n = int(opts.get("top_n_runs", 12))
    min_trades = int(opts.get("min_trades", 50))
    sort_by = str(opts.get("sort_by", "net_pnl")).strip().lower()
    include_regex = opts.get("include_run_id_regex")
    exclude_regex = opts.get("exclude_run_id_regex")

    # Recommendations options
    rec_enable = bool(opts.get("write_recommendations_json", True))
    rec_min_trades_run = int(opts.get("rec_min_trades_run", max(100, min_trades)))
    rec_min_trades_bucket = int(opts.get("rec_min_trades_bucket", 25))
    rec_max_hour_buckets = int(opts.get("rec_max_hour_buckets", 4))
    rec_max_atr_quartiles = int(opts.get("rec_max_atr_quartiles", 3))
    rec_require_better_by = float(opts.get("rec_require_better_by", 0.0))
    out_dir = str(opts.get("out_dir"))

    cfg = FeatureStoreConfig(
        bar_tf=bar_tf,
        htf_tf=htf_tf,
        ema_period=ema_period,
        atr_period=atr_period,
        include_micro=include_micro,
    )

    # In-report cache
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
        html.append("<div class='muted'>No feature data available. Ensure --market-data is provided and trades include an Instrument like 'NQ 03-26'.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    html.append(
        f"<div class='muted'>Features built from bars ({_h(bar_tf)} / {_h(htf_tf)})"
        + (" + ticks" if include_micro else "")
        + f". Total trades: <b>{len(feats):,}</b></div>"
    )

    # Build + write recommendations.json once per report build
    rec_status = None
    rec_path = None
    if rec_enable and not cache.get("_recommendations_written"):
        # Ensure required bucket columns exist (the report already creates these per-run; here we compute once globally)
        f_all = feats.copy()
        f_all["pnl"] = pd.to_numeric(f_all.get("pnl"), errors="coerce")

        # Global bucketing (same as report)
        f_all["atr"] = pd.to_numeric(f_all.get("atr"), errors="coerce")
        if f_all["atr"].notna().sum() > 4:
            f_all["atr_q"] = pd.qcut(f_all["atr"], 4, labels=["Q1 (low)", "Q2", "Q3", "Q4 (high)"], duplicates="drop")
        else:
            f_all["atr_q"] = pd.NA

        f_all["htf_slope"] = pd.to_numeric(f_all.get("htf_ema_slope"), errors="coerce")
        f_all["htf_slope_sign"] = pd.cut(f_all["htf_slope"], bins=[-1e18, 0, 1e18], labels=["<=0 (down/flat)", ">0 (up)"])

        f_all["vwap_dist_atr"] = pd.to_numeric(f_all.get("vwap_dist_atr"), errors="coerce")
        f_all["vwap_side"] = pd.cut(f_all["vwap_dist_atr"], bins=[-1e18, 0, 1e18], labels=["Below VWAP", "Above VWAP"])

        f_all["entry_hour"] = pd.to_numeric(f_all.get("entry_hour"), errors="coerce")
        f_all["hour_bucket"] = pd.cut(
            f_all["entry_hour"],
            bins=[-1, 3, 6, 9, 12, 15, 18, 21, 24],
            labels=["0-3", "4-6", "7-9", "10-12", "13-15", "16-18", "19-21", "22-24"],
        )

        # Apply include/exclude filters to run_ids (same semantics as report)
        if include_regex:
            f_all = f_all[f_all["run_id"].astype(str).apply(lambda s: _matches(include_regex, str(s)))]
        if exclude_regex:
            f_all = f_all[~f_all["run_id"].astype(str).apply(lambda s: _matches(exclude_regex, str(s)))]

        rec_cfg = RecommendationConfig(
            min_trades_run=rec_min_trades_run,
            min_trades_bucket=rec_min_trades_bucket,
            require_positive_if_better_by=rec_require_better_by,
            max_hour_buckets=rec_max_hour_buckets,
            max_atr_quartiles=rec_max_atr_quartiles,
        )

        payload = build_recommendations(
            f_all,
            cfg=rec_cfg,
            feature_config={
                "bar_tf": bar_tf,
                "htf_tf": htf_tf,
                "ema_period": ema_period,
                "atr_period": atr_period,
                "include_micro": include_micro,
            },
        )

        output_dir =  Path(str(out_dir))
        if out_dir is not None:
            rec_path = output_dir / "recommendations.json"
            ok, err = _write_json(rec_path, payload)
            cache["_recommendations_written"] = True
            rec_status = ("ok" if ok else "error", err)
        else:
            rec_status = ("error", "Could not determine output directory from report context; no file written.")
            cache["_recommendations_written"] = True
            cache["_recommendations_payload"] = payload  # keep accessible for debugging

    # Show status in report header card
    if rec_enable:
        if rec_status is None and cache.get("_recommendations_written"):
            # likely written by prior section render in this build
            html.append("<div class='muted' style='margin-top:8px;'><b>recommendations.json</b>: generated (see outputs folder).</div>")
        elif rec_status is not None:
            status, err = rec_status
            if status == "ok":
                html.append("<div class='muted' style='margin-top:8px;'><b>recommendations.json</b>: written successfully.</div>")
            else:
                html.append(f"<div class='muted' style='margin-top:8px;'><b>recommendations.json</b>: NOT written — {_h(err)}</div>")

    html.append("</div>")  # header card

    # -----------------------
    # Per-run selection list
    # -----------------------
    f = feats.copy()
    f["pnl"] = pd.to_numeric(f.get("pnl"), errors="coerce")

    # Filter by regex
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
    run_summary = pd.DataFrame({
        "run_id": g.size().index,
        "trades": g.size().values,
        "net_pnl": g["pnl"].sum(min_count=1).values,
        "avg_pnl": g["pnl"].mean().values,
        "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0).values,
    })

    run_summary = run_summary[run_summary["trades"] >= min_trades].copy()
    if run_summary.empty:
        html.append(f"<div class='card'><div class='muted'>No runs have at least {min_trades} trades after filtering.</div></div>")
        html.append("</div>")
        return "\n".join(html)

    if sort_by not in ("net_pnl", "trades", "win_rate"):
        sort_by = "net_pnl"
    run_summary = run_summary.sort_values(sort_by, ascending=False).head(top_n)

    # -----------------------
    # Render per-run cards
    # -----------------------
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
