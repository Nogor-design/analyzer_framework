from __future__ import annotations

"""
Strategy Discovery × Anchor Confluence
========================================
Combines Strategy Discovery signal rules with MA Anchor Interaction structural
data to surface high-probability trade entry setups.

Three panels:
  1. Session Performance Matrix  — SD trade stats + anchor segment quality per session
  2. Top Entry Setups            — For each SD top rule, matching anchor segments + TP/SL
  3. Regime × Anchor Matrix      — Regime breakdown joined with anchor structural data
"""

from typing import Any, Dict, List, Optional

import pandas as pd

_SESSION_ORDER = ["London", "NyPre", "NyOpen", "NyMid", "PowerHr", "Asia", "other"]

CSS = """
<style>
.sdac-wrap{font-family:'Segoe UI',system-ui,sans-serif;color:#e2e8f0;margin:0 0 28px 0}
.sdac-wrap h2{font-size:1.15rem;font-weight:700;color:#90cdf4;margin:0 0 6px 0;
  border-bottom:1px solid rgba(144,205,244,.25);padding-bottom:6px}
.sdac-wrap h3{font-size:.92rem;font-weight:600;color:#a0aec0;margin:18px 0 6px 0;
  text-transform:uppercase;letter-spacing:.05em}
.sdac-desc{font-size:.78rem;color:#a0aec0;margin:0 0 16px 0;line-height:1.5}

/* Tables */
.sdac-tbl{border-collapse:collapse;width:100%;font-size:.78rem;margin-bottom:20px}
.sdac-tbl th{background:rgba(255,255,255,.07);padding:5px 10px;text-align:left;
  font-weight:600;color:#a0aec0;border-bottom:1px solid rgba(255,255,255,.08)}
.sdac-tbl td{padding:4px 10px;border-bottom:1px solid rgba(255,255,255,.04);
  vertical-align:middle}
.sdac-tbl tr:hover td{background:rgba(255,255,255,.03)}
.sdac-tbl .num{text-align:right;font-variant-numeric:tabular-nums}
.sdac-tbl .dim{color:#718096;font-size:.72rem}

/* Confluence badge */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.72rem;font-weight:600}
.badge-go{background:rgba(72,187,120,.2);color:#68d391;border:1px solid rgba(72,187,120,.35)}
.badge-caution{background:rgba(246,173,85,.2);color:#f6ad55;border:1px solid rgba(246,173,85,.35)}
.badge-avoid{background:rgba(252,129,129,.2);color:#fc8181;border:1px solid rgba(252,129,129,.35)}
.badge-neutral{background:rgba(160,174,192,.15);color:#a0aec0;border:1px solid rgba(160,174,192,.25)}

/* Setup cards */
.sdac-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin-bottom:20px}
.sdac-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);
  border-radius:8px;padding:12px 14px}
.sdac-card-hdr{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:8px}
.sdac-card-title{font-size:.76rem;font-weight:600;color:#90cdf4;line-height:1.4;flex:1;margin-right:8px}
.sdac-card-score{font-size:.8rem;font-weight:700;color:#f6e05e}
.sdac-card-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px}
.sdac-kv{display:flex;flex-direction:column}
.sdac-kv-label{font-size:.65rem;color:#718096;text-transform:uppercase;letter-spacing:.04em}
.sdac-kv-val{font-size:.82rem;font-weight:600;color:#e2e8f0}
.sdac-kv-val.good{color:#68d391}
.sdac-kv-val.warn{color:#f6ad55}
.sdac-kv-val.bad{color:#fc8181}
.sdac-divider{border:none;border-top:1px solid rgba(255,255,255,.06);margin:8px 0}
.sdac-anchor-bar{font-size:.68rem;color:#a0aec0;margin-top:6px;
  background:rgba(255,255,255,.03);border-radius:4px;padding:5px 8px}
.sdac-anchor-bar strong{color:#63b3ed}

/* Checklist */
.sdac-checklist{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);
  border-radius:8px;padding:14px 16px;max-width:520px}
.sdac-check-row{display:flex;align-items:baseline;gap:8px;padding:4px 0;
  font-size:.8rem;border-bottom:1px solid rgba(255,255,255,.04)}
.sdac-check-row:last-child{border-bottom:none}
.sdac-check-icon{font-size:.9rem;flex-shrink:0;width:18px}
.sdac-check-label{color:#a0aec0;font-size:.72rem;width:130px;flex-shrink:0}
.sdac-check-val{color:#e2e8f0;font-weight:500}

/* Inline WR colour */
.wr-good{color:#68d391;font-weight:600}
.wr-warn{color:#f6ad55;font-weight:600}
.wr-bad{color:#fc8181;font-weight:600}
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_label(hour: int) -> str:
    if 0 <= hour <= 5:   return "London"
    if hour == 6:        return "NyPre"
    if hour in (7, 8):   return "NyOpen"
    if 9 <= hour <= 12:  return "NyMid"
    if hour == 13:       return "PowerHr"
    if 18 <= hour <= 23: return "Asia"
    return "other"


def _fmt_pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None: return "—"
    return f"{v * 100:.{decimals}f}%"


def _fmt_ticks(v: Optional[float], decimals: int = 1) -> str:
    if v is None: return "—"
    return f"{v:+.{decimals}f}t"


def _wr_class(v: Optional[float]) -> str:
    if v is None: return ""
    if v >= 0.60: return "wr-good"
    if v >= 0.48: return "wr-warn"
    return "wr-bad"


def _kv_class(v: Optional[float]) -> str:
    if v is None: return ""
    if v > 0: return "good"
    if v < 0: return "bad"
    return "warn"


def _confluence_badge(sd_wr: Optional[float], anchor_wr: Optional[float]) -> str:
    s_ok = sd_wr is not None and sd_wr >= 0.52
    a_ok = anchor_wr is not None and anchor_wr >= 0.52
    if s_ok and a_ok:
        return '<span class="badge badge-go">TRADE</span>'
    if s_ok or a_ok:
        return '<span class="badge badge-caution">CAUTION</span>'
    if sd_wr is None and anchor_wr is None:
        return '<span class="badge badge-neutral">NO DATA</span>'
    return '<span class="badge badge-avoid">AVOID</span>'


def _attach_session_to_segments(segments: pd.DataFrame) -> pd.DataFrame:
    """Add 'session' column from entry_ts hour."""
    seg = segments.copy()
    ts_col = "entry_ts" if "entry_ts" in seg.columns else None
    if ts_col is None:
        seg["session"] = "other"
        return seg
    dt = pd.to_datetime(seg[ts_col], errors="coerce")
    if dt.dt.tz is not None:
        try:
            dt = dt.dt.tz_convert("America/Denver")
        except Exception:
            pass
    seg["session"] = dt.dt.hour.apply(_session_label)
    return seg


def _anchor_stats_by_session(segments: pd.DataFrame) -> Dict[str, Dict]:
    """Return {session: {count, wr, mfe_p50, mae_p50}} from non-censored segments."""
    if segments is None or segments.empty:
        return {}
    seg = _attach_session_to_segments(segments)
    seg = seg[~seg.get("censored", pd.Series(False, index=seg.index))]
    result = {}
    for session, grp in seg.groupby("session"):
        n = len(grp)
        mfe = grp["mfe_ticks"].dropna() if "mfe_ticks" in grp.columns else pd.Series([], dtype=float)
        mae = grp["mae_ticks"].dropna() if "mae_ticks" in grp.columns else pd.Series([], dtype=float)
        wr  = float((mfe > 0).sum() / n) if n > 0 and len(mfe) > 0 else None
        result[str(session)] = {
            "count":    n,
            "wr":       wr,
            "mfe_p50":  float(mfe.quantile(0.5)) if len(mfe) > 0 else None,
            "mae_p50":  float(abs(mae).quantile(0.5)) if len(mae) > 0 else None,
        }
    return result


def _anchor_stats_by_regime(segments: pd.DataFrame) -> Dict[str, Dict]:
    """Return {regime: {count, wr, mfe_p50, mae_p50}} from non-censored segments."""
    if segments is None or segments.empty:
        return {}
    seg = segments[~segments.get("censored", pd.Series(False, index=segments.index))].copy()
    regime_col = "trend_regime_at_entry" if "trend_regime_at_entry" in seg.columns else None
    if regime_col is None:
        return {}
    result = {}
    for regime, grp in seg.groupby(regime_col):
        n = len(grp)
        mfe = grp["mfe_ticks"].dropna() if "mfe_ticks" in grp.columns else pd.Series([], dtype=float)
        mae = grp["mae_ticks"].dropna() if "mae_ticks" in grp.columns else pd.Series([], dtype=float)
        wr  = float((mfe > 0).sum() / n) if n > 0 and len(mfe) > 0 else None
        result[str(regime)] = {
            "count":    n,
            "wr":       wr,
            "mfe_p50":  float(mfe.quantile(0.5)) if len(mfe) > 0 else None,
            "mae_p50":  float(abs(mae).quantile(0.5)) if len(mae) > 0 else None,
        }
    return result


def _filter_segments_by_rule(
    segments: pd.DataFrame,
    conditions: List[Dict],
    seg_with_session: pd.DataFrame,
) -> pd.DataFrame:
    """Return segments matching the SD rule conditions (session_label + regime)."""
    mask = pd.Series(True, index=seg_with_session.index)
    for cond in conditions:
        col   = str(cond.get("column") or "")
        op    = str(cond.get("op") or "eq")
        value = cond.get("value")
        if col == "session_label" and op == "eq":
            mask &= seg_with_session.get("session", pd.Series("", index=seg_with_session.index)) == str(value)
        elif col == "regime" and op == "eq":
            reg_col = "trend_regime_at_entry"
            if reg_col in seg_with_session.columns:
                mask &= seg_with_session[reg_col].astype(str) == str(value)
        elif col == "direction" and op == "eq":
            try:
                v = float(value)
                dir_label = "long" if v > 0 else "short"
                if "direction" in seg_with_session.columns:
                    mask &= seg_with_session["direction"].astype(str).str.lower() == dir_label
            except Exception:
                pass
    return seg_with_session[mask & ~seg_with_session.get("censored", pd.Series(False, index=seg_with_session.index))]


def _best_recommendation(recommendations: Optional[pd.DataFrame], anchor_ids: Optional[List[str]] = None) -> Dict:
    """Return the best TP/SL recommendation (highest expectancy_score)."""
    if recommendations is None or (hasattr(recommendations, "empty") and recommendations.empty):
        return {}
    rec = recommendations
    if anchor_ids:
        mask = rec.get("anchor_id", pd.Series(dtype=str)).isin(anchor_ids)
        sub = rec[mask]
        if not sub.empty:
            rec = sub
    score_col = next((c for c in ["expectancy_score", "robust_score", "stability_score"] if c in rec.columns), None)
    if score_col:
        rec = rec.sort_values(score_col, ascending=False)
    row = rec.iloc[0].to_dict()
    return {
        "anchor_id": str(row.get("anchor_id", "—")),
        "tp_ticks":  row.get("tp_ticks"),
        "sl_ticks":  row.get("sl_ticks"),
        "win_rate":  row.get("win_rate"),
        "expectancy": row.get("expectancy"),
    }


# ---------------------------------------------------------------------------
# Panel 1: Session Matrix
# ---------------------------------------------------------------------------

def _render_session_matrix(
    sd_by_session: Dict[str, Any],
    anchor_by_session: Dict[str, Dict],
) -> str:
    rows = []
    for session in _SESSION_ORDER:
        sd  = sd_by_session.get(session) or {}
        anc = anchor_by_session.get(session) or {}
        sd_wr  = sd.get("win_rate")
        sd_at  = sd.get("avg_trade")
        sd_n   = sd.get("n_trades") or 0
        anc_wr = anc.get("wr")
        anc_n  = anc.get("count") or 0

        if sd_n == 0 and anc_n == 0:
            continue

        badge = _confluence_badge(sd_wr, anc_wr)
        rows.append(f"""
        <tr>
          <td><strong>{session}</strong></td>
          <td class="num {_wr_class(sd_wr)}">{_fmt_pct(sd_wr)}</td>
          <td class="num">{_fmt_ticks(sd_at)}</td>
          <td class="num dim">{sd_n}</td>
          <td class="num {_wr_class(anc_wr)}">{_fmt_pct(anc_wr)}</td>
          <td class="num">{anc.get('mfe_p50') and f"{anc['mfe_p50']:+.1f}t" or "—"}</td>
          <td class="num">{anc.get('mae_p50') and f"{anc['mae_p50']:.1f}t" or "—"}</td>
          <td class="num dim">{anc_n}</td>
          <td>{badge}</td>
        </tr>""")

    if not rows:
        return '<p class="sdac-desc">No session data available.</p>'

    return f"""
