# src/ta_foundation/reports/html/sections/apex_drawdown_survival_profile.py
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ta_foundation.analysis.apex_trailing_model import (
    InstrumentSpec,
    ApexParams,
    simulate_apex_trailing_for_run,
)


def _col(df: pd.DataFrame, *names: str) -> Optional[str]:
    if df is None or df.empty:
        return None
    m = {c.lower(): c for c in df.columns}
    for n in names:
        k = n.lower()
        if k in m:
            return m[k]
    return None


def _parse_instrument_root(instr: str) -> str:
    # "NQ 03-26" -> "NQ"
    parts = str(instr).strip().split()
    return parts[0] if parts else ""


def _money(v: float) -> str:
    # simple formatting; keep consistent with your existing style if you have one
    s = f"{abs(v):,.0f}"
    return f"(${s})" if v < 0 else f"${s}"


def render_apex_drawdown_survival_profile(ctx: dict[str, Any]) -> str:
    """
    Section id: apex_drawdown_survival_profile

    Requires:
      ctx["packages"]
      ctx["market"] : MarketDataStore (minute bars)
      ctx["section_options"] injected by HtmlReportBuilder (options from report.yaml)

    YAML options example:
      options:
        apex:
          starting_balance: 50000
          trailing_drawdown: 2500
          lock_profit: 2600
        instruments:
          NQ: { tick_size: 0.25, tick_value: 5.0 }
          ES: { tick_size: 0.25, tick_value: 12.5 }
    """
    packages = ctx.get("packages") or {}
    market = ctx.get("market")
    opts = ctx.get("section_options") or {}

    html: list[str] = []
    html.append("<div class='section'>")

    if market is None:
        html.append("<div class='muted'>No market data loaded. Provide --market-data with *.Last.txt files.</div>")
        html.append("</div>")
        return "\n".join(html)

    apex_cfg = (opts.get("apex") or {}) if isinstance(opts.get("apex"), dict) else {}
    apex = ApexParams(
        starting_balance=float(apex_cfg.get("starting_balance", 50_000.0)),
        trailing_drawdown=float(apex_cfg.get("trailing_drawdown", 2_500.0)),
        lock_profit=float(apex_cfg.get("lock_profit", 2_600.0)),
    )

    # default instrument specs (safe)
    inst_cfg = (opts.get("instruments") or {}) if isinstance(opts.get("instruments"), dict) else {}
    default_specs = {
        "NQ": InstrumentSpec(0.25, 5.0),
        "MNQ": InstrumentSpec(0.25, 0.5),
        "ES": InstrumentSpec(0.25, 12.5),
        "MES": InstrumentSpec(0.25, 1.25),
        "GC": InstrumentSpec(0.10, 10.0),
        "MGC": InstrumentSpec(0.10, 1.0),
    }

    def spec_for(root: str) -> InstrumentSpec:
        if root in inst_cfg and isinstance(inst_cfg[root], dict):
            d = inst_cfg[root]
            return InstrumentSpec(
                tick_size=float(d.get("tick_size")),
                tick_value=float(d.get("tick_value")),
            )
        return default_specs.get(root, InstrumentSpec(1.0, 1.0))

    for run_id, pkg in packages.items():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>No trades.</div></div>")
            continue

        c_instr = _col(trades, "Instrument")
        if c_instr is None or trades[c_instr].dropna().empty:
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>Trades missing Instrument.</div></div>")
            continue

        instr_raw = str(trades[c_instr].dropna().iloc[0])
        root = _parse_instrument_root(instr_raw)

        # Contract is the second token: "NQ 03-26"
        parts = str(instr_raw).strip().split()
        contract = parts[1] if len(parts) >= 2 else None
        if contract is None:
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>Instrument has no contract: {instr_raw}</div></div>")
            continue

        bars = market.get(root, contract)
        if bars is None or bars.empty:
            html.append(
                f"<div class='card'><h3>{run_id}</h3>"
                f"<div class='muted'>No minute bars for {root} {contract} (need {root} {contract}.Last.txt)</div></div>"
            )
            continue

        spec = spec_for(root)

        ledger, summary = simulate_apex_trailing_for_run(
            trades_df=trades,
            minute_bars_df=bars,
            spec=spec,
            apex=apex,
        )

        if ledger.empty:
            msg = summary.get("message", "No simulation output.")
            html.append(f"<div class='card'><h3>{run_id}</h3><div class='muted'>{msg}</div></div>")
            continue


        # Persist for later sections if desired
        derived = pkg.metadata.setdefault("derived", {})
        derived["apex_survival_summary"] = summary
        # Keeping full ledger in metadata can get heavy; store as a lightweight list if you want.
        # For now, store the top risk rows only.
        worst_rows = ledger.nsmallest(5, "min_buffer_intratrade")[
            ["trade_number", "pos", "realized_pnl", "min_buffer_intratrade", "buffer_close", "trail_locked"]
        ].copy()
        derived["apex_survival_worst_trades"] = worst_rows.to_dict(orient="records")

        # Render
        min_buf = float(summary["min_buffer_intratrade"])
        danger = min_buf < 0

        html.append("<div class='card'>")
        html.append(f"<h3>{run_id}</h3>")
        html.append(
            f"<div class='muted'>Apex 50K model • Trail ${apex.trailing_drawdown:,.0f} • Lock at +${apex.lock_profit:,.0f} "
            f"(trail fixed at ${summary['locked_trail_level']:,.0f}) • Instrument {root} {contract}</div>"
        )

        html.append("<div style='display:flex; gap:16px; flex-wrap:wrap; margin-top:8px;'>")
        html.append(f"<div><b>Final PnL</b><br/>{_money(summary['final_realized_pnl'])}</div>")
        html.append(f"<div><b>Min Buffer (intratrade)</b><br/>{_money(min_buf)}</div>")
        if summary.get("worst_60m_start") is not None:
            html.append(
                f"<div class='muted' style='margin-top:6px;'>Worst 60m window: "
                f"{summary.get('worst_60m_start')} → trough {summary.get('worst_60m_trough_time')}</div>"
            )
        # ✅ clarified streaks
        html.append(f"<div><b>Max Losing Trade Streak</b><br/>{int(summary.get('max_losing_trade_streak', 0))}</div>")
        html.append(f"<div><b>Max Losing Day Streak</b><br/>{int(summary.get('max_losing_day_streak', 0))}</div>")
        html.append(f"<div><b>Max Winning Day Streak</b><br/>{int(summary.get('max_winning_day_streak', 0))}</div>")

        # ✅ corrected worst 60m metric

        html.append(
            f"<div><b>Worst 60m intratrade drawdown</b><br/>{_money(float(summary.get('worst_60m_intratrade_drawdown', 0.0)))}</div>"
        )
        # html.append(
            # f"<div><b>Worst 60m realized drawdown</b><br/>{_money(float(summary.get('worst_60m_realized_drawdown', 0.0)))}</div>")
        if summary.get("worst_60m_intratrade_start") is not None:
            html.append(
                f"<div class='muted' style='margin-top:6px;'>Worst 60m intratrade window: "
                f"{summary.get('worst_60m_intratrade_start')} → trough {summary.get('worst_60m_intratrade_trough_time')}</div>"
            )

        html.append(f"<div><b>Trail Lock Reached</b><br/>{'Yes' if summary['trail_lock_reached'] else 'No'}</div>")
        html.append("</div>")

        if danger:
            html.append("<div style='margin-top:10px; padding:10px; border:2px solid #b00020; border-radius:10px;'>"
                        "<b>Danger:</b> Min intratrade buffer went below $0 (would violate trailing DD under conservative ordering).</div>")

        # Worst trades table
        html.append("<div style='margin-top:12px;'><b>Worst trades by intratrade buffer</b></div>")
        html.append("<table class='table' style='margin-top:6px;'>")
        html.append("<tr><th>Trade #</th><th>Pos</th><th>Realized</th><th>Min Buffer</th><th>Close Buffer</th><th>Trail Locked</th></tr>")
        for _, r in worst_rows.iterrows():
            html.append(
                "<tr>"
                f"<td>{int(r['trade_number'])}</td>"
                f"<td>{r['pos']}</td>"
                f"<td>{_money(float(r['realized_pnl']))}</td>"
                f"<td>{_money(float(r['min_buffer_intratrade']))}</td>"
                f"<td>{_money(float(r['buffer_close']))}</td>"
                f"<td>{'Yes' if bool(r['trail_locked']) else 'No'}</td>"
                "</tr>"
            )
        html.append("</table>")
        html.append("</div>")  # card

    html.append("</div>")  # section
    return "\n".join(html)
