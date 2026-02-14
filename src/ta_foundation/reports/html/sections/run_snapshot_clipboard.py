from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage


def render_run_snapshot_clipboard(ctx: dict) -> str:
    """
    Copy/paste-friendly per-run snapshot table intended for Google Slides.

    ctx expects:
      - packages: dict[str, AnalysisPackage]
      - options (optional): dict with:
          - compact: bool
          - show_hint: bool
          - style: str  (one of: "default", "clean", "contrast", "minimal", "slides")
          - density: str ("comfortable" | "compact" | "tight")
          - layout: str ("grid" | "stack")
          - columns: int (grid columns for desktop; default 2)
          - emphasize_negatives: bool (color negatives; default True)
    """
    print(f"Options: {ctx.get("options", {})}")
    packages: dict[str, AnalysisPackage] = ctx["packages"]
    options: dict[str, Any] = ctx.get("options", {}) or {}

    compact: bool = bool(options.get("compact", False))
    show_hint: bool = bool(options.get("show_hint", True))

    style: str = str(options.get("style", "default")).strip().lower()
    density: str = str(options.get("density", "comfortable")).strip().lower()
    layout: str = str(options.get("layout", "grid")).strip().lower()
    columns: int = int(options.get("columns", 2) or 2)
    emphasize_negatives: bool = bool(options.get("emphasize_negatives", True))

    style = style if style in {"default", "clean", "contrast", "minimal", "slides"} else "default"
    density = density if density in {"comfortable", "compact", "tight"} else "comfortable"
    layout = layout if layout in {"grid", "stack"} else "grid"
    columns = max(1, min(columns, 4))

    wrapper_classes = " ".join(
        [
            "tf-snap",
            f"tf-snap--{style}",
            f"tf-snap--{density}",
            f"tf-snap--{layout}",
            f"tf-snap--cols-{columns}",
            "tf-snap--neg" if emphasize_negatives else "tf-snap--no-neg",
        ]
    )

    cards: list[str] = []
    for run_id, pkg in sorted(packages.items(), key=lambda kv: str(kv[0]).lower()):
        cards.append(_render_one_run(run_id=str(run_id), pkg=pkg, compact=compact, show_hint=show_hint))

    if not cards:
        return "<div class='tf-note'>No runs found.</div>"

    # Self-contained CSS for this section (no external assets)
    css = _section_css()

    return (
        f"<style>{css}</style>"
        f"<div class='{wrapper_classes}'>"
        "<div class='tf-card-grid'>"
        + "".join(cards)
        + "</div></div>"
    )


def _render_one_run(run_id: str, pkg: AnalysisPackage, compact: bool, show_hint: bool) -> str:
    kpis_all = _get_summary_kpis(pkg)

    start_dt = getattr(pkg.summary, "start_dt", None) if pkg.summary is not None else None
    end_dt = getattr(pkg.summary, "end_dt", None) if pkg.summary is not None else None

    total_net_profit = _kpi(kpis_all, "Total net profit")
    max_drawdown = _kpi(kpis_all, "Max. drawdown", "Max drawdown")
    profit_factor = _kpi(kpis_all, "Profit factor")

    total_trades = _kpi(kpis_all, "Total # of trades", "Total number of trades", "Total trades")
    pct_profitable = _kpi(kpis_all, "Percent profitable", "% profitable", "Pct profitable")

    avg_winner = _kpi(kpis_all, "Avg. winning trade", "Avg winning trade", "Average winning trade")
    avg_loser = _kpi(kpis_all, "Avg. losing trade", "Avg losing trade", "Average losing trade")

    avg_mae = _kpi(kpis_all, "Avg. MAE", "Avg MAE")
    avg_mfe = _kpi(kpis_all, "Avg. MFE", "Avg MFE")
    avg_etd = _kpi(kpis_all, "Avg. ETD", "Avg ETD")

    # NinjaTrader does not export these ratios in the summary; compute them.
    mae_over_mfe = _safe_div(_to_float(avg_mae), _to_float(avg_mfe))   # MAE/MFE
    mfe_over_etd = _safe_div(_to_float(avg_mfe), _to_float(avg_etd))   # MFE/ETD

    lw_day, ll_day = _largest_win_loss_day(pkg)

    rows = [
        ("Total net profit", _fmt_money(total_net_profit)),
        ("Max drawdown", _fmt_money(max_drawdown)),
        ("Profit factor", _fmt_float(profit_factor)),
        ("Start date", _fmt_dt(start_dt)),
        ("End date", _fmt_dt(end_dt)),
        ("Total trades", _fmt_int(total_trades)),
        ("Percent profitable", _fmt_percent(pct_profitable)),
        ("Avg winning trade", _fmt_money(avg_winner)),
        ("Avg losing trade", _fmt_money(avg_loser)),
        ("Avg MAE", _fmt_money(avg_mae)),
        ("Avg MFE", _fmt_money(avg_mfe)),
        ("Avg ETD", _fmt_money(avg_etd)),
        ("MAE/MFE", _fmt_float(mae_over_mfe)),
        ("MFE/ETD", _fmt_float(mfe_over_etd)),
        ("Largest winning day", _fmt_day_tuple(lw_day)),
        ("Largest losing day", _fmt_day_tuple(ll_day)),
    ]

    header_html = "" if compact else f"<div class='tf-run-card-title'>Run: <span class='tf-mono'>{_esc(run_id)}</span></div>"
    table_html = _render_kv_table(rows)
    hint_html = (
        "<div class='tf-small tf-muted'>Tip: click-drag to select the table and paste into Google Slides.</div>"
        if show_hint
        else ""
    )

    diag_html = ""
    if not kpis_all:
        diag_html = "<div class='tf-small tf-muted'>Note: summary KPI dictionary is empty for this run.</div>"

    return "<div class='tf-card'>" + header_html + table_html + hint_html + diag_html + "</div>"