<table class="sdac-tbl">
  <thead><tr>
    <th>Session</th>
    <th>SD Win Rate</th><th>SD Avg Ticks</th><th>SD Trades</th>
    <th>Anchor WR</th><th>MFE p50</th><th>MAE p50</th><th>Segs</th>
    <th>Confluence</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""


# ---------------------------------------------------------------------------
# Panel 2: Top Entry Setups (SD rules × anchor segments)
# ---------------------------------------------------------------------------

def _render_entry_setups(
    top_rules: List[Dict],
    seg_with_session: pd.DataFrame,
    recommendations: Optional[pd.DataFrame],
    max_rules: int = 8,
) -> str:
    if not top_rules:
        return '<p class="sdac-desc">No signal rules available.</p>'

    cards = []
    for rule in top_rules[:max_rules]:
        conditions = rule.get("conditions") or []
        rule_str   = rule.get("rule_str", "Rule")
        rank       = rule.get("rank", "?")
        sd_wr      = rule.get("win_rate")
        sd_ticks   = rule.get("avg_ticks")
        sd_n       = rule.get("n_signals") or 0
        sd_score   = rule.get("score")
        sd_mfe     = rule.get("mfe_p50")
        sd_mae     = rule.get("mae_p50")

        # Matching anchor segments
        matching_segs = _filter_segments_by_rule(pd.DataFrame(), conditions, seg_with_session)
        anc_n   = len(matching_segs)
        anc_wr  = None
        anc_mfe = None
        anc_mae = None
        best_anchors: List[str] = []

        if anc_n > 0:
            mfe_s = matching_segs["mfe_ticks"].dropna() if "mfe_ticks" in matching_segs.columns else pd.Series(dtype=float)
            mae_s = matching_segs["mae_ticks"].dropna() if "mae_ticks" in matching_segs.columns else pd.Series(dtype=float)
            if len(mfe_s) > 0:
                anc_wr  = float((mfe_s > 0).sum() / len(mfe_s))
                anc_mfe = float(mfe_s.quantile(0.5))
            if len(mae_s) > 0:
                anc_mae = float(abs(mae_s).quantile(0.5))
            if "anchor_id" in matching_segs.columns:
                best_anchors = list(matching_segs["anchor_id"].value_counts().head(3).index)

        # Best structural recommendation for matching anchors
        rec = _best_recommendation(recommendations, best_anchors if best_anchors else None)
        tp  = rec.get("tp_ticks")
        sl  = rec.get("sl_ticks")
        tp_str = f"{tp:.0f}t" if tp else "—"
        sl_str = f"{sl:.0f}t" if sl else "—"
        rr_str = f"{tp/sl:.1f}R" if tp and sl else "—"
        anchor_label = rec.get("anchor_id") or (best_anchors[0] if best_anchors else "—")

        # Cond tags
        cond_tags = "".join(
            f'<span style="background:rgba(99,179,237,.12);color:#63b3ed;'
            f'border-radius:3px;padding:1px 5px;font-size:.65rem;margin:1px 2px 1px 0;'
            f'display:inline-block">{c.get("description","")}</span>'
            for c in conditions
        )

        score_str = f"{sd_score:.0f}" if sd_score is not None else "—"
        ticks_cls = _kv_class(sd_ticks)
        anchor_note = ""
        if best_anchors:
            anchor_note = (
                f'<div class="sdac-anchor-bar">Anchors in matching segments: '
                f'<strong>{" · ".join(best_anchors[:3])}</strong>'
                + (f" | Anchor WR: <strong>{_fmt_pct(anc_wr)}</strong>" if anc_wr is not None else "")
                + (f" | MFE p50: <strong>{anc_mfe:+.1f}t</strong>" if anc_mfe is not None else "")
                + (f" | MAE p50: <strong>{anc_mae:.1f}t</strong>" if anc_mae is not None else "")
                + f'</div>'
            )

        cards.append(f"""
<div class="sdac-card">
  <div class="sdac-card-hdr">
    <div class="sdac-card-title">#{rank} {rule_str}</div>
    <div class="sdac-card-score">⭑{score_str}</div>
  </div>
  <div style="margin-bottom:6px">{cond_tags}</div>
  <div class="sdac-card-grid">
    <div class="sdac-kv">
      <span class="sdac-kv-label">SD Win Rate</span>
      <span class="sdac-kv-val {_wr_class(sd_wr)}">{_fmt_pct(sd_wr)}</span>
    </div>
    <div class="sdac-kv">
      <span class="sdac-kv-label">SD Avg Ticks</span>
      <span class="sdac-kv-val {ticks_cls}">{_fmt_ticks(sd_ticks)}</span>
    </div>
    <div class="sdac-kv">
      <span class="sdac-kv-label">SD Signals</span>
      <span class="sdac-kv-val">{sd_n}</span>
    </div>
    <div class="sdac-kv">
      <span class="sdac-kv-label">SD MFE p50</span>
      <span class="sdac-kv-val">{_fmt_ticks(sd_mfe)}</span>
    </div>
  </div>
  <hr class="sdac-divider">
  <div class="sdac-card-grid">
    <div class="sdac-kv">
      <span class="sdac-kv-label">Anchor</span>
      <span class="sdac-kv-val" style="color:#90cdf4">{anchor_label}</span>
    </div>
    <div class="sdac-kv">
      <span class="sdac-kv-label">Structure TP/SL</span>
      <span class="sdac-kv-val">{tp_str} / {sl_str} ({rr_str})</span>
    </div>
  </div>
  {anchor_note}
</div>""")

    return f'<div class="sdac-cards">{"".join(cards)}</div>'


