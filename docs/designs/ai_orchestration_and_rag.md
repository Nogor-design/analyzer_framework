# Design: AI Orchestration & RAG Assistant

*Created 2026-06-05. Supports [strategy_business_roadmap.md](strategy_business_roadmap.md).
How we use the AI bench to get more done, and an honest take on the local-LLM + RAG idea.*

## The real bottleneck (name it before optimizing it)

Throughput is not limited by execution hands — Codex/Gemini can build fast. It is limited by
**(a) Claude writing self-contained specs** and **(b) Claude reviewing for correctness** (especially
edge-honesty and risk math). So "get more done with other AIs" = make specs runnable without
babysitting and make review cheap (tests + diff review). Every design doc in `docs/designs/` is
written in that handoff style on purpose: purpose, data contracts, module layout, tests, and an
explicit **Executor guidance** block.

## Roles

| AI | Best at | Use for |
|---|---|---|
| **Claude** (PM) | judgment, architecture, edge-honesty/risk calls, review | specs, scorer/risk-engine core, reviewing every diff |
| **Codex** | heavy mechanical builds from tight specs | replay/baseline harness, DD state machine, plumbing |
| **Gemini** | well-specified additive work, lighter tasks | config wiring, report/renderer pieces, test fixtures |
| **Local LLM** | cheap/bulk/offline, retrieval | RAG Q&A, draft routine artifacts, bulk text ops |
| **Grok** (not yet available) | web research | firm-rules research (APEX rulebook), market context |

Invocation (from [project_handoff_workflow]): Codex at
`C:\Users\Owner\AppData\Local\OpenAI\Codex\bin\codex.exe`
(`codex exec -s workspace-write --skip-git-repo-check -o <out.txt> -`, prompt on stdin);
Gemini `gemini --yolo --skip-trust -p`. Claude specs + reviews; the executor never merges unreviewed.

## Parallelization plan for the current roadmap

These three are independent and can run **concurrently** once specs are approved:
1. **Codex** → `daily_lineup_selector.md` harness (`replay.py`, `baselines.py`) — Phase 1.
2. **Codex (or Gemini)** → `account_risk_engine.md` DD state machine (`dd_engine.py`,
   `account_state.py`) — Phase 2b. Pure, table-tested, no NT.
3. **Web research** → authoritative APEX 4.0 profile (Claude for now, Grok later) — Phase 2a.

Claude holds the judgment cores (`scoring.py`, allocator objectives, lock/violation edge cases) and
reviews 1–3. Hand off in that order; review as each returns.

## The local-LLM + RAG idea — honest assessment

**Endorse, but scoped.** RAG is strong for *retrieval and orientation*, weak for *judgment*. Use it
where it offloads expensive Claude context, not where correctness matters.

**Good uses (build these):**
- **"Ask the project" retriever** — index `docs/`, `MEMORY.md` + memory files, `AI_REPO_INDEX.md`,
  `CAPABILITY_CATALOG.md`, and decision docs. Answers "where is the code that does X", "what did we
  decide about Y", "which doc covers Z". This is the highest-frequency repetitive task and it
  currently burns Claude/session context. A local model + RAG can serve it offline and cheaply.
- **Routine artifact drafting** — first drafts of the weekly client email from the selector output +
  outcome ledger, release notes, changelog. A human/Claude still approves.

**Bad uses (do NOT):**
- Strategy selection, risk/DD math, validation calls — local models are too weak; these stay with
  the deterministic engines + Claude.
- **Deterministic ops are not an LLM task at all** — driving recipes, building packages, parsing
  results are scripts (they already exist). Don't wrap a script in an LLM.

**Proposed RAG stack (local, hardware-permitting):**
- Embeddings: a small local embedding model (e.g. `nomic-embed-text` or `bge-small`) via Ollama.
- Generator: a local instruct/coding model — recommend **Qwen2.5-Coder (14B/32B)** or
  **Llama-3.3-70B** if VRAM allows; smaller (7–8B) is fine for pure retrieval-grounded answers.
- Index: chunk the docs/memory corpus; re-index on a hook when docs change.
- Surface: a CLI / small web tab "Ask the project".

> Eric authorized downloading a model if useful. **Recommendation, not yet done** — downloading a
> multi-GB model is a heavy action I'll confirm first. Decision needed: which generator size fits
> the available VRAM? (drives the model choice). Start with retrieval-only (cheapest win) before
> investing in a larger generator.

## Next action options

- Kick off the two Codex handoffs (selector harness + DD engine) now, in parallel, from the specs.
- Or build the RAG retriever first (compounding productivity for every later task).
- Pending Eric: real anonymized APEX account history (gates Phase 2 validation) + confirmed APEX
  profile numbers + target local model size.
