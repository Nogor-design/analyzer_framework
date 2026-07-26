# Discovery Web UI — Design

Status: in-progress build. Step 1 complete (instrument registry).

## Purpose

A new top-level page in the local web app for running the strategy discovery
funnel without hand-editing YAML. The user picks parameters in forms; the UI
generates the stage YAML, dispatches the existing
`python -m ta_foundation.cli.main --report-config <yaml>` job, parses results
into a structured summary, and lets the user one-click promote winning combos
into the next stage.

This is a sibling capability to Backtest Reports, Prediction, Strategy
Templates, and Strategy Discovery (see `docs/AI_CAPABILITY_MAP.md`). It does
not replace the CLI flow; it drives it.

## Audience

Designed for a beginner trader who has never edited a YAML file. Every
parameter has plain-language labels, default values with rationale, a
`?` modal with diagram + formula, and a permanent glossary panel. Result
pages lead with verdicts ("Strong edge — consider trading") before showing
raw numbers.

## Architectural fit

| Concern | Approach |
|---|---|
| Job dispatch | Reuse `web/jobs.py` `JobManager` |
| YAML build | New `web/discovery_builder.py` mirroring `web/report_builder.py` |
| Stage definitions | New `web/discovery_stages.py` (declarative) |
| Result parsing | New `analysis/strategy_discovery/summary_export.py` writing a sidecar JSON next to each report |
| Persistence | Per-browser session JSON under `.ta_artifacts/web_discovery/sessions/<id>/` |
| Frontend | New `web/templates/discovery.html` + static JS |

Contracts respected:
- New report behavior stays in YAML, not CLI flags. (`CLAUDE.md`)
- Sections remain pure HTML renderers — the sidecar is written by
  the analysis modules, not from inside a section.
- `pkg.metadata["derived"]` stays JSON-safe. The sidecar is its own file,
  not an addition to `metadata`.

## The 6-stage funnel (mirrors `discovery/README.md`)

```
1 Quick Scan → 2 Candle → 3 Zones/Levels → 4 NY Open → 5 Momentum → 6 Validate
```

Each stage corresponds to one of the existing `discovery/0?_*.yaml` files.
Stage 6 is special: it aggregates promoted combos from stages 2–5 into a
walk-forward IS/OOS validation run.

A separate sibling page hosts the Large Candle Excursion event study
(`discovery/large_candle_excursion.yaml`) — same widget toolkit, different
result shape.

## Sidecar JSON schema

Every stage run writes `discovery_summary.json` next to its HTML report.
The UI reads this to render Setup Cards and to compute next-stage defaults.
See "Sidecar JSON schema" in the v2 design plan in conversation history;
schema is versioned (`schema_version: 1`).

Key fields per ranking entry:

- `tier` — pre-computed verdict (`most_robust`, `high_quality`, `solid`,
  `marginal`) with criteria-met list. Owned by the backend so UI rendering
  stays consistent across stages.
- `explain` — three short strings (what_it_trades, when_it_works, risks)
  used directly on Setup Cards.
- `promote_payload.yaml_overrides` — exact YAML deltas the next stage needs.
  The UI does not construct YAML deltas itself.

## Disk persistence

Layout per session:

```
.ta_artifacts/web_discovery/sessions/<session_id>/
    session.json       # project context, current stage, label, form values
    stage_runs.json    # append-only list of completed runs
    promotions.json    # rows the user has promoted between stages
    stage_yaml/        # generated YAMLs from each run, keyed by timestamp
```

Session ID lives in a long-lived cookie (`ta_discovery_session_id`).
Auto-save on every form change (debounced 500 ms). Sessions are listable,
renameable, and resumable from a sessions index page.

## Instrument picker

`src/ta_foundation/web/discovery_instruments.py` is the source of truth.
Canonical entries cover NQ, MNQ, ES, MES, YM, RTY, M2K, CL, GC, MGC, NG,
6E. Each entry carries: tick_size, tick_value, point_value, RTH session
(in Denver local time, in the same shape as `session_filter`),
suggested ATR period, default contract hint, notes. Custom instruments
can be registered through the API and persist with the session.

Selecting an instrument rewrites every generated stage YAML's
`tick_size`, `tick_value`, and default `session_filter`.

### Resolved — NQ tick value fixed in legacy discovery YAMLs

The legacy YAMLs in `discovery/*.yaml` previously set `tick_value: 12.50`
for NQ-targeted runs. **`12.50` is the ES tick value**, not NQ. NQ is
`$5.00` per tick (1 point = $20, 4 ticks per point). The codebase already
encoded the correct `$5.00` for NQ in
`reports/html/sections/apex_drawdown_survival_profile.py:101`, and the new
instrument registry uses `5.00`.

Fixed: `discovery/01_quick_scan.yaml` (2 occurrences),
`03_levels_regions.yaml`, `04_ny_open.yaml`, `05_orb_momentum.yaml`,
`06_validate.yaml`. Caveat: any NQ discovery report rendered before this
fix understates dollar-denominated drawdown and expectancy by 2.5×.
PF and tick-counted metrics are unaffected.

## Build progress

| Step | Status |
|---|---|
| 1. Instrument registry + endpoint + tests | done |
| 2. Glossary YAML + endpoint + tests | done |
| 3. Stage definitions module + endpoints + tests | done |
| 4. YAML builder + parity snapshot tests + preview endpoint | done |
| 5a. Sidecar schema + tier classifier + builder + tests | done |
| 5b. Wire sidecar writer into CLI run | done |
| 6. DiscoverySession persistence + tests | done |
| 7. Flask routes for discovery | done |
| 8. Frontend skeleton (Stage 1) | done |
| 9. Shared form widgets | done |
| 10. Run & Watch pane | done — JobManager streaming, cancel, /log + /cancel routes, live-tail panel |
| 11. Results & Promote pane | done — sidecar load, tier-grouped Setup Cards, one-click Promote, next-stage recs |
| 12. Stages 2–5 | done — sub-signal multi-select per family (candle patterns, LCR signal types, MA/BB/breakout/etc. signals) |
| 13. Stage 6 validation view | done — promotions checklist replaces family/common sections; selected overrides deep-merged |
| 14. Onboarding + Glossary panel + "Stuck?" help | done — `static/discovery/help.js` adds a localStorage-gated tour, slide-in glossary with search/see-also, and per-stage Stuck? panel with curated terms |
| 15. Sessions index page | done — `/discovery/sessions` lists every saved session with rename/delete; `/discovery/sessions/<id>/resume` sets the cookie and 302s to `/discovery` |
| 16. Expert mode | done — header toggle (localStorage `ta_discovery_expert_mode_v1`) reveals an Expert overrides JSON section in the configure form; the JSON is deep-merged on top of form-built overrides for both preview and dispatch |
| 17. Large Candle Excursion sibling page | done — `/discovery/lce` reuses `discovery.html` with `lce_only=True`, hides the funnel sidebar, and force-selects the LCE stage |
