# Recipe Matrix Optimizer — User-Controlled Loop Design

Status: proposed design, created 2026-05-25.

Supersedes the autonomous-by-default orchestration model in
`docs/designs/recipe_matrix_optimizer_design.md` for the interactive web
experience. The engine primitives from that doc are reused; the difference is
**who drives stage-to-stage advancement**. In the original design the
orchestrator auto-selected winners and auto-advanced. In this design **the user
drives every gate**: filter, select, review, decide, run, repeat.

Related inputs:

- `docs/designs/recipe_matrix_optimizer_design.md` (engine + storage layout)
- `docs/designs/ninjatrader_optimizer_web_ui.md`
- `src/ta_foundation/web/templates/optimizer_recipe.html`
- `src/ta_foundation/web/optimizer_recipe_orchestrator.py`
- `src/ta_foundation/web/optimizer_recipe_selection.py`
- `src/ta_foundation/web/optimizer_recipe_results.py`
- `src/ta_foundation/web/app.py` (recipe routes)

---

## 1. Goals and Non-Goals

### Goals

1. Make the loop **fully user-controlled**. No automatic candidate selection,
   no automatic stage advancement. The system only acts when the user clicks.
2. Let the user **filter, select, and accumulate** candidates across multiple
   filter slices and even across stages into a persistent "review basket".
3. Give the user a dedicated **Final Review page** where each reviewed
   candidate is dispositioned to one of three destinations: **Refinement**,
   **Final Output**, or **Python Analysis**.
4. Make **Refinement** open a setup page that looks and behaves exactly like
   Stage 1 setup, but pre-seeded with refinement parameters derived from the
   originating run.
5. Make **Run** an explicit, per-stage action that generates templates from the
   user's chosen candidates and dispatches them to NinjaTrader — then stops.
   Results return to Candidate Results, and the loop repeats until the user has
   a final list.
6. **Retire the Run Dashboard** as the loop driver. The autonomous state
   machine and auto-advance are removed from the interactive path.

### Non-Goals

- Re-architecting the NinjaTrader execution bridge or RunBatch command format.
- Changing the standard (non-recipe) optimizer.
- Replacing the recipe planner / template writer / result parser. They are
  reused unchanged except where noted.
- Fully specifying the Python Analysis destination's analytics. That hook is
  scoped in section 7 and intentionally left phased.

---

## 2. Current State (as built)

This is what exists today, so the design below is a delta, not a rewrite.

### 2.1 UI surfaces (`optimizer_recipe.html`, ~3236 lines)

A single-page app with five tabs:

| Tab | id | Purpose | Status |
|---|---|---|---|
| Recipe Setup | `setup` | Strategy, seed, contract, dates | Keep |
| Stage 1 Setup | `roles` | Matrix axes, parameter roles, selection rules, refine stages | Keep + reuse for refinement |
| Stage Plan | `plan` | Combination/scope preview | Keep |
| Run Dashboard | `run` | Autonomous orchestration cockpit | **Retire / replace** |
| Candidate Results | `results` | Filter, slice, select, promote | Keep + rework actions |

Key client state (top of the `<script>` block):

- `UI_STAGES` — array of stage configs; each has `id`, `label`, `roles`
  (paramName → role + bounds), and `selection` rules. Stage 0 is "First run".
- `SELECTED_CANDIDATE_IDS` — a `Set` of currently-checked candidate IDs.
  **Cleared on every stage load** (`loadStageResults`, line ~2192).
- `FINAL_SOURCE_STAGE_ID` — remembers which stage fed "Send to Final".
- `SUGGESTED_REFINEMENT_PARAMS` — AI suggestion store.

### 2.2 Candidate Results actions (today)

- `configureSelectedForRefinement()` (line ~2903): saves selection with
  intent `refine`, calls `ensureRefinementStageFor()` to append a refinement
  stage to `UI_STAGES` (inheriting prior optimized params as `refine`), saves
  the recipe, and switches to the `roles` tab. **This is already ~80% of the
  desired "go to a Stage-1-like refine setup" behavior.**
- `sendSelectedToFinalOutput()` (line ~2928): saves selection with intent
  `final`, then jumps to the Run Dashboard. **No list of final picks is ever
  shown back to the user.**
