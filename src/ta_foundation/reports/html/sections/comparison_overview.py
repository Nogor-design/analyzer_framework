from __future__ import annotations

import pandas as pd
from ta_foundation.core.model import AnalysisPackage


def render_comparison_overview(ctx: dict) -> str:
    """
    ctx expects:
      - packages: dict[str, AnalysisPackage]
    """
    packages: dict[str, AnalysisPackage] = ctx["packages"]

    rows = []
    for run_id, pkg in packages.items():
        # Prefer summary KPIs if present; fall back to daily/trades if needed.
        net = None
        pf = None
        max_dd = None
        trades = None
        win_rate = None

        if pkg.summary and pkg.summary.kpis_all:
            k = pkg.summary.kpis_all
            net = k.get("total net profit")
            pf = k.get("profit factor")
            max_dd = k.get("max drawdown")
            trades = k.get("total number of trades")
            win_rate = k.get("percent profitable")

        # Daily fallback
        if pkg.daily is not None and isinstance(pkg.daily, pd.DataFrame) and len(pkg.daily) > 0:
            if net is None and "net_profit" in pkg.daily.columns:
                net = float(pkg.daily["net_profit"].sum())
            if trades is None and "trade_count" in pkg.daily.columns:
                trades = int(pkg.daily["trade_count"].sum())

        rows.append({
            "run_id": run_id,
            "net_profit": net,
            "profit_factor": pf,
            "max_drawdown": max_dd,
            "trades": trades,
            "win_rate": win_rate,
        })

    df = pd.DataFrame(rows)

    # Sorting: highest net profit first, with NaNs last
    if "net_profit" in df.columns:
        df["net_profit_sort"] = pd.to_numeric(df["net_profit"], errors="coerce")
        df = df.sort_values(by="net_profit_sort", ascending=False, na_position="last")
        df = df.drop(columns=["net_profit_sort"])

    def fmt_money(x):
        try:
            return f"${float(x):,.2f}"
        except Exception:
            return "" if x is None else str(x)

    def fmt_num(x):
        try:
            return f"{float(x):,.2f}"
        except Exception:
            return "" if x is None else str(x)

    def fmt_pct(x):
        # values may already be fraction (0.71) or percent string; we handle both
        try:
            v = float(x)
            if v <= 1.5:
                return f"{v*100:.2f}%"
            return f"{v:.2f}%"
        except Exception:
            return "" if x is None else str(x)

    # Build HTML table
    out = []
    out.append("<div class='muted'>Run ranking derived from Summary KPIs when available; otherwise daily totals.</div>")
    out.append("<table>")
    out.append("<thead><tr>"
               "<th>Run</th>"
               "<th class='right'>Net Profit</th>"
               "<th class='right'>Profit Factor</th>"
               "<th class='right'>Max Drawdown</th>"
               "<th class='right'>Trades</th>"
               "<th class='right'>Win Rate</th>"
               "</tr></thead><tbody>")

    for _, r in df.iterrows():
        out.append("<tr>"
                   f"<td class='mono'>{r.get('run_id','')}</td>"
                   f"<td class='right'>{fmt_money(r.get('net_profit'))}</td>"
                   f"<td class='right'>{fmt_num(r.get('profit_factor'))}</td>"
                   f"<td class='right'>{fmt_money(r.get('max_drawdown'))}</td>"
                   f"<td class='right'>{'' if pd.isna(r.get('trades')) else r.get('trades')}</td>"
                   f"<td class='right'>{fmt_pct(r.get('win_rate'))}</td>"
                   "</tr>")

    out.append("</tbody></table>")
    return "\n".join(out)
