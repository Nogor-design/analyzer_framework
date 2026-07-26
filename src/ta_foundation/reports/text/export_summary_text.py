"""
export_summary_text.py
──────────────────────
Writes a condensed, LLM-friendly plain-text summary of every run's executive
profile — the same data that appears in the HTML exec-card, but with no markup,
no images, and a compact structure that loads cheaply into any AI chat context.

Usage (internal):
    from ta_foundation.reports.text.export_summary_text import export_summary_text
    export_summary_text(packages, output_path, title="Strategy Comparison Report")
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.utils.kpi import normalize_kpi_key


# ─────────────────────────────────────────────────────────────
# Small, self-contained helpers (no HTML, no side-effects)
# ─────────────────────────────────────────────────────────────

def _settings_map(pkg: AnalysisPackage) -> dict[str, Any]:
    df = getattr(pkg, "settings", None)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict[str, Any] = {}
    for _, r in df.iterrows():
        item = str(r.get("item", "")).strip()
        if item:
            out[item.strip().lower()] = r.get("value", "")
    return out


def _kpi(pkg: AnalysisPackage, *keys: str) -> Any:
    s = getattr(pkg, "summary", None)
    kpis = getattr(s, "kpis_all", None) if s else None
    if not isinstance(kpis, dict):
        return None
    for k in keys:
        nk = normalize_kpi_key(k)
        if nk in kpis:
            return kpis[nk]
    return None


def _summary_cell(pkg: AnalysisPackage, row_label: str, col_label: str) -> Any:
    s = getattr(pkg, "summary", None)
    if s is None:
        return None
    r = normalize_kpi_key(row_label)
    c = normalize_kpi_key(col_label)
    for t in [
        getattr(s, "performance_table", None),
        getattr(s, "performance", None),
        (getattr(s, "tables", None) or {}).get("performance") if hasattr(s, "tables") else None,
    ]:
        if isinstance(t, dict):
            row = t.get(r)
            if isinstance(row, dict) and c in row:
                return row[c]
    if c == normalize_kpi_key("all trades"):
        k_all = getattr(s, "kpis_all", None)
        if isinstance(k_all, dict):
            return k_all.get(r)
    return None


def _to_float(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        if isinstance(x, str):
            x = x.replace("$", "").replace(",", "").strip().rstrip("%")
        return float(x)
    except Exception:
        return None


def _win_rate_pct(x: Any) -> Optional[float]:
    v = _to_float(x)
    if v is None:
        return None
    if isinstance(x, str) and "%" in str(x):
        return v
    if v >= 1.0:
        return v
    if 0.05 <= v < 1.0:
        return v * 100.0
    if 0.0 < v < 0.05:
        return v * 10000.0 if (v * 10000.0) <= 100.0 else v * 100.0
    return None


def _fmt_money(x: Any) -> str:
    v = _to_float(x)
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _fmt_num(x: Any, dec: int = 2) -> str:
    v = _to_float(x)
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{dec}f}"


def _fmt_pct(x: Any, dec: int = 1) -> str:
    v = _win_rate_pct(x)
    return f"{v:.{dec}f}%" if v is not None else "—"


def _date_range(pkg: AnalysisPackage) -> str:
    s = getattr(pkg, "summary", None)
    start = getattr(s, "start_dt", None) if s else None
    end = getattr(s, "end_dt", None) if s else None

    def _d(dt: Any) -> Optional[str]:
        try:
            return dt.strftime("%m/%d/%Y")
        except Exception:
            return None

    a, b = _d(start), _d(end)
    if a and b:
        return f"{a} – {b}"

    df = getattr(pkg, "daily", None)
    if isinstance(df, pd.DataFrame) and not df.empty:
        for col in ["date", "period"]:
            if col in df.columns:
                try:
                    dmin = pd.to_datetime(df[col]).min()
                    dmax = pd.to_datetime(df[col]).max()
                    return f"{dmin:%m/%d/%Y} – {dmax:%m/%d/%Y}"
                except Exception:
                    pass
    return "—"


def _active_window(sm: dict[str, Any]) -> str:
    def _int(k: str) -> Optional[int]:
        v = sm.get(k)
        try:
            return int(float(str(v).strip())) if v not in (None, "") else None
        except Exception:
            return None

    sh, smm = _int("start_time_(hh)"), _int("start_time_(mm)")
    dh, dmm = _int("duration_time_(hh)"), _int("duration_time_(mm)")
    if None in (sh, smm, dh, dmm):
        return "—"

    def _fmt(td: timedelta) -> str:
        total = int(td.total_seconds() // 60)
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    start = timedelta(hours=sh, minutes=smm)
    return f"{_fmt(start)} – {_fmt(start + timedelta(hours=dh, minutes=dmm))} Colorado"


def _daily_extremes(pkg: AnalysisPackage) -> tuple[Optional[float], Optional[float]]:
    df = getattr(pkg, "daily", None)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None, None
    col = next(
        (c for c in df.columns if str(c).strip().lower() in {"net_profit", "net profit", "pnl", "profit"}),
        None,
    )
    if col is None:
        return None, None
    try:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            return None, None
        return float(s.max()), float(s.min())
    except Exception:
        return None, None


def _direction(sm: dict[str, Any]) -> str:
    def _bool(k: str) -> Optional[bool]:
        v = str(sm.get(k, "")).strip().lower()
        return True if v in {"true", "1", "yes"} else (False if v in {"false", "0", "no"} else None)

    long_, short_ = _bool("long"), _bool("short")
    if long_ and short_:
        return "Long and Short"
    if long_:
        return "Long Only"
    if short_:
        return "Short Only"
    return "—"


def _rating_mae_mfe(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    if v < 0.4:
        return "Excellent"
    if v < 0.7:
        return "Good"
    if v <= 1.2:
        return "Fair"
    return "Poor"


def _rating_mfe_etd(v: Optional[float], etd: Optional[float]) -> str:
    if etd == 0.0:
        return "Excellent"
    if v is None:
        return "N/A"
    if v > 3.0:
        return "Excellent"
    if v >= 1.5:
        return "Good"
    if v >= 1.0:
        return "Fair"
    return "Poor"


# ─────────────────────────────────────────────────────────────
# Per-run text block
# ─────────────────────────────────────────────────────────────

def _run_block(run_id: str, pkg: AnalysisPackage) -> str:
    sm = _settings_map(pkg)
    derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}

    lines: list[str] = []

    def ln(text: str = "") -> None:
        lines.append(text)

    def kv(label: str, value: str, width: int = 22) -> None:
        lines.append(f"  {label:<{width}}{value}")

    SEP = "-" * 72

    ln(SEP)
    ln(f"RUN: {run_id}")
    ln(SEP)

    # ── Identity ──────────────────────────────────────────────
    kv("Period:", _date_range(pkg))

    instrument = derived.get("instrument") or sm.get("instrument") or "—"
    tick_value = derived.get("tick_value_usd")
    tick_str = f"  |  Tick Value: ${tick_value:,.2f}" if tick_value else ""
    kv("Instrument:", f"{instrument}{tick_str}")

    kv("Direction:", _direction(sm))
    kv("Contracts:", str(sm.get("contracts", "—")))
    kv("Active Window:", _active_window(sm))

    # ── Settings ──────────────────────────────────────────────
    ln()
    ln("SETTINGS")
    kv("Fast MA:", str(sm.get("averagefast", "—")))
    kv("Slow MA:", str(sm.get("averageslow", "—")))
    kv("Trend MA:", str(sm.get("averagetrend", "—")))

    chart_type = sm.get("type", "—")
    chart_val = sm.get("value", "—")
    label = sm.get("label", "—")
    kv("Chart:", f"{chart_val}  {chart_type}" if chart_val != "—" else "—")
    kv("Label:", str(label))
    kv("Max Trades/Day:", str(sm.get("maxtrades", "—")))

    max_stop = _to_float(sm.get("maxstop"))
    max_tp_ratio = _to_float(sm.get("maxtpratio"))
    kv("Max Stop (pts):", _fmt_num(max_stop, 0) if max_stop is not None else "—")
    kv("TP Ratio:", _fmt_num(max_tp_ratio, 2) if max_tp_ratio is not None else "—")
    if max_stop is not None and max_tp_ratio is not None:
        kv("Max TP (pts):", _fmt_num(max_stop * max_tp_ratio, 0))

    # ── Performance KPIs ──────────────────────────────────────
    ln()
    ln("PERFORMANCE")
    kv("Total Net Profit:", _fmt_money(_kpi(pkg, "total net profit", "net profit")))
    kv("Max Drawdown:", _fmt_money(_kpi(pkg, "max drawdown", "maximum drawdown")))

    raw_wr = _summary_cell(pkg, "Percent profitable", "All trades") or _kpi(pkg, "percent profitable")
    kv("Win Rate:", _fmt_pct(raw_wr))
    kv("Profit Factor:", _fmt_num(_kpi(pkg, "profit factor"), 2))

    total_trades = _kpi(pkg, "total number of trades", "total trades")
    kv("Total Trades:", _fmt_num(total_trades, 0))

    avg_win = _kpi(pkg, "avg. winning trade", "average winning trade")
    avg_loss = _kpi(pkg, "avg. losing trade", "average losing trade")
    kv("Avg Win:", _fmt_money(avg_win))
    kv("Avg Loss:", _fmt_money(avg_loss))

    # ── Trade Quality ─────────────────────────────────────────
    avg_mae = _kpi(pkg, "avg. mae", "average mae")
    avg_mfe = _kpi(pkg, "avg. mfe", "average mfe")
    avg_etd = _kpi(pkg, "avg. etd", "average etd")

    f_mae = _to_float(avg_mae)
    f_mfe = _to_float(avg_mfe)
    f_etd = _to_float(avg_etd)

    mae_mfe: Optional[float] = None
    mfe_etd: Optional[float] = None
    if f_mae is not None and f_mfe and f_mfe != 0:
        mae_mfe = f_mae / f_mfe
    if f_mfe is not None and f_etd is not None and f_etd != 0:
        mfe_etd = f_mfe / f_etd

    if any(v is not None for v in (avg_mae, avg_mfe, avg_etd)):
        ln()
        ln("TRADE QUALITY (avg per trade)")
        kv("MAE:", _fmt_money(avg_mae))
        kv("MFE:", _fmt_money(avg_mfe))
        kv("ETD:", _fmt_money(avg_etd))
        if mae_mfe is not None:
            kv("MAE/MFE:", f"{mae_mfe:.2f}  →  {_rating_mae_mfe(mae_mfe)}")
        if mfe_etd is not None:
            kv("MFE/ETD:", f"{mfe_etd:.2f}  →  {_rating_mfe_etd(mfe_etd, f_etd)}")

    # ── Daily Extremes ────────────────────────────────────────
    best_day, worst_day = _daily_extremes(pkg)
    if best_day is not None or worst_day is not None:
        ln()
        ln("DAILY EXTREMES")
        kv("Best Day:", _fmt_money(best_day) if best_day is not None else "—")
        kv("Worst Day:", _fmt_money(worst_day) if worst_day is not None else "—")

    # ── Risk / Potential ──────────────────────────────────────
    max_pot_profit = derived.get("max_potential_profit_usd")
    max_pot_loss = derived.get("max_potential_loss_usd")
    if max_pot_profit is not None or max_pot_loss is not None:
        ln()
        ln("RISK PARAMETERS")
        if max_pot_profit is not None:
            kv("Max Potential Profit:", _fmt_money(max_pot_profit))
        if max_pot_loss is not None:
            kv("Max Potential Loss:", _fmt_money(max_pot_loss))

    # ── Long / Short breakdown (if available) ─────────────────
    kpis_long = getattr(getattr(pkg, "summary", None), "kpis_long", None) or {}
    kpis_short = getattr(getattr(pkg, "summary", None), "kpis_short", None) or {}

    def _side_kpi(d: dict, *keys: str) -> Any:
        for k in keys:
            v = d.get(normalize_kpi_key(k))
            if v is not None:
                return v
        return None

    long_profit = _side_kpi(kpis_long, "total net profit", "net profit")
    short_profit = _side_kpi(kpis_short, "total net profit", "net profit")
    long_wr = _side_kpi(kpis_long, "percent profitable")
    short_wr = _side_kpi(kpis_short, "percent profitable")

    if any(v is not None for v in (long_profit, short_profit, long_wr, short_wr)):
        ln()
        ln("LONG / SHORT SPLIT")
        if long_profit is not None or long_wr is not None:
            lp = _fmt_money(long_profit) if long_profit is not None else "—"
            lw = _fmt_pct(long_wr) if long_wr is not None else "—"
            kv("Long:", f"Profit {lp}  |  Win Rate {lw}")
        if short_profit is not None or short_wr is not None:
            sp = _fmt_money(short_profit) if short_profit is not None else "—"
            sw = _fmt_pct(short_wr) if short_wr is not None else "—"
            kv("Short:", f"Profit {sp}  |  Win Rate {sw}")

    ln()
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────

def export_summary_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    title: str = "Strategy Summary",
) -> Path:
    """
    Write a condensed plain-text summary of all runs to *output_path*.

    Returns the resolved output path.

    This file is designed to be pasted directly into an LLM chat window.
    It contains all key metrics from the executive profile cards with no HTML,
    no images, and no redundant whitespace.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_ids = sorted(packages.keys())

    header = "\n".join([
        "=" * 72,
        f"STRATEGY EXECUTIVE SUMMARY",
        f"Report:    {title}",
        f"Generated: {now}",
        f"Runs:      {len(run_ids)}",
        "=" * 72,
        "",
        "This file is a condensed, LLM-friendly summary of every strategy run.",
        "All monetary values are USD.  Ratios are unitless.",
        "MAE/MFE rating: <0.4 Excellent | 0.4-0.7 Good | 0.7-1.2 Fair | >1.2 Poor",
        "MFE/ETD rating: >3.0 Excellent | 1.5-3.0 Good | 1.0-1.5 Fair | <1.0 Poor",
        "",
    ])

    blocks = [_run_block(rid, packages[rid]) for rid in run_ids]

    footer = "\n".join([
        "=" * 72,
        f"END OF SUMMARY  ({len(run_ids)} runs)",
        "=" * 72,
    ])

    output_path.write_text(
        header + "\n".join(blocks) + "\n" + footer,
        encoding="utf-8",
    )
    return output_path