def _section_css() -> str:
    # Note: we use CSS variables per style variant. No external assets.
    return """
/* --- Base layout --- */
.tf-snap .tf-card-grid{display:grid;grid-gap:12px;}
.tf-snap--grid .tf-card-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
.tf-snap--stack .tf-card-grid{grid-template-columns:1fr;}
.tf-snap--cols-1 .tf-card-grid{grid-template-columns:1fr;}
.tf-snap--cols-2 .tf-card-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
.tf-snap--cols-3 .tf-card-grid{grid-template-columns:repeat(3,minmax(0,1fr));}
.tf-snap--cols-4 .tf-card-grid{grid-template-columns:repeat(4,minmax(0,1fr));}

/* --- Card base --- */
.tf-snap .tf-card{
  border:1px solid var(--tf-border);
  background:var(--tf-bg);
  border-radius:14px;
  padding:12px 12px 10px 12px;
  box-shadow:var(--tf-shadow);
}
.tf-snap .tf-run-card-title{
  font-weight:700;
  margin-bottom:10px;
  color:var(--tf-title);
}
.tf-snap .tf-mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;}
.tf-snap .tf-small{font-size:12px;margin-top:8px;}
.tf-snap .tf-muted{color:var(--tf-muted);}

/* --- Table base --- */
.tf-snap .tf-kpi-table{width:100%;border-collapse:separate;border-spacing:0;}
.tf-snap .tf-kpi-table tr td{border-top:1px solid var(--tf-row-border);}
.tf-snap .tf-kpi-table tr:first-child td{border-top:none;}
.tf-snap .tf-kpi-label{
  width:58%;
  padding:8px 8px;
  color:var(--tf-label);
}
.tf-snap .tf-kpi-value{
  width:42%;
  padding:8px 8px;
  text-align:right;
  color:var(--tf-value);
  font-weight:600;
}

/* --- Density variants --- */
.tf-snap--comfortable .tf-kpi-label, .tf-snap--comfortable .tf-kpi-value{padding:8px 8px;}
.tf-snap--compact .tf-kpi-label, .tf-snap--compact .tf-kpi-value{padding:6px 8px;}
.tf-snap--tight .tf-kpi-label, .tf-snap--tight .tf-kpi-value{padding:4px 8px; font-size:13px;}

/* --- Negative emphasis (optional) ---
   We style only if the rendered value contains "-$" or starts with "-" (common in our formatters).
*/
.tf-snap--neg .tf-kpi-value[data-neg="1"]{color:var(--tf-neg);}

/* --- Style presets (CSS variables) --- */
.tf-snap--default{
  --tf-bg:#ffffff;
  --tf-border:#e6e6ea;
  --tf-row-border:#eeeeF2;
  --tf-title:#111827;
  --tf-label:#374151;
  --tf-value:#111827;
  --tf-muted:#6b7280;
  --tf-shadow:0 1px 2px rgba(0,0,0,0.06);
  --tf-neg:#b91c1c;
}

.tf-snap--clean{
  --tf-bg:#ffffff;
  --tf-border:#d7dbe3;
  --tf-row-border:#eef0f6;
  --tf-title:#0f172a;
  --tf-label:#334155;
  --tf-value:#0f172a;
  --tf-muted:#64748b;
  --tf-shadow:none;
  --tf-neg:#991b1b;
}

.tf-snap--contrast{
  --tf-bg:#0b1020;
  --tf-border:#23304f;
  --tf-row-border:#1b2644;
  --tf-title:#e5e7eb;
  --tf-label:#cbd5e1;
  --tf-value:#ffffff;
  --tf-muted:#94a3b8;
  --tf-shadow:0 6px 18px rgba(0,0,0,0.35);
  --tf-neg:#fb7185;
}

.tf-snap--minimal{
  --tf-bg:transparent;
  --tf-border:#e5e7eb;
  --tf-row-border:#f1f5f9;
  --tf-title:#111827;
  --tf-label:#4b5563;
  --tf-value:#111827;
  --tf-muted:#6b7280;
  --tf-shadow:none;
  --tf-neg:#b91c1c;
}

.tf-snap--slides{
  /* Intentionally "Google Slides friendly": slightly larger text, stronger borders */
  --tf-bg:#ffffff;
  --tf-border:#cbd5e1;
  --tf-row-border:#dbe3ef;
  --tf-title:#0f172a;
  --tf-label:#1f2937;
  --tf-value:#0f172a;
  --tf-muted:#475569;
  --tf-shadow:none;
  --tf-neg:#b91c1c;
}

/* Responsive safety: stack on narrow viewports */
@media (max-width: 900px){
  .tf-snap .tf-card-grid{grid-template-columns:1fr;}
}
""".strip()


