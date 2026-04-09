# Contributing to ta_foundation

This is a mature production framework. Architectural consistency is more important than feature velocity.

Before changing code, read:
- `ARCHITECTURE.md`
- `REPORTING_SECTIONS.md`
- `PROJECT_CONTEXT.md`

---

## 1) Hard rules (do not break)

### Layering
- Parsers: parse + normalize artifacts only.
- Pipeline: assemble `AnalysisPackage` + `MarketDataStore`, route artifacts.
- Analysis: compute reusable derived metrics.
- Report sections: pure HTML rendering from context.

**Sections must never**:
- read files,
- call ingest/pipeline,
- parse YAML,
- run heavy analytics inline.

### Time policy
- Canonical timezone is `America/Denver`.
- Canonical datetimes must be tz-aware.
- Never mix naive and tz-aware datetimes.

### Data ownership
- Run-scoped artifacts attach to `AnalysisPackage` (`run_id != None`).
- Shared market artifacts attach to `MarketDataStore` (`run_id = None`).
- Never duplicate shared market data into each run package.

### Derived data contract
- Attach derived outputs under:
  - `pkg.metadata["derived"][...]`
- Do not create ad-hoc top-level package attributes.

---

## 2) Where new work belongs

| Feature type | Location |
|---|---|
| New file parser | `src/ta_foundation/parsers/...` |
| New derived metric / analytics | `src/ta_foundation/analysis/...` |
| Ingest behavior change | `src/ta_foundation/core/pipeline.py` |
| HTML visualization | `src/ta_foundation/reports/html/sections/...` |
| Section order/options | report YAML (`report.yaml` / report configs) |

If unclear, stop and decide layer first.

---

## 3) Parser contribution checklist

- Implement parser interface from `parsers/base.py`:
  - `can_parse(path, header) -> bool`
  - `parse(path, run_id) -> ParsedArtifact`
- Normalize datetime + numeric fields.
- Ensure timezone policy compliance.
- Return shared artifacts with `run_id=None`.
- Register parser in CLI registry setup.

Never perform cross-run analysis in parser code.

---

## 4) Analysis contribution checklist

- No HTML rendering.
- No file I/O.
- Operate on package/store/dataframes already loaded.
- Return/attach derived outputs to metadata contract.
- Preserve existing behavior for unaffected reports.

---

## 5) Report section contribution checklist

Section signature pattern:

```python
def render_my_section(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
```

Rules:
- Use `ctx["options"]` for section config.
- Keep section rendering pure (HTML only).
- Embed figures as base64 data URIs (no image file writes).
- Register section in `src/ta_foundation/reports/html/registry.py`.

---

## 6) Config behavior rules

- Runtime report behavior must come from report YAML.
- Do not introduce CLI flags for report display behavior.
- Do not hardcode section ordering/behavior in code if it belongs in config.

---

## 7) Pre-commit verification

Run relevant checks for your change scope (examples):
- targeted unit tests,
- report generation smoke test,
- lint/type checks if configured.

Minimum manual checks:
- timezone contract preserved,
- no shared/run ownership violations,
- derived outputs attached under metadata,
- no section-side data loading.

---

## 8) Change philosophy

- Smallest possible change set.
- No opportunistic refactors unless requested.
- Match existing naming/style patterns.
- Prefer extending existing helpers/orchestrators over introducing new layers.
