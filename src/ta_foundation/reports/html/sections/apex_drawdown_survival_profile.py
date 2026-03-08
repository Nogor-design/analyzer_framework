# src/ta_foundation/reports/html/sections/apex_drawdown_survival_profile.py
from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd

from ta_foundation.analysis.apex_trailing_model import (
    ApexParams,
    InstrumentSpec,
    simulate_apex_trailing_for_run,
)
from ta_foundation.analysis.apex_portfolio_mc import (
    ApexPortfolioParams,
    run_portfolio_monte_carlo,
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
    parts = str(instr).strip().split()
    return parts[0] if parts else ""


def _extract_contract_token(instr: str) -> Optional[str]:
    parts = str(instr).strip().split()
    if len(parts) >= 2:
        return parts[1].strip()
    return None


def _normalize_contract(contract: str) -> str:
    """
    Normalize common contract token formats to match file naming conventions.
    Examples:
      - "03-26"   -> "03-26"
      - "03-2026" -> "03-26"
      - "03/26"   -> "03-26"
      - "03/2026" -> "03-26"
    """
    c = (contract or "").strip()

    m = re.match(r"^(\d{2})-(\d{4})$", c)
    if m:
        mm, yyyy = m.group(1), m.group(2)
        return f"{mm}-{yyyy[-2:]}"

    m = re.match(r"^(\d{2})/(\d{2})$", c)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    m = re.match(r"^(\d{2})/(\d{4})$", c)
    if m:
        mm, yyyy = m.group(1), m.group(2)
        return f"{mm}-{yyyy[-2:]}"

    return c


def _money(v: float) -> str:
    s = f"{abs(v):,.0f}"
    return f"(${s})" if v < 0 else f"${s}"


def render_apex_drawdown_survival_profile(ctx: dict[str, Any]) -> str:
    # --- required standardized ctx access pattern ---
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or ctx.get("section_options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")  # kept for contract completeness

    html: list[str] = []
    html.append("<div class='section'>")

    if market is None:
        html.append("<div class='muted'>No market data loaded.</div>")
        html.append("</div>")
        return "\n".join(html)

    apex_cfg = (options.get("apex") or {}) if isinstance(options.get("apex"), dict) else {}
    apex = ApexParams(
        starting_balance=float(apex_cfg.get("starting_balance", 50_000.0)),
        trailing_drawdown=float(apex_cfg.get("trailing_drawdown", 2_500.0)),
        lock_profit=float(apex_cfg.get("lock_profit", 2_600.0)),
    )

    # Instrument specs (YAML overrides supported)
    inst_cfg = (options.get("instruments") or {}) if isinstance(options.get("instruments"), dict) else {}
    default_specs = {
        "NQ": InstrumentSpec(0.25, 5.0),
        "MNQ": InstrumentSpec(0.25, 0.5),
        "ES": InstrumentSpec(0.25, 12.5),
        "MES": InstrumentSpec(0.25, 1.25),
        "YM": InstrumentSpec(1.0, 5.0),
        "GC": InstrumentSpec(0.10, 10.0),
        "MGC": InstrumentSpec(0.10, 1.0),
        "NG": InstrumentSpec(0.001, 10.0),
        "RTY": InstrumentSpec(0.10, 5.0),  # safe default; override if needed
        "M2K": InstrumentSpec(0.10, 1.25),
    }

    def spec_for(root: str) -> InstrumentSpec:
        if root in inst_cfg and isinstance(inst_cfg[root], dict):
            d = inst_cfg[root]
            return InstrumentSpec(
                tick_size=float(d.get("tick_size")),
                tick_value=float(d.get("tick_value")),
            )
        return default_specs.get(root, InstrumentSpec(1.0, 1.0))

    summary_rows: list[dict[str, Any]] = []
    run_cards: list[str] = []

    # ---------
    # Per-run compute (render-only; uses market store already in memory)
    # ---------
    for run_id, pkg in packages.items():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue

        c_instr = _col(trades, "Instrument")
        if c_instr is None or trades[c_instr].dropna().empty:
            continue

        instr_raw = str(trades[c_instr].dropna().iloc[0])
        root = _parse_instrument_root(instr_raw)
        contract_raw = _extract_contract_token(instr_raw)
        if not root or not contract_raw:
            continue
        contract = _normalize_contract(contract_raw)

        bars = market.get_bars(root, contract, timeframe="1m", source="auto")
        if bars is None or bars.empty:
            # Keep a small card so you can see which bots were skipped.
            run_cards.append(
                "<div class='card'>"
                f"<h3>{run_id}</h3>"
                f"<div class='muted'>No bars for {root} {contract} (parsed from '{instr_raw}').</div>"
                "</div>"
            )
            continue

        spec = spec_for(root)
        ledger, summary = simulate_apex_trailing_for_run(
            trades_df=trades,
            minute_bars_df=bars,
            spec=spec,
            apex=apex,
        )

        if ledger is None or ledger.empty:
            run_cards.append(
                "<div class='card'>"
                f"<h3>{run_id}</h3>"
                "<div class='muted'>No simulation output.</div>"
                "</div>"
            )
            continue

        # Persist JSON-safe derived metadata (lightweight)
        derived = pkg.metadata.setdefault("derived", {})
        derived["apex_survival_summary"] = summary

        # Summary row
        min_buffer = float(summary["min_buffer_intratrade"])
        worst_60m = float(summary.get("worst_60m_intratrade_drawdown", 0.0))
        final_pnl = float(summary["final_realized_pnl"])
        trail_locked = bool(summary.get("trail_lock_reached", False))
        losing_trade_streak = int(summary.get("max_losing_trade_streak", 0))
        losing_day_streak = int(summary.get("max_losing_day_streak", 0))
        survived = min_buffer >= 0

        summary_rows.append(
            {
                "run_id": run_id,
                "instrument": f"{root} {contract}",
                "final_pnl": final_pnl,
                "min_buffer": min_buffer,
                "worst_60m": worst_60m,
                "trail_locked": trail_locked,
                "max_losing_trade_streak": losing_trade_streak,
                "max_losing_day_streak": losing_day_streak,
                "survived": survived,
            }
        )

        # Worst trades table (keep small)
        worst_rows = ledger.nsmallest(5, "min_buffer_intratrade")[
            ["trade_number", "pos", "realized_pnl", "min_buffer_intratrade", "buffer_close", "trail_locked"]
        ].copy()
        derived["apex_survival_worst_trades"] = worst_rows.to_dict(orient="records")

        danger = min_buffer < 0

        # Render card
        card: list[str] = []
        card.append("<div class='card'>")
        card.append(f"<h3>{run_id}</h3>")
        card.append(
            f"<div class='muted'>Apex model • Trail ${apex.trailing_drawdown:,.0f} • Lock at +${apex.lock_profit:,.0f} "
            f"(trail fixed at ${summary.get('locked_trail_level', 0.0):,.0f}) • Instrument {root} {contract}</div>"
        )

        card.append("<div style='display:flex; gap:16px; flex-wrap:wrap; margin-top:8px;'>")
        card.append(f"<div><b>Final PnL</b><br/>{_money(final_pnl)}</div>")
        card.append(f"<div><b>Min Buffer (intratrade)</b><br/>{_money(min_buffer)}</div>")
        card.append(f"<div><b>Worst 60m intratrade DD</b><br/>{_money(worst_60m)}</div>")
        card.append(f"<div><b>Trail Lock Reached</b><br/>{'Yes' if trail_locked else 'No'}</div>")
        card.append(f"<div><b>Max Losing Trade Streak</b><br/>{losing_trade_streak}</div>")
        card.append(f"<div><b>Max Losing Day Streak</b><br/>{losing_day_streak}</div>")
        card.append("</div>")

        if danger:
            card.append(
                "<div style='margin-top:10px; padding:10px; border:2px solid #b00020; border-radius:10px;'>"
                "<b>Danger:</b> Min intratrade buffer went below $0.</div>"
            )

        card.append("<div style='margin-top:12px;'><b>Worst trades by intratrade buffer</b></div>")
        card.append("<table class='table' style='margin-top:6px;'>")
        card.append("<tr><th>Trade #</th><th>Pos</th><th>Realized</th><th>Min Buffer</th><th>Close Buffer</th><th>Trail Locked</th></tr>")
        for _, r in worst_rows.iterrows():
            card.append(
                "<tr>"
                f"<td>{int(r['trade_number'])}</td>"
                f"<td>{r['pos']}</td>"
                f"<td>{_money(float(r['realized_pnl']))}</td>"
                f"<td>{_money(float(r['min_buffer_intratrade']))}</td>"
                f"<td>{_money(float(r['buffer_close']))}</td>"
                f"<td>{'Yes' if bool(r['trail_locked']) else 'No'}</td>"
                "</tr>"
            )
        card.append("</table>")
        card.append("</div>")  # card

        run_cards.append("\n".join(card))

    # -----------------------
    # Portfolio Monte Carlo (across bots)
    # -----------------------
    # Build per-bot daily PnL matrix: index=local dates, columns=run_id
    bot_daily = {}
    for run_id, pkg in packages.items():
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        c_pnl = _col(trades, "ProfitCurrency", "profit", "pnl", "pnl_usd")
        c_exit = _col(trades, "ExitTime", "exit time", "exit_time")
        if c_pnl is None or c_exit is None:
            continue

        s = trades.groupby(trades[c_exit].dt.date)[c_pnl].sum()
        if not s.empty:
            bot_daily[run_id] = s.astype(float)

    if bot_daily:
        daily_df = pd.concat(bot_daily, axis=1).fillna(0.0)
        portfolio_series = daily_df.sum(axis=1)

        mc_cfg = options.get("portfolio_mc", {}) if isinstance(options.get("portfolio_mc"), dict) else {}
        n_paths = int(mc_cfg.get("n_paths", 2000))
        seed = int(mc_cfg.get("seed", 42))
        mode = str(mc_cfg.get("mode", "iid")).strip().lower()
        if mode not in ("iid", "correlated"):
            mode = "iid"

        apex_port = ApexPortfolioParams(
            starting_balance=apex.starting_balance,
            trailing_drawdown=apex.trailing_drawdown,
            lock_profit=apex.lock_profit,
        )

        # mode="iid" uses summed portfolio series (shuffle daily portfolio pnl)
        # mode="correlated" uses daily_df (shuffle day ordering applied across all bots)
        mc_result = run_portfolio_monte_carlo(
            daily_df if mode == "correlated" else portfolio_series,
            apex_port,
            n_paths=n_paths,
            seed=seed,
            mode="correlated" if mode == "correlated" else "iid",
        )

        html.append("<h2>Portfolio Monte Carlo</h2>")
        if mc_result.get("ok"):
            surv = float(mc_result["survival_probability"])
            surv_color = "#2e7d32" if surv >= 0.7 else "#b00020"

            html.append("<div class='card'>")
            html.append(
                f"<div class='muted'>Sampling mode: <b>{mc_result.get('mode', '')}</b> • Paths: {int(mc_result['n_paths'])} • Days: {int(mc_result['n_days'])}</div>")
            html.append(
                f"<div><b>Survival Probability:</b> "
                f"<span style='color:{surv_color}; font-weight:bold;'>{surv:.1%}</span></div>"
            )
            html.append(f"<div><b>P10 Min Buffer:</b> {_money(float(mc_result['min_buffer_p10']))}</div>")
            html.append(
                f"<div><b>Expected Final Balance:</b> {_money(float(mc_result['expected_final_balance']))}</div>")
            if mc_result.get("breach_day_p90") is not None:
                html.append(f"<div><b>P90 Breach Day:</b> {int(float(mc_result['breach_day_p90']))}</div>")
            html.append("</div>")
        else:
            html.append(f"<div class='muted'>Portfolio MC unavailable: {mc_result.get('reason', 'unknown')}</div>")

    # -----------------------
    # Summary table (top)
    # -----------------------
    if summary_rows:
        html.append("<h2>Apex Survival Summary</h2>")
        html.append("<table class='table'>")
        html.append(
            "<tr>"
            "<th>Bot</th>"
            "<th>Instrument</th>"
            "<th>Final PnL</th>"
            "<th>Min Buffer</th>"
            "<th>Worst 60m DD</th>"
            "<th>Trail Locked</th>"
            "<th>Max Losing Trade Streak</th>"
            "<th>Max Losing Day Streak</th>"
            "<th>Status</th>"
            "</tr>"
        )

        for row in sorted(summary_rows, key=lambda r: r["min_buffer"]):
            status_color = "#2e7d32" if row["survived"] else "#b00020"
            status_label = "PASS" if row["survived"] else "FAIL"
            html.append(
                "<tr>"
                f"<td>{row['run_id']}</td>"
                f"<td>{row['instrument']}</td>"
                f"<td>{_money(float(row['final_pnl']))}</td>"
                f"<td>{_money(float(row['min_buffer']))}</td>"
                f"<td>{_money(float(row['worst_60m']))}</td>"
                f"<td>{'Yes' if bool(row['trail_locked']) else 'No'}</td>"
                f"<td>{int(row['max_losing_trade_streak'])}</td>"
                f"<td>{int(row['max_losing_day_streak'])}</td>"
                f"<td style='color:{status_color}; font-weight:bold;'>{status_label}</td>"
                "</tr>"
            )
        html.append("</table>")

    # -----------------------
    # Detailed per-bot cards (below summary)
    # -----------------------
    if run_cards:
        html.append("<div style='margin-top:16px;'>")
        html.extend(run_cards)
        html.append("</div>")

    html.append("</div>")  # section
    return "\n".join(html)