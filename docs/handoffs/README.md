# Handoff Specs

Self-contained task specs written by Claude for execution by Codex,
Gemini, Grok, or Claude. Each spec is a single Markdown file under
this directory; the index below is the work queue.

> **New here?** Read [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md) first —
> it explains your 6-step loop for getting tasks done with the AI
> fleet.

## How to use

1. Claude writes a spec for a well-bounded task → drops it as
   `NNN-slug.md` and adds it to the table below as `Open`.
2. Operator copies the file contents into the target AI's prompt.
3. Operator runs whatever verification the spec lists.
4. If the AI ships it, operator flips the entry to `Done`; if the
   AI fumbles and Claude has to take over, flip to `Done (Claude)`
   so we learn which kinds of tasks don't translate.

## Spec template

Use [`TEMPLATE.md`](TEMPLATE.md) as the starting point. Specs are
self-contained — the executing AI sees only the spec, no chat
history. If a fact isn't in the spec, the AI can't use it.

## Target-AI tags

| Tag | Use for |
|---|---|
| `[codex]` | Non-trivial code, web/UI, multi-file changes, careful refactors |
| `[gemini]` | Mechanical edits, doc rewrites, small bounded section/component additions |
| `[grok]` | Same as Gemini; pick based on availability |
| `[claude]` | Design decisions, cross-cutting refactors, gnarly debugging, or tasks the others have already tried and bounced |

## Queue

| # | Title | Tag | Status | Notes |
|---|---|---|--------|---|
| 001 | On-demand per-candidate report picker page | `[codex]` | Done   | First real handoff — exercises the workflow |
| 002 | Flag-adjusted rank on the decision dashboard | `[codex]` | Done   | Penalize candidates with `warn`/`fail` badges so curve-fits sink below cleaner candidates |

When the queue gets long, move `Done` rows to an archive section
below. Keep the active table short enough to scan at a glance.

## Continuation prompts (cold-start briefs)

Self-contained context dumps for picking up a long workstream in a new
chat. Not tied to the queue above; read these before resuming the
named topic.

- [`recipe_optimizer_continuation_prompt.md`](recipe_optimizer_continuation_prompt.md) — Recipe Optimizer Phases 4 & 5
- [`promote_and_run_continuation_2026-05-30.md`](promote_and_run_continuation_2026-05-30.md) — Shortlist → Promote & Run (one-click NT batch); shipped 2026-05-30
- [`weekly_coverage_package_continuation_2026-06-01.md`](weekly_coverage_package_continuation_2026-06-01.md) - Weekly Pantheon coverage package and next coverage-recipe phase

- [`weekly_coverage_report_repair_handoff_2026-06-03.md`](weekly_coverage_report_repair_handoff_2026-06-03.md) - Weekly package report repair pass for `opt_f2ac9fefeb44`: deduped executive report, naming, MaxTrades card value, Daily Winner names, and data-ended W/L windows

## Done (archive)

_(empty)_