- `applySuggestedRefinementsAndRun()` (line ~2689): applies AI suggestions to a
  new refine stage then routes to `roles`.

### 2.3 Backend selection endpoint

`POST /api/optimizer/sessions/<id>/recipe/stages/<stage_id>/select`
(`app.py` line ~2280) already accepts `intent ∈ {refine, final}` and writes:

- Per-stage: `parsed_results/<stage_id>/selected.json` (+ `.csv`) for refine,
  or `final_selected.json` (+ `_rejected`) for final.
- Session root: `recipe_selection.json`/`.csv` for refine, or
  `recipe_final_selection.json`/`.csv` for final.

So the persistence for a "final list" **already exists**; it is simply never
read back into the UI.

### 2.4 The autonomous conflict

`RecipeRunOrchestrator.advance_once()`
(`optimizer_recipe_orchestrator.py` line ~118) ingests stage output, then calls
`select_recipe_stage_candidates()` to **auto-pick winners by guardrails** and
auto-generates the next child stage / final backtest. This ignores the user's
manual `/select` picks. The Run Dashboard's "Auto advance" checkbox
(`_optimizer_recipe_panel.html` line 17) polls every 4 s and fires
`recipeAction("advance")` (`maybeAutoAdvanceRecipe`, line ~2095). This is the
"legacy auto-advance" behavior that must be removed from the interactive path.

### 2.5 Known bug — AI suggestion on boolean params

`renderSuggestedRefinements()` (line ~2538) iterates every param whose role is
`optimize`/`refine` and builds a numeric refine range **with no type guard**.
For a `bool` it computes `castValue(true,"bool") → 1`, then
`recMin = 1 - radius`, `recMax = 1 + radius`, `recStep = defaultIncrement/2`,
producing the nonsensical "refine 0.0 to 2.0 step 0.5" suggestion. The role
table (lines ~1112 / ~1124) also lets a boolean be set to "Optimize" with
continuous semantics. `getStepsCount()` (line ~1342) already special-cases
bool as 2 states, so a correct pattern exists to copy.

---

## 3. Target User Flow (walkthrough)

This is the experience the design delivers, told as a single session.

### Step 1 — Configure and run Stage 1

The user opens the recipe optimizer, picks a strategy and seed template on
**Recipe Setup**, then on **Stage 1 Setup** defines matrix axes (e.g.
`StartTimeH`, `Reverse`), fixes setup params, and sets optimize ranges for the
swept params. They review scope on **Stage Plan**, then click **Generate & Run
Stage 1**. Templates are written and dispatched to NinjaTrader. (This part is
unchanged from today except the run trigger is explicit and per-stage.)

### Step 2 — Filter and build a review basket

When NinjaTrader finishes, the user opens **Candidate Results** and selects the
stage. They use the existing filters — bucket, optimizer target, guided
drilldown (Time Slice → Reverse → Slow MA), and live guardrail filters — to
narrow to a slice. They check the candidates that look strong and click
**Add to Review**. The chosen rows move into a persistent **Review Basket**
(a counter badge updates: "Review (5)").

Crucially, the user can now **change the filter slice and select more**. The
basket is not cleared when they re-filter or even when they switch stages. They
repeat filter → check → **Add to Review** as many times as they want, gathering
candidates from different slices and different runs into one basket.

### Step 3 — Open the Final Review page

When ready, the user clicks **Review (N)**. This opens a dedicated **Review**
page listing every basketed candidate with its key metrics and parameter
config, grouped by source stage. Here the user assigns a **disposition** to each
candidate (individually or in bulk):

- **Send to Refinement** — this candidate should be narrowed further.
- **Send to Final Output** — this candidate is a keeper; add it to the final
  list.
- **Send to Python Analysis** — run deeper TA Foundation analysis on this
  candidate (see section 7).
- (Default `review` = undecided; can also **Remove** from basket.)

The Final Output list is visible and accumulates on this page, so the user can
always see "what they have" so far — directly addressing the missing list.

### Step 4 — Refine (loop back to a Stage-1-like setup)

