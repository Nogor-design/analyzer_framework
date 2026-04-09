# ta_foundation — Reporting Sections

This document describes how report sections work and where to find the authoritative registry.

---

## 1) Source of truth

Sections are registry-driven.

- Registry file: `src/ta_foundation/reports/html/registry.py`
- Each section entry maps:
  - `id`
  - default title
  - `render_fn`

Use the registry as the authoritative list of available sections.

---

## 2) Section runtime contract

Report flow:

```text
report.yaml
  ↓ load_report_config(s)
build_report_from_config(packages, cfg, market, optimization_store)
  ↓ HtmlReportBuilder.build(context)
section.render_fn(section_ctx)
```

Every section receives context with at least:
- `ctx["packages"]`
- `ctx["market"]`
- `ctx["report_config"]`
- `ctx["section_id"]`
- `ctx["section"]`
- `ctx["options"]` (section-local options)
- `ctx["all_options"]` (full merged YAML)

Expected section pattern:

```python
def render_my_section(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    ...
```

---

## 3) Section hard boundaries

Sections must:
- render HTML only,
- gracefully handle missing data,
- read behavior from `ctx["options"]`.

Sections must not:
- read files from disk,
- call ingest/pipeline,
- parse YAML,
- run heavy analytics inline,
- bypass the registry.

If new derived data is needed, compute it in analysis/pipeline first and pass through context.

---

## 4) Images and tables

- Embed charts as base64 data URIs.
- Do not write image files during report rendering.
- Keep table rendering resilient to absent columns.

---

## 5) Section families in this project

The codebase currently includes a large set of sections (100+), including:
- core run/comparison views,
- drawdown/survival diagnostics,
- discovery sections (candle/MA/ORB/BB/LCR/levels/etc.),
- strategy discovery and validation suites,
- pattern engine outputs,
- large-candle excursion (base, discovery, findings),
- optimization and ancillary diagnostics.

For exact IDs and titles, consult `registry.py`.

---

## 6) Adding a new section

1. Create file under `src/ta_foundation/reports/html/sections/`.
2. Implement render function using context contract.
3. Register in `src/ta_foundation/reports/html/registry.py`.
4. Enable/order via report YAML `sections:`.

Prefer YAML options over hardcoded behavior.
