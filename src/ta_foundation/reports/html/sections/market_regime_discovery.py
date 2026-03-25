from __future__ import annotations

from typing import Any, Optional
import re
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import pandas as pd
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.analysis.trade_feature_store import FeatureStoreConfig, build_trade_feature_frame
from ta_foundation.analysis.market_regime_store import (
    MarketRegimeConfig,
    build_market_regime_frame,
    summarize_regime_performance,
    summarize_entry_hour_performance,
    summarize_entry_hour_drawdown_risk,
    summarize_entry_hour_risk,
    optimize_entry_hour_window,
    order_session_summary,
    pick_best_worst_regimes,
    build_regime_validation_summary,
)
from ta_foundation.reports.html.embed import fig_to_base64_png


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _matches(regex: Optional[str], text: str) -> bool:
    if not regex:
        return True
    try:
        return re.search(regex, text) is not None
    except Exception:
        return True


def _render_df_table(df: pd.DataFrame, max_rows: int = 12) -> str:
    if df is None or df.empty:
        return "<div class='muted'>No rows.</div>"
    z = df.head(max_rows).copy()
    for c in z.columns:
        if pd.api.types.is_float_dtype(z[c]):
            z[c] = z[c].map(lambda v: "" if pd.isna(v) else round(float(v), 4))
    cols = list(z.columns)
    out = ["<div class='mrd-table-wrap'><table class='table mrd-table'>"]
    out.append("<tr>" + "".join([f"<th>{_h(str(c))}</th>" for c in cols]) + "</tr>")
    for _, r in z.iterrows():
        out.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in cols]) + "</tr>")
    out.append("</table></div>")
    return "\n".join(out)


def _run_baseline_card(sub: pd.DataFrame) -> str:
    pnl = pd.to_numeric(sub.get("pnl"), errors="coerce")
    trades = int(len(sub))
    net_pnl = float(pnl.sum(min_count=1)) if trades else 0.0
    avg_pnl = float(pnl.mean()) if trades else 0.0
    win_rate = float((pnl > 0).mean()) if trades else 0.0
    return (
        "<div class='muted'>"
        f"Trades: <b>{trades:,}</b> • "
        f"Net PnL: <b>{round(net_pnl, 2)}</b> • "
        f"Avg/Trade: <b>{round(avg_pnl, 4)}</b> • "
        f"Win rate: <b>{win_rate:.2%}</b>"
        "</div>"
    )


def _fig_uri(fig) -> str:
    uri = fig_to_base64_png(fig)
    plt.close(fig)
    return uri


def _render_session_edge_chart(summary_df: pd.DataFrame) -> str:
    if summary_df is None or summary_df.empty or "session_regime" not in summary_df.columns:
        return "<div class='muted'>No session chart data.</div>"
    x = summary_df.copy()
    x["edge_vs_baseline"] = pd.to_numeric(x.get("edge_vs_baseline"), errors="coerce")
    x = x.dropna(subset=["edge_vs_baseline"])
    if x.empty:
        return "<div class='muted'>No session chart data.</div>"
    try:
        fig = plt.figure(figsize=(8, 3))
        ax = fig.add_subplot(111)
        ax.bar(x["session_regime"].astype(str), x["edge_vs_baseline"])
        ax.set_title("Session edge vs baseline")
        ax.set_xlabel("Session regime")
        ax.set_ylabel("Edge vs baseline")
        ax.tick_params(axis="x", rotation=30)
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Session chart failed: {_h(str(e))}</div>"


def _render_entry_hour_edge_chart(hour_df: pd.DataFrame) -> str:
    if hour_df is None or hour_df.empty or "hour_label" not in hour_df.columns:
        return "<div class='muted'>No entry-hour chart data.</div>"
    x = hour_df.copy()
    x["edge_vs_baseline"] = pd.to_numeric(x.get("edge_vs_baseline"), errors="coerce")
    x = x.dropna(subset=["edge_vs_baseline"])
    if x.empty:
        return "<div class='muted'>No entry-hour chart data.</div>"
    try:
        fig = plt.figure(figsize=(10, 3.5))
        ax = fig.add_subplot(111)
        ax.bar(x["hour_label"].astype(str), x["edge_vs_baseline"])
        ax.set_title("Entry hour edge vs baseline")
        ax.set_xlabel("Entry hour (America/Denver)")
        ax.set_ylabel("Edge vs baseline")
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Entry-hour chart failed: {_h(str(e))}</div>"


def _render_entry_hour_mae_chart(dd_hour_df: pd.DataFrame) -> str:
    if dd_hour_df is None or dd_hour_df.empty or "hour_label" not in dd_hour_df.columns:
        return "<div class='muted'>No entry-hour drawdown chart data.</div>"
    x = dd_hour_df.copy()
    x["mae_p90"] = pd.to_numeric(x.get("mae_p90"), errors="coerce")
    x["mae_max"] = pd.to_numeric(x.get("mae_max"), errors="coerce")
    x = x.dropna(subset=["mae_p90", "mae_max"])
    if x.empty:
        return "<div class='muted'>No entry-hour drawdown chart data.</div>"
    try:
        fig = plt.figure(figsize=(10, 3.8))
        ax = fig.add_subplot(111)
        ax.plot(x["hour_label"].astype(str), x["mae_p90"], marker="o", label="MAE p90")
        ax.plot(x["hour_label"].astype(str), x["mae_max"], marker="o", label="MAE max")
        ax.set_title("Entry hour drawdown risk")
        ax.set_xlabel("Entry hour (America/Denver)")
        ax.set_ylabel("Adverse excursion (USD)")
        ax.legend()
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Entry-hour drawdown chart failed: {_h(str(e))}</div>"


