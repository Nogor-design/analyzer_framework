from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from ta_foundation.core.model import AnalysisPackage

@dataclass(frozen=True)
class SessionWindow:
    label: str
    start: time  # inclusive
    end: time    # exclusive


DEFAULT_SESSION_WINDOWS: Tuple[SessionWindow, ...] = (
    SessionWindow("London", time(0, 0), time(4, 0)),
    SessionWindow("NY Pre→Early", time(4, 0), time(10, 30)),
    SessionWindow("NY Late", time(10, 30), time(16, 0)),
    SessionWindow("Asia", time(16, 0), time(23, 59, 59)),
)


def _safe_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    cols = list(df.columns)
    low = {c.lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    return None


def _parse_time_hhmm(s: str) -> time:
    hh, mm = (s or "").strip().split(":")
    return time(int(hh), int(mm))


def _parse_session_windows(options: Dict[str, Any]) -> Tuple[SessionWindow, ...]:
    wins = options.get("session_windows")
    if not wins:
        return DEFAULT_SESSION_WINDOWS

    out: List[SessionWindow] = []
    for w in wins:
        try:
            out.append(
                SessionWindow(
                    label=str(w["label"]),
                    start=_parse_time_hhmm(str(w["start"])),
                    end=_parse_time_hhmm(str(w["end"])),
                )
            )
        except Exception:
            continue
    return tuple(out) if out else DEFAULT_SESSION_WINDOWS


def _kpi_value(pkg, key: str) -> Optional[float]:
    try:
        sb = getattr(pkg, "summary", None)
        if not sb:
            return None
        k = getattr(sb, "kpis_all", None)
        if not k:
            return None
        v = k.get(key)
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def _ranking_value(pkg, metric: str) -> Optional[float]:
    m = (metric or "").strip().lower()
    if m in ("profit_factor", "profit factor", "pf"):
        return _kpi_value(pkg, "profit factor")
    if m in ("total_net_profit", "total net profit", "net profit", "total profit"):
        return _kpi_value(pkg, "total net profit")
    if m in ("max_drawdown", "max drawdown", "dd"):
        return _kpi_value(pkg, "max drawdown")
    return _kpi_value(pkg, m)


def _infer_market_root(pkg) -> Optional[str]:
    df = getattr(pkg, "trades", None)
    if df is None or len(df) == 0:
        return None
    col = _safe_col(df, ["Instrument"])
    if not col:
        return None
    try:
        v = df[col].dropna().astype(str).iloc[0]
    except Exception:
        return None
    if not v:
        return None
    return v.strip().split()[0].strip().upper() or None


def _infer_session(pkg, windows: Iterable[SessionWindow]) -> Optional[str]:
    df = getattr(pkg, "trades", None)
    if df is None or len(df) == 0:
        return None
    col = _safe_col(df, ["Entry time", "Entry Time", "entry_time"])
    if not col:
        return None

    s = df[col]
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s, errors="coerce")

    s = s.dropna()
    if len(s) == 0:
        return None

    counts = {w.label: 0 for w in windows}

    def in_window(tt: time, w: SessionWindow) -> bool:
        if w.start <= w.end:
            return (tt >= w.start) and (tt < w.end)
        return (tt >= w.start) or (tt < w.end)

    for ts in s:
        try:
            tt = ts.timetz().replace(tzinfo=None)
        except Exception:
            continue
        for w in windows:
            if in_window(tt, w):
                counts[w.label] += 1
                break

    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else None


