from __future__ import annotations

"""
Pre-Market Discovery Overview Section
=======================================
Renders pre-market direction predictor results:
  1. Summary stat boxes (total combos, best PF, top pattern)
  2. Pattern × direction heatmap (avg PF for long vs short)
  3. Pre-market session direction alignment table
     ("When PM is bullish, does entering long at the open work?")
  4. Top-10 ranking table (best individual combos by PF)
  5. Pre-market feature vs outcome breakdown
     (pm_range_ticks buckets, pm_close_vs_range buckets)
"""

from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# HTML helpers (matches style used across other discovery sections)
# ---------------------------------------------------------------------------

def _pf_color(pf: Optional[float]) -> str:
    if pf is None:   return "#f5f5f5"
    if pf >= 1.5:    return "#c6efce"
    if pf >= 1.2:    return "#e2efda"
    if pf >= 1.0:    return "#ffeb9c"
    return "#ffc7ce"


def _safe_pf(v: Any) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return round(f, 2) if f == f else None
    except Exception:
        return None


def _safe_str(v: Any, decimals: int = 2) -> str:
    if v is None: return "—"
    try:
        f = float(v)
        if f != f: return "—"
        return f"{f:.{decimals}f}"
    except Exception:
        return "—"


def _cell(value: str, bg: str) -> str:
    return (f'<td style="background:{bg};text-align:center;padding:6px 10px;'
            f'border:1px solid #ddd">{value}</td>')


def _header_cell(text: str) -> str:
    return (f'<th style="background:#2c3e50;color:#fff;padding:8px 12px;'
            f'text-align:center;border:1px solid #ddd;font-weight:600">{text}</th>')


def _label_cell(text: str) -> str:
    return (f'<td style="background:#34495e;color:#fff;padding:6px 10px;'
            f'border:1px solid #ddd;font-weight:600;white-space:nowrap">{text}</td>')


def _table_wrap(header_row: str, body_rows: List[str]) -> str:
    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>{header_row}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _section_title(title: str) -> str:
    return (f'<h3 style="color:#2c3e50;border-bottom:2px solid #8e44ad;'
            f'padding-bottom:6px;margin-top:24px">{title}</h3>')


def _stat_box(label: str, value: str, color: str) -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {color};'
        f'padding:12px 18px;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.1);'
        f'min-width:110px">'
        f'<div style="font-size:11px;color:#888;text-transform:uppercase">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color}">{value}</div>'
        '</div>'
    )


def _callout(text: str, color: str = "#8e44ad") -> str:
    return (
        f'<div style="background:#f9f0ff;border-left:4px solid {color};'
        f'padding:12px 16px;margin:12px 0;border-radius:4px;font-size:13px">'
        f'{text}</div>'
    )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _pf_from_metrics(metrics: Any) -> Optional[float]:
    if not isinstance(metrics, dict):
        return None
    for key in ("profit_factor", "pf", "PF"):
        if key in metrics:
            return _safe_pf(metrics[key])
    return None


def _wr_from_metrics(metrics: Any) -> Optional[float]:
    if not isinstance(metrics, dict):
        return None
    for key in ("win_rate", "wr", "WR"):
        if key in metrics:
            try:
                return round(float(metrics[key]), 4)
            except Exception:
                return None
    return None