# ---------------------------------------------------------------------------
# Panel 3: Regime × Anchor Matrix
# ---------------------------------------------------------------------------

_REGIME_LABELS = {"trending_up": "Trending Up", "trending_down": "Trending Down", "ranging": "Ranging"}

def _render_regime_matrix(
    sd_by_regime: Dict[str, Any],
    anchor_by_regime: Dict[str, Dict],
    recommendations: Optional[pd.DataFrame],
) -> str:
    regimes = ["trending_up", "trending_down", "ranging"]
    rows = []
    for regime in regimes:
        sd  = sd_by_regime.get(regime) or {}
        anc = anchor_by_regime.get(regime) or {}
        sd_wr  = sd.get("win_rate")
        sd_pf  = sd.get("profit_factor")
        sd_n   = sd.get("n_trades") or 0
        anc_wr = anc.get("wr")
        anc_n  = anc.get("count") or 0

        if sd_n == 0 and anc_n == 0:
            continue

        # Best TP/SL for this regime
        tp_str, sl_str, rr_str = "—", "—", "—"
        if recommendations is not None and not (hasattr(recommendations, "empty") and recommendations.empty):
            rec = _best_recommendation(recommendations)
            tp, sl = rec.get("tp_ticks"), rec.get("sl_ticks")
            tp_str = f"{tp:.0f}t" if tp else "—"
            sl_str = f"{sl:.0f}t" if sl else "—"
            rr_str = f"{tp/sl:.1f}R" if tp and sl else "—"

        label = _REGIME_LABELS.get(regime, regime)
        rows.append(f"""
        <tr>
          <td><strong>{label}</strong></td>
          <td class="num {_wr_class(sd_wr)}">{_fmt_pct(sd_wr)}</td>
          <td class="num">{f"{sd_pf:.2f}" if sd_pf else "—"}</td>
          <td class="num dim">{sd_n}</td>
          <td class="num {_wr_class(anc_wr)}">{_fmt_pct(anc_wr)}</td>
          <td class="num">{anc.get('mfe_p50') and f"{anc['mfe_p50']:+.1f}t" or "—"}</td>
          <td class="num dim">{anc_n}</td>
          <td class="num">{tp_str} / {sl_str} ({rr_str})</td>
        </tr>""")

    if not rows:
        return '<p class="sdac-desc">No regime data available.</p>'

    return f"""
<table class="sdac-tbl">
  <thead><tr>
    <th>Regime</th>
    <th>SD Win Rate</th><th>SD PF</th><th>SD Trades</th>
    <th>Anchor WR</th><th>Anchor MFE p50</th><th>Segs</th>
    <th>Structural TP / SL</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""


# ---------------------------------------------------------------------------
# Panel 4: Entry Readiness Checklist
# ---------------------------------------------------------------------------

def _render_checklist(
    sd_by_session: Dict[str, Any],
    sd_by_regime: Dict[str, Any],
    sd_by_direction: Dict[str, Any],
    top_rules: List[Dict],
    recommendations: Optional[pd.DataFrame],
) -> str:
    # Best session
    best_sessions = sorted(
        [(s, (d.get("win_rate") or 0)) for s, d in sd_by_session.items() if (d.get("n_trades") or 0) >= 5],
        key=lambda x: x[1], reverse=True,
    )[:3]

    # Best regime
    best_regimes = sorted(
        [(r, (d.get("win_rate") or 0)) for r, d in sd_by_regime.items() if (d.get("n_trades") or 0) >= 5],
        key=lambda x: x[1], reverse=True,
    )[:2]

    # Best direction
    long_wr  = (sd_by_direction.get("Long")  or {}).get("win_rate")
    short_wr = (sd_by_direction.get("Short") or {}).get("win_rate")

    def dir_icon(wr: Optional[float]) -> str:
        if wr is None: return "○"
        return "✅" if wr >= 0.52 else "❌"

    # Top anchor
    rec = _best_recommendation(recommendations)
    anchor_label = rec.get("anchor_id") or "—"
    tp, sl = rec.get("tp_ticks"), rec.get("sl_ticks")
    tp_sl_str = f"{tp:.0f}t / {sl:.0f}t ({tp/sl:.1f}R)" if tp and sl else "—"

    # Build rows
    session_str = ", ".join(f"{s} ({_fmt_pct(wr)})" for s, wr in best_sessions) or "—"
    regime_str  = ", ".join(f"{_REGIME_LABELS.get(r, r)} ({_fmt_pct(wr)})" for r, wr in best_regimes) or "—"

    def row(icon: str, label: str, value: str) -> str:
        return f"""<div class="sdac-check-row">
          <span class="sdac-check-icon">{icon}</span>
          <span class="sdac-check-label">{label}</span>
          <span class="sdac-check-val">{value}</span>
        </div>"""

    long_icon  = dir_icon(long_wr)
    short_icon = dir_icon(short_wr)
    long_str   = f"Long {_fmt_pct(long_wr)}"  if long_wr  else "Long — no data"
    short_str  = f"Short {_fmt_pct(short_wr)}" if short_wr else "Short — no data"

    return f"""
