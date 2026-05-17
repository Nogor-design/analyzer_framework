# 001 — On-demand per-candidate report section picker

**Target AI:** `[codex]`
**Status:** Open
**Estimated effort:** ~2-3h

---

## Goal

Add a web page that lets the operator pick which report sections
to include in a per-candidate finalist report, then click a button
to regenerate that candidate's HTML with just those sections. The
ingest-time auto-generated report already exists (8-section base
set) — this page exposes the full ~127-section catalog and groups
the sections so the picker is scannable.

Reached via a "Customize report" link on each candidate row of the
existing decision dashboard.

## Why

The base auto report ships fast, no-bar-data sections that work
without market data staged. Some investigations need the heavier
bar-dependent sections (pattern engine, anchor interaction, large
candle) — but staging market data and re-running the heavy report
for every candidate by default is wasteful. The picker is opt-in
per-candidate.

Background: see `docs/designs/optimizer_known_issues.md` ("Auto-stage
market data alongside optimization sessions" — deferred). Bar-data
sections will silently render empty placeholders when bars aren't
staged; that's expected, not a bug.

## Files to touch

- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\optimizer_candidate_report.py` — already supports `sections=[...]` arg; **do not change its signature**. May add a small helper `group_sections_by_bucket()` if useful.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\app.py` — add 2 routes (page + POST). Insert near the existing `optimizer_candidate_report_page` route.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\templates\optimizer_candidate_report_builder.html` `(new)` — the picker page.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\templates\optimizer_decision_dashboard.html` — add "Customize report" link next to the existing `↗` report link on each candidate row.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\tests\web\test_optimizer_candidate_report_builder.py` `(new)` — at least one test that POSTs a section subset and verifies the rendered HTML only contains those sections.

## Acceptance criteria

- [ ] `GET /optimizer/sessions/<session_id>/candidates/<run_id>/report-builder` renders a page listing every section in `ta_foundation.reports.html.registry.SECTION_REGISTRY` as a checkbox, grouped into three buckets (see Bucket rules below).
- [ ] Sections that are in the current base auto set (`ta_foundation.web.optimizer_candidate_report.DEFAULT_FINALIST_SECTIONS`) are pre-checked.
- [ ] A "Rebuild this candidate's report" button POSTs to `/api/optimizer/sessions/<session_id>/candidates/<run_id>/report-builder` with the selected section ids; on success, server calls `build_candidate_report(session, run_id, sections=<selected>, images_dir=doc.god_images_dir or None)` and returns `{"result": <CandidateReportResult.to_dict()>}` JSON.
- [ ] After successful POST the page links to the regenerated HTML at `/optimizer/sessions/<session_id>/candidates/<run_id>/report` (opens new tab).
- [ ] Decision dashboard candidate rows show **two** links: existing `↗` to the report, plus new "Customize" to `/optimizer/sessions/<session_id>/candidates/<run_id>/report-builder`.
- [ ] No regressions: `python -m pytest src/ta_foundation/tests/web src/ta_foundation/tests/optimization src/ta_foundation/tests/parsers --ignore=src/ta_foundation/tests/web/test_conditional_promotion.py -q` stays green (currently 442 passing).

## Bucket rules

Group SECTION_REGISTRY ids into three buckets by prefix-matching the
section id. Render each bucket as a `<details>` block, in this order:

1. **Run-scoped (safe — always works)** — `run_*`, `daily_*`, `trade_*`, `equity_curve_*`, `exit_policy_*`, `apex_*`, `exec_card_*`, `analysis_chart_replica`, `optimization_overview`, `strategy_parameter_matrix`.
2. **Bar/market-data dependent (may render empty without staged bars)** — `pattern_engine_*`, `anchor_interaction_*`, `anchor_tp_sl_*`, `large_candle_*`, `tick_*`, `filter_*`, `*_discovery_*`, `regime_*`, `market_regime_*`, `horizon_*`, `trade_candle_overlay`.
3. **Multi-candidate (single-candidate report — these will render thin)** — `comparison_*`, `weekly_*`, `strategy_lifecycle_*`, `strategy_momentum_*`, `strategy_session_momentum_*`, `deployment_board_*`.

Anything that doesn't match → put in bucket 1. Add a one-line
description above each bucket explaining the caveat (especially that
bucket 2 needs staged bars and bucket 3 is multi-run-oriented).

## Gotchas

- **Section options.** `build_candidate_report` injects `template_path`, `images_dir`, `run_id`, `label`, `analysis_csv_path` into every section's options dict. Don't reinvent that — just pass through.
- **CSRF / auth.** This is a local-only Flask app; no auth, no CSRF tokens. Match the style of existing `optimizer_*` routes.
- **Style.** Match the existing dark theme used in `optimizer_session_detail.html` and `optimizer_decision_dashboard.html` (Tailwind CDN, `card` class, amber accent `#fbbf24`). Reuse the same `.card`/`.pill`/`.btn-primary`/`.btn-ghost` CSS pattern.
- **Don't ingest twice.** `build_candidate_report` does its own ingest; just call it. Don't pre-build a package.
- **JSON-safe everywhere** — `CandidateReportResult.to_dict()` already JSON-safes itself; trust it.
- **No DataFrames in `pkg.metadata`** (CLAUDE.md contract). You shouldn't need to touch metadata at all for this task, but flagging in case you go exploring.

## Out of scope

- Don't change `DEFAULT_FINALIST_SECTIONS` or the auto-ingest base set.
- Don't add new SECTION_REGISTRY entries.
- Don't add auto-stage-market-data logic — bar-dependent sections rendering empty is the documented behavior.
- Don't add a "preview" / live re-render in the picker; one full rebuild per click is fine.
- Don't add a "save my picks as a preset" feature yet — single-shot rebuild only.

## How to verify

```powershell
cd D:\Backup\projects\PythonProject\ta_foundation
python -m pytest src/ta_foundation/tests/web/test_optimizer_candidate_report_builder.py -q
python -m pytest src/ta_foundation/tests/web src/ta_foundation/tests/optimization src/ta_foundation/tests/parsers --ignore=src/ta_foundation/tests/web/test_conditional_promotion.py -q
```

Then UI smoke-test (web app must be running, NT optional):

```powershell
python -m ta_foundation.web.app --port 7734
```

Open `http://127.0.0.1:7734/optimizer/sessions/opt_5bab6a5ee1ea/decision`,
click "Customize" on F_001, uncheck everything in bucket 1 except
`run_kpi_cards`, click rebuild, confirm the new HTML opens and
contains only the KPI cards section.

## Notes for the executing AI

- Read [CLAUDE.md](../../CLAUDE.md) before starting. The 4-layer
  architecture and "sections are pure renderers, no IO" rule are
  load-bearing for this task.
- `ta_foundation.reports.html.registry.SECTION_REGISTRY` is a
  `dict[str, SectionDef]` where `SectionDef` has `id`,
  `default_title`, `render_fn`. Use `default_title` for the
  checkbox label.
- If `default_title` is empty (e.g. `exec_card_god_banner`), fall
  back to the section id verbatim.
- The PM (Claude) wrote this spec. If something feels missing or
  contradictory, stop and report back — don't guess.