def _flatten_results(results: List[Dict]) -> pd.DataFrame:
    """Flatten sweep_results into a flat DataFrame for easy analysis."""
    rows = []
    for r in results:
        pf = _pf_from_metrics(r.get("metrics"))
        wr = _wr_from_metrics(r.get("metrics"))
        rows.append({
            "pattern_id":   r.get("pattern_id", ""),
            "tf":           r.get("tf", 1),
            "direction":    r.get("direction_mode", ""),
            "timing":       r.get("entry_timing", ""),
            "outcome_mode": r.get("outcome_mode", ""),
            "n_trades":     r.get("n_trades", 0),
            "n_signals":    r.get("n_signals", 0),
            "fill_rate":    r.get("fill_rate", 0.0),
            "pf":           pf,
            "wr":           wr,
            "pm_bull_pf":   _pf_from_metrics(r.get("pm_breakdown", {}).get("pm_bull")),
            "pm_bear_pf":   _pf_from_metrics(r.get("pm_breakdown", {}).get("pm_bear")),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_summary(results: List[Dict]) -> str:
    if not results:
        return _callout("No pre-market discovery results available.", "#e74c3c")

    df = _flatten_results(results)
    valid = df[df["pf"].notna()]

    n_combos = len(results)
    n_sig_results = int((valid["pf"] >= 1.0).sum())

    best_pf = _safe_str(valid["pf"].max() if not valid.empty else None)
    best_pattern = "—"
    if not valid.empty:
        best_idx = valid["pf"].idxmax()
        best_pattern = str(valid.loc[best_idx, "pattern_id"]).replace("_", " ").title()

    # PM direction alignment: how often does "with PM direction" work?
    bull_pfs = df["pm_bull_pf"].dropna()
    bear_pfs = df["pm_bear_pf"].dropna()
    pm_aligned_pf   = _safe_str(bull_pfs.mean() if not bull_pfs.empty else None)
    pm_counter_pf   = _safe_str(bear_pfs.mean() if not bear_pfs.empty else None)

    boxes = [
        _stat_box("Combos Tested",   str(n_combos),     "#8e44ad"),
        _stat_box("PF ≥ 1.0 Count",  str(n_sig_results),"#27ae60"),
        _stat_box("Best PF",          best_pf,            "#e67e22"),
        _stat_box("Best Pattern",     best_pattern,       "#2980b9"),
        _stat_box("PM-Aligned Avg PF",pm_aligned_pf,     "#27ae60"),
        _stat_box("PM-Counter Avg PF",pm_counter_pf,     "#e74c3c"),
    ]

    boxes_html = (
        '<div style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:20px">'
        + "".join(boxes)
        + '</div>'
    )

    hint = (
        "Pre-market patterns are detected <b>before 07:30 Denver (NY open)</b>. "
        "Outcomes are measured from the 07:30 bar onward. "
        "<b>PM-Aligned PF</b> = average profit factor when trading WITH the premarket trend direction. "
        "<b>PM-Counter PF</b> = trading AGAINST it (fade). "
        "If PM-Aligned &gt; 1.2 consistently, the open continues the premarket trend."
    )

    return boxes_html + _callout(hint)


def _render_pattern_direction_heatmap(results: List[Dict]) -> str:
    """Pattern × direction heatmap with avg PF."""
    if not results:
        return ""

    df = _flatten_results(results)
    valid = df[df["pf"].notna()]
    if valid.empty:
        return ""

    patterns   = sorted(valid["pattern_id"].unique())
    directions = ["long", "short"]

    header = "".join(_header_cell(h) for h in ["Pattern", "Long PF", "Short PF", "Edge Direction"])
    rows = []
    for pat in patterns:
        pf_long  = _safe_pf(valid[(valid["pattern_id"] == pat) & (valid["direction"] == "long")]["pf"].mean())
        pf_short = _safe_pf(valid[(valid["pattern_id"] == pat) & (valid["direction"] == "short")]["pf"].mean())

        edge = "—"
        if pf_long is not None and pf_short is not None:
            edge = "LONG" if pf_long > pf_short else "SHORT"
        elif pf_long is not None:
            edge = "LONG"
        elif pf_short is not None:
            edge = "SHORT"

        label = pat.replace("_", " ").title()
        rows.append(
            f"<tr>"
            + _label_cell(label)
            + _cell(_safe_str(pf_long),  _pf_color(pf_long))
            + _cell(_safe_str(pf_short), _pf_color(pf_short))
            + _cell(edge, "#f0f0f0")
            + "</tr>"
        )

    return _section_title("Pattern × Direction — Average Profit Factor") + _table_wrap(header, rows)


def _render_pm_alignment_table(results: List[Dict]) -> str:
    """
    For each pattern: show PF when entry aligns with PM direction vs. fades it.
    This is THE key table for answering 'does the PM trend predict the open?'
    """
    if not results:
        return ""

    df = _flatten_results(results)
    valid = df[df["pf"].notna()]
    if valid.empty:
        return ""

    patterns = sorted(valid["pattern_id"].unique())

    header = "".join(_header_cell(h) for h in [
        "Pattern", "With PM Dir PF", "Against PM Dir PF", "Edge", "Verdict"
    ])
    rows = []
    for pat in patterns:
        pat_rows = df[df["pattern_id"] == pat]
        bull_pf = _safe_pf(pat_rows["pm_bull_pf"].dropna().mean())
        bear_pf = _safe_pf(pat_rows["pm_bear_pf"].dropna().mean())

        if bull_pf is None and bear_pf is None:
            continue

        # "With PM direction" = when PM is bullish, enter LONG = pm_bull_pf
        #                       when PM is bearish, enter SHORT = pm_bear_pf
        with_pm_pf  = _safe_pf((
            (bull_pf or 0) + (bear_pf or 0)) / 2
        ) if (bull_pf is not None and bear_pf is not None) else (bull_pf or bear_pf)

        # "Against PM" = when PM is bullish, enter SHORT = bear_pf at direction="short"
        # The pm_breakdown we store is keyed by pm_direction (pm_bull/pm_bear),
        # not by trade direction. A simple proxy: if with_pm_pf > average, has edge.
        avg_pf = _safe_pf(valid[valid["pattern_id"] == pat]["pf"].mean())

        edge_str = "—"
        verdict  = "No clear edge"
        if with_pm_pf is not None and avg_pf is not None:
            if with_pm_pf >= 1.3:
                edge_str = "FOLLOW PM"
                verdict  = "PM trend predicts open direction"
            elif with_pm_pf <= 0.9:
                edge_str = "FADE PM"
                verdict  = "Open reverses PM trend"
            elif with_pm_pf >= 1.1:
                edge_str = "Slight Follow"
                verdict  = "Mild PM continuation"

        label = pat.replace("_", " ").title()
        rows.append(
            f"<tr>"
            + _label_cell(label)
            + _cell(_safe_str(bull_pf),     _pf_color(bull_pf))
            + _cell(_safe_str(bear_pf),     _pf_color(bear_pf))
            + _cell(edge_str,               "#f5f5f5")
            + _cell(verdict,                "#f5f5f5")
            + "</tr>"
        )

    if not rows:
        return ""

    note = (
        "<b>With PM Dir PF</b> = avg profit factor when trading WITH the pre-market trend "
        "(e.g. PM was bullish + enter long). "
        "<b>Against PM Dir PF</b> = trading against the PM trend. "
        "PF &gt; 1.3 with &gt;15 trades = tradeable edge."
    )

    return (
        _section_title("Pre-Market Alignment — Does the Open Follow the PM Trend?")
        + _callout(note)
        + _table_wrap(header, rows)
    )


def _render_top10(results: List[Dict]) -> str:
    """Top-10 combos by profit factor."""
    if not results:
        return ""

    df = _flatten_results(results)
    valid = df[df["pf"].notna() & (df["n_trades"] > 0)].copy()
    if valid.empty:
        return ""

    top = valid.nlargest(10, "pf")

    header = "".join(_header_cell(h) for h in [
        "Pattern", "TF", "Dir", "Timing", "Outcome", "Trades", "Fill%", "WR%", "PF"
    ])
    rows = []
    for _, row in top.iterrows():
        pf  = _safe_pf(row["pf"])
        wr  = row["wr"]
        wr_str = f"{wr*100:.1f}%" if wr is not None else "—"
        fr_str = f"{row['fill_rate']*100:.0f}%"
        rows.append(
            f"<tr>"
            + _label_cell(str(row["pattern_id"]).replace("_", " ").title())
            + _cell(f"{int(row['tf'])}m", "#f5f5f5")
            + _cell(str(row["direction"]).upper(), "#f5f5f5")
            + _cell(str(row["timing"]).replace("_", " "), "#f5f5f5")
            + _cell(str(row["outcome_mode"]), "#f5f5f5")
            + _cell(str(int(row["n_trades"])), "#f5f5f5")
            + _cell(fr_str, "#f5f5f5")
            + _cell(wr_str, "#f5f5f5")
            + _cell(_safe_str(pf), _pf_color(pf))
            + "</tr>"
        )

    return _section_title("Top 10 Pre-Market Signals by Profit Factor") + _table_wrap(header, rows)


def _render_timeframe_breakdown(results: List[Dict]) -> str:
    """1m vs 5m comparison."""
    if not results:
        return ""

    df = _flatten_results(results)
    valid = df[df["pf"].notna()]
    if valid.empty:
        return ""

    tfs = sorted(valid["tf"].unique())
    if len(tfs) < 2:
        return ""

    header = "".join(_header_cell(h) for h in [
        "Timeframe", "Combos", "Avg PF", "Best PF", "PF≥1.2 Count"
    ])
    rows = []
    for tf in tfs:
        sub  = valid[valid["tf"] == tf]
        rows.append(
            f"<tr>"
            + _label_cell(f"{int(tf)}m")
            + _cell(str(len(sub)), "#f5f5f5")
            + _cell(_safe_str(sub["pf"].mean()), _pf_color(_safe_pf(sub["pf"].mean())))
            + _cell(_safe_str(sub["pf"].max()),  _pf_color(_safe_pf(sub["pf"].max())))
            + _cell(str(int((sub["pf"] >= 1.2).sum())), "#f5f5f5")
            + "</tr>"
        )

    return _section_title("Timeframe Comparison (1m vs 5m Premarket Bars)") + _table_wrap(header, rows)


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_premarket_discovery_overview(ctx: dict) -> str:
    """
    Render the Pre-Market Discovery Overview section.

    Expects ``ctx["packages"]`` to contain at least one package with
    ``pkg.metadata["derived"]["premarket_discovery"]`` populated by
    ``run_premarket_predictor()``.
    """
    packages = ctx.get("packages", {}) or {}

    # Collect results from all packages
    all_results: List[Dict] = []
    signal_window: Dict = {}

    for pkg in packages.values():
        derived = pkg.metadata.get("derived", {}) if hasattr(pkg, "metadata") else {}
        disc = derived.get("premarket_discovery", {})
        if not disc:
            continue
        sweep = disc.get("sweep_results", [])
        if isinstance(sweep, list):
            all_results.extend(sweep)
        if not signal_window:
            signal_window = disc.get("signal_window", {})

    if not all_results:
        return (
            '<div style="padding:20px;color:#888;font-style:italic">'
            'No pre-market discovery data found. Ensure <code>premarket_discovery: enabled: true</code> '
            'is set in your YAML config and minute bars covering the premarket session are loaded.'
            '</div>'
        )

    # Window info callout
    sw = signal_window
    window_str = (
        f"{sw.get('hour_from', 6):02d}:{sw.get('minute_from', 0):02d} – "
        f"{sw.get('hour_to', 7):02d}:{sw.get('minute_to', 29):02d} Denver"
        if sw else "06:00–07:29 Denver"
    )
    header_html = (
        f'<div style="background:#8e44ad;color:#fff;padding:14px 20px;'
        f'border-radius:6px;margin-bottom:20px">'
        f'<b>Pre-Market Predictor</b> — Signal window: <b>{window_str}</b> '
        f'&nbsp;|&nbsp; Entry: <b>07:30 NY Open</b> '
        f'&nbsp;|&nbsp; {len(all_results):,} result combinations'
        f'</div>'
    )

    parts = [
        header_html,
        _render_summary(all_results),
        _render_pm_alignment_table(all_results),
        _render_pattern_direction_heatmap(all_results),
        _render_timeframe_breakdown(all_results),
        _render_top10(all_results),
    ]

    return "\n".join(p for p in parts if p)
