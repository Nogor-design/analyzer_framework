from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    fmt,
    hdr,
    info_box,
    section_title,
)
from ta_foundation.analysis.large_candle_excursion.session_classifier import SESSION_ORDER


def render_large_candle_excursion_session_context(ctx: dict) -> str:
    lce = None
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            lce = d["large_candle_excursion"]
            break

    if not lce:
        return info_box("Session context unavailable: large_candle_excursion analytics not found.")

    ca = lce.get("context_analysis") or {}
    sc = ca.get("session_context") or {}

    if not sc.get("enabled"):
        return info_box(
            "Session context not computed. Check that large_candle_excursion ran with bar data "
            "containing tz-aware timestamps."
        )

    by_session = sc.get("by_session") or []
    if not by_session:
        return info_box("No session rows available.", color="#f8f9fa", border="#dee2e6")

    # Sort by canonical session order
    order_map = {s: i for i, s in enumerate(SESSION_ORDER)}
    by_session = sorted(
        by_session,
        key=lambda r: (order_map.get(r.get("session_bucket", ""), 99), r.get("session_bucket", "")),
    )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Session Context — Excursion & Trade Edge by Session")

    has_trade = any(r.get("cont_win_rate") is not None for r in by_session)

    html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += "<thead><tr>"
    cols = ["Session", "N Obs", "Mean Fav T", "Median Fav T", "Mean Adv T", "Median Adv T"]
    if has_trade:
        cols += ["Cont WR %", "Rev WR %", "Better"]
    for h in cols:
        html += hdr(h)
    html += "</tr></thead><tbody>"

    for i, r in enumerate(by_session):
        bg = "#f9f9f9" if i % 2 == 0 else "#fff"
        n = r.get("n_observations", 0)
        cont_wr = r.get("cont_win_rate")
        rev_wr = r.get("rev_win_rate")
        better = r.get("better_mode")

        html += "<tr>"
        html += cell(f'<b>{r.get("session_bucket", "?")}</b>', bg=bg)
        html += cell(str(n), bg=bg)
        html += cell(fmt(r.get("mean_fav_ticks")), bg=bg)
        html += cell(fmt(r.get("median_fav_ticks")), bg=bg)
        html += cell(fmt(r.get("mean_adv_ticks")), bg=bg)
        html += cell(fmt(r.get("median_adv_ticks")), bg=bg)

        if has_trade:
            cont_bg = bg
            rev_bg = bg
            if cont_wr is not None and rev_wr is not None:
                if float(cont_wr) > float(rev_wr) + 2:
                    cont_bg = "#d4edda"
                elif float(rev_wr) > float(cont_wr) + 2:
                    rev_bg = "#d4edda"
            html += cell(fmt(cont_wr, digits=1, suffix="%") if cont_wr is not None else "—", bg=cont_bg)
            html += cell(fmt(rev_wr, digits=1, suffix="%") if rev_wr is not None else "—", bg=rev_bg)
            html += cell(better or "—", bg=bg)

        html += "</tr>"

    html += "</tbody></table>"

    # Session × size interaction table if present
    sx = sc.get("by_session_x_size") or []
    if sx:
        html += section_title("Session × Candle Size Interaction")
        html += '<table style="border-collapse:collapse;width:100%;font-size:12px;margin-top:8px">'
        html += "<thead><tr>"
        ix_cols = ["Session", "Candle Bucket", "N Obs", "Mean Fav T", "Mean Adv T"]
        if any(r.get("cont_win_rate") is not None for r in sx):
            ix_cols += ["Cont WR %", "Rev WR %"]
        for h in ix_cols:
            html += hdr(h)
        html += "</tr></thead><tbody>"

        for i, r in enumerate(sx):
            bg = "#f9f9f9" if i % 2 == 0 else "#fff"
            html += "<tr>"
            html += cell(r.get("session_bucket", "?"), bg=bg)
            html += cell(r.get("candle_bucket", "?"), bg=bg)
            html += cell(str(r.get("n_observations", 0)), bg=bg)
            html += cell(fmt(r.get("mean_fav_ticks")), bg=bg)
            html += cell(fmt(r.get("mean_adv_ticks")), bg=bg)
            if any(r2.get("cont_win_rate") is not None for r2 in sx):
                html += cell(fmt(r.get("cont_win_rate"), digits=1, suffix="%") if r.get("cont_win_rate") is not None else "—", bg=bg)
                html += cell(fmt(r.get("rev_win_rate"), digits=1, suffix="%") if r.get("rev_win_rate") is not None else "—", bg=bg)
            html += "</tr>"

        html += "</tbody></table>"

    html += "</div>"
    return html
