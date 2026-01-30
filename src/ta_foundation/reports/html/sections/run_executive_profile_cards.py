from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.utils.kpi import normalize_kpi_key
from ta_foundation.reports.html.sections._wlr_strip import (
    compute_shared_trading_days,
    render_wlr_strip,
)

from ta_foundation.reports.html.sections._session_timeline import render_session_timeline
from ta_foundation.core.exec_ratio_ratings import derive_exec_ratio_ratings

def _background_style(style: str, bg_uri: str | None) -> str:
    """
    Returns inline CSS for the card wrapper based on style.
    """
    if style == "solid" or not bg_uri:
        return "background:#000000;"

    if style == "image-cover":
        return (
            f"background-image:url('{bg_uri}');"
            "background-size:cover;"
            "background-position:center;"
        )

    if style == "image-soft-overlay":
        return (
            f"background-image:linear-gradient(rgba(0,0,0,0.35), rgba(0,0,0,0.35)),"
            f"url('{bg_uri}');"
            "background-size:cover;"
            "background-position:center;"
        )

    if style == "image-dark-overlay":
        return (
            f"background-image:linear-gradient(rgba(0,0,0,0.65), rgba(0,0,0,0.65)),"
            f"url('{bg_uri}');"
            "background-size:cover;"
            "background-position:center;"
        )

    return "background:#000000;"


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_money(x: Any) -> str:
    if x is None or x == "":
        return "—"
    try:
        if isinstance(x, str) and "$" in x:
            return x.strip()
        v = float(x)
        sign = "-" if v < 0 else ""
        v = abs(v)
        return f"{sign}${v:,.0f}"
    except Exception:
        return str(x)


def _fmt_number(x: Any, decimals: int = 2) -> str:
    if x is None or x == "":
        return "—"
    try:
        v = float(x)
        if abs(v - int(v)) < 1e-12:
            return str(int(v))
        return f"{v:.{decimals}f}"
    except Exception:
        return str(x)


def _to_bool(x: Any) -> Optional[bool]:
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in {"true", "t", "1", "yes", "y"}:
        return True
    if s in {"false", "f", "0", "no", "n"}:
        return False
    return None


def _settings_map(pkg: AnalysisPackage) -> dict[str, Any]:
    """
    Build a tolerant mapping from settings item -> value using lowercased item names.
    pkg.settings is DataFrame with cols: section, item, value
    """
    df = getattr(pkg, "settings", None)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict[str, Any] = {}
    for _, r in df.iterrows():
        item = str(r.get("item", "")).strip()
        if not item:
            continue
        out[item.strip().lower()] = r.get("value", "")
    return out


def _kpi_lookup(pkg: AnalysisPackage, *keys: str) -> Any:
    s = getattr(pkg, "summary", None)
    if s is None:
        return None
    kpis = getattr(s, "kpis_all", None)
    if not isinstance(kpis, dict):
        return None
    for k in keys:
        nk = normalize_kpi_key(k)
        if nk in kpis:
            return kpis.get(nk)
    return None

def _win_rate_to_percent(x: Any) -> Optional[float]:
    """
    Normalize win rate to a 0–100 percent value.

    Handles common representations:
      - "71.05%" -> 71.05
      - 71.05    -> 71.05
      - 0.7105   -> 71.05     (fraction)
      - 0.007105 -> 71.05     (double-scaled bug/variant)
    """
    if x is None or x == "":
        return None

    # String with %
    if isinstance(x, str):
        s = x.strip()
        if s.endswith("%"):
            try:
                return float(s[:-1].strip())
            except Exception:
                return None
        # fall through to numeric parse

    try:
        v = float(x)
    except Exception:
        return None

    # Already in percent range
    if 1.0 <= v <= 100.0:
        return v

    # Typical fraction-of-1 representation
    if 0.05 <= v < 1.0:
        return v * 100.0

    # Very small values: could be double-scaled.
    # For win rate, we expect something materially > 5%.
    # If scaling by 100 yields < 5%, try 10000.
    if 0.0 < v < 0.05:
        if v * 100.0 < 5.0 and (v * 10000.0) <= 100.0:
            return v * 10000.0
        return v * 100.0

    return None


def _fmt_percent_value(pct: Optional[float], decimals: int = 0) -> str:
    if pct is None:
        return "—"
    try:
        return f"{pct:.{decimals}f}%"
    except Exception:
        return "—"