def _get_summary_kpis(pkg: AnalysisPackage) -> dict[str, Any]:
    summary = getattr(pkg, "summary", None)
    if summary is None:
        return {}

    d = getattr(summary, "kpis_all", None)
    if isinstance(d, dict) and d:
        return d

    d = getattr(summary, "kpis", None)
    if isinstance(d, dict) and d:
        return d

    return {}


def _largest_win_loss_day(pkg: AnalysisPackage) -> tuple[Optional[tuple[datetime, float]], Optional[tuple[datetime, float]]]:
    daily = getattr(pkg, "daily", None)
    if daily is None or not isinstance(daily, pd.DataFrame) or daily.empty:
        return None, None

    date_col = _first_existing_col(daily, ["date", "period", "Period"])
    net_col = _first_existing_col(daily, ["net_profit", "Net profit", "net profit", "Net_profit"])

    if date_col is None or net_col is None:
        return None, None

    df = daily[[date_col, net_col]].copy()
    df[net_col] = _to_float_series(df[net_col])
    df = df.dropna(subset=[net_col])
    if df.empty:
        return None, None

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    if df.empty:
        return None, None

    max_row = df.loc[df[net_col].idxmax()]
    min_row = df.loc[df[net_col].idxmin()]

    return (
        (max_row[date_col].to_pydatetime(), float(max_row[net_col])),
        (min_row[date_col].to_pydatetime(), float(min_row[net_col])),
    )


def _kpi(kpis_all: dict[str, Any], *keys: str) -> Any:
    if not isinstance(kpis_all, dict) or not kpis_all:
        return None

    for k in keys:
        if k in kpis_all:
            return kpis_all.get(k)

    candidates: list[str] = []
    for k in keys:
        nk = _norm_key_space(k)
        candidates.extend([nk, nk.replace(" ", "_"), nk.replace(" ", ""), k.strip().lower(), k.strip().lower().replace(" ", "_")])

    for c in candidates:
        if c in kpis_all:
            return kpis_all.get(c)

    norm_map: dict[str, str] = {}
    for existing in kpis_all.keys():
        if not isinstance(existing, str):
            continue
        ne = _norm_key_space(existing)
        norm_map[ne] = existing
        norm_map[ne.replace(" ", "_")] = existing
        norm_map[ne.replace(" ", "")] = existing

    for c in candidates:
        hit = norm_map.get(c)
        if hit is not None:
            return kpis_all.get(hit)

    return None


