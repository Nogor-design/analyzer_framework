from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Tuple


def compute_shared_trading_days(packages: Dict[str, Any], days_back: int) -> List[str]:
    """
    Build a shared date axis (ISO date strings) so W/L/NT strips align across runs.

    Strategy:
      - collect all dates present in pkg.metadata["derived"]["daily_outcomes"]["by_date"]
      - sort
      - take last N

    This avoids injecting weekends/holidays by default and matches "days with data".
    """
    all_days: List[str] = []
    for pkg in packages.values():
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
        outcomes = (derived.get("daily_outcomes") or {}).get("by_date", {}) or {}
        all_days.extend(outcomes.keys())

    # De-dupe + sort lexicographically works for ISO dates.
    unique_sorted = sorted(set(all_days))
    if days_back <= 0:
        return unique_sorted
    return unique_sorted[-days_back:]


def render_wlr_strip(
    run_id: str,
    pkg: Any,
    shared_days: List[str],
    *,
    box_px: int = 10,
    gap_px: int = 2,
    show_legend: bool = False,
) -> str:
    """
    Render a Slides-safe, table-based strip with colored boxes aligned to shared_days.

    Colors:
      WIN      -> green
      LOSS     -> red
      NO_TRADE -> gray
      FLAT     -> gray (same visual as NO_TRADE; still distinguishable via title tooltip)
    """
    derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
    outcomes = (derived.get("daily_outcomes") or {}).get("by_date", {}) or {}

    # Minimal inline styles (no external CSS) for copy/paste safety.
    # We still include class names for Playwright/export styling consistency if desired.
    td_style = f"width:{box_px}px;height:{box_px}px;padding:0;margin:0;border:1px solid #2b2b2b;"
    spacer_style = f"width:{gap_px}px;height:{box_px}px;padding:0;margin:0;"

    cells = []
    for d in shared_days:
        o = outcomes.get(d)
        status = (o or {}).get("status", "NO_TRADE")
        title = f"{run_id} {d} {status}"

        bg = "#7a7a7a"  # default gray
        if status == "WIN":
            bg = "#1faa59"
        elif status == "LOSS":
            bg = "#e04b4b"
        else:
            bg = "#7a7a7a"

        cells.append(
            f'<td class="ta-wlr-box ta-wlr-box--{status.lower()}" '
            f'style="{td_style}background:{bg};" title="{_esc(title)}"></td>'
        )
        # gap
        cells.append(f'<td class="ta-wlr-gap" style="{spacer_style}"></td>')

    if cells:
        cells.pop()  # remove trailing gap

    legend = ""
    if show_legend:
        legend = (
            '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:4px;">'
            "<tr>"
            f'<td style="{td_style}background:#1faa59;"></td>'
            f'<td style="padding-left:6px;padding-right:10px;font-size:11px;color:#222;">Win</td>'
            f'<td style="{td_style}background:#e04b4b;"></td>'
            f'<td style="padding-left:6px;padding-right:10px;font-size:11px;color:#222;">Loss</td>'
            f'<td style="{td_style}background:#7a7a7a;"></td>'
            f'<td style="padding-left:6px;font-size:11px;color:#222;">No Trade / Flat</td>'
            "</tr>"
            "</table>"
        )

    return (
        '<div class="ta-wlr-strip-wrap">'
        '<table class="ta-wlr-strip" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;">'
        f"<tr>{''.join(cells)}</tr>"
        "</table>"
        f"{legend}"
        "</div>"
    )


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