def _fmt_ratio(v: object, decimals: int = 2) -> str:
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "—"
    if fv != fv:  # NaN
        return "—"
    return f"{fv:.{decimals}f}"


def _get_avg_ratio_block(pkg) -> dict:
    md = getattr(pkg, "metadata", None) or {}
    if not isinstance(md, dict):
        return {}
    derived = md.get("derived", {})
    if not isinstance(derived, dict):
        return {}
    avg = derived.get("avg_ratios", {})
    return avg if isinstance(avg, dict) else {}

def _render_avg_mfe_etd(pct: Optional[float], etd: Optional[float]) -> str:
    """
        MFE/ETD >5.0: Excellent
        3.0-5.0: Good
        1.5-3.0: Fair
        <1.5: Poor
        """
    if etd == 0.0:
        return "Excellent"
    if pct is None:
        return "Not Ranked"

    rule = "MFE/ETD: >5.0 Excellent; 3.0–5.0 Good; 1.5–3.0 Fair; <1.5 Poor"
    if pct > 3.0:
        return "Excellent"
    if pct >= 1.5:
        return "Good"
    if pct >= 1.0:
        return "Fair"
    return "Poor"


def _render_avg_mae_mfe(pct: Optional[float]) -> str:
    """
       MAE/MFE <0.3: Excellent
       0.3-.6: Good
       0.6-.9: Fair
       >1.0: Poor

       Engineering assumption: 0.9–1.0 is treated as Fair to avoid an unclassified gap.
       """
    if pct is None:
        return "Not Ranked"

    if pct < 0.4:
        return "Excellent"
    if pct < 0.7:
        return "Good"
    if pct <= 1.2:
        return "Fair"
    if pct > 1.2:
        return "Poor"
    return "Not Ranked"

def _render_avg_ratio_lines(pkg) -> str:
    """
    Dark-card-friendly stacked ratio table.
    """
    avg = _get_avg_ratio_block(pkg)

    def _row(display_name: str, key: str) -> str:
        data = avg.get(key) or {}
        val = _fmt_number(data.get("value"), 2)
        label = data.get("label") or "N/A"
        css = data.get("css_class") or ""
        rule = (data.get("rule") or "").replace('"', "&quot;")

        if val in (None, "", "—"):
            return (
                "<tr>"
                f"<td class='ta-avg-name'>{_esc(display_name)}</td>"
                "<td class='ta-avg-value'>—</td>"
                "<td class='ta-avg-meaning'><span class='ta-rating-badge'>N/A</span></td>"
                "</tr>"
            )

        return (
            "<tr>"
            f"<td class='ta-avg-name'>{_esc(display_name)}</td>"
            f"<td class='ta-avg-value'>{_esc(val)}</td>"
            "<td class='ta-avg-meaning'>"
            f"<span class='ta-rating-badge {css}' title=\"{rule}\">{_esc(label)}</span>"
            "</td>"
            "</tr>"
        )

    return (
        "<table class='ta-avg-ratios'>"
        + _row("MFE/ETD", "mfe_etd")
        + _row("MAE/MFE", "mae_mfe")
        + "</table>"
    )


def _fmt_date_range(pkg: AnalysisPackage) -> str:
    s = getattr(pkg, "summary", None)
    start = getattr(s, "start_dt", None) if s else None
    end = getattr(s, "end_dt", None) if s else None

    def _d(dt: Any) -> Optional[str]:
        if dt is None:
            return None
        try:
            # dt is tz-aware per your contract; we only need date for the card
            return dt.strftime("%m/%d/%Y")
        except Exception:
            return None

    a = _d(start)
    b = _d(end)
    if a and b:
        return f"{a} – {b}"

    # fallback: daily date range if summary missing
    df = getattr(pkg, "daily", None)
    if isinstance(df, pd.DataFrame) and not df.empty:
        for cand in ["date", "period"]:
            if cand in df.columns:
                try:
                    dmin = pd.to_datetime(df[cand]).min()
                    dmax = pd.to_datetime(df[cand]).max()
                    return f"{dmin:%m/%d/%Y} – {dmax:%m/%d/%Y}"
                except Exception:
                    pass

    return "—"