def _render_entry_hour_stop_breach_chart(dd_hour_df: pd.DataFrame) -> str:
    if dd_hour_df is None or dd_hour_df.empty or "hour_label" not in dd_hour_df.columns:
        return "<div class='muted'>No stop-breach chart data.</div>"
    x = dd_hour_df.copy()
    x["stop_breach_rate"] = pd.to_numeric(x.get("stop_breach_rate"), errors="coerce")
    x = x.dropna(subset=["stop_breach_rate"])
    if x.empty:
        return "<div class='muted'>No stop-breach chart data.</div>"
    try:
        fig = plt.figure(figsize=(10, 3.2))
        ax = fig.add_subplot(111)
        ax.bar(x["hour_label"].astype(str), x["stop_breach_rate"])
        ax.set_title("Entry hour stop-breach rate")
        ax.set_xlabel("Entry hour (America/Denver)")
        ax.set_ylabel("Rate")
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Stop-breach chart failed: {_h(str(e))}</div>"


def _render_danger_score_chart(risk_df: pd.DataFrame) -> str:
    if risk_df is None or risk_df.empty or "hour_label" not in risk_df.columns:
        return "<div class='muted'>No danger-score chart data.</div>"
    x = risk_df.copy()
    x["danger_score"] = pd.to_numeric(x.get("danger_score"), errors="coerce")
    x = x.dropna(subset=["danger_score"])
    if x.empty:
        return "<div class='muted'>No danger-score chart data.</div>"
    try:
        fig = plt.figure(figsize=(10, 3.2))
        ax = fig.add_subplot(111)
        ax.bar(x["hour_label"].astype(str), x["danger_score"])
        ax.set_title("Entry hour danger score")
        ax.set_xlabel("Entry hour (America/Denver)")
        ax.set_ylabel("Danger score")
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Danger-score chart failed: {_h(str(e))}</div>"


def _render_session_optimizer_chart(win_df: pd.DataFrame) -> str:
    if win_df is None or win_df.empty:
        return "<div class='muted'>No session-optimizer chart data.</div>"
    x = win_df.head(12).copy()
    x["expected_net_pnl"] = pd.to_numeric(x.get("expected_net_pnl"), errors="coerce")
    x = x.dropna(subset=["expected_net_pnl"])
    if x.empty:
        return "<div class='muted'>No session-optimizer chart data.</div>"
    try:
        fig = plt.figure(figsize=(10, 3.5))
        ax = fig.add_subplot(111)
        ax.bar(x["window_label"].astype(str), x["expected_net_pnl"])
        ax.set_title("Top session windows by expected net PnL")
        ax.set_xlabel("Contiguous hour window")
        ax.set_ylabel("Expected net PnL")
        ax.tick_params(axis="x", rotation=30)
        uri = _fig_uri(fig)
        return f"<div style='margin-top:12px;'><img src='{uri}' style='max-width:100%; border-radius:10px;'/></div>"
    except Exception as e:
        return f"<div class='muted'>Session-optimizer chart failed: {_h(str(e))}</div>"


def _resolve_style_tokens(opts: dict[str, Any]) -> dict[str, str]:
    preset = str(opts.get("style_preset", "classic_dark") or "classic_dark").strip().lower()
    density = str(opts.get("style_density", "normal") or "normal").strip().lower()
    accent = str(opts.get("style_accent", "blue") or "blue").strip().lower()

    presets = {
        "classic_dark": {
            "panel": "rgba(255,255,255,0.02)",
            "soft": "rgba(255,255,255,0.03)",
            "shadow": "0 10px 25px rgba(0,0,0,0.12)",
            "radius": "14px",
        },
        "executive_dark": {
            "panel": "rgba(255,255,255,0.03)",
            "soft": "rgba(255,255,255,0.04)",
            "shadow": "0 14px 30px rgba(0,0,0,0.18)",
            "radius": "16px",
        },
        "compact_dark": {
            "panel": "rgba(255,255,255,0.018)",
            "soft": "rgba(255,255,255,0.028)",
            "shadow": "0 8px 18px rgba(0,0,0,0.10)",
            "radius": "12px",
        },
        "clean_light": {
            "panel": "rgba(0,0,0,0.03)",
            "soft": "rgba(0,0,0,0.04)",
            "shadow": "0 8px 18px rgba(0,0,0,0.06)",
            "radius": "14px",
        },
    }
    accents = {
        "blue": "#6aa6ff",
        "green": "#40c97b",
        "amber": "#f0b24b",
        "violet": "#9a7cff",
        "red": "#ff6a6a",
    }
    dens = {
        "compact": {"pad": "10px", "gap": "8px", "title": "14px", "sub": "11px"},
        "normal": {"pad": "12px", "gap": "10px", "title": "15px", "sub": "12px"},
        "comfortable": {"pad": "14px", "gap": "12px", "title": "16px", "sub": "12px"},
    }

    base = presets.get(preset, presets["classic_dark"]).copy()
    base.update(dens.get(density, dens["normal"]))
    base["accent"] = accents.get(accent, accents["blue"])
    base["danger"] = "#ff8a8a"
    base["warn"] = "#f0b24b"
    base["ok"] = "#6aff9a"
    base["preset"] = preset
    return base


