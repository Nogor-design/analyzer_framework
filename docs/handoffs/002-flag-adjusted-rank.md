# 002 — Flag-adjusted rank on the decision dashboard

**Target AI:** `[codex]`
**Status:** Open
**Estimated effort:** ~2h

---

## Goal

Today the decision dashboard ranks candidates by the engine's
in-sample score, which puts curve-fit candidates at the top even
when bootstrap / walk-forward / neighborhood / shadow checks have
flagged them as fragile. Add a **robustness-adjusted rank** that
penalizes each `warn` and `fail` badge, then reorder the candidate
table by it. Show both the original recommended-status pill and the
new adjusted-rank pill so the operator can see when the engine and
the robustness checks disagree.

The first practical effect on `opt_5bab6a5ee1ea`: F_001 (rank 1, but
walk-forward FAIL + shadow FAIL) should sink below candidates with
clean checks even if their raw score is lower.

## Why

The robustness data already exists on each `CandidateRow.checks`
(see `optimizer_decision_dashboard.py`). The dashboard surfaces
flags but doesn't let them influence ordering, which means the
operator has to manually scan badges to decide who's actually
trustworthy. This change makes the visual ordering match the data
the dashboard already exposes.

Background: bootstrap/walk-forward/neighborhood/shadow were added
2026-05-16 / 2026-05-17 (see
[`docs/designs/optimizer_known_issues.md`](../designs/optimizer_known_issues.md)
"Resolved" section). The decision dashboard came online with the
two-stage report rollout the same week.

## Files to touch

- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\optimizer_decision_dashboard.py` — add `adjusted_score` and `adjusted_rank` fields to `CandidateRow`; add a small `_compute_adjusted_score(score, checks)` helper; change the table sort key from `_ranked_then_score` to a new `_by_adjusted_rank` ordering.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\web\templates\optimizer_decision_dashboard.html` — add a leftmost column "Adj #" showing the adjusted-rank pill, plus an inline movement marker (⬆/⬇) when adjusted_rank differs from the engine's `rank` by 2+ positions.
- `D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\tests\web\test_optimizer_decision_dashboard.py` — add at least 4 tests covering the rules below.

## Penalty rules

Pure function — no UI knowledge.

```text
adjusted_score = (raw_score or 0)
                 - 30 * count(checks where severity == "fail")
                 - 10 * count(checks where severity == "warn")
```

