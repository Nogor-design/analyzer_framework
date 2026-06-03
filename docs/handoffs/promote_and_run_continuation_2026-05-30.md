# Handoff: Shortlist → Promote & Run (one-click NT batch)

**Date:** 2026-05-30
**Status:** Shipped end-to-end. Tests green. No production verification yet (needs NT running with `BatchStrategyOptimizerAddOn` authorized).
**Branch:** `CandleDiscovery`
**Last commits before this session:** `68ec01c web: lineage inspector — "How candidate was discovered" page`

---

## Why this work exists

The web optimizer already had a session-scoped **shortlist** (server-side
"shopping cart" of interesting rows from any stage). Items from
refinement stages (`stage_1`, `stage_2`, …) showed status `pending`
because no fixed-backtest XML had been stamped for them yet — they
couldn't be backtested OOS.

The operator's desired flow: **pick rows → one click → templates
stamped, NinjaTrader runs them, reports built, dashboard updates**. No
folder dragging, no Refine flow (which fails on multi-stage selections),
no manual NT import step. The system already has the IPC bridge — we
just needed to wire shortlist → that bridge.

This handoff documents what was built. The "what's next" section flags
open items if you continue.

---

## Architecture summary

Three parallel directory trees mirror the existing `final_backtest` layout
so the per-candidate report renderer, the deployment-package builder,
and the decision dashboard don't have to know that promotion exists:

```
<session>/
├── generated_templates/promoted/
│   ├── P_NNN.xml                       # one per promoted shortlist row
│   └── recipe_template_manifest.json   # mirrors final_backtest schema
├── nt_output/promoted/<P_NNN>/         # NT writes here via AddOn
├── parsed_results/promoted/
│   ├── scored_rows.{csv,json}          # enriched rows, stage_id="promoted"
└── deployment_package/
    └── promoted_handoff/
        ├── named_backtest_templates/recipe/<P_NNN>.xml  # XML mirror
        ├── nt8_backtest_results/<P_NNN>/                # NT output mirror
        └── promoted_review/
            └── evaluated_candidates.json                # decorated w/ source pointers
```

`per_candidate_reports/` is shared between F_NNN finalists and P_NNN
promoted rows. The Decision Dashboard merges
`final_backtest_review/evaluated_candidates.json` +
`promoted_handoff/promoted_review/evaluated_candidates.json` into one
ranked list with `kind={finalist,promoted}` and (for promoted) a
`source={stage_id, candidate_id}` back-pointer to the originating
shortlist row.

### One-click flow (when operator clicks "Promote & Run")

1. **`POST /api/optimizer/sessions/<id>/shortlist/promote`** runs:
   - `promote_pending(session)` — stamps `P_NNN.xml` for each pending
     shortlist row. Idempotent: finalists skipped, already-promoted
     skipped. Marks shortlist items with `promoted_run_id` / `promoted_at`.
   - `start_promoted_run(session)` — wipes `nt_output/promoted/`,
     writes the IPC command to `C:\temp\nt8_command.json`:
     ```json
     {
       "action": "RunBatch",
       "runId": "promoted_YYYYMMDD_HHMMSS",
       "sourceFolder": "<session>/generated_templates/promoted",
       "destFolder":   "<session>/nt_output/promoted",
       "closeTempTabs": true,
       "instrument": "NQ 06-26",
       "timeoutSeconds": <from session document>
     }
     ```
   - Returns `{result, shortlist, run}`. UI updates sidebar immediately,
     starts polling.

2. **JS background polling** (`OptimizerShortlist._pollPromotedRun`):
   - Hits `GET /api/optimizer/sessions/<id>/shortlist/promote/status`
     every 10s.
   - `advance_promoted_run` reads `C:\temp\nt8_status.json` (scoped to
     our runId) for the AddOn heartbeat. Falls back to counting
     `Summary.csv` files under the dest folder when no heartbeat is
     present.
   - On `complete` (declared by AddOn OR all expected templates have
     `Summary.csv`): runs `load_promoted_results(session)` (mirrors NT
     output → handoff, writes promoted review, decorates rows with
     source pointers) and `build_all_candidate_reports(session)` (renders
     per-candidate HTML for every F_NNN and P_NNN). Marks
     `promoted_run.json` state=`complete`.
   - On `failed` / `cancelled`: marks accordingly with `last_error`.

