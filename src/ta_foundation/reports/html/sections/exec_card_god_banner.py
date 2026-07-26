from __future__ import annotations

"""Report header banner for an optimizer-finalist candidate.

Shows the canonical decoded name (per the template-naming guide), a
plain-English summary of what the strategy is, and — if a portrait
matches via the image-lookup fallback chain — the god/monster image
as a background.

Section contract: pure renderer, returns HTML; reads inputs from
``ctx['options']``:

- ``template_path``    — XML used by the decoder
- ``images_dir``       — directory of portrait images (optional)
- ``run_id``           — candidate run id (e.g. ``F_001``) for the caption
- ``label``            — operator-facing label fallback if decode fails

If no template path or decoder is available it falls back to plain
text so the banner is always emitted.
"""

import base64
from pathlib import Path
from typing import Any


def render_exec_card_god_banner(ctx: dict[str, Any]) -> str:
    options = ctx.get("options") or {}
    template_path = options.get("template_path")
    images_dir = options.get("images_dir")
    market_suffix = options.get("market_suffix") or ""
    run_id = options.get("run_id") or ""
    label = options.get("label") or ""

    try:
        from ta_foundation.web.optimizer_image_lookup import (
            ImageLookupResult,
            lookup_image_for_template,
            plain_english_summary,
        )
    except Exception:
        return _plain_banner(run_id=run_id, title=label or "(strategy)", subtitle="",
                             notes=["image-lookup module unavailable"])

    if not template_path:
        return _plain_banner(run_id=run_id, title=label or "(no template)",
                             subtitle="", notes=["no template_path provided"])

    result = lookup_image_for_template(
        Path(template_path), images_dir=images_dir, market_suffix=market_suffix or None,
    )
    decoded = result.decoded
    title = decoded.get("compact_name") or label or "(strategy)"
    subtitle = plain_english_summary(decoded)
    image_uri = _image_to_data_uri(result.image_path) if result.image_path else None

    if image_uri:
        return _portrait_banner(run_id=run_id, title=title, subtitle=subtitle,
                                image_uri=image_uri, matched_step=result.matched_step or "")
    return _plain_banner(run_id=run_id, title=title, subtitle=subtitle,
                         notes=result.notes)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _portrait_banner(*, run_id: str, title: str, subtitle: str,
                     image_uri: str, matched_step: str) -> str:
    return f'''
<section style="position:relative;margin:0 0 16px 0;border-radius:12px;overflow:hidden;
                min-height:220px;display:flex;align-items:flex-end;
                background-image:linear-gradient(rgba(17,24,39,0.20), rgba(17,24,39,0.92)),url('{image_uri}');
                background-size:cover;background-position:center;color:#f3f4f6;
                font-family:Inter,system-ui,sans-serif;">
  <div style="padding:18px 22px;width:100%;">
    <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#fbbf24;">
      {_esc(run_id)}
    </div>
    <div style="font-size:26px;font-weight:700;line-height:1.1;margin-top:2px;">
      {_esc(title)}
    </div>
    {f'<div style="font-size:13px;color:#d1d5db;margin-top:6px;">{_esc(subtitle)}</div>' if subtitle else ''}
    <div style="margin-top:10px;font-size:10px;color:#9ca3af;font-family:ui-monospace,Menlo,Consolas,monospace;">
      portrait matched via <code>{_esc(matched_step)}</code>
    </div>
  </div>
</section>'''


def _plain_banner(*, run_id: str, title: str, subtitle: str, notes: list[str]) -> str:
    note_text = " · ".join(n for n in notes if n) if notes else ""
    return f'''
<section style="margin:0 0 16px 0;padding:18px 22px;border-radius:12px;
                background:linear-gradient(180deg,#1f2937,#111827);color:#f3f4f6;
                border:1px solid #374151;font-family:Inter,system-ui,sans-serif;">
  <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#fbbf24;">
    {_esc(run_id)}
  </div>
  <div style="font-size:26px;font-weight:700;line-height:1.1;margin-top:2px;">
    {_esc(title)}
  </div>
  {f'<div style="font-size:13px;color:#d1d5db;margin-top:6px;">{_esc(subtitle)}</div>' if subtitle else ''}
  {f'<div style="margin-top:10px;font-size:10px;color:#9ca3af;">{_esc(note_text)}</div>' if note_text else ''}
</section>'''


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _image_to_data_uri(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    suffix = p.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
    }.get(suffix, "application/octet-stream")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace('"', "&quot;").replace("'", "&#39;"))