def _summary_cell(pkg: AnalysisPackage, row_label: str, col_label: str) -> Any:
    """
    Fetch a value from the summary "Performance" table by row label + column label.
    Expected summary structures (tolerant):
      - pkg.summary.performance_table: dict[row_norm][col_norm] = value
      - pkg.summary.performance: dict[row_norm][col_norm] = value
      - pkg.summary.tables["performance"]: same structure
      - fallback to kpis_all for All trades if available
    """
    s = getattr(pkg, "summary", None)
    if s is None:
        return None

    r = normalize_kpi_key(row_label)
    c = normalize_kpi_key(col_label)

    # Most likely locations (based on a "Performance,All trades,Long trades,Short trades" table)
    candidates = [
        getattr(s, "performance_table", None),
        getattr(s, "performance", None),
        (getattr(s, "tables", None) or {}).get("performance") if hasattr(s, "tables") else None,
    ]

    for t in candidates:
        if isinstance(t, dict):
            row = t.get(r)
            if isinstance(row, dict) and c in row:
                return row.get(c)

    # Fallback: if asking for "All trades", try kpis_all
    if c == normalize_kpi_key("all trades"):
        k_all = getattr(s, "kpis_all", None)
        if isinstance(k_all, dict):
            return k_all.get(r)

    return None