3. **Page reload resilience**: `OptimizerShortlist._resumePollIfActive`
   fires on init; if `promoted_run.json` shows a non-terminal state, the
   poll picks back up automatically.

4. **Cancellation**: `POST /shortlist/promote/cancel` removes
   `C:\temp\nt8_command.json` (only if it still belongs to our runId)
   and marks the record cancelled.

---

## Files changed / added

### New modules

| Path | Purpose |
|---|---|
| `src/ta_foundation/web/optimizer_promotion.py` | `promote_pending`, `PromotionResult`, `PromotedTemplateEntry`, `_resolve_promoted_params`, P_NNN assignment, manifest append-merge |
| `src/ta_foundation/web/optimizer_promotion_results.py` | `load_promoted_results`, `_mirror_promoted_results`, decorates `evaluated_candidates.json` with `source_stage_id`/`source_candidate_id` |
| `src/ta_foundation/web/optimizer_promotion_run.py` | `start_promoted_run`, `advance_promoted_run`, `cancel_promoted_run`, `PromotedRunRecord`, persistence at `<session>/promoted_run.json` |

### Modified modules

| Path | What changed |
|---|---|
| `src/ta_foundation/web/optimizer_shortlist.py` | Schema bump v1→v2; added `promoted_run_id`/`promoted_at` to `ShortlistItem`/`ResolvedItem`; added `ITEM_STATUS_PROMOTED`, `promoted_count`, `mark_promoted()` |
| `src/ta_foundation/web/optimizer_lineage.py` | Exposed `extract_params_from_row` publicly (kept `_extract_params` alias for back-compat) — promotion reuses Pantheon display→prop normalization |
| `src/ta_foundation/web/optimizer_decision_dashboard.py` | Loads `promoted_handoff/promoted_review/evaluated_candidates.json` alongside the finalist review; `CandidateRow` gained `kind` (default `finalist`) and `source` fields; P_NNN rows participate in same adjusted-rank sort; promoted rows now get `report_url` resolution too |
| `src/ta_foundation/web/optimizer_candidate_report.py` | `build_candidate_report` uses new `_resolve_candidate_results_dir` (final → promoted fallback); `_find_template_path_for_run_id` recognizes `P_NNN` via new `_find_promoted_template_path`; `build_all_candidate_reports` walks both result trees |
| `src/ta_foundation/web/optimizer_deployment_package.py` | `build_deployment_package` now calls `load_promoted_results` when a promoted manifest exists; surfaces `promoted_template_count`, `promoted_row_count`, `promoted_review_dir`, `files.promoted_review` in `manifest.json`. Never blocks final-backtest path. |
| `src/ta_foundation/web/app.py` | `POST /shortlist/promote` now dispatches by default (`{"dispatch": false}` to opt out); new `GET /shortlist/promote/status` (advances the run) and `POST /shortlist/promote/cancel` |
| `src/ta_foundation/web/templates/_shortlist_sidebar.html` | New "Promote & Run" button + promoted pill + CSS |
| `src/ta_foundation/web/templates/optimizer_decision_dashboard.html` | `pill-promoted` pill in status cell + source-pointer line under `run_id` for promoted rows |
| `src/ta_foundation/web/static/optimizer_shortlist.js` | `promotePending` now POSTs with `dispatch:true`, then starts background poll; `_pollPromotedRun` (every 10s until terminal); `_resumePollIfActive` on init; `cancelPromotedRun` |

### New test files

| Path | Coverage |
|---|---|
| `src/ta_foundation/tests/web/test_optimizer_promotion.py` | 13 tests: P_NNN assignment, param resolution merge order (base_matrix fixed → pin → row param_*), happy path stamp+manifest+mutate shortlist, idempotent re-click, error path missing scored_rows, finalist skip, missing-seed raises, API round-trip |
| `src/ta_foundation/tests/web/test_optimizer_promotion_results.py` | 12 tests: mirror copies/skips/overwrites, resolver final→promoted fallback, promoted template-path resolver, batch builder walks both dirs (monkeypatched) |
| `src/ta_foundation/tests/web/test_optimizer_promotion_run.py` | 19 tests: IPC payload shape, state transitions, addon failure/cancellation, post-pipeline triggered on completion, idempotent terminal, runId scoping, dispatch-skip mode, all 3 endpoints |

