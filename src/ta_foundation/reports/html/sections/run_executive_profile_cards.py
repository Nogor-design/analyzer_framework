from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.utils.kpi import normalize_kpi_key


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
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options = ctx.get("options", {}) or {}

    show_hint = bool(options.get("show_hint", True))
    card_width = int(options.get("card_width_px", 1280))
    pad = int(options.get("card_padding_px", 24))
    img_w = int(options.get("image_width_px", 520))
    show_run_image = bool(options.get("show_run_image", True))
    background_style = options.get("background_style", "solid")

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

        daily_max_profit, daily_max_loss = _daily_max_profit_loss(pkg)

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        max_potential_profit = derived.get("max_potential_profit_usd")
        max_potential_loss = derived.get("max_potential_loss_usd")
        instrument = derived.get("instrument")
        tick_value = derived.get("tick_value_usd")

        # Render card
        # parts.append(
        #     f'<div style="{base_font}{bg}{white} padding:{pad}px; width:{card_width}px; box-sizing:border-box;'
        #     ' border-radius:14px; margin: 0 0 18px 0;">'
        # )

        # bg_css = _background_style(background_style, bg_uri)

        # parts.append(
        #     f'<div style="{base_font}{white}{bg_css} '
        #     f'padding:{pad}px; width:{card_width}px; box-sizing:border-box;'
        #     'border-radius:14px; margin: 0 0 18px 0;">'
        # )

        # parts.append('<table style="width:100%; border-collapse:collapse;">')
        # parts.append("<tr>")

        # Left image
        # parts.append(f'<td style="width:{img_w}px; vertical-align:top; padding-right:22px;">')
        # if img_uri:
        #     parts.append(
        #         f'<img src="{img_uri}" style="width:{img_w}px; height:auto; border-radius:12px; display:block;" />'
        #     )
        # else:
        #     parts.append(f'<div style="{muted} font-size:16px;">No image for {_esc(run_id)}</div>')
        # parts.append("</td>")
        #
        # # Right side content
        # parts.append('<td style="vertical-align:top;">')
        # parts.append(f'<div style="{h1}">Pantheon Master Bot — Executive Strategy Profile</div>')
        #
        # parts.append(f'<div style="{body}">')
        # parts.append(f'<div><span style="font-weight:800;">Strat:</span> {_esc(label)}</div>')
        # parts.append(f'<div><span style="font-weight:800;">Bot Profile:</span> {_esc(run_id)}</div>')
        # parts.append(f'<div><span style="font-weight:800;">Timeframe:</span> {_esc(timeframe)}</div>')
        # if instrument and tick_value:
        #     parts.append(f'<div style="{body}"><span style="font-weight:800;">Instrument:</span> {_esc(instrument)} '
        #                  f'(<span style="font-weight:800;">Tick:</span> ${_esc(tick_value)})</div>')
        # parts.append(f'<div><span style="font-weight:800;">Timeframe:</span> {_esc(chart_values)}</div>')
        #
        # parts.append("</div>")
        #
        # parts.append('<table style="width:100%; border-collapse:collapse; margin-top:14px;">')
        # parts.append("<tr>")
        #
        # # Left inner column
        # parts.append('<td style="vertical-align:top; padding-right:26px; width:62%;">')
        #
        # parts.append(f'<div style="{h2}">Core Trading Logic</div>')
        # parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>Fast MA:</b> {_esc(fast_ma)}</li>")
        # parts.append(f"<li><b>Slow MA:</b> {_esc(slow_ma)}</li>")
        # parts.append(f"<li><b>Trend MA:</b> {_esc(trend_ma)}</li>")
        # parts.append(f"<li><b>Direction Bias:</b> {_esc(direction_bias)}</li>")
        # parts.append("</ul></div>")
        #
        # parts.append(f'<div style="{h2}">Session Constraints</div>')
        # parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>Active Window:</b> {_esc(active_window)}</li>")
        # parts.append(f"<li><b>Max Trades per Session:</b> {_esc(max_trades)}</li>")
        # parts.append("</ul></div>")
        #
        # parts.append(f'<div style="{h2}">Risk &amp; Trade Controls</div>')
        # parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>Contracts:</b> {_esc(contracts)}</li>")
        # parts.append(f"<li><b>Max Stop Loss:</b> {_esc(_fmt_number(max_stop, 0))} ticks</li>")
        # parts.append(
        #     f"<li><b>Max Take Profit:</b> "
        #     f"{_esc('—' if max_take_profit is None else _fmt_number(max_take_profit, 0))} ticks</li>"
        # )
        # parts.append(
        #     f"<li><b>P/L:</b> "
        #     f"{_esc('—' if max_tp_ratio in (None, '') else f'{_fmt_number(max_tp_ratio, 2)}/1')} P/L</li>"
        # )
        # parts.append("</ul></div>")
        #
        # parts.append("</td>")
        #
        # # Right inner column
        # parts.append('<td style="vertical-align:top; width:38%;">')
        #
        # parts.append(f'<div style="{h2}">Performance</div>')
        # parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>Total Net Profit:</b> {_esc(_fmt_money(total_profit))}</li>")
        # parts.append(f"<li><b>Profit Factor:</b> {_esc(_fmt_number(pf, 2))}</li>")
        # parts.append(f"<li><b>Max Drawdown:</b> {_esc(_fmt_money(max_dd))}</li>")
        # # parts.append(f"<li><b>Win Rate:</b> {_esc(_fmt_number(win_rate, 0))}%</li>")
        # parts.append(f"<li><b>Win Rate:</b> {_esc(_fmt_percent_value(win_rate_pct, 0))}</li>")
        #
        # parts.append("</ul></div>")
        #
        # parts.append(f'<div style="{h2}">Averages</div>')
        # parts.append(f'<div style="{body}">')
        # parts.append(f'<div style="{muted} margin-bottom:6px;">MAE/MFE: {_esc(_fmt_number(mae_mfe, 2))} &nbsp;&nbsp; MFE/ETD: {_esc(_fmt_number(mfe_etd, 2))}</div>')
        # parts.append(f'<ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>MAE:</b> {_esc(_fmt_money(avg_mae))}</li>")
        # parts.append(f"<li><b>MFE:</b> {_esc(_fmt_money(avg_mfe))}</li>")
        # parts.append(f"<li><b>ETD:</b> {_esc(_fmt_money(avg_etd))}</li>")
        # parts.append(f"<li><b>Avg win:</b> {_esc(_fmt_money(avg_win))}</li>")
        # parts.append(f"<li><b>Avg loss:</b> {_esc(_fmt_money(avg_loss))}</li>")
        # parts.append("</ul></div>")
        #
        # parts.append(f'<div style="{h2}">Daily Risk to Reward</div>')
        # parts.append(f'<div style="{body}"><ul style="margin: 0 0 0 22px; padding:0;">')
        # parts.append(f"<li><b>Daily Max Profit:</b> {_esc(_fmt_money(daily_max_profit))}</li>")
        # parts.append(f"<li><b>Daily Max Loss:</b> {_esc(_fmt_money(daily_max_loss))}</li>")
        # parts.append(f"<li><b>Max Potential Profit:</b> {_esc(_fmt_money(max_potential_profit))}</li>")
        # parts.append(f"<li><b>Max Potential Loss:</b> {_esc(_fmt_money(max_potential_loss))}</li>")
        #
        # parts.append("</ul></div>")
        #
        # parts.append("</td>")
        #
        # parts.append("</tr>")
        # parts.append("</table>")  # inner
        #
        # parts.append("</td>")  # right main
        # parts.append("</tr>")
        # parts.append("</table>")  # outer
        #
        # parts.append("</div>")  # card
    ###############################################################################################
        # --- inside: for run_id in sorted(packages.keys()): ---

        card_parts: list[str] = []

        # Card wrapper (OPEN)
        bg_css = _background_style(background_style,
                                   bg_uri) if "background_style" in locals() else "background:#000000;"
        card_parts.append(
            f'<div style="{base_font}{white}{bg_css} '
            f'padding:{pad}px; width:{card_width}px; box-sizing:border-box;'
            'border-radius:14px; margin: 0 0 18px 0;">'
        )

        # Outer 3-column table (OPEN)
        card_parts.append('<table style="width:100%; border-collapse:collapse;">')
        card_parts.append("<tr>")

        # Column 1: image (OPEN td)
        card_parts.append(f'<td style="width:{img_w}px; vertical-align:top; padding-right:22px;">')
        if img_uri:
            card_parts.append(
                # f'<img src="{img_uri}" style="width:{img_w}px; height:auto; border-radius:12px; display:block;" />'
                f'<img src="{img_uri}" style="width:{img_w}px; max-height:auto; object-fit:cover; border-radius:12px; display:block;" />'
            )
        else:
            card_parts.append(f'<div style="{muted} font-size:16px;">No image for {_esc(run_id)}</div>')
        card_parts.append("</td>")  # Column 1: image (CLOSE td)

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
        card_parts.append(
            f'<div style="{muted} margin-bottom:6px;">'
            f"MAE/MFE: {_esc(_fmt_number(mae_mfe, 2))} &nbsp;&nbsp; "
            f"MFE/ETD: {_esc(_fmt_number(mfe_etd, 2))}"
            f"</div>"
        )
        card_parts.append(f'<ul style="margin: 0 0 0 22px; padding:0;">')
        card_parts.append(f"<li><b>MAE:</b> {_esc(_fmt_money(avg_mae))}</li>")
        card_parts.append(f"<li><b>MFE:</b> {_esc(_fmt_money(avg_mfe))}</li>")
        card_parts.append(f"<li><b>ETD:</b> {_esc(_fmt_money(avg_etd))}</li>")
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

        card_parts.append("</div>")  # Card wrapper (CLOSE)

        # Append one fully-closed card
        parts.append("\n".join(card_parts))

    ###############################################################################################
    return "\n".join(parts)