def _active_window_from_settings(sm: dict[str, Any]) -> str:
    """
    Active Window: Start_Time_(HH/mm) to start+duration (HH/mm).
    Render as HH:MM – HH:MM Colorado.
    """
    def _int(key: str) -> Optional[int]:
        v = sm.get(key)
        try:
            if v is None or v == "":
                return None
            return int(float(str(v).strip()))
        except Exception:
            return None

    sh = _int("start_time_(hh)")
    smm = _int("start_time_(mm)")
    dh = _int("duration_time_(hh)")
    dmm = _int("duration_time_(mm)")

    if None in (sh, smm, dh, dmm):
        return "—"

    start = timedelta(hours=sh, minutes=smm)
    dur = timedelta(hours=dh, minutes=dmm)
    end = start + dur

    def _fmt(td: timedelta) -> str:
        total_minutes = int(td.total_seconds() // 60)
        hh = (total_minutes // 60) % 24
        mm = total_minutes % 60
        return f"{hh:02d}:{mm:02d}"

    return f"{_fmt(start)} – {_fmt(end)} Colorado"


def _daily_max_profit_loss(pkg: AnalysisPackage) -> Tuple[Optional[float], Optional[float]]:
    """
    From pkg.daily, compute:
      - max daily profit (largest positive net_profit)
      - max daily loss (most negative net_profit, returned as positive magnitude)
    """
    df = getattr(pkg, "daily", None)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None, None

    # Try common canonical column names first
    candidates = ["net_profit", "net profit", "pnl", "profit"]
    col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in candidates:
            col = c
            break

    if col is None:
        return None, None

    try:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            return None, None
        max_profit = float(s.max())
        max_loss = float(s.min())  # negative if losing day exists
        max_loss_mag = abs(max_loss) if max_loss < 0 else 0.0
        return max_profit, max_loss_mag
    except Exception:
        return None, None


def render_run_executive_profile_cards(ctx: dict) -> str:
    """
       Renders the Executive Strategy Profile cards.

       Expects ctx:
         - packages: dict[str, AnalysisPackage]
         - options (optional):
             - wlr_days_back: int (default 20)
             - wlr_box_px: int (default 10)
             - wlr_gap_px: int (default 2)
             - wlr_show_legend: bool (default False)
       """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options = ctx.get("options", {}) or {}

    show_hint = bool(options.get("show_hint", True))
    card_width = int(options.get("card_width_px", 1280))
    pad = int(options.get("card_padding_px", 24))
    img_w = int(options.get("image_width_px", 520))
    show_run_image = bool(options.get("show_run_image", True))
    background_style = options.get("background_style", "solid")

    show_detail_charts = bool(options.get("show_detail_charts", False))
    detail_chart_width_px = int(options.get("detail_chart_width_px", 540))
    detail_chart_gap_px = int(options.get("detail_chart_gap_px", 16))
    detail_chart_layout = options.get("detail_chart_layout", "two-up")  # "two-up" or "stack"

    days_back = int(options.get("wlr_days_back", 20))
    box_px = int(options.get("wlr_box_px", 10))
    gap_px = int(options.get("wlr_gap_px", 2))
    show_legend = bool(options.get("wlr_show_legend", False))

    # Timeline options
    tl_cell_px = int(options.get("timeline_cell_px", 8))
    tl_gap_px = int(options.get("timeline_gap_px", 1))
    tl_show_hours = bool(options.get("timeline_show_hours", True))
    tl_show_summary = bool(options.get("timeline_show_summary", True))

    shared_days = compute_shared_trading_days(packages, days_back)

    base_font = "font-family: Arial, Helvetica, sans-serif;"
    muted = "color:#b9b9b9;"
    white = "color:#ffffff;"
    bg = "background:#000000;"
    h1 = "font-size:40px; font-weight:800; margin:0 0 16px 0;"
    h2 = "font-size:28px; font-weight:800; margin:22px 0 10px 0;"
    body = "font-size:20px; line-height:1.35;"

    parts: list[str] = []

    if show_hint:
        parts.append(
            f'<div style="{base_font}{muted}font-size:14px; margin: 0 0 10px 0;">'
            "Tip: In the browser, select a full card, copy, then paste into Google Slides. "
            "If Slides strips layout, paste into a Google Doc first, then copy into Slides."
            "</div>"

        )

    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        sm = _settings_map(pkg)

        # Image URI
        # assets = getattr(pkg, "assets", None) or {}
        # img_uri = assets.get("run_image_uri") if show_run_image else None

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        img_uri = derived.get("run_image_uri")

        analysis_chart_uri = derived.get("analysis_image_uri")
        summery_chart_uri = derived.get("summery_image_uri")


        # fallback for older versions
        if not img_uri:
            assets = getattr(pkg, "assets", None) or {}
            img_uri = assets.get("run_image_uri")

        bg_uri = derived.get("background_image_uri")

        # Mappings you specified
        fast_ma = sm.get("averagefast", "—")
        slow_ma = sm.get("averageslow", "—")
        trend_ma = sm.get("averagetrend", "—")

        # Direction Bias derived from Long/Short flags
        long_flag = _to_bool(sm.get("long"))
        short_flag = _to_bool(sm.get("short"))
        if long_flag and short_flag:
            direction_bias = "Long and Short"
        elif long_flag:
            direction_bias = "Long Only"
        elif short_flag:
            direction_bias = "Short Only"
        else:
            direction_bias = "—"

        label = sm.get("label","-")
        chart_values = f" {sm.get("value", "-")}  {sm.get("type","-")} "
        chart_type = sm.get("type","-")
        chart_value = sm.get("value", "-")
        max_trades = sm.get("maxtrades", "—")

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        bg_uri = derived.get("background_image_uri")

        # Max Stop Loss is MaxStop
        max_stop = sm.get("maxstop", None)

        # P/L is MaxTPRatio
        max_tp_ratio = sm.get("maxtpratio", None)

        # Max Take Profit is MaxStop * MaxTPRatio
        max_take_profit = None
        try:
            if max_stop not in (None, "") and max_tp_ratio not in (None, ""):
                max_take_profit = float(max_stop) * float(max_tp_ratio)
        except Exception:
            max_take_profit = None

        # Timeframe is start date to end date
        timeframe = _fmt_date_range(pkg)

        # Active window derived from settings start/duration
        active_window = _active_window_from_settings(sm)

        # Contracts (exists in your settings export)
        contracts = sm.get("contracts", "—")

        # Right-side KPIs + derived ratios
        total_profit = _kpi_lookup(pkg, "total net profit", "net profit")
        max_dd = _kpi_lookup(pkg, "max drawdown", "maximum drawdown")
        # win_rate = _kpi_lookup(pkg, "percent profitable", "win rate")
        raw_win_rate = _summary_cell(pkg, "Percent profitable", "All trades")
        if raw_win_rate is None:
            raw_win_rate = _kpi_lookup(pkg, "percent profitable")

        win_rate_pct = _win_rate_to_percent(raw_win_rate)

        pf = _kpi_lookup(pkg, "profit factor")

        avg_win = _kpi_lookup(pkg, "avg. winning trade", "average winning trade")
        avg_loss = _kpi_lookup(pkg, "avg. losing trade", "average losing trade")

        avg_mae = _kpi_lookup(pkg, "avg. mae", "average mae")
        avg_mfe = _kpi_lookup(pkg, "avg. mfe", "average mfe")
        avg_etd = _kpi_lookup(pkg, "avg. etd", "average etd")

        mae_mfe = None
        mfe_etd = None
        try:
            if avg_mae not in (None, "") and avg_mfe not in (None, "") and float(avg_mfe) != 0:
                mae_mfe = float(avg_mae) / float(avg_mfe)
        except Exception:
            mae_mfe = None

        try:
            if avg_mfe not in (None, "") and avg_etd not in (None, "") and float(avg_etd) != 0:
                mfe_etd = float(avg_mfe) / float(avg_etd)
        except Exception:
            mfe_etd = None

        # ✅ Canonicalize these ratios into pkg.metadata["derived"] so both:
        # - HTML renderers
        # - Playwright card export
        # - downstream analysis
        # can reuse them consistently.
        if not hasattr(pkg, "metadata") or not isinstance(getattr(pkg, "metadata", None), dict):
            pkg.metadata = {}

        derived = pkg.metadata.setdefault("derived", {})
        if not isinstance(derived, dict):
            derived = {}
            pkg.metadata["derived"] = derived

        # Store raw ratio values (even if None)
        derived["mae_mfe_ratio"] = mae_mfe
        derived["mfe_etd_ratio"] = mfe_etd

        # Derive labels + css + rule text into derived["avg_ratios"]
        derive_exec_ratio_ratings(pkg)

        daily_max_profit, daily_max_loss = _daily_max_profit_loss(pkg)

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        max_potential_profit = derived.get("max_potential_profit_usd")
        max_potential_loss = derived.get("max_potential_loss_usd")
        instrument = derived.get("instrument")
        tick_value = derived.get("tick_value_usd")

        daily_max_profit, daily_max_loss = _daily_max_profit_loss(pkg)

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        max_potential_profit = derived.get("max_potential_profit_usd")
        max_potential_loss = derived.get("max_potential_loss_usd")
        instrument = derived.get("instrument")
        tick_value = derived.get("tick_value_usd")

        # Render card

        # --- inside: for run_id in sorted(packages.keys()): ---

        card_parts: list[str] = []

        bg_css = _background_style(background_style,
                                   bg_uri) if "background_style" in locals() else "background:#000000;"
        safe_run_id = _esc(run_id)

        card_parts.append(
            f'<div class="ta-exec-card" id="exec-card--{safe_run_id}" data-run-id="{safe_run_id}" '
            f'style="{base_font}{white}{bg_css} '
            f'padding:{pad}px; width:{card_width}px; box-sizing:border-box;'
            'border-radius:14px; margin: 0 0 18px 0;">'
        )

        # NOTE: Best practice is to put this once per section, not once per card,
        # but it won't break rendering. Keeping it here since that matches your snippet.
        card_parts.append(
            """
        <style>
        .ta-wlr-strip-wrap { margin-top: 6px; margin-bottom: 8px; }
        .ta-wlr-strip { border-collapse: collapse; }
        .ta-wlr-box { border-radius: 0; }
        .ta-session-timeline { }
        

        .ta-rating-badge{
          display:inline-block;
          padding:1px 6px;
          border-radius:10px;
          font-size:11px;
          line-height:16px;
          vertical-align:middle;
          border:1px solid rgba(0,0,0,0.12);
          background:rgba(255,255,255,0.65);
        }
        
        .ta-rating--excellent{ font-weight:700; }
        .ta-rating--good{ font-weight:600; }
        .ta-rating--fair{ font-weight:600; opacity:0.95; }
        .ta-rating--poor{ font-weight:700; text-decoration:underline; }
        
        .ta-avg-ratios{
          width:100%;
          border-collapse:collapse;
        }
        .ta-avg-ratios td{
          padding:2px 0;
          vertical-align:top;
          white-space:nowrap;
        }
        .ta-avg-ratios .ta-avg-name{
          width:72px;
          color:rgba(0,0,0,0.70);
        }
        .ta-avg-ratios .ta-avg-value{
          width:70px;
          text-align:right;
          padding-right:8px;
          font-variant-numeric: tabular-nums;
        }
        .ta-avg-ratios .ta-avg-meaning{
          text-align:left;
          white-space:normal;
        }

        /* ---------- Averages panel ---------- */
        .ta-avg-panel{
          width:100%;
          border-collapse:collapse;
          border-spacing:0;
          background: rgba(0,0,0,0.18);
          border: 1px solid rgba(255,255,255,0.10);
          border-radius: 10px;
        }
        
        .ta-avg-panel td{
          padding:8px 10px;
          vertical-align:top;
        }
        
        .ta-avg-divider{
          border-top: 1px solid rgba(255,255,255,0.10);
          padding:0 !important;
          height:0;
          line-height:0;
          font-size:0;
        }
        
        /* ---------- Ratio lines (stacked) ---------- */
        .ta-avg-ratios{
          width:100%;
          border-collapse:collapse;
        }
        
        .ta-avg-ratios td{
          padding:2px 0;
          vertical-align:middle;
        }
        
        .ta-avg-name{
          width:74px;
          color: rgba(255,255,255,0.70);
        }
        
        .ta-avg-value{
          width:68px;
          text-align:right;
          padding-right:8px;
          font-variant-numeric: tabular-nums;
        }
        
        .ta-avg-meaning{
          text-align:left;
          white-space:nowrap;
        }
        
        /* ---------- KPI list below ratios ---------- */
        .ta-avg-kpis{
          width:100%;
          border-collapse:collapse;
        }
        
        .ta-avg-kpis td{
          padding:2px 0;
          vertical-align:middle;
        }
        
        .ta-avg-kpi-name{
          color: rgba(255,255,255,0.78);
          padding-right:10px;
        }
        
        .ta-avg-kpi-val{
          text-align:right;
          font-variant-numeric: tabular-nums;
          white-space:nowrap;
        }
        
        /* ---------- Rating badge (dark/gold theme) ---------- */
        .ta-rating-badge{
          display:inline-block;
          padding:1px 7px;
          border-radius:999px;
          font-size:11px;
          line-height:16px;
          border: 1px solid rgba(255,255,255,0.14);
          background: rgba(0,0,0,0.25);
          color: rgba(255,255,255,0.90);
        }
        
        .ta-rating--excellent{
          border-color: rgba(255,215,128,0.40);
          color: rgba(255,236,200,0.95);
          font-weight:700;
        }
        .ta-rating--good{
          border-color: rgba(255,215,128,0.28);
          color: rgba(255,236,200,0.88);
          font-weight:600;
        }
        .ta-rating--fair{
          border-color: rgba(255,255,255,0.18);
          color: rgba(255,255,255,0.85);
          font-weight:600;
        }
        .ta-rating--poor{
          border-color: rgba(255,120,120,0.35);
          color: rgba(255,190,190,0.95);
          font-weight:700;
        }


        </style>
        """.strip()
        )

        # Outer 3-column table (OPEN)
        card_parts.append('<table style="width:100%; border-collapse:collapse;">')

        # ------------------------------------------------------------
        # Row 1: Full-width W/L/NT strip + timeline
        # ------------------------------------------------------------
        wlr_html = render_wlr_strip(
            safe_run_id,
            pkg,
            shared_days,
            box_px=box_px,
            gap_px=gap_px,
            show_legend=show_legend,
        )

        timeline_html = render_session_timeline(
            safe_run_id,
            pkg,
            render_bin_minutes=int(options.get("timeline_render_bin_minutes", 60)),
            cell_h_px=int(options.get("timeline_cell_h_px", 10)),
            show_hour_labels=bool(options.get("timeline_show_hours", True)),
            show_summary=bool(options.get("timeline_show_summary", True)),
        )

        card_parts.append("<tr>")
        card_parts.append(
            '<td colspan="3" style="padding:0 0 8px 0; vertical-align:top; width:100%;">'
            # Use an inner table to force full width in all renderers (browser + Playwright + Slides paste)
            '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%; table-layout:fixed;">'
            "<tr>"
            '<td style="width:100%; padding:0; margin:0;">'
            f"{wlr_html}"
            f"{timeline_html}"
            "</td>"
            "</tr>"
            "</table>"
            "</td>"
        )
        card_parts.append("</tr>")

        # ------------------------------------------------------------
        # FIX: Row 2 must be opened before adding column <td> cells
        # ------------------------------------------------------------
        card_parts.append("<tr>")

        # Column 1: image (OPEN td)
        card_parts.append(f'<td style="width:{img_w}px; vertical-align:top; padding-right:22px;">')
        if img_uri:
            card_parts.append(
                f'<img src="{img_uri}" style="width:{img_w}px; height:auto; object-fit:cover; border-radius:12px; display:block;" />'
            )
        else:
            card_parts.append(f'<div style="{muted} font-size:16px;">No image for {_esc(run_id)}</div>')
        card_parts.append("</td>")  # Column 1 (CLOSE td)


        # Column 2: strategy + logic blocks (OPEN td)
        # card_parts.append('<td style="vertical-align:top; padding-right:26px; width:52%;">')
        card_parts.append('<td style="vertical-align:top; padding-right:26px; width:33%;">')

        # card_parts.append(f'<div style="{h1}">Pantheon Master Bot — Executive Strategy Profile</div>')
        card_parts.append(f'<div style="{h2}">Pantheon Bot Profile</div>')

        # Strategy Profile block
        card_parts.append(f'<div style="{body}">')
        card_parts.append(f'<div><span style="font-weight:800;">Bot Profile:</span> {_esc(run_id)}</div>')
        card_parts.append(f'<div><span style="font-weight:800;">Timeframe:</span> {_esc(timeframe)}</div>')
        if instrument and tick_value:
            card_parts.append(f'<div"><span style="font-weight:800;">Instrument:</span> {_esc(instrument)} 'f'(<span style="font-weight:800;">Tick:</span> ${_esc(tick_value)})</div>')
        card_parts.append(f'<div><span style="font-weight:800;">Time:</span> {_esc(chart_values)}</div>')
        card_parts.append("</div>")

        # Core Trading Logic
        card_parts.append(f'<div style="{h2}">Core Trading Logic</div>')
        card_parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>Fast MA:</b> {_esc(fast_ma)}</li>")
        card_parts.append(f"<li><b>Slow MA:</b> {_esc(slow_ma)}</li>")
        card_parts.append(f"<li><b>Trend MA:</b> {_esc(trend_ma)}</li>")
        card_parts.append(f"<li><b>Direction Bias:</b> {_esc(direction_bias)}</li>")
        card_parts.append("</ul></div>")

        # Session Constraints
        card_parts.append(f'<div style="{h2}">Session Constraints</div>')
        card_parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>Active Window:</b> {_esc(active_window)}</li>")
        card_parts.append(f"<li><b>Max Trades per Session:</b> {_esc(max_trades)}</li>")
        card_parts.append("</ul></div>")

        # Risk & Trade Controls
        card_parts.append(f'<div style="{h2}">Risk &amp; Trade Controls</div>')
        card_parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>Contracts:</b> {_esc(contracts)}</li>")
        card_parts.append(f"<li><b>Max Stop Loss:</b> {_esc(_fmt_number(max_stop, 0))} ticks</li>")
        card_parts.append(
            f"<li><b>Max Take Profit:</b> "
            f"{_esc('—' if max_take_profit is None else _fmt_number(max_take_profit, 0))} ticks</li>"
        )
        card_parts.append(
            f"<li><b>P/L:</b> "
            f"{_esc('—' if max_tp_ratio in (None, '') else f'{_fmt_number(max_tp_ratio, 2)}/1')} P/L</li>"
        )
        card_parts.append("</ul></div>")

        card_parts.append("</td>")  # Column 2 (CLOSE td)

        # Column 3: performance blocks (OPEN td)
        card_parts.append('<td style="vertical-align:top; width:30%;">')

        card_parts.append(f'<div style="{h2}">Performance</div>')
        card_parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>Total Net Profit:</b> {_esc(_fmt_money(total_profit))}</li>")
        card_parts.append(f"<li><b>Profit Factor:</b> {_esc(_fmt_number(pf, 2))}</li>")
        card_parts.append(f"<li><b>Max Drawdown:</b> {_esc(_fmt_money(max_dd))}</li>")
        card_parts.append(f"<li><b>Win Rate:</b> {_esc(_fmt_percent_value(win_rate_pct, 2))}</li>")
        card_parts.append("</ul></div>")

        card_parts.append(f'<div style="{h2}">Averages</div>')
        card_parts.append(f'<div style="{body}">')
        # card_parts.append(
        #     f'<div style="{muted} margin-bottom:6px;">'
        #     f"MAE/MFE: {_esc(_fmt_number(mae_mfe, 2))} &nbsp;&nbsp; "
        #     f"MFE/ETD: {_esc(_fmt_number(mfe_etd, 2))}"
        #     f"</div>"
        # )
        card_parts.append(f'<ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>MAE:</b> {_esc(_fmt_money(avg_mae))}</li>")
        card_parts.append(f"<li><b>MFE:</b> {_esc(_fmt_money(avg_mfe))}</li>")
        card_parts.append(f"<li><b>ETD:</b> {_esc(_fmt_money(avg_etd))}</li>")

        card_parts.append(f"<li><b>MAE/MFE:</b> {_esc(_fmt_number(mae_mfe, 2))} : {_render_avg_mae_mfe(float(mae_mfe))}</li>")
        card_parts.append(f"<li><b>MFE/ETD:</b> {_esc(_fmt_number(mfe_etd, 2))} : {_render_avg_mfe_etd(mfe_etd,avg_etd)}</li>")

        card_parts.append(f"<li><b>Avg win:</b> {_esc(_fmt_money(avg_win))}</li>")
        card_parts.append(f"<li><b>Avg loss:</b> {_esc(_fmt_money(avg_loss))}</li>")
        card_parts.append("</ul></div>")

        card_parts.append(f'<div style="{h2}">Daily Risk to Reward</div>')
        card_parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>Daily Max Profit:</b> {_esc(_fmt_money(daily_max_profit))}</li>")
        card_parts.append(f"<li><b>Daily Max Loss:</b> {_esc(_fmt_money(daily_max_loss))}</li>")
        card_parts.append(f"<li><b>Max Potential Profit:</b> {_esc(_fmt_money(max_potential_profit))}</li>")
        card_parts.append(f"<li><b>Max Potential Loss:</b> {_esc(_fmt_money(max_potential_loss))}</li>")
        card_parts.append("</ul></div>")

        card_parts.append("</td>")  # Column 3 (CLOSE td)

        card_parts.append("</tr>")
        card_parts.append("</table>")  # Outer table (CLOSE)

        # --- optional detail charts (below the profile content) ---
        if show_detail_charts and (analysis_chart_uri or summery_chart_uri):
            card_parts.append(f'<div style="margin-top:16px;"></div>')

            if detail_chart_layout == "stack":
                # Stack vertically
                if analysis_chart_uri:
                    card_parts.append(
                        f'<div style="margin-bottom:{detail_chart_gap_px}px;">'
                        f'<img src="{analysis_chart_uri}" style="width:{detail_chart_width_px}px; height:auto; '
                        f'border-radius:12px; display:block;" />'
                        f"</div>"
                    )
                if summery_chart_uri:
                    card_parts.append(
                        f'<div>'
                        f'<img src="{summery_chart_uri}" style="width:{detail_chart_width_px}px; height:auto; '
                        f'border-radius:12px; display:block;" />'
                        f"</div>"
                    )
            else:
                # Default: two-up side-by-side
                card_parts.append('<table style="width:100%; border-collapse:collapse;">')
                card_parts.append("<tr>")

                # Left chart
                card_parts.append(f'<td style="vertical-align:top; padding-right:{detail_chart_gap_px}px;">')
                if analysis_chart_uri:
                    card_parts.append(
                        f'<img src="{analysis_chart_uri}" style="width:{detail_chart_width_px}px; height:auto; '
                        f'border-radius:12px; display:block;" />'
                    )
                else:
                    card_parts.append(f'<div style="{muted} font-size:16px;">No _Analysis image</div>')
                card_parts.append("</td>")

                # Right chart
                card_parts.append(f'<td style="vertical-align:top;">')
                if summery_chart_uri:
                    card_parts.append(
                        f'<img src="{summery_chart_uri}" style="width:{detail_chart_width_px}px; height:auto; '
                        f'border-radius:12px; display:block;" />'
                    )
                else:
                    card_parts.append(f'<div style="{muted} font-size:16px;">No _Summery/_Summary image</div>')
                card_parts.append("</td>")

                card_parts.append("</tr>")
                card_parts.append("</table>")

        card_parts.append("</div>")  # Card wrapper (CLOSE)

        # Append one fully-closed card
        parts.append("\n".join(card_parts))

    ###############################################################################################
    return "\n".join(parts)
