from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Any

from ta_foundation.reports.html.theme import default_css


@dataclass
class HtmlSection:
    """
    A section renders HTML given a context dict.
    """
    id: str
    title: str
    render_fn: Callable[[dict], str]
    options: Dict[str, Any] = field(default_factory=dict)


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

            body = s.render_fn(section_ctx)

            parts.append(
                f"""
                <section class="card">
                  <h2>{_esc(s.title)}</h2>
                  {body}
                </section>
                """
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
  <div class="wrap">
    <div class="header">
      <div>
        <div class="title">{_esc(self.report_title)}</div>
        <div class="subtitle">Generated: <span class="mono">{_esc(generated_at)}</span></div>
      </div>
      <div class="row">
        <span class="pill">Timezone: America/Denver</span>
        <span class="pill">Embedded images</span>
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