<div class="sdac-checklist">
  {row("📅", "Best Sessions",  session_str)}
  {row("📊", "Best Regime",    regime_str)}
  {row(long_icon,  "Long",     long_str)}
  {row(short_icon, "Short",    short_str)}
  {row("📍", "Watch Anchor",   anchor_label)}
  {row("🎯", "Structural TP/SL", tp_sl_str)}
</div>"""


# ---------------------------------------------------------------------------
# Run-level renderer
# ---------------------------------------------------------------------------

def _render_run_confluence(run_id: str, pkg: Any) -> str:
    derived   = (getattr(pkg, "metadata", {}) or {}).get("derived", {})
    ai_meta   = derived.get("anchor_interaction") or {}
    sd_meta   = derived.get("strategy_discovery") or {}
    assets    = getattr(pkg, "assets", {}) or {}

    ai_diag = ai_meta.get("diagnostics") or {}
    if not ai_diag.get("ok"):
        reason = ai_diag.get("reason") or ai_meta.get("reason") or "skipped"
        return f'<p class="sdac-desc">MA Anchor not available for <strong>{run_id}</strong>: {reason}</p>'

    # Pull DataFrames from assets
    ai_assets = assets.get("anchor_interaction") or {}
    segments  = ai_assets.get("segments")
    recs_df   = ai_assets.get("recommendations")

    has_segs = isinstance(segments, pd.DataFrame) and not segments.empty
    has_recs = isinstance(recs_df, pd.DataFrame) and not recs_df.empty

    # SD evaluation breakdowns
    ev          = sd_meta.get("evaluation") or {}
    by_session  = ev.get("by_session")  or {}
    by_regime   = ev.get("by_regime")   or {}
    by_direction= ev.get("by_direction") or {}

    # Signal entry discovery top rules
    sed        = sd_meta.get("signal_entry_discovery") or {}
    top_rules  = sed.get("top_signal_rules") or []

    # Preprocess segments
    seg_with_session = _attach_session_to_segments(segments) if has_segs else pd.DataFrame()
    anchor_by_session = _anchor_stats_by_session(segments) if has_segs else {}
    anchor_by_regime  = _anchor_stats_by_regime(segments) if has_segs else {}

    # Diagnostics header
    n_segs     = ai_diag.get("n_segments", 0)
    anchors    = (ai_meta.get("engine") or {}).get("anchors_resolved") or []
    anchor_str = ", ".join(str(a) for a in anchors) if anchors else "—"
    n_rules    = len(top_rules)
    n_recs     = int(len(recs_df)) if has_recs else 0

    p1 = _render_session_matrix(by_session, anchor_by_session)
    p2 = _render_entry_setups(top_rules, seg_with_session, recs_df if has_recs else None)
    p3 = _render_regime_matrix(by_regime, anchor_by_regime, recs_df if has_recs else None)
    p4 = _render_checklist(by_session, by_regime, by_direction, top_rules, recs_df if has_recs else None)

    return f"""
