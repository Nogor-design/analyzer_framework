from __future__ import annotations

from typing import Any, Dict, Optional


def get_derived_payload(ctx: dict, key: str) -> Optional[Dict[str, Any]]:
    if ctx.get(key):
        v = ctx.get(key)
        if isinstance(v, dict):
            return v
    for pkg in (ctx.get("packages") or {}).values():
        derived = getattr(pkg, "metadata", {}).get("derived", {})
        if key in derived:
            v = derived.get(key)
            if isinstance(v, dict):
                return v
    return None


def fmt(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except Exception:
        return str(v)


def hdr(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
        f'text-align:center;border:1px solid #ddd;font-size:12px;white-space:nowrap">'
        f"{text}</th>"
    )


def cell(v: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:5px 8px;'
        f'border:1px solid #ddd;font-size:12px;{fw}">{v}</td>'
    )


def section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:24px;margin-bottom:12px">{text}</h3>'
    )


def info_box(text: str, color: str = "#fff3cd", border: str = "#ffc107") -> str:
    return (
        f'<div style="padding:16px;background:{color};border:1px solid {border};border-radius:4px">'
        f"{text}</div>"
    )