- `none` (check didn't run) and `ok` contribute zero.
- A candidate whose `score` is `None` is allowed; treat raw_score as 0.
- The constants `FAIL_PENALTY = 30` and `WARN_PENALTY = 10` should be
  module-level so they're easy to tune later. Don't make them
  configurable via UI yet.

Sort order:

1. Higher `adjusted_score` first.
2. Tie-break by raw `score` desc (i.e. when penalties match, the
   engine's preferred candidate wins).
3. Final tie-break by `run_id` ascending so order is deterministic.

Then assign `adjusted_rank = 1..N` in that order. Every candidate
gets an `adjusted_rank` regardless of recommended/rejected status.

## Display rules

In `optimizer_decision_dashboard.html`:

- Add a new leftmost `<th class="pr-3">Adj #</th>` column. Cell
  shows the `adjusted_rank` in a new `pill-adj` style (use the
  existing `pill-rank` amber styling, but slightly muted —
  `background: #d97706; color: #1f2937;` is fine).
- Right after the `Adj #` pill, show a movement marker **only when**
  `abs(adjusted_rank - rank) >= 2` and both ranks exist:
  - moved up (adjusted_rank smaller): `<span style="color:#34d399;font-size:11px;" title="...">⬆ +N</span>`
  - moved down (adjusted_rank larger): `<span style="color:#fecaca;font-size:11px;" title="...">⬇ -N</span>`
  - Tooltip text: `"engine rank #{rank} → robustness-adjusted #{adjusted_rank}"`
- Candidates with no engine `rank` (rejected/evaluated) show only
  the `adjusted_rank` pill, no movement marker.
- The existing Status column (recommended / rejected / evaluated)
  stays untouched — that's the engine's call and should remain
  visible for comparison.
- Update the description text under the "Candidates" heading: replace
  the current sort description with
  `"Sorted by robustness-adjusted rank: lower is better. The engine's
   recommendation pill is shown for comparison — when the two disagree
   by 2+ positions you'll see a ⬆/⬇ marker."`

## Acceptance criteria

- [ ] `CandidateRow` has new fields `adjusted_score: float | None` and `adjusted_rank: int`.
- [ ] `to_dict()` includes both new fields.
- [ ] Table on the rendered HTML page is sorted by `adjusted_rank` ascending; first row is the candidate with the best adjusted score.
- [ ] On `opt_5bab6a5ee1ea`, F_001 (which has two `fail` badges in `walkforward` and `shadow`) drops below at least one candidate that has cleaner checks but a lower raw score. Operator can verify by visiting `/optimizer/sessions/opt_5bab6a5ee1ea/decision`.
- [ ] Tests:
  - [ ] adjustment penalizes `fail` more than `warn`
  - [ ] `none` severities apply no penalty
  - [ ] adjusted_rank is 1-indexed and unique per dashboard
  - [ ] candidate with `score=None` doesn't crash and gets adjusted_score = -30*fails -10*warns
- [ ] `python -m pytest src/ta_foundation/tests/web src/ta_foundation/tests/optimization src/ta_foundation/tests/parsers --ignore=src/ta_foundation/tests/web/test_conditional_promotion.py -q` stays green (currently 445 passing).

## Gotchas

- **Sort stability.** Don't keep `_ranked_then_score` and chain it — replace it. Two sort keys fighting each other is what got us here.
- **`CandidateRow` is `@dataclass(frozen=True)`.** New fields must be added to the dataclass declaration, not patched in.
- **JSON safety.** `adjusted_rank: int` (1-indexed, always populated). `adjusted_score: float | None` is allowed when no checks exist at all (treat as score itself, no penalty); but in practice every candidate has at least 4 `CheckSummary` entries, so it will normally be a float.
- **Don't introduce a separate route or template.** Same page; just new column and sort key.
- **Style tokens.** Match the existing `.pill` / `.badge` CSS palette in the template head — don't introduce a new Tailwind utility class for the adj-rank pill.
- **Don't touch** the recommendation engine, the per-check severity logic, the report-builder, or the candidate report. This change is purely view-model + template.

## Out of scope

- Surfacing the penalty constants in UI / config — fixed for now.
- Replacing the Status pill (recommended/rejected/evaluated). Keep both.
- A "show only adjusted top-3" filter. Whole table renders.
- Storing the adjusted rank to disk. Recomputed each request.
- Re-ranking the on-disk `recommendations.json` (that's the engine's job).

## How to verify

```powershell
cd D:\Backup\projects\PythonProject\ta_foundation
python -m pytest src/ta_foundation/tests/web/test_optimizer_decision_dashboard.py -q
python -m pytest src/ta_foundation/tests/web src/ta_foundation/tests/optimization src/ta_foundation/tests/parsers --ignore=src/ta_foundation/tests/web/test_conditional_promotion.py -q
```

UI smoke (web app running):

```powershell
python -m ta_foundation.web.app --port 7734
```

Open `http://127.0.0.1:7734/optimizer/sessions/opt_5bab6a5ee1ea/decision`.
Expected: F_001 is no longer the first row; whichever candidate has
the fewest fails/warns + reasonable raw score sits at Adj # 1. F_001
should display its original `recommended #1` pill *and* a `⬇ -N`
movement marker.

## Notes for the executing AI

- Read [CLAUDE.md](../../CLAUDE.md) before starting. Sections are
  pure renderers; the view-model assembler is where derived state
  lives. Don't reach into checks/flag computation — those are
  upstream.
- `CheckSummary.severity` values are the constants `SEVERITY_OK`,
  `SEVERITY_WARN`, `SEVERITY_FAIL`, `SEVERITY_NONE` exported from
  `optimizer_decision_dashboard.py`. Use those, not string literals.
- The existing `_ranked_then_score` function is no longer needed
  after this change; remove it so we don't keep dead code.
- The PM (Claude) wrote this spec. If anything is ambiguous, stop
  and report back — don't guess on the penalty constants or the
  display rules.
