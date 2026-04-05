from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Any, Optional

from ta_foundation.reports.html.theme import default_css

RenderFn = Callable[[dict[str, Any]], str]


@dataclass
class HtmlSection:
    """
    A section renders HTML given a context dict.
    """
    id: str
    title: str
    render_fn: RenderFn
    options: dict[str, Any] = field(default_factory=dict)
    # options: Dict[str, Any] = field(default_factory=dict)


class HtmlReportBuilder:
    def __init__(self, report_title: str, sections: list[HtmlSection]) -> None:
        self.report_title = report_title
        self.sections = sections

    def build(self, context: dict) -> str:
        css = default_css()
        generated_at = datetime.now().isoformat(timespec="seconds")

        parts: list[str] = []
        for s in self.sections:
            # ---- SECTION CONTEXT INJECTION (CRITICAL FIX) ----
            section_ctx = dict(context)
            section_ctx["section_id"] = s.id
            section_ctx["section_options"] = s.options or {}
            section_ctx["section"] = {
                "id": s.id,
                "title": s.title,
                "options": s.options or {},
            }

            # ✅ NEW: canonical options key used by many sections (including run_snapshot_clipboard)
            section_ctx["options"] = s.options or {}

            body = s.render_fn(section_ctx)

            parts.append(
                f"""
                <section id="{_esc(s.id)}" class="card">
                  <h2>{_esc(s.title)}</h2>
                  {body}
                </section>
                """
            )

        nav_links = "".join(
            f'<a href="#{_esc(s.id)}" style="color:#64748b;font-size:13px;'
            f'white-space:nowrap;padding:4px 0;text-decoration:none">'
            f'{_esc(s.title)}</a>'
            for s in self.sections
        )

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{_esc(self.report_title)}</title>
  <style>{css}</style>
</head>
<body>
  <!-- Sticky top nav -->
  <nav style="position:sticky;top:0;z-index:100;background:#1e2a3a;
              border-bottom:1px solid #2d3f55;padding:0 24px;">
    <div style="max-width:1400px;margin:0 auto;display:flex;align-items:center;
                gap:20px;height:44px;overflow-x:auto;">
      <span style="color:#ffffff;font-size:14px;font-weight:700;
                   white-space:nowrap;margin-right:8px">{_esc(self.report_title)}</span>
      {nav_links}
    </div>
  </nav>

  <div class="wrap">
    <div class="header">
      <div>
        <div class="title">{_esc(self.report_title)}</div>
        <div class="subtitle">Generated: <span class="mono">{_esc(generated_at)}</span></div>
      </div>
      <div class="row">
        <span class="pill">America/Denver</span>
        <span class="pill">{len(self.sections)} sections</span>
      </div>
    </div>

    <div class="grid">
      {''.join(parts)}
    </div>
  </div>
</body>
</html>
"""


def _esc(s: object) -> str:
    t = "" if s is None else str(s)
    return (
        t.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
