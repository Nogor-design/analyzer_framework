# Operator Guide — How to get work done

Your day-to-day workflow for shipping changes with Claude as PM and
Codex / Gemini / Grok as executors. Keep this open in a tab when
you're driving the AI fleet.

---

## The 6-step loop

### 1. Bring the task to Claude

State what you want done in plain English. Don't pre-decide who
should do it — that's Claude's call.

> "I want a way to compare two finalists side by side on the
> decision dashboard."

### 2. Claude decides the path

Claude will tell you which path it's taking *before* doing anything:

- **"I'll handle this inline"** — design, debugging, cross-cutting
  refactor, or a small thing that's faster than writing a spec.
- **"I'll write a spec, target = `codex`"** (or `gemini` / `grok`) —
  the task is well-bounded and another AI can execute it cheaper.
- **"Before we start, here's a concern…"** — Claude pushes back if
  the request has a problem. Hear it out; the pushback usually
  saves rework.

### 3. If Claude writes a spec

You get a file at `docs/handoffs/NNN-slug.md`. Claude also adds the
row to the queue in `docs/handoffs/README.md` with status `Open`.

### 4. Hand off to the executor

Open the spec file and **copy the entire contents** into a fresh
prompt for the target AI (Codex / Gemini / Grok). The spec is
self-contained — the executor sees only that file, nothing else
from your Claude conversation.

Tell the executor:

> "Execute the attached handoff spec. If anything is unclear or
> looks wrong, stop and tell me — don't guess."

### 5. Verify

Run the `How to verify` block from the spec. That's usually:

```powershell
cd D:\Backup\projects\PythonProject\ta_foundation
python -m pytest <paths from the spec> -q
```

Plus any UI smoke-test the spec calls out.

### 6. Close the loop

Edit `docs/handoffs/README.md` and flip the queue row:

| Outcome | Status to set |
|---|---|
| Executor shipped cleanly | `Done` |
| Executor shipped but with issues you fixed yourself | `Done (manual cleanup)` |
| Executor couldn't do it, Claude finished it | `Done (Claude)` |
| Spec was wrong, abandon | `Cancelled` — leave a one-line reason |

Move finished rows to the `## Done (archive)` section when the
active queue gets noisy.

---

## When to come back to Claude

- The executor produced something that doesn't compile / breaks
  tests / misses the acceptance criteria.
- The spec turned out to be wrong; the executor reported back asking
  for clarification.
- You realize mid-execution that the design needs to change.

Bring Claude the executor's output **plus** your read of what went
wrong. Claude will either patch the spec, take the task over, or
escalate to a different executor.

---

## What to avoid

- **Don't paste old Claude-conversation context into Codex / Gemini.**
  The spec is the contract. Extra context just adds noise and tokens.
- **Don't let executors invent missing details.** If the spec doesn't
  say it, the executor should stop and ask — not guess. Tell them
  this up front.
- **Don't skip the verify step.** A green test suite catches 80% of
  bad executor output before it ships.
- **Don't ask Claude to "just do it" when a spec is faster.** Claude
  is the most expensive AI in the rotation. Reserve Claude tokens
  for judgment, not throughput.

---

## When in doubt

Ask Claude: *"is this a Claude task or a spec task?"* and let
Claude route it. The decision matrix lives in
`feedback_critical_thinking.md` and `project_handoff_workflow.md`
in Claude's memory — you don't need to memorize it.

---

## Files in this directory

- `README.md` — the work queue and target-AI tag legend
- `TEMPLATE.md` — what every new spec starts from
- `OPERATOR_GUIDE.md` — this file
- `NNN-slug.md` — one per task, self-contained