_STYLE_BLOCK_ONCE = """
<style>
.mrd-summary-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:var(--mrd-gap,10px);margin-top:12px;}
.mrd-summary-card{grid-column:span 3;background:var(--mrd-panel,rgba(255,255,255,.02));border:1px solid var(--border);border-radius:var(--mrd-radius,14px);padding:var(--mrd-pad,12px);box-shadow:var(--mrd-shadow,none);}
.mrd-summary-card--wide{grid-column:span 6;}
.mrd-summary-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}
.mrd-summary-value{font-size:var(--mrd-title,15px);font-weight:700;line-height:1.25;}
.mrd-summary-sub{font-size:var(--mrd-sub,12px);color:var(--muted);margin-top:4px;}
.mrd-summary-badge{display:inline-block;margin-top:8px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-size:11px;font-weight:700;}
.mrd-summary-badge--ok{color:var(--mrd-ok,#6aff9a);}
.mrd-summary-badge--warn{color:var(--mrd-warn,#f0b24b);}
.mrd-summary-badge--danger{color:var(--mrd-danger,#ff8a8a);}
.mrd-run-top{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap;margin-top:10px;}
.mrd-run-main{flex:1 1 420px;min-width:320px;}
.mrd-run-side{flex:0 1 360px;min-width:260px;}
.mrd-strategy-card{background:var(--mrd-soft,rgba(255,255,255,.03));border:1px solid var(--border);border-radius:var(--mrd-radius,14px);padding:var(--mrd-pad,12px);box-shadow:var(--mrd-shadow,none);margin-top:12px;}
.mrd-strategy-card h4{margin:0 0 8px 0;font-size:14px;}
.mrd-strategy-card img{width:100%;height:auto;display:block;border-radius:10px;border:1px solid var(--border);}
.mrd-note{margin-top:10px;padding:10px 12px;border-left:3px solid var(--mrd-accent,#6aa6ff);background:var(--mrd-soft,rgba(255,255,255,.03));border-radius:10px;}
.mrd-table-wrap{width:100%;max-width:100%;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;margin-top:8px;border:1px solid var(--border);border-radius:10px;}
.mrd-table{min-width:max-content;margin:0;}
.mrd-table th,.mrd-table td{white-space:nowrap;}
@media (max-width: 980px){.mrd-summary-card,.mrd-summary-card--wide{grid-column:span 6;}}
@media (max-width: 720px){.mrd-summary-card,.mrd-summary-card--wide{grid-column:span 12;}.mrd-run-side,.mrd-run-main{min-width:100%;}}
</style>
"""


def _render_style_scope(tokens: dict[str, str]) -> str:
    return (
        "<div class='mrd-style-scope' style='"
        f"--mrd-panel:{tokens['panel']};"
        f"--mrd-soft:{tokens['soft']};"
        f"--mrd-shadow:{tokens['shadow']};"
        f"--mrd-radius:{tokens['radius']};"
        f"--mrd-pad:{tokens['pad']};"
        f"--mrd-gap:{tokens['gap']};"
        f"--mrd-title:{tokens['title']};"
        f"--mrd-sub:{tokens['sub']};"
        f"--mrd-accent:{tokens['accent']};"
        f"--mrd-danger:{tokens['danger']};"
        f"--mrd-warn:{tokens['warn']};"
        f"--mrd-ok:{tokens['ok']};"
        "'>"
    )


def _render_strategy_card(card_uri: Optional[str], run_id: str) -> str:
    if not card_uri:
        return "<div class='mrd-strategy-card'><h4>Strategy Card</h4><div class='muted'>No strategy card image found in metadata.</div></div>"
    return (
        "<div class='mrd-strategy-card'>"
        f"<h4>Strategy Card • {_h(run_id)}</h4>"
        f"<img src='{_h(str(card_uri))}' alt='Strategy card for {_h(run_id)}'/>"
        "</div>"
    )


def _first_pkg_for_run(packages: dict[str, Any], run_id: str) -> Any:
    if run_id in packages:
        return packages.get(run_id)

    for k, pkg in (packages or {}).items():
        if str(k) == str(run_id):
            return pkg

        md = getattr(pkg, "metadata", None) or {}
        derived = md.get("derived", {}) if isinstance(md, dict) else {}
        candidates = [
            getattr(pkg, "run_id", None),
            getattr(pkg, "name", None),
            getattr(pkg, "label", None),
            md.get("run_id") if isinstance(md, dict) else None,
            derived.get("run_id") if isinstance(derived, dict) else None,
            derived.get("source_run_id") if isinstance(derived, dict) else None,
        ]
        if any(str(c) == str(run_id) for c in candidates if c is not None):
            return pkg

    return None


