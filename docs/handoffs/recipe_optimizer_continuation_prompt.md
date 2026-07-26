# Prompt: Resume Recipe Optimizer Implementation (Phases 4 & 5)

## Overview & Context
You are resuming work on a premium, high-fidelity quantitative trading software system called **TA Foundation**. Specifically, you are working on the **Recipe/Matrix/Autonomous Optimizer** control plane dashboard. 

The project resides in the following workspace:
- **Workspace Directory**: `D:\Backup\projects\PythonProject\ta_foundation`
- **Core Technology Stack**: Flask (Python) backend server, Vanilla CSS and Javascript frontend (HTML templates, premium styling, glassmorphism theme).
- **Web App Port**: `7734`
- **Background Runner Server**: `.venv\Scripts\python -m ta_foundation.web.app --port 7734`

---

## 1. What Has Been Completed So Far

### Part 1: End-to-End Discovery Loop Verification
- Pre-registered, executed, and triaged a unique `orb_failure_reclaim` test hypothesis: `h_auth_20260522T133000_orbfailurerecl_testloop_1234`.
- Verified execution of the backtest sweep operator, which successfully ingested, parsed, and logged results inside `.ta_artifacts/research_ledger.db`.

### Part 2: Recipe Optimizer Re-Design (Phase 1 Completed)
- **Modern Multi-Tab Layout**: Transformed the static flat page in `optimizer_recipe.html` into a premium multi-tab dashboard layout matching state-of-the-art designs:
  - *Recipe Setup*: Strategy selection, seed configuration, parameter presets, and visual orchestrator loops.
  - *Matrix & Roles*: Multi-axis setup, parameter role-bindings, real-time scope indicators.
  - *Selection Rules*: Trades/PF limits, drawdown caps, multi-target filters.
  - *Stage Plan*: Combo counts, XML templates generation, pre-flight checks.
  - *Run Dashboard*: Live monitoring, events console, manual stage overrides.
  - *Review*: Final candidates list and deployment templates downloads.
- **Dynamic Parameter Role-Binding Table**: Integrates directly with the C# strategy metadata endpoint `/api/optimizer/strategies/<strategy_id>`. Lists all properties grouped by `GroupName` with 6 interactive roles (*Fixed, Matrix axis, Optimize, Refine, Inherit, Validation only*) and contextual inputs (ranges, tags, fixed values, bounds).

### Part 3: Dynamic Matrix Axes Cards & Safety Cap (Phase 2 Completed)
- **Tag-Based Card Editor Grid**: Generates a card grid at the top of *Matrix & Roles* with inline "+ add value" text fields (triggers on `Enter`) and close-pills to delete tags.
- **Bi-directional Binding**: Changing values in cards instantly updates the parameter table and vice versa.
- **Real-Time Safety Guardrails**: Prevents server load spikes by tracking estimated Stage 1 combinations. If combinations exceed `250,000`, a beautiful **Safety Cap Exceeded** warning alert is displayed, and the execution/preview buttons are safely locked.

### Part 4: Run Dashboard, Override APIs & Console Logs (Phase 3 Completed)
- **Active Stage Highlighting**: Stage Loop cards glow with a smooth amber shadow, scale to `1.02`, and pulse with `Active/Running` or emerald `Completed` badges.
- **macOS-Style Events Stream**: Chronological terminal logger that parses backend events with custom colored badges (blue `[INFO]`, emerald `[SUCCESS]`, yellow `[WARNING]`, red `[FAILURE]`) and smooth automatic bottom tail scrolling.
- **Backend Route Integration**: Fully restored and mapped all orchestrator routes inside `app.py`, including the new `/api/optimizer/sessions/<session_id>/recipe/override` endpoint for `rerun`, `skip`, and `reset` actions.

### Part 5: "Save & Preview Plan" Usability Bug-Fixes
- Wrapped `saveAndPreviewRecipe()` in a robust JavaScript `try-catch` block to handle silent validation errors and write user-friendly notifications directly to the visible `#recipe-status` area.
- Added strategy-detail metadata parser logic inside `loadStrategyDetail()` to automatically select the **first valid optimization seed template** instead of leaving it empty, saving strategist setup time.
- All 18 routes in the web test suite successfully pass verification.

---

## 2. Codebase Reference Map

Ensure you interact with these files directly:
* **Frontend UI Template**: `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\templates\optimizer_recipe.html`
* **Web Server API Controller**: `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\app.py`
* **Test Suites**:
  - `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\tests\web\test_optimizer_routes.py`
  - `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\tests\web\test_optimizer_recipe.py`
* **Backend Modules**:
  - `optimizer_recipe.py` (generic recipe schemas)
  - `optimizer_recipe_orchestrator.py` (state machine & transition controls)
  - `optimizer_recipe_selection.py` (bucket candidate ranking & sorting logic)
  - `optimizer_recipe_templates.py` (template overrides & refined sweeps generation)

---

## 3. Your Goals & Tasks

Proceed immediately to **Phase 4** and **Phase 5** implementation:

### Phase 4: Stage Buckets & Selection Review
1. **Matrix Buckets Grid UI**:
   - In `optimizer_recipe.html`, design a premium, responsive responsive Grid representing one row per matrix combination bucket.
   - Show key metrics (net profit, PF, drawdown, trade count), chosen candidate IDs, and statuses (*Selected*, *No Candidate*, *Boundary Hit*).
   - Apply a cohesive color scheme: emerald green for promoted, amber for warnings, slate for skipped/no-trades.
2. **Interactive Context Menus**:
   - Provide interactive popup menus or card action controls to allow operators to:
     - *Rerun a bucket* with widened axis ranges.
     - *Promote a sibling candidate* manually overriding selection rules.
     - *Reject a candidate* instantly.

### Phase 5: SVG Child Lineage Inspector
1. **Interactive Node Tree View**:
   - Parse `recipe_selection.json` to extract parent-child derivations (Stage 1 matrix winner -> Stage 2 child refinement sweep -> Stage 3 final backtest validation).
   - Render a visual, interactive parent-child tree mapping the lineage using clean CSS Flexbox vectors, Canvas, or SVGs.
2. **Selected Node Inspector Panel**:
   - When the user clicks a node in the lineage tree, dynamically populate a details panel on the right with parameter values and full metrics (PF, Sharpe, Trades, Win Rate, Drawdown).
3. **Deployment Package Dashboard**:
   - Add a premium deployment overview panel showing candidate file details (`selected.json`, `selected.csv`), verifying pre-flight checkmarks, and offering a one-click download for final template ZIP handoffs.

---

## 4. Key Constraints & Design Principles

* **Aesthetic Superiority**: Utilize rich styling, deep slate/blue `#0b0f19` glassmorphic cards, smooth transitions, glowing amber active states, and custom HSL colors. Avoid default colors or flat designs.
* **No Code Placeholders**: Write fully integrated, operational, and resilient JS and HTML. Ensure errors are caught, visually surfaced, and logged.
* **Resilient Port Binding**: Run Flask via `.venv\Scripts\python -m ta_foundation.web.app --port 7734` in the background. Verify routes with the pytest suite:
  ```powershell
  .venv\Scripts\pytest src/ta_foundation/tests/web/test_optimizer_routes.py
  ```

---
Let's begin! First, inspect the current frontend structure in `optimizer_recipe.html` to locate where Phase 4 and Phase 5 sections should be integrated, review the existing API controllers, and formulate a detailed implementation plan.