### Extended tests

| Path | What was added |
|---|---|
| `src/ta_foundation/tests/web/test_optimizer_shortlist.py` | v1 read-compat, v2 write, `mark_promoted` mutation, promoted status flip |
| `src/ta_foundation/tests/web/test_optimizer_decision_dashboard.py` | Promoted-row merge (kind/source), promoted-only dashboard renders, missing-review backward-compat |

---

## Test status

- **All new + extended tests pass.** Full suite at session end: **538
  passed, 9 failed**.
- The 9 failures are **pre-existing on this branch** (verified by
  stashing the changes — they fail identically on the baseline). All in
  `test_optimizer_template_writer.py` /
  `test_optimizer_strategy_catalog.py` / `test_optimizer_routes.py`,
  unrelated to anything in this work (template XML tag emission, NT
  property declaration parsing, deployment-package decision-state
  defaults).

To run just the work from this session:

```powershell
python -m pytest src/ta_foundation/tests/web/test_optimizer_promotion.py `
                 src/ta_foundation/tests/web/test_optimizer_promotion_results.py `
                 src/ta_foundation/tests/web/test_optimizer_promotion_run.py `
                 src/ta_foundation/tests/web/test_optimizer_shortlist.py `
                 src/ta_foundation/tests/web/test_optimizer_decision_dashboard.py -v
```

---

## Reference: NT IPC contract (used by `start_promoted_run`)

From `NINJATRADER_INTEGRATION_RUNBOOK.md` and
`optimizer_recipe_runner.py:69`:

- **Command file:** `C:\temp\nt8_command.json` — Python writes, AddOn
  reads. Watched by `BatchStrategyOptimizerAddOn` at
  `D:\ninjatraderOptimizer\` (outside repo, must be compiled +
  authorized in NT).
- **Status file:** `C:\temp\nt8_status.json` — AddOn writes, Python
  reads. Heartbeat scoped by `runId`. States we recognize: `running`,
  `complete`/`completed`/`success`, `failed`/`error`,
  `cancelled`/`canceled`.
- **Prereq:** NT must be running with the AddOn authorized. See
  `NINJATRADER_INTEGRATION_RUNBOOK.md` for `ensure-nt-ready` and the
  60–150s startup wait.
- **Recipe runner reuses the same contract** (see
  `start_recipe_stage_run` in `optimizer_recipe_runner.py`); promotion
  uses its own persistence file (`promoted_run.json`) so the two
  pipelines don't collide on `recipe_run.json`.

---

## Things to know that aren't obvious from code

1. **`promote_pending` always requires a saved recipe** (uses
   `recipe.base_matrix` and `recipe.stages.pin`). If a future caller
   needs to dispatch a promoted run without promoting anything new (zero
   pending), the recipe is still loaded — that's by design today.
2. **Each click of "Promote & Run" wipes `nt_output/promoted/` and
   re-dispatches every P_NNN in the manifest**, not just new ones. This
   is intentional for first ship — simpler, correct. If re-running
   already-completed P_NNN templates becomes painful, gate dispatch on
   "any template lacks a `Summary.csv`".
3. **Shortlist schema v2 is forward-compatible.** v1 files (no
   `promoted_run_id`/`promoted_at`) load cleanly with those fields
   defaulting to `None`. Test `test_load_v1_shortlist_file_is_still_readable`
   pins this.
4. **`extract_params_from_row`** is the canonical display→prop name
   normalizer (e.g. `Start_Time_(HH)` → `StartTimeH`) via the Pantheon
   strategy's `_DISPLAY_TO_PROP`. Non-Pantheon strategies fall through
   to identity. This is what makes `param_*` columns from
   `scored_rows.json` map cleanly to the seed XML's strategy element
   names. Don't re-implement it.
5. **P_NNN reuses the F_NNN per-candidate HTML pipeline** via a tiny
   fallback in `_resolve_candidate_results_dir` (final → promoted). The
   renderer doesn't care which kind it's rendering. Same for the
   template-path lookup the banner uses.
6. **Promoted rows in the dashboard rank against finalists in the same
   adjusted-score sort.** A strong P_NNN can out-rank a weak F_NNN. The
   only difference is the `kind` field and the `pill-promoted` decoration.
7. **Deployment package builder's promoted hook is wrapped in a broad
   try/except** so a partial promoted layout never blocks the
   final-backtest path. Failures surface as notes in `manifest.json`.

---

## Open items / suggested next work

Ordered roughly by user value, not effort:

1. **Production smoke test against `opt_5bab6a5ee1ea` or a clone.** The
   one-click flow has never run against a real NT instance. Sequence:
   (a) save a shortlist with 2-3 pending rows, (b) click Promote & Run,
   (c) confirm `C:\temp\nt8_command.json` written, (d) wait for NT, (e)
   confirm `per_candidate_reports/P_001.html` exists and renders, (f)
   confirm Decision Dashboard shows P_NNN alongside F_NNN.

2. **Visible run status in the sidebar.** Today the run state lives in
   `promoted_run.json` and the JS console; the sidebar only shows
   pending/promoted/stale pills. Add a small "Run: requested · 0/3" or
   "Run: complete · 3 reports" line in the sidebar footer that updates
   on each poll. The state is already in the API response — UI work
   only.

3. **Skip dispatch when no new work exists.** If `promote_pending` adds
   zero new templates AND every existing P_NNN already has a
   `per_candidate_reports/<P_NNN>.html`, return early without firing
   NT. Currently we re-run NT every click. ~20 lines in
   `api_optimizer_shortlist_promote` or `start_promoted_run`.

4. **Rescind promotion** — a way to take a P_NNN back out (e.g., the
   operator picked the wrong row). Drops the manifest entry, deletes
   the XML, clears `promoted_run_id` on the shortlist item.

5. **Per-row OOS date override.** Today the promoted run uses the
   session's `oos_from_date`/`oos_to_date` from the document. If the
   operator wants to OOS-test a row over a different window, they have
   to edit the session. Could be exposed as a per-row attribute on the
   shortlist item.

6. **Promoted leaderboard.** `optimizer_leaderboard.py` currently only
   reads finalist + stage rows. Could be extended to read
   `promoted_handoff/promoted_review/` so promoted candidates show up
   on the leaderboard alongside finalists with `kind` labeling.

---

## Where to start reading if you're picking this up cold

1. **`CLAUDE.md`** — architecture invariants (4-layer model, tz-aware
   timestamps, `pkg.metadata` JSON-safety, etc.).
2. **`docs/AI_REPO_INDEX.md`** — auto-generated inventory. Search for
   `optimizer_promotion` to see the new modules in context.
3. **`NINJATRADER_INTEGRATION_RUNBOOK.md`** — IPC bridge details, NT
   startup prereqs, troubleshooting.
4. **`src/ta_foundation/web/optimizer_promotion_run.py`** — the
   one-click pipeline orchestrator. Reading it top to bottom takes ~5
   minutes.
5. **`src/ta_foundation/web/optimizer_recipe_runner.py:69`
   (`start_recipe_stage_run`)** — the pattern we mirror. Useful for
   understanding why promotion has its own persistence.
6. **`src/ta_foundation/tests/web/test_optimizer_promotion_run.py`** —
   the test that explains the lifecycle better than the code does.

---

## Conventions to honor

- **Frozen dataclasses + `to_dict()` everywhere** in `web/`. Mirror
  `optimizer_leaderboard.py` style.
- **Graceful degradation, not exceptions**, for missing optional
  artifacts (missing manifest, no NT output yet, partial review). Use
  `notes`/`errors` lists. Only raise for "session is fundamentally
  misconfigured" (no recipe, no seed XML on disk).
- **Never mutate `pkg.metadata` to non-JSON-safe values** — no
  DataFrames, callables, registries (see CLAUDE.md).
- **No new CLI flags for report rendering** — that lives in
  `report.yaml`. CLI flags are for ingest behavior only.
- **Tests live under `src/ta_foundation/tests/web/`** with the pattern:
  `set_storage_root(tmp_path/sessions)`, `create_session(...)`, write
  synthetic JSON files directly, assert on `to_dict()` outputs.