def _card_uri_for_pkg(pkg: Any) -> Optional[str]:
    if not pkg:
        return None

    md = getattr(pkg, "metadata", None) or {}
    derived = md.get("derived", {}) if isinstance(md, dict) else {}
    candidates = [
        derived.get("run_image_uri") if isinstance(derived, dict) else None,
        derived.get("card_uri") if isinstance(derived, dict) else None,
        md.get("run_image_uri") if isinstance(md, dict) else None,
        md.get("card_uri") if isinstance(md, dict) else None,
        getattr(pkg, "run_image_uri", None),
        getattr(pkg, "card_uri", None),
    ]

    for uri in candidates:
        if uri:
            return str(uri)
    return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def _top_hour_label(df: pd.DataFrame, col: str, ascending: bool = False) -> Optional[str]:
    if df is None or df.empty or col not in df.columns:
        return None
    x = df.copy()
    x[col] = pd.to_numeric(x.get(col), errors="coerce")
    x = x.dropna(subset=[col])
    if x.empty:
        return None
    r = x.sort_values(col, ascending=ascending).iloc[0]
    return str(r.get("hour_label") or f"{int(r.get('entry_hour')):02d}")


def _render_recommendation_summary(
    *,
    hour_df: pd.DataFrame,
    dd_hour_df: pd.DataFrame,
    risk_df: pd.DataFrame,
    win_df: pd.DataFrame,
    optimizer_max_stop_breach_rate: Any,
    assumed_stop_usd: Optional[float],
) -> str:
    cards: list[str] = []

    best_raw_hour = _top_hour_label(hour_df, "avg_pnl", ascending=False)
    worst_loss_hour = _top_hour_label(hour_df, "worst_loss", ascending=True)
    highest_risk_hour = _top_hour_label(risk_df, "danger_score", ascending=False) or _top_hour_label(dd_hour_df, "stop_breach_rate", ascending=False)

    recommended_window = None
    recommendation_sub = "No optimizer output yet."
    summary_badge = ("Review", "warn")
    narrowing_needed = None

    if win_df is not None and not win_df.empty:
        feasible = win_df[win_df.get("feasible", False) == True] if "feasible" in win_df.columns else pd.DataFrame()
        chosen = feasible.iloc[0] if feasible is not None and not feasible.empty else win_df.iloc[0]
        recommended_window = str(chosen.get("window_label"))
        exp_net = _safe_float(chosen.get("expected_net_pnl"))
        avg_trade = _safe_float(chosen.get("avg_pnl_per_trade"))
        recommendation_sub = (
            f"Expected net PnL {round(exp_net or 0.0, 2)} • Avg/trade {round(avg_trade or 0.0, 4)}"
        )
        if feasible is not None and not feasible.empty:
            summary_badge = ("Feasible", "ok")
        else:
            summary_badge = ("Constraint-limited", "warn")
            recommendation_sub += " • No fully feasible window under current constraints."

        if hour_df is not None and not hour_df.empty and recommended_window:
            active_hours = hour_df["hour_label"].astype(str).tolist() if "hour_label" in hour_df.columns else []
            window_hours = str(chosen.get("hours") or "").split(",") if chosen.get("hours") else []
            if active_hours and window_hours and len(window_hours) < len(active_hours):
                narrowing_needed = "Yes — optimizer prefers a narrower window than the current active set."
            elif active_hours:
                narrowing_needed = "No clear narrowing signal from current optimizer settings."

    cards.append(
        "<div class='mrd-summary-card'>"
        "<div class='mrd-summary-label'>Recommended Window</div>"
        f"<div class='mrd-summary-value'>{_h(recommended_window or 'Not determined')}</div>"
        f"<div class='mrd-summary-sub'>{_h(recommendation_sub)}</div>"
        f"<div class='mrd-summary-badge mrd-summary-badge--{summary_badge[1]}'>{_h(summary_badge[0])}</div>"
        "</div>"
    )

    cards.append(
        "<div class='mrd-summary-card'>"
        "<div class='mrd-summary-label'>Best Observed Hour</div>"
        f"<div class='mrd-summary-value'>{_h(best_raw_hour or 'n/a')}</div>"
        "<div class='mrd-summary-sub'>Highest raw average PnL among active entry-hour buckets.</div>"
        "</div>"
    )

    cards.append(
        "<div class='mrd-summary-card'>"
        "<div class='mrd-summary-label'>Highest-Risk Hour</div>"
        f"<div class='mrd-summary-value'>{_h(highest_risk_hour or 'n/a')}</div>"
        "<div class='mrd-summary-sub'>Based on shrinkage-adjusted danger score and drawdown-risk diagnostics.</div>"
        "</div>"
    )

    cards.append(
        "<div class='mrd-summary-card'>"
        "<div class='mrd-summary-label'>Largest Loss Hour</div>"
        f"<div class='mrd-summary-value'>{_h(worst_loss_hour or 'n/a')}</div>"
        "<div class='mrd-summary-sub'>Earliest candidate for pruning when loss magnitude matters more than expectancy.</div>"
        "</div>"
    )

    if risk_df is not None and not risk_df.empty and assumed_stop_usd is not None:
        x = risk_df.copy()
        x["shrunk_stop_breach_rate"] = pd.to_numeric(x.get("shrunk_stop_breach_rate"), errors="coerce")
        x["shrunk_mae_p90"] = pd.to_numeric(x.get("shrunk_mae_p90"), errors="coerce")
        x = x.dropna(subset=["shrunk_stop_breach_rate", "shrunk_mae_p90"], how="all")
        if not x.empty:
            peak_stop = _safe_float(x["shrunk_stop_breach_rate"].max())
            peak_mae = _safe_float(x["shrunk_mae_p90"].max())
            stop_frac = (peak_mae / assumed_stop_usd) if peak_mae is not None and assumed_stop_usd > 0 else None
            cards.append(
                "<div class='mrd-summary-card mrd-summary-card--wide'>"
                "<div class='mrd-summary-label'>Prop Risk Snapshot</div>"
                f"<div class='mrd-summary-value'>Peak stop-breach {round(peak_stop or 0.0, 4)} • Peak MAE p90 {round(peak_mae or 0.0, 2)}</div>"
                f"<div class='mrd-summary-sub'>MAE p90 / assumed stop = {round(stop_frac or 0.0, 4)}. "
                f"Current optimizer stop-breach limit = {optimizer_max_stop_breach_rate if optimizer_max_stop_breach_rate is not None else 'none'}.</div>"
                "</div>"
            )

    guidance_parts: list[str] = []
    if recommended_window:
        guidance_parts.append(f"Focus trading around {recommended_window} America/Denver.")
    if worst_loss_hour:
        guidance_parts.append(f"Reduce exposure to hour {worst_loss_hour} first if you are narrowing the session.")
    if narrowing_needed:
        guidance_parts.append(narrowing_needed)
    if win_df is not None and not win_df.empty and ("feasible" in win_df.columns) and not bool(win_df["feasible"].any()):
        guidance_parts.append("No fully feasible optimizer window was found under current downside limits; for NQ, that often means the thresholds are too strict rather than the strategy being invalid.")
    guidance = " ".join(guidance_parts) or "No recommendation summary could be derived from current report tables."

    return (
        "<div class='mrd-note'><b>Recommendation Summary</b><div class='muted' style='margin-top:6px;'>"
        f"{_h(guidance)}"
        "</div></div>"
        "<div class='mrd-summary-grid'>" + "".join(cards) + "</div>"
    )


