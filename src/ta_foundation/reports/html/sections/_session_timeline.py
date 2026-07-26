from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ta_foundation.core.market_time_profile import (
    DEFAULT_SESSIONS,
    SessionDef,
    sessions_as_denver_bins,
    summarize_overlap,
)

TZ_DENVER = ZoneInfo("America/Denver")


def _settings_map(pkg: Any) -> dict[str, Any]:
    df = getattr(pkg, "settings", None)
    if df is None or getattr(df, "empty", True):
        return {}

    out: dict[str, Any] = {}
    for _, row in df.iterrows():
        item = str(row.get("item", "")).strip().lower()
        if item:
            out[item] = row.get("value", "")
    return out


def _int_setting(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).strip()))
    except Exception:
        return None


def _build_settings_window_profile(pkg: Any, *, bin_minutes: int) -> dict[str, Any] | None:
    settings = _settings_map(pkg)
    start_h = _int_setting(settings.get("start_time_(hh)"))
    start_m = _int_setting(settings.get("start_time_(mm)"))
    duration_h = _int_setting(settings.get("duration_time_(hh)"))
    duration_m = _int_setting(settings.get("duration_time_(mm)"))

    if None in (start_h, start_m, duration_h, duration_m):
        return None

    bins = 1440 // bin_minutes
    active = [0] * bins

    start_total = (start_h * 60 + start_m) % 1440
    duration_total = max(0, duration_h * 60 + duration_m)
    end_total = start_total + duration_total

    for idx in range(bins):
        bin_start = idx * bin_minutes
        bin_end = bin_start + bin_minutes

        if duration_total == 0:
            overlap = bin_start <= start_total < bin_end
        elif end_total <= 1440:
            overlap = start_total < bin_end and end_total > bin_start
        else:
            wrapped_end = end_total - 1440
            overlap = (
                (start_total < bin_end and 1440 > bin_start)
                or (0 < bin_end and wrapped_end > bin_start)
            )

        if overlap:
            active[idx] = 1

    return {
        "tz": "America/Denver",
        "bin_minutes": bin_minutes,
        "bins": bins,
        "active": active,
        "anchor_date": datetime.now(TZ_DENVER).date().isoformat(),
        "source": "settings",
    }


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

    if mm == 0:
        return f"{hh:02d}"
    return "&nbsp;"


def render_session_timeline(
    run_id: str,
    pkg: Any,
    *,
    render_bin_minutes: int = 60,
    cell_h_px: int = 12,
    show_hour_labels: bool = True,
    show_summary: bool = True,
    label_mode: str = "hour",
    sessions: Optional[List[SessionDef]] = None,
    prefer_settings_window: bool = True,
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

    if render_bin_minutes <= 0 or 1440 % render_bin_minutes != 0:
        raise ValueError(f"render_bin_minutes must divide 1440; got {render_bin_minutes}")

    settings_profile = None
    if prefer_settings_window:
        settings_profile = _build_settings_window_profile(pkg, bin_minutes=render_bin_minutes)

    if settings_profile is not None:
        base_bin = int(settings_profile.get("bin_minutes", render_bin_minutes))
        base_active = settings_profile.get("active") or []
        anchor_date = settings_profile.get("anchor_date")
    else:
        base_bin = int(prof.get("bin_minutes", 15))
        base_active = prof.get("active") or []
        anchor_date = prof.get("anchor_date") or datetime.now(TZ_DENVER).date().isoformat()

    if base_bin <= 0 or 1440 % base_bin != 0:
        raise ValueError(f"trade_time_profile.bin_minutes must divide 1440; got {base_bin}")

    bins = 1440 // render_bin_minutes

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
