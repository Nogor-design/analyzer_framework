# AI Workflow

Use this workflow when working on this repository with Codex, Claude, or another AI coding agent. The goal is to keep context small, avoid generated noise, and make each task land in the right subsystem.

## Starting Context

For a new task, read only these first:

- `CLAUDE.md`
- `docs/AI_REPO_INDEX.md`
- The specific source and tests identified from the relevant category

Avoid opening the entire repository tree or large generated output folders.

## Standard Task Prompt

```text
Task: <describe the requested change>

Use a narrow-context workflow:
1. Read CLAUDE.md and docs/AI_REPO_INDEX.md.
2. Identify the smallest relevant subsystem.
3. Inspect only the source and tests needed for that subsystem.
4. Before editing, summarize the files you believe are relevant.
5. Make the smallest safe change.
6. Run targeted tests first; broaden only if shared behavior changed.

Ignore .venv*/, outputs*/, .pytest_cache/, .ta_artifacts/, __pycache__/, *.pyc, *.duckdb, logs, and IDE metadata unless explicitly needed.
```

## Discovery-Only Prompt

```text
I only want discovery, no code edits yet.

Read CLAUDE.md and docs/AI_REPO_INDEX.md, then identify:
- the likely subsystem
- the files to inspect
- the tests that probably cover this area
- any open questions or risks

Do not scan generated folders or output artifacts.
```

## Implementation Prompt

```text
Implement the change using the discovery result.

Keep edits scoped to the identified subsystem. Do not refactor unrelated code. Add or update focused tests when behavior changes. Run the narrowest relevant pytest command and report the result.
```

## Review Prompt

```text
Review this change as a code reviewer.

Prioritize correctness bugs, regressions, missing tests, data model violations, and places where generated/runtime files were accidentally included. Reference exact files and lines. Keep summary secondary to findings.
```

## RAG Indexing Guidance

Good candidates for retrieval:

- `*.py`
- `*.md`
- `*.yaml`, `*.yml`
- `*.toml`
- selected `*.txt`

Bad candidates for retrieval:

- `.venv*/`
- `outputs*/`
- `.pytest_cache/`
- `.ta_artifacts/`
- `__pycache__/`
- `*.pyc`
- `*.duckdb`
- logs
- generated HTML/report artifacts unless the task is about a specific generated result

For code RAG, prefer chunks around modules, classes, functions, and tests. For docs, chunk by headings. Store path, category, symbol name, and last modified time as metadata.

## Refreshing The Repo Index

Run:

```bash
python scripts/build_ai_index.py
```

The generated `docs/AI_REPO_INDEX.md` should be small enough to give an AI agent a map without flooding context with implementation details.