def _render_intro_block(
    *,
    run_id: str,
    baseline_html: str,
    recommendation_html: str,
    strategy_card_html: str,
    show_strategy_card: bool,
    strategy_card_position: str,
) -> str:
    parts: list[str] = []
    pos = (strategy_card_position or "top").strip().lower()
    if pos not in {"top", "right", "below_summary"}:
        pos = "top"

    if show_strategy_card and strategy_card_html and pos == "top":
        parts.append(strategy_card_html)

    if show_strategy_card and strategy_card_html and pos == "right":
        parts.append("<div class='mrd-run-top'>")
        parts.append("<div class='mrd-run-main'>")
        parts.append(baseline_html)
        parts.append(recommendation_html)
        parts.append("</div><div class='mrd-run-side'>")
        parts.append(strategy_card_html)
        parts.append("</div></div>")
        return "\n".join(parts)

    parts.append(baseline_html)
    parts.append(recommendation_html)

    if show_strategy_card and strategy_card_html and pos == "below_summary":
        parts.append(strategy_card_html)
    return "\n".join(parts)


def render_market_regime_discovery(ctx: dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    opts = ctx.get("options") or {}

    bar_tf = str(opts.get("bar_tf", "5m"))
    htf_tf = str(opts.get("htf_tf", "15m"))
    ema_period = int(opts.get("ema_period", 50))
    atr_period = int(opts.get("atr_period", 14))
    include_micro = bool(opts.get("include_micro", False))

    top_n_runs = int(opts.get("top_n_runs", 12))
    min_trades_run = int(opts.get("min_trades_run", 50))
    min_trades_bucket = int(opts.get("min_trades_bucket", 10))
    hour_min_trades_bucket = int(opts.get("hour_min_trades_bucket", min_trades_bucket))
    mae_min_trades_bucket = int(opts.get("mae_min_trades_bucket", hour_min_trades_bucket))
    risk_min_trades_bucket = int(opts.get("risk_min_trades_bucket", hour_min_trades_bucket))
    entry_hour_top_n = int(opts.get("entry_hour_top_n", 24))
    danger_top_n = int(opts.get("danger_top_n", 5))
    max_rows_per_table = int(opts.get("max_rows_per_table", 12))

    include_regex = opts.get("include_run_id_regex")
    exclude_regex = opts.get("exclude_run_id_regex")

    trend_flat_threshold = float(opts.get("trend_flat_threshold", 0.0))
    vwap_near_threshold_atr = float(opts.get("vwap_near_threshold_atr", 0.25))

    show_validation = bool(opts.get("show_validation", True))
    show_entry_hour_discovery = bool(opts.get("show_entry_hour_discovery", True))
    show_entry_hour_drawdown_risk = bool(opts.get("show_entry_hour_drawdown_risk", True))
    show_entry_hour_risk_model = bool(opts.get("show_entry_hour_risk_model", True))
    show_session_window_optimizer = bool(opts.get("show_session_window_optimizer", True))
    show_recommendation_summary = bool(opts.get("show_recommendation_summary", True))
    show_strategy_card = bool(opts.get("show_strategy_card", False))
    hide_missing_strategy_card = bool(opts.get("hide_missing_strategy_card", False))
    strategy_card_position = str(opts.get("strategy_card_position", "top") or "top")

    risk_stop_ticks = opts.get("risk_stop_ticks")
    tick_value_usd = float(opts.get("tick_value_usd", 5.0))
    severe_mae_frac = float(opts.get("severe_mae_frac", 0.50))
    tail_loss_frac = float(opts.get("tail_loss_frac", 0.50))
    prior_strength_mean = float(opts.get("prior_strength_mean", 30.0))
    prior_strength_rate = float(opts.get("prior_strength_rate", 20.0))

    optimizer_min_hours = int(opts.get("optimizer_min_hours", 1))
    optimizer_max_hours = int(opts.get("optimizer_max_hours", 8))
    optimizer_max_stop_breach_rate = opts.get("optimizer_max_stop_breach_rate")
    optimizer_max_tail_loss_rate = opts.get("optimizer_max_tail_loss_rate")
    optimizer_max_mae_p90_frac_of_stop = opts.get("optimizer_max_mae_p90_frac_of_stop")

    assumed_stop_usd = None
    if risk_stop_ticks is not None:
        assumed_stop_usd = float(risk_stop_ticks) * tick_value_usd

    feature_cfg = FeatureStoreConfig(
        bar_tf=bar_tf,
        htf_tf=htf_tf,
        ema_period=ema_period,
        atr_period=atr_period,
        include_micro=include_micro,
    )
    regime_cfg = MarketRegimeConfig(
        trend_flat_threshold=trend_flat_threshold,
        vwap_near_threshold_atr=vwap_near_threshold_atr,
    )

    cache = ctx.setdefault("_cache", {})
    feat_key = f"trade_features|{bar_tf}|{htf_tf}|ema{ema_period}|atr{atr_period}|micro{int(include_micro)}"
    feats = cache.get(feat_key)
    if feats is None:
        feats = build_trade_feature_frame(packages, market, feature_cfg)
        cache[feat_key] = feats

    regime_key = f"market_regime|{feat_key}|trend{trend_flat_threshold}|vwap{vwap_near_threshold_atr}|futures_sessions"
    regime_df = cache.get(regime_key)
    if regime_df is None:
        regime_df = build_market_regime_frame(feats, regime_cfg)
        cache[regime_key] = regime_df



    html: list[str] = []
    html.append(_STYLE_BLOCK_ONCE)
    html.append(_render_style_scope(_resolve_style_tokens(opts)))
    html.append("<div class='section'>")
    html.append("<div class='card'>")
    html.append("<h3>Market Regime Discovery</h3>")

    if regime_df is None or regime_df.empty or "run_id" not in regime_df.columns:
        html.append("<div class='muted'>No regime-ready trade features available. Ensure market bars are loaded and trades include Instrument.</div>")
        html.append("</div></div></div>")
        return "\n".join(html)

    html.append(
        f"<div class='muted'>Features built from bars ({_h(bar_tf)} / {_h(htf_tf)})"
        + (" + ticks" if include_micro else "")
        + f". Total trades: <b>{len(regime_df):,}</b></div>"
    )
    html.append("</div>")

    if show_validation:
        val_df = build_regime_validation_summary(regime_df)
        html.append("<div class='card'>")
        html.append("<h3>Validation</h3>")
        html.append("<div class='muted'>Use this to verify every trade was classified and totals reconcile before trusting the report.</div>")
        html.append(_render_df_table(val_df, max_rows=top_n_runs))
        html.append("</div>")

    runs = []
    for rid in sorted(set([str(x) for x in regime_df["run_id"].dropna().unique()])):
        if not _matches(include_regex, rid):
            continue
        if exclude_regex and _matches(exclude_regex, rid):
            continue
        runs.append(rid)

    if not runs:
        html.append("<div class='card'><div class='muted'>No runs match include/exclude filters.</div></div>")
        html.append("</div></div>")
        return "\n".join(html)

    run_rows = []
    for rid, sub in regime_df[regime_df["run_id"].astype(str).isin(runs)].groupby("run_id", dropna=False):
        pnl = pd.to_numeric(sub.get("pnl"), errors="coerce")
        run_rows.append({
            "run_id": str(rid),
            "trades": int(len(sub)),
            "net_pnl": float(pnl.sum(min_count=1)) if len(sub) else 0.0,
            "avg_pnl": float(pnl.mean()) if len(sub) else 0.0,
            "win_rate": float((pnl > 0).mean()) if len(sub) else 0.0,
        })

    run_summary = pd.DataFrame(run_rows)
    run_summary = run_summary[run_summary["trades"] >= min_trades_run].copy()
    if run_summary.empty:
        html.append(f"<div class='card'><div class='muted'>No runs have at least {min_trades_run} trades after filtering.</div></div>")
        html.append("</div></div>")
        return "\n".join(html)

    run_summary = run_summary.sort_values(["trades", "net_pnl"], ascending=[False, False]).head(top_n_runs)

    scan_rows = []
    for _, rr in run_summary.iterrows():
        rid = str(rr["run_id"])
        sub = regime_df[regime_df["run_id"].astype(str) == rid].copy()
        combo_summary = summarize_regime_performance(sub, "combo_regime", min_trades_bucket=min_trades_bucket)
        picks = pick_best_worst_regimes(combo_summary, "combo_regime")
        scan_rows.append({
            "run_id": rid,
            "trades": int(rr["trades"]),
            "net_pnl": float(rr["net_pnl"]),
            "avg_pnl": float(rr["avg_pnl"]),
            "best_combo_regime": picks["best_regime"],
            "best_combo_avg_pnl": picks["best_avg_pnl"],
            "worst_combo_regime": picks["worst_regime"],
            "worst_combo_avg_pnl": picks["worst_avg_pnl"],
        })

    html.append("<div class='card'>")
    html.append("<h3>Top combo regime scan</h3>")
    html.append(_render_df_table(pd.DataFrame(scan_rows), max_rows=top_n_runs))
    html.append("</div>")

    regime_specs = [
        ("trend_regime", "Performance by trend regime", True),
        ("vol_regime", "Performance by volatility regime", True),
        ("vwap_regime", "Performance by VWAP regime", True),
        ("session_regime", "Performance by session regime", False),
        ("combo_regime", "Performance by combo regime", True),
    ]

    for _, rr in run_summary.iterrows():
        rid = str(rr["run_id"])
        sub = regime_df[regime_df["run_id"].astype(str) == rid].copy()
        if sub.empty:
            continue

        combo_summary = summarize_regime_performance(sub, "combo_regime", min_trades_bucket=min_trades_bucket)
        picks = pick_best_worst_regimes(combo_summary, "combo_regime")
        session_summary = summarize_regime_performance(sub, "session_regime", min_trades_bucket=min_trades_bucket, sort_by_score=False)
        session_summary = order_session_summary(session_summary, "session_regime")

        hour_df = summarize_entry_hour_performance(sub, min_trades_bucket=hour_min_trades_bucket) if show_entry_hour_discovery else pd.DataFrame()
        dd_hour_df = summarize_entry_hour_drawdown_risk(
            sub,
            min_trades_bucket=mae_min_trades_bucket,
            mae_col="mae",
            assumed_stop_usd=assumed_stop_usd,
            severe_mae_frac=severe_mae_frac,
        ) if show_entry_hour_drawdown_risk else pd.DataFrame()
        risk_df = summarize_entry_hour_risk(
            sub,
            min_trades_bucket=risk_min_trades_bucket,
            pnl_col="pnl",
            mae_col="mae",
            assumed_stop_usd=assumed_stop_usd,
            prior_strength_mean=prior_strength_mean,
            prior_strength_rate=prior_strength_rate,
            tail_loss_frac=tail_loss_frac,
            severe_mae_frac=severe_mae_frac,
        ) if show_entry_hour_risk_model else pd.DataFrame()
        win_df = optimize_entry_hour_window(
            risk_df,
            min_hours=optimizer_min_hours,
            max_hours=optimizer_max_hours,
            max_stop_breach_rate=float(optimizer_max_stop_breach_rate) if optimizer_max_stop_breach_rate is not None else None,
            max_tail_loss_rate=float(optimizer_max_tail_loss_rate) if optimizer_max_tail_loss_rate is not None else None,
            max_mae_p90_frac_of_stop=float(optimizer_max_mae_p90_frac_of_stop) if optimizer_max_mae_p90_frac_of_stop is not None else None,
            assumed_stop_usd=assumed_stop_usd,
        ) if show_session_window_optimizer and not risk_df.empty else pd.DataFrame()




        pkg = _first_pkg_for_run(packages, rid)
        card_uri = _card_uri_for_pkg(pkg)


        if show_strategy_card and hide_missing_strategy_card and not card_uri:
            continue

        baseline_html = _run_baseline_card(sub) + (
            "<div class='muted' style='margin-top:8px;'>"
            f"Preferred combo: <b>{_h(str(picks.get('best_regime') or 'n/a'))}</b> • "
            f"Avoid combo: <b>{_h(str(picks.get('worst_regime') or 'n/a'))}</b>"
            "</div>"
        )

        recommendation_html = _render_recommendation_summary(
            hour_df=hour_df,
            dd_hour_df=dd_hour_df,
            risk_df=risk_df,
            win_df=win_df,
            optimizer_max_stop_breach_rate=optimizer_max_stop_breach_rate,
            assumed_stop_usd=assumed_stop_usd,
        ) if show_recommendation_summary else ""

        strategy_card_html = _render_strategy_card(card_uri, rid) if show_strategy_card else ""

        html.append("<div class='card'>")
        html.append(f"<h3>{_h(rid)}</h3>")
        html.append(
            _render_intro_block(
                run_id=rid,
                baseline_html=baseline_html,
                recommendation_html=recommendation_html,
                strategy_card_html=strategy_card_html,
                show_strategy_card=show_strategy_card,
                strategy_card_position=strategy_card_position,
            )
        )

        html.append(_render_session_edge_chart(session_summary))

        if show_entry_hour_discovery:
            html.append("<h4 style='margin-top:16px;'>Entry Hour Discovery</h4>")
            html.append("<div class='muted'>Use exact entry hours to refine London-session windows and identify transition-hour leakage.</div>")
            html.append(_render_entry_hour_edge_chart(hour_df))
            if hour_df.empty:
                html.append("<div class='muted'>No entry-hour rows met minimum trade threshold.</div>")
            else:
                html.append(_render_df_table(hour_df, max_rows=entry_hour_top_n))

        if show_entry_hour_drawdown_risk:
            html.append("<h4 style='margin-top:16px;'>Entry Hour Drawdown Risk</h4>")
            html.append("<div class='muted'>Use MAE distribution by entry hour to detect prop-risk leakage, trailing-drawdown stress, and session degradation that expectancy alone can hide.</div>")
            html.append(_render_entry_hour_mae_chart(dd_hour_df))
            html.append(_render_entry_hour_stop_breach_chart(dd_hour_df))
            if dd_hour_df.empty:
                html.append("<div class='muted'>No drawdown-risk rows met minimum trade threshold or MAE was unavailable.</div>")
            else:
                html.append(_render_df_table(dd_hour_df, max_rows=entry_hour_top_n))

        if show_entry_hour_risk_model:
            html.append("<h4 style='margin-top:16px;'>Entry Hour Risk Model</h4>")
            html.append("<div class='muted'>Shrinkage-adjusted downside metrics help rank dangerous hours more robustly than raw hourly averages, especially in thin buckets.</div>")
            html.append(_render_danger_score_chart(risk_df))
            if risk_df.empty:
                html.append("<div class='muted'>No risk-model rows met minimum trade threshold.</div>")
            else:
                keep_review_drop = risk_df[[c for c in ["hour_label", "trades", "shrunk_avg_pnl", "shrunk_mae_p90", "shrunk_stop_breach_rate", "danger_score", "keep_drop_flag"] if c in risk_df.columns]]
                html.append(_render_df_table(keep_review_drop, max_rows=entry_hour_top_n))
                danger_df = risk_df.sort_values("danger_score", ascending=False).head(danger_top_n)
                html.append("<div class='muted' style='margin-top:8px;'>Most dangerous hours by shrinkage-adjusted downside risk.</div>")
                html.append(_render_df_table(danger_df[[c for c in ["hour_label", "trades", "shrunk_avg_pnl", "shrunk_avg_loss", "shrunk_mae_p90", "shrunk_stop_breach_rate", "shrunk_tail_loss_rate", "danger_score", "keep_drop_flag"] if c in danger_df.columns]], max_rows=danger_top_n))

        if show_session_window_optimizer and not risk_df.empty:
            html.append("<h4 style='margin-top:16px;'>Session Window Optimizer</h4>")
            html.append("<div class='muted'>Find the best contiguous trading window using shrinkage-adjusted edge and downside constraints rather than raw hourly averages.</div>")
            html.append(_render_session_optimizer_chart(win_df))
            if win_df.empty:
                html.append("<div class='muted'>No feasible session windows were generated.</div>")
            else:
                feasible = win_df[win_df["feasible"] == True] if "feasible" in win_df.columns else pd.DataFrame()
                best = feasible.iloc[0] if not feasible.empty else win_df.iloc[0]
                html.append(
                    "<div class='muted' style='margin-top:8px;'>"
                    f"Best window: <b>{_h(str(best.get('window_label')))}</b> • "
                    f"Trades: <b>{int(best.get('trades', 0))}</b> • "
                    f"Expected net PnL: <b>{round(float(best.get('expected_net_pnl', 0.0)), 2)}</b> • "
                    f"Avg/trade: <b>{round(float(best.get('avg_pnl_per_trade', 0.0)), 4)}</b>"
                    "</div>"
                )
                html.append(_render_df_table(win_df, max_rows=min(max_rows_per_table, 12)))

        for regime_col, title, sort_by_score in regime_specs:
            html.append(f"<h4 style='margin-top:12px;'>{_h(title)}</h4>")
            t = summarize_regime_performance(sub, regime_col, min_trades_bucket=min_trades_bucket, sort_by_score=sort_by_score)
            if regime_col == "session_regime":
                t = order_session_summary(t, "session_regime")
            if t.empty:
                html.append(f"<div class='muted'>No {_h(regime_col)} rows met minimum trade threshold.</div>")
            else:
                html.append(_render_df_table(t, max_rows=max_rows_per_table))

        html.append("</div>")

    html.append("</div></div>")
    return "\n".join(html)