The user selects the candidates marked **Refinement** and clicks **Open
Refinement Setup**. This creates a new refinement stage and opens it on a setup
page that is visually and functionally identical to Stage 1 Setup, but
pre-seeded: each parameter that was optimized upstream is set to **Refine**,
with a sensible radius/step derived from the originating run (and the AI
suggestions, with the bool fix applied). The user adjusts bounds, then clicks
**Generate & Run** for that stage.

### Step 5 — Loop

The refinement run completes, results appear back in **Candidate Results**, and
the user repeats Steps 2–4. Each pass narrows the field. The user keeps
promoting keepers to **Final Output** until the final list is complete.

### Step 6 — Finalize

From the Review page, the user clicks **Generate Final Backtests** on the Final
Output list. This hands the final selected rows to the existing fixed-backtest /
deployment-package / final-review path (unchanged engine), and the final
validation results surface in Candidate Results under `final_backtest`.

At no point does the system advance on its own. The Run Dashboard is gone from
this flow.

---

## 4. Information Architecture Changes

### 4.1 Tabs after this change

| Tab | Change |
|---|---|
| Recipe Setup | Unchanged |
| Stage Setup | Renamed from "Stage 1 Setup"; reused for every stage incl. refinement |
| Stage Plan | Add a per-stage **Generate & Run** button (replaces "Start & Monitor Run") |
| Candidate Results | Rework action bar: **Add to Review** + **Review (N)** |
| **Review** | **New** tab: basket + dispositions + Final Output list |
| ~~Run Dashboard~~ | Removed (or demoted to a passive progress strip; section 6) |

### 4.2 Candidate Results action bar

Replace the current two buttons (`Configure Refinement`, `Send to Final`) in
`#selection-actions-container` with:

- **Add to Review** — adds checked rows to the basket (disposition `review`).
- **Review (N)** — navigates to the Review tab; `N` is the live basket count.
- Keep: Select All / Deselect All, the selected-count display.
- The AI Suggested Refinements card stays, but its "Use Suggestions in
  Refinement Setup" button now seeds dispositions/bounds via the Review page
  rather than jumping straight into a stage (see 5.4).

---

## 5. Implementation Detail

### 5.1 Persistent Review Basket (new durable artifact)

Add one session-root file, consistent with the existing storage layout under
`.ta_artifacts/web_optimizer/sessions/<id>/`:

```text
recipe_review_basket.json
```

Schema:

```json
{
  "basket_version": 1,
  "recipe_id": "rec_xxx",
  "updated_at": "2026-05-25T14:02:11-06:00",
  "items": [
    {
      "candidate_id": "stage_1__start08__reverse_false__row_42",
      "source_stage_id": "stage_1",
      "disposition": "review",            // review | refine | final | analysis
      "added_at": "2026-05-25T14:00:00-06:00",
      "decided_at": null,
      "row": { "...": "snapshot of the result row at add time" }
    }
  ]
}
```

Notes:

- Store a **snapshot of the row** at add time so the Review page renders without
  re-reading every stage CSV, and so the basket survives stage re-runs that
  overwrite parsed results.
- `candidate_id` is unique per stage row (already injected by the results
  loader). De-dupe on `(source_stage_id, candidate_id)`.
- Persisting to disk (not just browser memory) means the basket survives page
  refresh and app restart, matching the durability of every other recipe
  artifact. **Decision point:** confirm disk-backed is wanted (recommended) vs.
  browser-only.

### 5.2 New / changed API routes

All under `/api/optimizer/sessions/<id>/recipe/...`, beside the existing routes
in `app.py`.

```text
GET    .../review                      -> returns recipe_review_basket.json
POST   .../review/add                  -> body {stage_id, candidate_ids[]}; append items as disposition "review"
POST   .../review/dispose              -> body {candidate_ids[], disposition}; set refine|final|analysis|review
POST   .../review/remove               -> body {candidate_ids[]}; drop items
POST   .../stages/<stage_id>/run       -> generate templates from THIS user's selection and dispatch to NT (no auto-select/advance)
POST   .../final/generate              -> hand "final" items to fixed-backtest/deployment path
POST   .../analysis/dispatch           -> body {candidate_ids[], analysis_id}; run a TA Foundation analysis (section 7)
```

