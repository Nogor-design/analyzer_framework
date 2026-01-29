from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ta_foundation.core.market_time_profile import (
    DEFAULT_SESSIONS,
    SessionDef,
    sessions_as_denver_bins,
    summarize_overlap,
)


def _downsample_or(arr: List[int], factor: int, bins: int) -> List[int]:
    out = []
    for i in range(0, len(arr), factor):
        out.append(1 if any(arr[i : i + factor]) else 0)
    return (out + [0] * bins)[:bins]


def _downsample_sessions(
    session_masks: List[Tuple[SessionDef, List[int]]],
    factor: int,
    bins: int,
) -> List[str]:
    labels: List[str] = []
    base_len = len(session_masks[0][1]) if session_masks else 0

    for i in range(bins):
        start = i * factor
        end = min(start + factor, base_len)
        label = "Off-session"
        if start < base_len:
            for s, mask in session_masks:
                if any(mask[start:end]):
                    label = s.label
                    break
        labels.append(label)

    return (labels + ["Off-session"] * bins)[:bins]


def _format_time_label(minutes_of_day: int, mode: str) -> str:
    """
    mode:
      - "hour": show HH only at :00 boundaries, blank otherwise
      - "hm": show HH:MM at every cell boundary
      - "smart": show HH for :00 boundaries, show :30 for 30m bins, show :15/:30/:45 for 15m bins
    """
    hh = (minutes_of_day // 60) % 24
    mm = minutes_of_day % 60

    if mode == "hm":
        return f"{hh:02d}:{mm:02d}"

    if mode == "smart":
        if mm == 0:
            return f"{hh:02d}"
        if mm in (15, 30, 45):
            return f":{mm:02d}"
        return "&nbsp;"

    # default "hour"
    if mm == 0:
        return f"{hh:02d}"
    return "&nbsp;"


def render_session_timeline(
    run_id: str,
    pkg: Any,
    *,
    render_bin_minutes: int = 60,        # 24 columns when 60; 48 when 30; 96 when 15
    cell_h_px: int = 12,
    show_hour_labels: bool = True,
    show_summary: bool = True,
    label_mode: str = "hour",            # ✅ new: "hour" | "smart" | "hm"
    sessions: Optional[List[SessionDef]] = None,
) -> str:
    """
    Single-table session timeline sharing one column grid:
      - sessions row
      - activity row
      - optional labels row

    Label behavior is bin-aware via label_mode.
    """
    derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
    prof = derived.get("trade_time_profile") or {}

    base_bin = int(prof.get("bin_minutes", 15))
    base_active = prof.get("active") or []
    anchor_date = prof.get("anchor_date")

    if render_bin_minutes <= 0 or 1440 % render_bin_minutes != 0:
        raise ValueError(f"render_bin_minutes must divide 1440; got {render_bin_minutes}")
    if base_bin <= 0 or 1440 % base_bin != 0:
        raise ValueError(f"trade_time_profile.bin_minutes must divide 1440; got {base_bin}")

    bins = 1440 // render_bin_minutes

    # Do not upsample (avoid fake precision)
    if render_bin_minutes < base_bin:
        render_bin_minutes = base_bin
        bins = 1440 // render_bin_minutes

    factor = max(1, render_bin_minutes // base_bin)

    sessions = sessions or DEFAULT_SESSIONS
    session_masks = sessions_as_denver_bins(anchor_date, bin_minutes=base_bin, sessions=sessions)

    active = _downsample_or(base_active, factor, bins)
    labels = _downsample_sessions(session_masks, factor, bins)

    label_to_color = {s.label: s.color for s in sessions}

    td_box = (
        f"width:1%;height:{cell_h_px}px;"
        "padding:0;margin:0;"
        "border:1px solid #475569;"
        "box-sizing:border-box;"
        "overflow:hidden;"
    )

    table_style = "width:100%;border-collapse:collapse;table-layout:fixed;"

    session_row = "".join(
        f'<td style="{td_box}background:{label_to_color.get(l, "#111827")};" title="{_esc(l)}"></td>'
        for l in labels
    )

    activity_row = "".join(
        f'<td style="{td_box}background:{"#2563eb" if a else "#0b1220"};" '
        f'title="{_esc(run_id)} {"active" if a else "inactive"}"></td>'
        for a in active
    )

    hour_row = ""
    if show_hour_labels:
        label_td = (
            "width:1%;height:14px;"
            "padding:0;margin:0;"
            "font-size:10px;color:#94a3b8;text-align:center;"
            "border:0;"
        )
        label_cells: List[str] = []
        for i in range(bins):
            minutes_of_day = i * render_bin_minutes
            txt = _format_time_label(minutes_of_day, label_mode)
            label_cells.append(f'<td style="{label_td}">{txt}</td>')
        hour_row = "<tr>" + "".join(label_cells) + "</tr>"

    summary_html = ""
    if show_summary:
        session_masks_ds = [(s, _downsample_or(m, factor, bins)) for (s, m) in session_masks]
        overlaps = summarize_overlap(active, session_masks_ds)
        top = [f"{lab} {pct*100:.0f}%" for lab, pct in overlaps if pct > 0][:2]
        txt = ", ".join(top) if top else "No concentration"
        summary_html = (
            '<div style="margin-top:4px;font-size:11px;color:#94a3b8;">'
            '<span style="font-weight:600;color:#cbd5e1;">Time-of-day:</span> '
            f"{_esc(txt)}"
            "</div>"
        )

    rows = [
        f"<tr>{session_row}</tr>",
        f"<tr>{activity_row}</tr>",
    ]
    if show_hour_labels:
        rows.append(hour_row)

    return (
        '<div class="ta-session-timeline" style="margin-top:6px;margin-bottom:8px;width:100%;">'
        f'<table cellpadding="0" cellspacing="0" style="{table_style}">'
        f"{''.join(rows)}"
        "</table>"
        f"{summary_html}"
        "</div>"
    )


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