def render_run_card_catalog(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    metric = options.get("metric", "profit_factor")
    sort_desc = bool(options.get("sort_desc", True))
    max_per_group = int(options.get("max_per_group", 12))
    hide_missing_cards = bool(options.get("hide_missing_cards", True))
    fallback_session = str(options.get("fallback_session_label", "Unclassified"))
    fallback_market = str(options.get("fallback_market_label", "Unknown"))

    windows = _parse_session_windows(options)


    rows: List[Dict[str, Any]] = []
    # for run_id, pkg in packages.items():
    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg else {}
        # card_uri = derived.get("card_image_uri")# or (getattr(pkg, "assets", {}) or {}).get("card_image_uri")

        # derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        card_uri = derived.get("card_image_uri")
        # print(f"path: {derived.get("card_image_uri")}")
        if hide_missing_cards and not card_uri:
            continue

        session = _infer_session(pkg, windows) or fallback_session
        market = _infer_market_root(pkg) or fallback_market
        val = _ranking_value(pkg, metric)
        # print(f"path: {card_uri}")
        rows.append(
            {
                "run_id": run_id,
                "session": session,
                "market": market,
                "metric_value": val,
                "card_uri": card_uri,
            }
        )

    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for r in rows:
        grouped.setdefault(r["session"], {}).setdefault(r["market"], []).append(r)

    session_order = [w.label for w in windows]
    sessions_sorted = sorted(grouped.keys(), key=lambda s: (session_order.index(s) if s in session_order else 999, s))

    def fmt_metric(v: Optional[float]) -> str:
        if v is None:
            return "—"
        m = (metric or "").lower()
        if "profit" in m and "factor" in m:
            return f"{v:.2f}"
        if "profit" in m:
            return f"{v:,.0f}"
        return f"{v:,.2f}"

    css = """
    <style>
      .tf-cardcat { display: flex; flex-direction: column; gap: 16px; }
      .tf-cardcat-session { padding: 12px; border-radius: 14px; background: rgba(255,255,255,0.03); }
      .tf-cardcat-session h3 { margin: 0 0 10px 0; font-size: 1.05rem; }
      .tf-cardcat-market { margin-top: 10px; }
      .tf-cardcat-market h4 { margin: 0 0 8px 0; font-size: 0.95rem; opacity: 0.95; }
      .tf-cardcat-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 12px;
        align-items: start;
      }
      .tf-cardcat-item {
        border-radius: 14px;
        overflow: hidden;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-cardcat-img { width: 100%; height: auto; display: block; }
      .tf-cardcat-meta { padding: 10px; display: flex; flex-direction: column; gap: 4px; }
      .tf-cardcat-run { font-weight: 650; font-size: 0.92rem; }
      .tf-cardcat-kpi { font-size: 0.85rem; opacity: 0.9; }
      .tf-cardcat-missing { padding: 18px; font-size: 0.9rem; opacity: 0.75; }
    </style>
    """

    html = [css, '<div class="tf-cardcat">']

    for sess in sessions_sorted:
        html.append('<section class="tf-cardcat-session">')
        html.append(f"<h3>{sess}</h3>")

        markets_sorted = sorted(grouped[sess].keys())
        for mk in markets_sorted:
            items = grouped[sess][mk]

            def sort_key(r: Dict[str, Any]):
                v = r.get("metric_value")
                return (v is None, v if v is not None else 0.0)

            items_sorted = sorted(items, key=sort_key, reverse=sort_desc)
            if max_per_group > 0:
                items_sorted = items_sorted[:max_per_group]

            html.append('<div class="tf-cardcat-market">')
            html.append(f"<h4>{mk}</h4>")
            html.append('<div class="tf-cardcat-grid">')

            for r in items_sorted:
                run_id = r["run_id"]
                card_uri = r.get("card_uri")
                mv = r.get("metric_value")

                html.append('<div class="tf-cardcat-item">')
                if card_uri:
                    html.append(f'<img class="tf-cardcat-img" src="{card_uri}" alt="{run_id} Card" />')
                else:
                    html.append('<div class="tf-cardcat-missing">Missing _Card.png</div>')

                html.append('<div class="tf-cardcat-meta">')
                html.append(f'<div class="tf-cardcat-run">{run_id}</div>')
                html.append(f'<div class="tf-cardcat-kpi">{metric}: {fmt_metric(mv)}</div>')
                html.append("</div></div>")

            html.append("</div></div>")  # grid, market

        html.append("</section>")

    html.append("</div>")
    return "\n".join(html)
