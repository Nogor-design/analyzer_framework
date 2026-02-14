from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional, Tuple

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.reports.html.embed import fig_to_base64_png


def _col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """Return the first matching column name (case-insensitive), else None."""
    if df is None or df.empty:
        return None
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _parse_instrument_contract(instrument_value: str) -> Tuple[str, Optional[str]]:
    """
    "NQ 03-26" -> ("NQ", "03-26")
    "NQ" -> ("NQ", None)
    """
    parts = str(instrument_value).strip().split()
    if len(parts) >= 2 and len(parts[1]) == 5 and parts[1][2] == "-":
        return parts[0], parts[1]
    return parts[0], None


def _nearest_bar_index(bar_dt: pd.Series, ts: pd.Timestamp) -> int:
    """
    Find nearest bar index to timestamp `ts`. Assumes bar_dt sorted ascending.
    """
    if len(bar_dt) == 0:
        return 0
    idx = bar_dt.searchsorted(ts)
    if idx <= 0:
        return 0
    if idx >= len(bar_dt):
        return len(bar_dt) - 1
    prev_dt = bar_dt.iloc[idx - 1]
    next_dt = bar_dt.iloc[idx]
    return (idx - 1) if (ts - prev_dt) <= (next_dt - ts) else idx


def _plot_candles(ax, bars: pd.DataFrame) -> None:
    """
    Pure-matplotlib candlesticks:
      - wick via vlines
      - body via rectangle outline
    """
    if bars.empty:
        return

    # Use positional x for speed/simplicity
    x = range(len(bars))
    o = bars["open"].astype(float).values
    h = bars["high"].astype(float).values
    l = bars["low"].astype(float).values
    c = bars["close"].astype(float).values

    ax.vlines(list(x), l, h, linewidth=0.8)

    for i in range(len(bars)):
        y0 = min(o[i], c[i])
        y1 = max(o[i], c[i])
        height = max(1e-9, y1 - y0)
        ax.add_patch(plt.Rectangle((i - 0.3, y0), 0.6, height, fill=False, linewidth=0.8))

    ax.set_xlim(-1, len(bars) + 1)

    # light x ticks (avoid clutter)
    if "dt" in bars.columns and len(bars) > 1:
        step = max(1, len(bars) // 6)
        tick_idx = list(range(0, len(bars), step))
        ax.set_xticks(tick_idx)
        ax.set_xticklabels(
            [bars["dt"].iloc[i].strftime("%m-%d %H:%M") for i in tick_idx],
            rotation=25,
            ha="right",
        )


def render_trade_candle_overlay(ctx: dict[str, Any]) -> str:
    """
    Section: trade_candle_overlay
    Needs:
      ctx["packages"] : dict[str, AnalysisPackage]
      ctx["market"]   : MarketDataStore | None

    YAML options (example):
      options:
        window_minutes: 480
        padding_minutes: 30
        max_trades: 200
        prefer_contract: "03-26"   # optional override
    """
    packages = ctx.get("packages") or {}
    market = ctx.get("market")

    # How section options are passed depends on HtmlReportBuilder.
    # Common pattern in this project: builder injects `section_options`.
    opts = ctx.get("section_options") or {}
    window_minutes = int(opts.get("window_minutes", 8 * 60))
    padding_minutes = int(opts.get("padding_minutes", 30))
    max_trades = int(opts.get("max_trades", 250))
    prefer_contract = opts.get("prefer_contract")

    html = []
    html.append("<div class='section'>")

    if market is None:
        html.append("<div class='muted'>No market data loaded. Provide --market-data folder with *.Last.txt files.</div>")
        html.append("</div>")
        return "\n".join(html)

    for run_id, pkg in packages.items():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>No trades.</div></div>")
            continue

        c_instrument = _col(trades, "Instrument")
        c_pos = _col(trades, "Market pos.", "Market pos", "market_pos")
        c_entry_time = _col(trades, "Entry time", "entry_time")
        c_exit_time = _col(trades, "Exit time", "exit_time")
        c_entry_price = _col(trades, "Entry price", "entry_price")
        c_exit_price = _col(trades, "Exit price", "exit_price")

        missing = [name for name, c in [
            ("Instrument", c_instrument),
            ("Entry time", c_entry_time),
            ("Exit time", c_exit_time),
            ("Entry price", c_entry_price),
            ("Exit price", c_exit_price),
        ] if c is None]

        if missing:
            html.append(
                f"<div class='card'><h3>{run_id}</h3>"
                f"<div class='muted'>Trades missing columns: {', '.join(missing)}</div></div>"
            )
            continue

        instrument_raw = str(trades[c_instrument].dropna().iloc[0]).strip()
        instrument, contract = _parse_instrument_contract(instrument_raw)

        if prefer_contract:
            contract = str(prefer_contract).strip()

        if contract is None:
            html.append(
                f"<div class='card'><h3>{run_id}</h3>"
                f"<div class='muted'>Could not infer contract from Instrument='{instrument_raw}'.</div></div>"
            )
            continue

        bars = market.get(instrument, contract)
        if bars is None or bars.empty:
            html.append(
                f"<div class='card'><h3>{run_id}</h3>"
                f"<div class='muted'>No minute bars loaded for {instrument} {contract}.</div></div>"
            )
            continue

        # Determine time window based on last trade exit (tz-aware America/Denver already)
        last_exit = trades[c_exit_time].dropna().max()
        if pd.isna(last_exit):
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>No valid Exit time.</div></div>")
            continue

        start_dt = last_exit - timedelta(minutes=window_minutes)
        end_dt = last_exit + timedelta(minutes=padding_minutes)

        # Filter bars to window
        b = bars
        if "dt" not in b.columns:
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>Bars missing dt column.</div></div>")
            continue

        bw = b[(b["dt"] >= start_dt) & (b["dt"] <= end_dt)].copy()
        if bw.empty:
            html.append(
                f"<div class='card'><h3>{run_id}</h3>"
                f"<div class='muted'>No bars in window for {instrument} {contract}: {start_dt} → {end_dt}</div></div>"
            )
            continue

        # Filter trades to window and cap
        tw = trades[(trades[c_entry_time] >= start_dt) & (trades[c_entry_time] <= end_dt)].copy()
        tw = tw.sort_values(c_entry_time).head(max_trades)

        # Plot
        fig = plt.figure(figsize=(12, 4.8))
        ax = fig.add_subplot(111)

        bw = bw.sort_values("dt").reset_index(drop=True)
        _plot_candles(ax, bw)

        bar_dt = bw["dt"]
        close_px = bw["close"].astype(float)

        for _, tr in tw.iterrows():
            et = tr[c_entry_time]
            xt = tr[c_exit_time]
            if pd.isna(et) or pd.isna(xt):
                continue

            ei = _nearest_bar_index(bar_dt, et)
            xi = _nearest_bar_index(bar_dt, xt)

            # marker direction by Market pos if available
            pos = str(tr[c_pos]).strip().lower() if c_pos is not None else ""
            entry_marker = "^" if pos == "long" else "v" if pos == "short" else "o"
            exit_marker = "x"

            ax.scatter([ei], [close_px.iloc[ei]], marker=entry_marker, s=24)
            ax.scatter([xi], [close_px.iloc[xi]], marker=exit_marker, s=24)

            # holding line
            ax.plot([ei, xi], [close_px.iloc[ei], close_px.iloc[xi]], linewidth=0.8)

        ax.set_title(f"{run_id} — {instrument} {contract} (America/Denver)")
        ax.set_ylabel("Price")

        img_uri = fig_to_base64_png(fig)
        plt.close(fig)

        html.append(
            f"<div class='card'>"
            f"<h3>{run_id}</h3>"
            f"<div class='muted'>Instrument: {instrument} {contract} • Window: {start_dt} → {end_dt} • Trades shown: {len(tw)}</div>"
            f"<img class='chart' src='{img_uri}'/>"
            f"</div>"
        )

    html.append("</div>")
    return "\n".join(html)
