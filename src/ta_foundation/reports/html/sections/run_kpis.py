from __future__ import annotations

import pandas as pd
from ta_foundation.core.model import AnalysisPackage


def render_run_kpis(ctx: dict) -> str:
    packages: dict[str, AnalysisPackage] = ctx["packages"]

    def fmt_money(x):
        try:
            return f"${float(x):,.2f}"
        except Exception:
            return "—" if x is None else str(x)

    def fmt_num(x):
        try:
            return f"{float(x):,.2f}"
        except Exception:
            return "—" if x is None else str(x)

    def fmt_pct(x):
        try:
            v = float(x)
            if v <= 1.5:
                return f"{v*100:.2f}%"
            return f"{v:.2f}%"
        except Exception:
            return "—" if x is None else str(x)

    blocks = []
    for run_id, pkg in packages.items():
        k = pkg.summary.kpis_all if (pkg.summary and pkg.summary.kpis_all) else {}

        net = k.get("total net profit")
        pf = k.get("profit factor")
        max_dd = k.get("max drawdown")
        win = k.get("percent profitable")
        trades = k.get("total number of trades") or k.get("total of trades")

        blocks.append(f"""
        <div class="card" style="grid-column: span 12;">
          <div class="row" style="justify-content:space-between; align-items:baseline;">
            <div class="mono" style="font-size:14px; font-weight:650;">{run_id}</div>
            <div class="muted">Summary KPIs</div>
          </div>
          <div class="kpis" style="margin-top:10px;">
            <div class="kpi"><div class="label">Net Profit</div><div class="value">{fmt_money(net)}</div></div>
            <div class="kpi"><div class="label">Profit Factor</div><div class="value">{fmt_num(pf)}</div></div>
            <div class="kpi"><div class="label">Max Drawdown</div><div class="value">{fmt_money(max_dd)}</div></div>
            <div class="kpi"><div class="label">Win Rate</div><div class="value">{fmt_pct(win)}</div><div class="sub">Trades: {trades if trades is not None else "—"}</div></div>
          </div>
        </div>
        """)

    if not blocks:
        return "<div class='muted'>No runs found.</div>"
    return "\n".join(blocks)