<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);
     border-radius:10px;padding:16px 20px;margin-bottom:24px">
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:12px">
    <span style="font-size:.88rem;font-weight:700;color:#e2e8f0">{run_id}</span>
    <span class="sdac-desc" style="margin:0">
      {n_segs} anchor segments · anchors: {anchor_str} · {n_rules} signal rules · {n_recs} recommendations
    </span>
  </div>

  <h3>Session Performance Matrix</h3>
  <p class="sdac-desc">
    SD columns come from executed-trade evaluation by session.
    Anchor columns come from MA cross segments in that session window.
    TRADE = both SD win rate and anchor win rate ≥ 52 %.
  </p>
  {p1}

  <h3>Top Entry Setups</h3>
  <p class="sdac-desc">
    Each card represents one top signal rule from the pattern corpus.
    Anchor data shows structural context when the rule's conditions are active.
    TP/SL is the best structural recommendation matching those anchor levels.
  </p>
  {p2}

  <h3>Regime × Anchor Matrix</h3>
  {p3}

  <h3>Entry Readiness Checklist</h3>
  <p class="sdac-desc">Quick-reference summary of best conditions across both systems.</p>
  {p4}
</div>"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_strategy_discovery_anchor_confluence(ctx: dict) -> str:
    packages = ctx.get("packages") or {}
    options  = ctx.get("options") or {}
    max_runs = int(options.get("max_runs") or 5)

    parts = [CSS, '<div class="sdac-wrap">',
             '<h2>Strategy Discovery × Anchor Confluence</h2>',
             '<p class="sdac-desc">'
             'Combines MA Anchor structural cross events with Strategy Discovery signal rules '
             'to identify sessions, regimes and anchor levels where both systems agree on '
             'high-probability entries.'
             '</p>']

    rendered = 0
    for run_id, pkg in packages.items():
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {})
        # Require both anchor_interaction and strategy_discovery to be present
        if not derived.get("anchor_interaction") or not derived.get("strategy_discovery"):
            continue
        parts.append(_render_run_confluence(str(run_id), pkg))
        rendered += 1
        if rendered >= max_runs:
            break

    if rendered == 0:
        parts.append(
            '<p class="sdac-desc" style="color:#f6ad55">⚠ No packages have both '
            'MA Anchor Interaction and Strategy Discovery results. '
            'Enable both <code>anchor_interaction:</code> and '
            '<code>strategy_discovery:</code> in your report YAML.</p>'
        )

    parts.append("</div>")
    return "\n".join(parts)
