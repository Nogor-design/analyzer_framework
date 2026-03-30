from __future__ import annotations

import html
from typing import Any, Dict, List


def _fmt(v: Any, dec: int = 2) -> str:
    try:
        return f"{float(v):.{dec}f}"
    except Exception:
        return "—"


def _collect_rows(packages: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {})
        sd = derived.get("strategy_discovery", {}) if isinstance(derived, dict) else {}
        pdm = sd.get("pure_discovery", {}) if isinstance(sd, dict) else {}
        if not isinstance(pdm, dict) or pdm.get("status") != "ok":
            continue
        for row in (pdm.get("leaderboard") or []):
            out = dict(row)
            out["run_id"] = run_id
            rows.append(out)
    return sorted(rows, key=lambda r: (r.get("rank", 999), -(r.get("score") or 0.0)))


def render_strategy_discovery_pure_discovery(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    _market = ctx.get("market")
    _report_config = ctx.get("report_config")

    rows = _collect_rows(packages)
    max_rows = int(options.get("max_rows", 100))
    rows = rows[:max_rows]

    if not rows:
        return '<div style="padding:12px;border:1px solid #ddd;border-radius:8px">Pure discovery is enabled but no qualifying strategies were found.</div>'

    table_rows = ""
    for r in rows:
        table_rows += (
            "<tr>"
            f"<td>{html.escape(str(r.get('run_id', '—')))}</td>"
            f"<td>{int(r.get('rank', 0))}</td>"
            f"<td>{html.escape(str(r.get('strategy_name', '—')))}</td>"
            f"<td>{html.escape(str(r.get('archetype', '—')))}</td>"
            f"<td>{html.escape(str(r.get('direction', '—')))}</td>"
            f"<td>{html.escape(str(r.get('session', '—')))}</td>"
            f"<td>{int(r.get('trade_count', 0))}</td>"
            f"<td>{_fmt(r.get('PF'))}</td>"
            f"<td>{_fmt((r.get('WR') or 0) * 100, 1)}%</td>"
            f"<td>{_fmt(r.get('Sharpe'))}</td>"
            f"<td>{_fmt(r.get('Sortino'))}</td>"
            f"<td>{_fmt(r.get('max_DD'))}</td>"
            f"<td>{html.escape(str(r.get('walk_forward_result', '—')))}</td>"
            f"<td>{_fmt(r.get('decay_score'))}</td>"
            f"<td>{html.escape(str(r.get('sensitivity_class', '—')))}</td>"
            f"<td>{html.escape(str(r.get('cluster_badge', '—')))}</td>"
            f"<td>{html.escape(str(r.get('implementation_complexity', '—')))}</td>"
            "</tr>"
        )

    rejection_items: List[str] = []
    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {})
        sd = derived.get("strategy_discovery", {}) if isinstance(derived, dict) else {}
        pdm = sd.get("pure_discovery", {}) if isinstance(sd, dict) else {}
        for rej in (pdm.get("rejections") or [])[: int(options.get("max_rejections_per_run", 20))]:
            reasons = ", ".join(rej.get("reasons") or [])
            rejection_items.append(
                f"<li><b>{html.escape(str(run_id))}</b> · {html.escape(str(rej.get('strategy_name', 'unknown')))}"
                f" — {html.escape(reasons or 'rejected')}</li>"
            )

    rej_html = "<ul>" + "".join(rejection_items) + "</ul>" if rejection_items else "<div>No rejections captured.</div>"

    return f"""
    <div style='display:flex;flex-direction:column;gap:12px'>
      <div style='padding:12px;border:1px solid #ddd;border-radius:8px;background:#fafafa'>
        <b>Pure Discovery Leaderboard</b>
        <div style='overflow-x:auto;margin-top:8px'>
          <table style='border-collapse:collapse;width:100%;font-size:12px'>
            <thead>
              <tr style='background:#f1f1f1'>
                <th style='padding:6px;border:1px solid #ddd'>Run</th>
                <th style='padding:6px;border:1px solid #ddd'>Rank</th>
                <th style='padding:6px;border:1px solid #ddd'>Strategy</th>
                <th style='padding:6px;border:1px solid #ddd'>Archetype</th>
                <th style='padding:6px;border:1px solid #ddd'>Direction</th>
                <th style='padding:6px;border:1px solid #ddd'>Session</th>
                <th style='padding:6px;border:1px solid #ddd'>Trades</th>
                <th style='padding:6px;border:1px solid #ddd'>PF</th>
                <th style='padding:6px;border:1px solid #ddd'>WR</th>
                <th style='padding:6px;border:1px solid #ddd'>Sharpe</th>
                <th style='padding:6px;border:1px solid #ddd'>Sortino</th>
                <th style='padding:6px;border:1px solid #ddd'>Max DD</th>
                <th style='padding:6px;border:1px solid #ddd'>WF</th>
                <th style='padding:6px;border:1px solid #ddd'>Decay</th>
                <th style='padding:6px;border:1px solid #ddd'>Sensitivity</th>
                <th style='padding:6px;border:1px solid #ddd'>Cluster</th>
                <th style='padding:6px;border:1px solid #ddd'>Complexity</th>
              </tr>
            </thead>
            <tbody>{table_rows}</tbody>
          </table>
        </div>
      </div>

      <div style='padding:12px;border:1px solid #ddd;border-radius:8px;background:#fff'>
        <b>Rejection Audit Trail</b>
        <div style='margin-top:8px'>{rej_html}</div>
      </div>
    </div>
    """