`review/add` resolves each `candidate_id` against
`load_recipe_stage_results(session, stage_id)` (same call the existing `/select`
route uses) to snapshot the row, then merges into the basket file via an atomic
write (reuse `_atomic_write_json` from `optimizer_recipe.py`).

`review/dispose` is the engine behind the three Review-page buttons. Sending
disposition `final` should **also** write the existing
`recipe_final_selection.json` (via the same logic as `/select?intent=final`) so
the downstream fixed-backtest path keeps working unchanged. Sending `refine`
should write `selected.json` for the source stage (intent `refine`) so the
manual run in 5.3 can read it.

### 5.3 Manual per-stage run (decoupled from auto-select)

This is the heart of "the loop is user-controlled." Today, running a stage means
driving `RecipeRunOrchestrator` through `start`/`advance`, which auto-selects.
We add a **manual run** that:

1. Reads the user's chosen parent candidates from the basket
   (disposition `refine`) or from `parsed_results/<parent>/selected.json`.
2. Generates child-stage templates from those exact rows, reusing the existing
   template generation in `optimizer_recipe_templates.py` (the same code path
   `_generate_and_start_child_stage` already calls) — but sourcing the parent
   rows from the **manual selection**, not from
   `select_recipe_stage_candidates()`.
3. Dispatches to NinjaTrader via the existing runner
   (`optimizer_recipe_runner.py`).
4. Sets state to a simple `running_stage` / `waiting_for_results` so the
   progress strip can poll output completion — **but does not auto-ingest,
   auto-select, or auto-advance.** When NT finishes, the user goes to Candidate
   Results to ingest+filter+select again.

Implementation approach — lowest risk:

- Introduce a **selection source** indirection. Add a helper, e.g.
  `resolve_stage_parent_rows(session, stage_id)`, that returns the manual
  `selected.json` rows if present, else falls back to the auto-selector. Have
  the template-generation path call this helper instead of calling
  `select_recipe_stage_candidates()` directly.
- Add `RecipeRunOrchestrator.generate_and_run_stage(stage_id)` (or a thin
  standalone function) that performs steps 1–3 and sets `waiting_for_results`,
  with **no** call to the advance chain. The existing
  `_generate_and_start_child_stage` can be refactored to share the template
  build but skip the auto-select preamble.
- Stage 1 has no parent; its manual run just generates from the matrix plan
  (already what happens) and dispatches.

This keeps the autonomous `advance_once` code intact (for any future headless
mode) but removes it from the interactive surface.

### 5.4 Refinement setup reuse

The desired "page like Stage 1" already exists — it is the `roles` tab driven by
`UI_STAGES`. Reuse it:

- From the Review page, "Open Refinement Setup" collects the candidates marked
  `refine`, calls the existing `ensureRefinementStageFor(sourceStageId)` to
  append a refinement stage, and seeds each upstream-optimized param to role
  `refine` with radius/step from `defaultRadius`/`defaultIncrement` and the AI
  suggestions (section 5.5), then opens the Stage Setup tab on that stage.
- This is essentially the current `configureSelectedForRefinement()` logic
  (line ~2903) plus the bool fix, repointed to read from the basket instead of
  the live `SELECTED_CANDIDATE_IDS`. Much of it is reuse, not new code.
- The refinement stage compiles into the recipe exactly as today via
  `buildRecipe()` (line ~3058): `refine_around_parent_result` for refine roles,
  `pin` for `fixed_from_parent`, `optimize_inside_template` for any new
  optimize. No schema change needed.

### 5.5 Boolean-safe AI suggestions (bug fix)

In `renderSuggestedRefinements()`, `addRefineStage()`, and
`applySuggestedRefinementsAndRun()`, add a type guard before building numeric
refine bounds:

```js
const isBool = (matchingParam.type_name || "").toLowerCase() === "bool";
if (isBool) {
  // A boolean has no radius. Either sweep both states or pin to the winner.
  SUGGESTED_REFINEMENT_PARAMS[name] = { role: "optimize", values: [false, true] };
  // render as: "Refine: try both false / true" (no min/max/step)
  return; // skip the numeric recMin/recMax/recStep math
}
```