def _norm_key_space(s: str) -> str:
    out = []
    prev_space = False
    for ch in str(s).lower():
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        else:
            if not prev_space:
                out.append(" ")
                prev_space = True
    return " ".join("".join(out).split())


def _first_existing_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = list(df.columns)
    colset = set(cols)
    for c in candidates:
        if c in colset:
            return c
    lower_map = {str(c).lower(): c for c in cols}
    for c in candidates:
        key = str(c).lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _to_float_series(s: pd.Series) -> pd.Series:
    def parse_one(x: Any) -> float | None:
        if x is None:
            return None
        if isinstance(x, (int, float)) and not pd.isna(x):
            return float(x)
        txt = str(x).strip()
        if txt == "" or txt.lower() == "nan":
            return None
        neg = False
        if txt.startswith("(") and txt.endswith(")"):
            neg = True
            txt = txt[1:-1].strip()
        txt = txt.replace("$", "").replace(",", "").strip()
        try:
            v = float(txt)
        except Exception:
            return None
        return -v if neg else v

    return s.apply(parse_one)


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not pd.isna(v):
        return float(v)

    txt = str(v).strip()
    if txt == "" or txt.lower() == "nan":
        return None

    if txt.endswith("%"):
        txt = txt[:-1].strip()

    neg = False
    if txt.startswith("(") and txt.endswith(")"):
        neg = True
        txt = txt[1:-1].strip()

    txt = txt.replace("$", "").replace(",", "").strip()
    try:
        fv = float(txt)
    except Exception:
        return None

    return -fv if neg else fv


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    if b == 0:
        return None
    return a / b


def _render_kv_table(rows: list[tuple[str, str]]) -> str:
    trs = []
    for label, value in rows:
        trs.append(
            "<tr>"
            f"<td class='tf-kpi-label'>{_esc(label)}</td>"
            f"<td class='tf-kpi-value'>{_esc(value)}</td>"
            "</tr>"
        )
    return "<table class='tf-kpi-table'>" + "".join(trs) + "</table>"


def _fmt_dt(dt: Any) -> str:
    if dt is None:
        return "--"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    try:
        parsed = pd.to_datetime(dt, errors="coerce")
        if pd.isna(parsed):
            return "--"
        return parsed.to_pydatetime().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "--"


def _fmt_money(v: Any) -> str:
    if v is None or v == "":
        return "--"
    if isinstance(v, str):
        vv = _to_float_series(pd.Series([v])).iloc[0]
        if vv is None or pd.isna(vv):
            return v
        v = vv
    try:
        fv = float(v)
    except Exception:
        return str(v)
    sign = "-" if fv < 0 else ""
    fv = abs(fv)
    return f"{sign}${fv:,.2f}"


def _fmt_percent(v: Any) -> str:
    if v is None or v == "":
        return "--"
    if isinstance(v, str):
        txt = v.strip().replace("%", "")
        vv = _to_float_series(pd.Series([txt])).iloc[0]
        if vv is None or pd.isna(vv):
            return v
        v = vv
    try:
        fv = float(v)
    except Exception:
        return str(v)
    if 0 <= fv <= 1:
        fv *= 100.0
    return f"{fv:,.2f}%"


def _fmt_float(v: Any) -> str:
    if v is None or v == "":
        return "--"
    try:
        fv = float(v)
    except Exception:
        return str(v)
    return f"{fv:,.2f}"


def _fmt_int(v: Any) -> str:
    if v is None or v == "":
        return "--"
    try:
        return f"{int(round(float(v)))}"
    except Exception:
        return str(v)


def _fmt_day_tuple(t: Optional[tuple[datetime, float]]) -> str:
    if not t:
        return "--"
    dt, val = t
    try:
        dts = dt.strftime("%Y-%m-%d")
    except Exception:
        dts = str(dt)
    return f"{dts} ({_fmt_money(val)})"


def _esc(s: Any) -> str:
    text = "" if s is None else str(s)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
