from __future__ import annotations

from datetime import datetime
from typing import Optional

from ta_foundation.core.model import AnalysisPackage


def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    # dt is tz-aware (America/Denver). Display with offset for auditability.
    return dt.strftime("%Y-%m-%d %I:%M %p %Z")


def _fmt_duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not start or not end:
        return "—"
    delta = end - start
    days = delta.days
    seconds = delta.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def render_run_metadata_cards(ctx: dict) -> str:
    """
    ctx expects:
      - packages: dict[str, AnalysisPackage]
    """
    packages: dict[str, AnalysisPackage] = ctx["packages"]

    blocks: list[str] = []
    for run_id, pkg in packages.items():
        start_dt = pkg.summary.start_dt if pkg.summary else None
        end_dt = pkg.summary.end_dt if pkg.summary else None
        dur = _fmt_duration(start_dt, end_dt)

        has_trades = "Yes" if pkg.trades is not None and len(pkg.trades) > 0 else "No"
        has_daily = "Yes" if pkg.daily is not None and len(pkg.daily) > 0 else "No"
        has_summary = "Yes" if pkg.summary is not None else "No"

        blocks.append(f"""
        <div class="card" style="grid-column: span 12;">
          <div class="row" style="justify-content:space-between; align-items:baseline;">
            <div class="mono" style="font-size:14px; font-weight:650;">{run_id}</div>
            <div class="muted">Run metadata</div>
          </div>

          <div class="kpis" style="margin-top:10px;">
            <div class="kpi" style="grid-column: span 6;">
              <div class="label">Start</div>
              <div class="value mono">{_fmt_dt(start_dt)}</div>
              <div class="sub">America/Denver (local PC time)</div>
            </div>

            <div class="kpi" style="grid-column: span 6;">
              <div class="label">End</div>
              <div class="value mono">{_fmt_dt(end_dt)}</div>
              <div class="sub">Duration: {dur}</div>
            </div>

            <div class="kpi" style="grid-column: span 4;">
              <div class="label">Trades file</div>
              <div class="value">{has_trades}</div>
            </div>

            <div class="kpi" style="grid-column: span 4;">
              <div class="label">Daily Analysis file</div>
              <div class="value">{has_daily}</div>
            </div>

            <div class="kpi" style="grid-column: span 4;">
              <div class="label">Summary file</div>
              <div class="value">{has_summary}</div>
            </div>
          </div>
        </div>
        """)

    if not blocks:
        return "<div class='muted'>No runs found.</div>"

    return "\n".join(blocks)