Corresponding rendering: show "try both states" or "pin to winner (X)" instead
of a numeric range. When compiled in `buildRecipe()`, a boolean refine becomes
either a 2-value `optimize_inside_template` entry (`[false, true]`) or a
`fixed_from_parent` pin — never a `refine_around_parent_result` with
radius/step.

Also constrain the role dropdown in `makeParamRow()` (lines ~1109–1129): for
`bool` params, offer **Fixed / Optimize (both states) / Fixed from parent**, and
omit the continuous "Refine" option, so the nonsensical state can't be entered
in the first place.

### 5.6 Review page (new panel)

Add `#panel-review` to `optimizer_recipe.html` and a `tab-review` nav button.
Client behavior:

- On open, `GET .../review` and render items grouped by `source_stage_id`.
- Each row: keep/metrics (PF, net, DD, trades, score) + param config (reuse the
  `param_*` extraction already in `renderWinnerShowcase`), plus a disposition
  control (segmented buttons: Refine / Final / Analysis / Remove).
- A live **Final Output** sub-list shows all items with disposition `final`,
  with a **Generate Final Backtests** button → `POST .../final/generate`.
- A **Refinement queue** sub-list shows items marked `refine`, with **Open
  Refinement Setup** → routes to Stage Setup (5.4).
- An **Analysis queue** sub-list with **Run Analysis** → `POST
  .../analysis/dispatch` (section 7).
- Bulk actions (select N rows → set disposition) for speed.

State: introduce `REVIEW_BASKET = []` client-side mirror, refreshed after each
mutation. Stop clearing `SELECTED_CANDIDATE_IDS` across stage loads is **not**
required anymore because the basket is the durable accumulator — but the
in-grid selection can still reset per stage; the basket persists regardless.

### 5.7 Candidate Results edits (small)

- Swap the action buttons (4.2).
- `Add to Review` → `POST .../review/add` with the checked IDs and current
  stage, then toast "Added N to review (basket now M)".
- Add a small persistent **Review (N)** badge in the header that reads basket
  size on load and after each add.
- Remove the jump-to-Run-Dashboard behavior from the old "Send to Final".

---

## 6. Retiring the Run Dashboard

The Run Dashboard (`#panel-run` + `_optimizer_recipe_panel.html`) is the
autonomous cockpit and conflicts with the manual loop. Two options:

**Option A — Remove (recommended for clarity).** Delete the `run` tab and the
auto-advance polling. Move the only still-useful pieces (NT run progress + the
`recipe_events.jsonl` console) into a compact **progress strip** shown on the
Stage Plan / Candidate Results tabs after a manual run is dispatched.

**Option B — Demote to passive monitor.** Keep the panel but: remove the
"Auto advance" checkbox and `maybeAutoAdvanceRecipe`, remove Start/Advance/
Pause/Resume/Stop and the "Danger Zone" override panel, and keep only the state
readout + event console. The hardcoded Stage 1–4 stepper cards (which assume the
old fixed pipeline) should be removed regardless since stages are now dynamic.

Either way, remove from the interactive path:

- `recipe-auto-advance` checkbox and `maybeAutoAdvanceRecipe()` (line ~2095).
- `recipeAction("advance")` wiring (line ~3218) and the advance button.
- The 4-stage hardcoded stepper in `_optimizer_recipe_panel.html`.

The orchestrator's `advance_once`/`start` can remain in the codebase for a
future headless/batch mode, but no UI control should invoke the auto-select +
auto-advance chain.

---

## 7. Python Analysis destination (phased)

"Send to Python Analysis" routes a candidate's winning parameter set into TA
Foundation's analysis subsystems for deeper inspection beyond NT's optimizer
KPIs. This is the one genuinely new integration and is intentionally scoped in
phases.

**Decision needed:** which analysis is meant? Candidate targets in the repo:

- The `analysis/` subsystems (e.g. `pattern_engine`, `entry_strategies`,
  `regime_recommender`) — would require materializing the candidate's settings
  into a runnable config.
- The backtest **report sections** path (`reports/html/registry.py`) — generate
  an HTML report for the candidate.
- The **deployment package / final review** path
  (`optimizer_deployment_package.py`, `optimization.review`) — richer
  validation artifacts.

**Phase 1 (thin):** `POST .../analysis/dispatch` writes the selected
candidates' parameter sets to
`parsed_results/<stage>/analysis_queue.json` and returns a stub "queued"
response, with a link out to the chosen analysis entry point. No heavy compute
yet. This unblocks the UI and lets the disposition exist end-to-end.

**Phase 2:** wire `analysis_id` to a concrete runner (most likely a report
build or a deployment-package review for the candidate), reusing existing
CLI/web job patterns (`web/jobs.py`) so long-running work doesn't block the
request. Persist outputs under the session folder and surface links on the
Review page.

Until Phase 2 is scoped, the button should clearly read "Queue for Analysis" to
set expectations.

---

## 8. Data / Contract Impact

- **No change** to `recipe.json` schema, `RecipeStage`, or the
  `refine_around_parent_result` / `optimize_inside_template` contracts.
- **New file** `recipe_review_basket.json` at session root (JSON-safe, atomic
  writes).
- Reuses existing `recipe_final_selection.json` and per-stage `selected.json`
  for the final/refine dispositions — the Review page is a new producer of the
  same files the engine already consumes.
- All datetimes tz-aware `America/Denver`, per the project contract.

---

## 9. Build Order

1. **Bool fix (5.5).** Smallest, highest-value, self-contained. Ship first.
2. **Review basket persistence + routes (5.1, 5.2).** Backend file + add/get/
   dispose/remove. Unit-test merge/de-dupe and disposition writes.
3. **Candidate Results action swap (5.7).** Add to Review + Review (N) badge.
4. **Review page (5.6).** Render basket, dispositions, Final Output list,
   Refinement queue.
5. **Manual per-stage run (5.3).** Selection-source indirection +
   `generate_and_run_stage`. This is the riskiest backend change; test that a
   refine stage runs from manual picks and does **not** auto-advance.
6. **Refinement setup reuse (5.4).** Wire "Open Refinement Setup" from the
   Review page; confirm `buildRecipe()` compiles refine roles correctly.
7. **Retire Run Dashboard (section 6).** Do this after 5 is proven, so no
   capability is lost in transit.
8. **Final generate wiring (5.2 `final/generate`).** Confirm the existing
   fixed-backtest/deployment path accepts the final list unchanged.
9. **Python Analysis Phase 1 (section 7).** Thin queue + link-out.

---

## 10. Test Plan

- Basket add de-dupes on `(stage_id, candidate_id)`; survives page refresh and
  app restart.
- Disposition `final` writes `recipe_final_selection.json` identical in shape to
  the existing `/select?intent=final` output.
- Disposition `refine` writes per-stage `selected.json` that the manual run
  reads.
- Manual `stages/<id>/run` generates templates from the **manual** parent rows
  (not the auto-selected ones) and does not transition past
  `waiting_for_results`.
- Boolean param: AI suggestion renders "both states"/"pin", never a numeric
  range; compiled recipe contains a 2-value optimize or a pin, never a
  bool `refine_around_parent_result`.
- Refinement stage from the Review page seeds upstream-optimized params to
  `refine` with valid radius/step; `buildRecipe()` round-trips.
- Removing the Run Dashboard does not break Stage 1 generation or NT dispatch.
- Final generate hands off to the existing fixed-backtest path and surfaces
  results under `final_backtest`.
- Standard (non-recipe) optimizer is untouched.

---

## 11. Open Questions

1. Should the review basket be disk-backed (recommended, survives restart) or
   browser-session only?
2. For "Send to Python Analysis," which subsystem is the Phase 2 target —
   report build, deployment-package review, or a specific `analysis/` module?
3. Should the Final Output list allow re-ordering / priority, or is membership
   enough before `final/generate`?
4. When a refine run completes, should its candidates auto-appear in the basket
   as `review`, or must the user always re-add from Candidate Results?
   (Default: re-add, to keep the loop explicit.)
5. Retire the Run Dashboard entirely (Option A) or keep a passive monitor
   (Option B)?
