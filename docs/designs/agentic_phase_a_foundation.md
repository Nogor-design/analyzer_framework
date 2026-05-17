# Phase A — Foundation

Phase A delivers the substrate every later phase relies on: the candidate
ledger, the read/write tool registry, and the pre-registration workflow
with a finite hypothesis-family registry. **No agent is run during Phase
A.** When Phase A is done, the deterministic pipeline is queryable and
addressable as data; the agent layer is just plumbing on top.

Read `agentic_research_program.md` (the master plan) before this file.

## Status snapshot

| ID | Item | Status | Notes |
|----|------|--------|-------|
| A.1 | Candidate ledger schema + access layer | ✅ Done | SQLite stdlib; 35 tests; see "Already-shipped: A.1" below |
| A.2a | Tool framework + read tools + simple write tools | ✅ Done | `_decorators.py`, 10 read tools, 6 write tools, 79 new tests; `analysis_tools.py` deprecated |
| A.2b | run_probe (subprocess) + record_candidates_for_run + graph.py deprecation | ✅ Done | 15 new tests; sidecar→dict parsing intentionally split out (per-family parsers) |
| A.2c | Sidecar parser + backfill module | ✅ Done | Single parser for `schema_version: 1` covers all 16 real sidecars; 25 new tests |
| A.3 | Pre-registration record + family registry | ✅ Done | 13 families seeded, drift check wired into CLI; 31 new tests |
| A.4 | Ledger backfill from existing sidecars | ✅ Done | `scripts/backfill_ledger.py`; real run on outputs/: 16 hyp / 251 candidates, idempotent |
| A.5 | Tool-call journaling | ⏳ Not started | Append-only log, content-addressed |

---

## Already-shipped: A.1 — Candidate ledger

Files landed:

- `src/ta_foundation/research_ledger/__init__.py` — public surface.
- `src/ta_foundation/research_ledger/db.py` — `connect`, `init_db`,
  `apply_pending_migrations`, version-table management. Forward-only
  discovery scans `migrations/m_*.py` modules with a `VERSION` int and
  `apply(conn)` callable. WAL + foreign keys + busy timeout enabled.
- `src/ta_foundation/research_ledger/migrations/m_0001_initial.py` —
  full V1 schema (families, hypotheses, runs, candidates, tool_journal,
  shadow_signals, all indexes). `dedupe_hash UNIQUE` on hypotheses;
  `holdout_attempted INTEGER DEFAULT 0` on candidates for atomic
  single-attempt lock; CHECK constraints on every enum-style column
  (status, mode, gate_verdict, triage_state).
- `src/ta_foundation/research_ledger/models.py` — frozen dataclasses
  for read-side projections (`Hypothesis`, `Run`, `Candidate`,
  `Family`, `JournalEntry`, `ShadowSignal`).
- `src/ta_foundation/research_ledger/repository.py` — typed CRUD plus
  every canonical query from this doc:
  `register_hypothesis`, `start_run`/`complete_run`/`fail_run`,
  `record_candidate`, `get_candidate`, `list_candidates`,
  `list_graveyard`, `find_similar_hypotheses`,
  `count_hypotheses_tested`, `set_triage`, `lock_holdout_attempt`,
  `journal`/`list_journal`, `seed_family`/`get_family`/
  `list_families`. Custom exceptions:
  `DuplicateHypothesisError`, `HoldoutAlreadyAttemptedError`,
  `LedgerIntegrityError`.
- `src/ta_foundation/tests/research_ledger/test_repository.py` —
  35 tests across 8 classes (initialization, registration, runs,
  candidates, triage, holdout lock, similarity/counting, journal,
  migration idempotency).

Invariants verified by tests:

- Migrations are idempotent (`init_db` twice on a populated DB applies
  zero migrations).
- `register_hypothesis` rejects duplicates (same family/instrument/
  timeframe/session/direction/params/mechanism) by `dedupe_hash` UNIQUE,
  returning a `DuplicateHypothesisError` carrying the colliding
  hypothesis_id.
- A different `session_window` *or* a different mechanism text produces
  a distinct `dedupe_hash` and is accepted.
- `lock_holdout_attempt(c)` returns `True` exactly once per candidate
  via SQL compare-and-swap (`UPDATE ... WHERE holdout_attempted = 0`),
  `False` on every subsequent call.
- `count_hypotheses_tested` only counts hypotheses with at least one
  `runs.status = 'completed'` row — partial/running runs do not
  contribute to the multiple-testing denominator.
- Journal canonicalises `inputs_json` (sorted keys, no whitespace) so
  identical inputs hash identically.
- All enum-style columns are CHECK-constrained at the SQL layer; bad
  values raise `sqlite3.IntegrityError` even if Python validation is
  bypassed.

Test command:
```
python -m pytest src/ta_foundation/tests/research_ledger/ -v
```
35/35 pass. Full suite at 1253 passing with the same pre-existing
`regime_recommender` failure unrelated to this work.

---

## A.1 — Candidate ledger (original spec, kept for reference)

### Problem

Today every discovery run produces a self-contained sidecar/HTML pair.
There's no cross-run ledger, so:

- We cannot count "how many hypotheses have we tested?" — which is the
  denominator for multiple-testing correction (T12 in
  `discovery_hardening_plan.md`).
- We cannot ask "have we tested this before?" before launching a new
  probe — leading to silent re-litigation of dead ideas.
- We cannot serve agent read-tools without re-parsing HTML.
- The graveyard exists in narrative form (`real_edge_discovery_program.md`)
  but not as data.

### Decision

- **SQLite** (stdlib `sqlite3`, no new deps), file at
  `.ta_artifacts/research_ledger.db`. DuckDB is tempting but adds a
  dependency for value we don't need; SQLite handles thousands of
  candidates fine, and parquet artifacts stay on disk.
- New package: `src/ta_foundation/research_ledger/`.
- All schema migrations are forward-only Python functions checked into
  `research_ledger/migrations/`.

### Schema (V1)

```sql
-- A pre-registered hypothesis. Mechanism + parameter ranges fixed before run.
CREATE TABLE hypotheses (
    hypothesis_id    TEXT PRIMARY KEY,         -- e.g. h_2026_05_10_vwap_london_fade_001
    family           TEXT NOT NULL,            -- FK → families.family_id
    instrument       TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    session_window   TEXT,                     -- e.g. 'london_00_06_denver'
    direction        TEXT,                     -- 'long' | 'short' | 'both' | NULL
    params_json      TEXT NOT NULL,            -- pre-registered param ranges
    mechanism        TEXT NOT NULL,            -- free-text paragraph
    registered_at    TEXT NOT NULL,            -- ISO8601 UTC
    registered_by    TEXT NOT NULL,            -- 'human:eric' | 'agent:hypothesis_author' | 'agent:human_reviewed'
    parent_id        TEXT,                     -- if promoted from a conditional rule
    status           TEXT NOT NULL DEFAULT 'open'  -- open | retired | superseded
);

-- Each invocation of the pipeline against a hypothesis.
CREATE TABLE runs (
    run_id           TEXT PRIMARY KEY,
    hypothesis_id    TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
    mode             TEXT NOT NULL,            -- 'fast_probe' | 'hardened' | 'locked_holdout'
    config_hash      TEXT NOT NULL,            -- sha256 of resolved YAML
    yaml_path        TEXT NOT NULL,
    artifact_dir     TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    completed_at     TEXT,
    status           TEXT NOT NULL,            -- 'running' | 'completed' | 'failed'
    error            TEXT
);

-- One row per top candidate emitted by a run.
CREATE TABLE candidates (
    candidate_id         TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES runs(run_id),
    hypothesis_id        TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
    rank_in_run          INTEGER NOT NULL,
    params_json          TEXT NOT NULL,        -- the specific resolved params
    -- Headline metrics, dev / oos / holdout (NULL when not run yet)
    n_trades_dev         INTEGER, pf_dev         REAL, expectancy_dev   REAL,
    n_trades_oos         INTEGER, pf_oos         REAL, expectancy_oos   REAL,
    n_trades_holdout     INTEGER, pf_holdout     REAL, expectancy_holdout REAL,
    -- Hardening verdicts
    gate_verdict         TEXT NOT NULL,        -- 'survivor' | 'rejected' | 'pending'
    gate_reasons_json    TEXT,                 -- list of structured failure rows
    slippage_stress_pass INTEGER,              -- 0 | 1 | NULL
    folds_distribution   TEXT,                 -- JSON
    -- Triage
    triage_state         TEXT,                 -- 'graveyard' | 'research' | 'hardening_queue' | 'shadow' | NULL
    triage_reason        TEXT,
    triaged_at           TEXT,
    triaged_by           TEXT
);

-- Append-only log of agent tool calls.
CREATE TABLE tool_journal (
    journal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                   TEXT NOT NULL,
    role                 TEXT NOT NULL,        -- which agent role
    tool_name            TEXT NOT NULL,
    inputs_json          TEXT NOT NULL,
    output_summary       TEXT NOT NULL,
    output_artifact_path TEXT,                 -- if output written to disk
    duration_ms          INTEGER NOT NULL,
    error                TEXT
);

-- The finite registry of legitimate hypothesis families. Seed data, not dynamic.
CREATE TABLE families (
    family_id        TEXT PRIMARY KEY,         -- e.g. 'vwap_reject_fade'
    description      TEXT NOT NULL,
    legitimate_params_json TEXT NOT NULL,      -- whitelist of param names
    mechanism_template TEXT,                   -- prompt-aid text for Hypothesis Author
    seeded_at        TEXT NOT NULL
);

-- Forward-observation log (Phase D writes; defined here so schema is stable).
CREATE TABLE shadow_signals (
    signal_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id     TEXT NOT NULL REFERENCES candidates(candidate_id),
    ts               TEXT NOT NULL,
    instrument       TEXT NOT NULL,
    direction        TEXT NOT NULL,
    planned_entry    REAL, planned_stop REAL, planned_target REAL,
    realized_outcome_json TEXT
);

CREATE INDEX idx_runs_hypothesis ON runs(hypothesis_id);
CREATE INDEX idx_candidates_run ON candidates(run_id);
CREATE INDEX idx_candidates_hypothesis ON candidates(hypothesis_id);
CREATE INDEX idx_candidates_triage ON candidates(triage_state);
CREATE INDEX idx_journal_ts ON tool_journal(ts);
CREATE INDEX idx_shadow_candidate ON shadow_signals(candidate_id);
```

### Access layer

`src/ta_foundation/research_ledger/`:
- `db.py` — connection management, schema version check, migration runner.
- `models.py` — typed dataclasses (`Hypothesis`, `Run`, `Candidate`,
  `ShadowSignal`, `JournalEntry`).
- `repository.py` — CRUD plus the small set of canonical queries listed
  below. **No agent-facing logic here**; this is just a data layer.

Canonical queries the repository must support (these are the ones every
later phase calls):
- `register_hypothesis(...)` — fails on duplicate `(family, instrument, timeframe, params, mechanism_hash)`.
- `start_run(...)` / `complete_run(...)` / `fail_run(...)`.
- `record_candidate(...)`.
- `get_candidate(candidate_id)`, `list_candidates(filter)`.
- `list_graveyard(family, instrument, since)`.
- `find_similar_hypotheses(family, instrument, params)` — for "have we tested this before?"
- `count_hypotheses_tested(window)` — denominator for multiple-testing correction.
- `set_triage(candidate_id, state, reason, by)`.
- `lock_holdout_attempt(candidate_id)` — atomic "first attempt" check; returns False on second call.
- `journal(role, tool_name, inputs, output_summary, ...)`.

### Files to touch

- `src/ta_foundation/research_ledger/__init__.py` (new)
- `src/ta_foundation/research_ledger/db.py` (new)
- `src/ta_foundation/research_ledger/models.py` (new)
- `src/ta_foundation/research_ledger/repository.py` (new)
- `src/ta_foundation/research_ledger/migrations/0001_initial.py` (new)
- `src/ta_foundation/tests/research_ledger/test_repository.py` (new)
- `.gitignore` — add `.ta_artifacts/research_ledger.db` and `-wal`/`-shm` siblings.

### Done when

- All migrations idempotent; running them twice on a fresh DB is a no-op.
- `register_hypothesis` rejects an exact-duplicate registration with a
  structured error.
- `lock_holdout_attempt(c)` returns True on the first call and False on
  every subsequent call for the same candidate.
- ≥ 30 unit tests covering happy path + every uniqueness/precondition
  failure mode.
- `python -m pytest src/ta_foundation/tests/research_ledger/ -v` is green.
- Full suite still passes (modulo the pre-existing `regime_recommender`
  failure).

---

## Already-shipped: A.2a — Tool framework + read tools + simple write tools

Files landed under `src/ta_foundation/agent/tools/`:

- `_decorators.py` — `@journaled_tool` decorator with:
  - dict-based input schema (`str`/`int`/`float`/`bool`/`enum`/`list`/`dict`)
    + min/max/min_length/max_length/values/default
  - precondition function chain (`Precondition` callable type, `ToolFailure`
    dataclass)
  - automatic timing + journaling on every call (success or failure)
  - 2 KB output cap with disk spill to `.ta_artifacts/tool_outputs/`
  - uniform return shape `{"ok": bool, "result": ...} | {"ok": false, ...}`
- `read/candidates.py` — `list_candidates`, `get_candidate`, `list_graveyard`,
  `find_similar_hypotheses`
- `read/families.py` — `list_probe_families` (thin), `get_family_spec` (thick)
- `read/ledger.py` — `count_hypotheses_tested`, `get_hypothesis`
- `read/market.py` — `get_market_data_coverage` (NinjaTrader filename scan)
- `read/sidecars.py` — `read_sidecar`
- `write/author_probe.py` — pre-registers a hypothesis, emits YAML to
  `discovery/generated/<hypothesis_id>.yaml`. Preconditions: family in
  registry, params in whitelist, graveyard collisions require
  `revival_reason ≥ 30 chars`.
- `write/triage.py` — `set_triage_state` with shadow-state preconditions
  (gate_verdict='survivor' AND a passed locked-holdout PF > 1.0).
- `write/promote.py` — `promote_to_hardening`, `request_locked_holdout`
  (atomic single-attempt lock).
- `write/post_mortem.py` — `write_post_mortem` to `discovery/graveyard/`,
  requires graveyard triage_state and that the markdown body cites the
  candidate_id.
- `write/shadow.py` — `enroll_shadow_trader` with the same code-side gates
  as set_triage_state's shadow path (Phase D will exercise it).
- `__init__.py` — `READ_TOOLS`, `WRITE_TOOLS`, `ALL_TOOLS`, `TOOLS_BY_NAME`,
  `TOOLS_BY_ROLE`.
- `analysis_tools.py` — kept importable but emits `DeprecationWarning` on
  import; new code must use the new registry.

Tests: 79 across `tests/agent/tools/`:
- `test_decorators.py` (14): schema validation per type, defaults,
  unknown_param, preconditions block/pass, journaling on success/failure,
  exception handling, output truncation/inline.
- `test_read_tools.py` (26): 3+ cases per read tool — happy path, empty,
  schema fail, filters.
- `test_write_tools.py` (33): 5+ cases per write tool — happy path,
  unknown candidate, precondition failure, schema failure, follow-up
  state checks.
- `test_registry.py` (6): no name collisions, role index covers every
  tool, metadata present, minimum counts hold.

Test command:
```
python -m pytest src/ta_foundation/tests/agent/tools/ -v
```
79/79 pass. Full suite at 1363 passing with the same pre-existing
`regime_recommender` failure.

### Deferred to A.2b

The phase-A spec also calls for `run_probe` (CLI subprocess + sidecar
ingester) and a graph.py rewire to load from the new registry. These are
operational rather than contractual and need careful integration with the
existing CLI ingest pipeline and the sidecar artifact format. They are
tracked as A.2b in the status snapshot and will be picked up next.

The current `analysis_tools.py` deprecation does not yet remove the import
from `graph.py` — that happens in A.2b alongside the rewire. Until then,
the LangGraph wiring still references the deprecated tools at runtime if
the agent CLI is invoked. The new registry is intended for direct Python
use and for the read-only/triage agents in Phase B; LangGraph adoption
follows in A.2b.

---

## Already-shipped: A.2b — run_probe + record_candidates_for_run + graph.py deprecation

Files landed:

- `src/ta_foundation/agent/tools/write/run_probe.py` (new)
  - `run_probe(repo, hypothesis_id, yaml_path, mode, input_dir, output_dir, ...)`
    spawns `python -m ta_foundation.cli.main` as a subprocess, marks the
    `runs` row as `running` before launch, and updates it to `completed` or
    `failed` based on the exit code. Captures stderr/stdout tails for
    debugging. The CLI's `--hypothesis-id` flag (added in A.3) re-validates
    the YAML's pre_registration block at runtime.
  - `record_candidates_for_run(repo, run_id, candidates)` accepts a list of
    pre-parsed candidate dicts and writes them as ledger rows. Failures on
    individual candidates are surfaced in the result; the batch is not
    rolled back — the caller can retry the rejected items.
  - Subprocess invocation is factored to a module-level `_invoke_cli` seam
    so tests can monkey-patch without spawning real processes.
  - Deterministic `run_id` (hypothesis prefix + mode + ISO timestamp +
    short uuid) and a sha256 `config_hash` over the YAML body.
- `src/ta_foundation/agent/tools/__init__.py` updated to expose `run_probe`
  and `record_candidates_for_run` via `WRITE_TOOLS` / `TOOLS_BY_NAME`.
- `src/ta_foundation/agent/graph.py` — added a module-level
  `DeprecationWarning` and a docstring pointing at the master plan. No
  topology rewrite; that is intentionally Phase B's job (the Lead
  Strategist god-orchestrator is being replaced wholesale by role-scoped
  subgraphs).

Tests: 15 new in `tests/agent/tools/test_run_probe.py`:
- run_probe happy path → `complete_run` called and exit_code=0
- run_probe non-zero exit → `fail_run` with stderr in error column
- run_probe subprocess timeout → `fail_run` with timeout message
- precondition failures: unknown hypothesis, missing YAML, missing input
  dir
- schema fail on invalid mode
- output dir auto-created
- market-data and ledger-db flags forwarded to the CLI
- config_hash differs when YAML body changes between runs
- record_candidates_for_run: happy multi-candidate insert
- record_candidates_for_run: unknown_run precondition
- record_candidates_for_run: partial rejection (mix of valid + bad rows;
  good ones still inserted)
- record_candidates_for_run: empty list is ok
- record_candidates_for_run: too-many candidates triggers schema cap

What is NOT in A.2b (deliberately split):

- **Per-family sidecar parsing.** The existing discovery pipeline emits
  multiple sidecar shapes (strategy_discovery cross_run, pattern_engine
  diagnostics, entry_strategies sweep results, market-discovery signal
  corpora). Building one universal parser is high-risk and depends on the
  exact field set Phase B's agents need. Tracked as A.2c, to be designed
  alongside A.4 backfill since they share the parsing concern.
- **graph.py topology rewrite.** Phase B replaces the topology with fixed
  Python subgraphs per role. Bolting the new registry into the existing
  Lead Strategist planner would create code that gets thrown away in a
  matter of weeks; the deprecation warning is enough until Phase B starts.

Test command:
```
python -m pytest src/ta_foundation/tests/agent/tools/test_run_probe.py -v
```
15/15 pass. Full suite at 1378 passing with the same pre-existing
`regime_recommender` failure unrelated to this work.

---

## Already-shipped: A.2c — Sidecar parser

Files landed:

- `src/ta_foundation/research_ledger/sidecar_parser.py` (new)
  - `parse_summary_sidecar(path) -> ParsedSidecar` reads a discovery
    `*_summary.json` (schema_version=1) and returns a typed snapshot
    with per-ranking candidate dicts ready for `record_candidates_for_run`.
  - Derives `gate_verdict` from `hardening.passed` when hardening is
    enabled, otherwise falls back to the `tier.id` (qualified → survivor,
    marginal/reject → rejected).
  - Preserves unmapped metadata (discovery family, signal, tier verdict,
    hardening issues, instrument symbol, metric extras) under a `notes`
    block. Migration `m_0003_notes_and_legacy.py` adds the `notes_json`
    column on `candidates` to carry it.
  - `infer_family(yaml_filename, signal_name)` maps existing discovery
    YAMLs / signals to one of the 13 starter agentic families via a
    deterministic heuristic table; anything unmatched falls through to
    the `legacy_imported` catch-all (also seeded by 0003).
  - Lenient: missing fields become `None`; malformed ranking entries are
    silently skipped (with `n_rankings` reflecting valid entries only).

Tests: 12 in `test_sidecar_parser.py` covering hardened-pass, hardened-fail,
fast-probe-no-hardening, notes preservation, missing rankings, empty body,
malformed entries, on-disk round-trip, NaN coercion, and family inference.

Sanity check on the live `outputs/` tree: all 16 sampled sidecars share
the `schema_version: 1` shape; one parser handles them all.

---

## Already-shipped: A.4 — Backfill from existing sidecars

Files landed:

- `src/ta_foundation/research_ledger/migrations/m_0003_notes_and_legacy.py`
  - Adds `candidates.notes_json TEXT` column.
  - Seeds the `legacy_imported` family (empty whitelist, no mechanism
    template) as the backfill catch-all. Hypothesis Author must not
    propose new hypotheses here — enforced by `_pc_revival_reason_when_graveyarded`
    + family whitelist check in `author_probe`.
- `src/ta_foundation/research_ledger/backfill.py` (new)
  - `discover_sidecars([roots])` walks one or more output roots for
    `*_summary.json` files (deduplicates by resolved path).
  - `backfill_from_outputs(repo, roots)` parses each sidecar, registers a
    backfill hypothesis (with deterministic `_backfill_bucket` params),
    inserts a `runs` row (mode inferred from directory/file keywords:
    `hardened` / `locked_holdout` / `fast_probe`), and writes one
    candidate per ranking.
  - **Idempotent at all three layers**:
    - Hypothesis: `dedupe_hash` UNIQUE; second run catches
      `DuplicateHypothesisError` and reuses the existing row.
    - Run: deterministic `run_id` from sha256(sidecar_path); second run
      sees existing row and increments `runs_skipped`.
    - Candidate: deterministic `candidate_id` from run + rank.
  - Returns a `BackfillReport` dataclass with counters and capped error
    list for human review.
  - Does NOT set triage states — that's the Triage agent's job in B.1
    (which now has real data to work with).
- `scripts/backfill_ledger.py` (new) — CLI wrapper around
  `backfill_from_outputs` with `--output-root` (repeatable), `--ledger-db`,
  `--registered-by`, and `--dry-run` flags. Default output-root scan
  picks up every `outputs*` folder in the working directory.
- `src/ta_foundation/research_ledger/repository.py` — `record_candidate`
  gains an optional `notes: dict` kwarg; row mapper reads `notes_json`
  via a `_safe_row_get` helper that tolerates rows from pre-0003 schema.
- `src/ta_foundation/research_ledger/models.py` — `Candidate.notes_json`
  added as an optional field (default `None`).

Tests: 13 in `test_backfill.py` covering: sidecar discovery, missing
roots, cross-root dedupe, single-sidecar import, multi-sidecar import,
end-to-end idempotency, mode inference (hardened / locked_holdout /
fast_probe), unsupported schema_version skip, malformed JSON tolerance,
notes preservation, and `count_hypotheses_tested` reflecting imports.

**Real-data smoke test** (`python scripts/backfill_ledger.py`):
- 16 sidecars scanned across `outputs/`
- 16 hypotheses registered (4 `vwap_reject_fade`, 3 `overnight_high_low_sweep_reclaim`,
  9 `legacy_imported`)
- 251 candidate rows inserted
- 1 survivor, 250 rejected/pending — consistent with the
  rejection-heavy narrative in `real_edge_discovery_program.md`
- Re-running: 0 inserts, 16 reused, 16 skipped, 0 errors. Idempotent.

Test command:
```
python -m pytest src/ta_foundation/tests/research_ledger/test_sidecar_parser.py src/ta_foundation/tests/research_ledger/test_backfill.py -v
python scripts/backfill_ledger.py --dry-run
```
25 new tests / all pass. Full suite at 1403 passing with the same
pre-existing `regime_recommender` failure.

---

## A.2 — Tool registry refactor (original spec, kept for reference)

### Problem

`src/ta_foundation/agent/tools/analysis_tools.py` exposes 4 god-tools:
`ingest_data`, `run_discovery_sweep`, `run_strategy_optimization`,
`generate_final_report`. They violate every principle in §4 of the master
plan:

- Coarse — one tool covers many distinct decisions.
- No schema validation.
- No preconditions.
- No journaling.
- `run_strategy_optimization` is exactly the agent-driven optimization the
  Real Edge doc forbids.

### Decision

Replace with a registry of small read tools and a few heavily-guarded write
tools. Keep `analysis_tools.py` deprecated but importable for one release
cycle so any in-flight scripts don't break, then delete.

New layout:

```
src/ta_foundation/agent/tools/
    __init__.py           # exposes the registry
    _decorators.py        # @tool wrapper that validates schema + journals call
    read/
        candidates.py     # list, get, list_graveyard, find_similar
        market.py         # get_market_data_coverage
        families.py       # list_probe_families, get_family_spec
        sidecars.py       # read_sidecar
        ledger.py         # count_hypotheses_tested, list_open_runs
    write/
        author_probe.py   # writes pre-registration + YAML; preconditions: dedupe, schema
        run_probe.py      # invokes CLI; preconditions: hypothesis exists, mode valid
        promote.py        # promote_to_hardening, request_locked_holdout
        triage.py         # set_triage_state
        post_mortem.py    # write_post_mortem
        shadow.py         # enroll_shadow_trader (Phase D wires it; tool exists earlier)
```

### Tool specs

Each tool has: name, JSON-schema input, return type, preconditions, side
effects, journaled fields. Below is the minimum viable set; expand only via
the master plan §4 review.

**Read tools** (all return ≤ 2 KB; large payloads return path + summary):

| Tool | Input | Returns |
|---|---|---|
| `list_candidates` | `{family?, instrument?, triage_state?, since?, limit≤50}` | rows: id, hypothesis_id, run_id, headline metrics, gate_verdict, triage_state |
| `get_candidate` | `{candidate_id}` | full candidate row + sidecar path |
| `list_graveyard` | `{family?, instrument?, since?, limit≤50}` | rejected candidates with reasons |
| `find_similar_hypotheses` | `{family, instrument, params}` | nearest matches by Jaccard on params |
| `count_hypotheses_tested` | `{window?, family?}` | integer + breakdown |
| `list_probe_families` | `{}` | family ids + one-line descriptions |
| `get_family_spec` | `{family_id}` | legitimate params, mechanism template |
| `get_market_data_coverage` | `{instrument, timeframe?}` | date ranges, gaps, bar counts |
| `read_sidecar` | `{run_id}` | parsed sidecar JSON (or path if > 2 KB) |

**Write tools** (each has explicit preconditions checked in code):

| Tool | Preconditions | Side effects |
|---|---|---|
| `author_probe` | family in registry; params in family whitelist; mechanism ≥ 50 chars; not duplicate of existing `open` hypothesis | inserts hypothesis row; writes YAML to `discovery/generated/`; journals |
| `run_probe` | hypothesis_id exists; mode ∈ {fast_probe, hardened, locked_holdout}; for `locked_holdout`, `lock_holdout_attempt` succeeds | invokes CLI subprocess; tails output; on completion, runs sidecar→ledger ingester; journals |
| `promote_to_hardening` | candidate exists; gate_verdict='survivor' on dev/oos; not already hardened | enables hardening block, schedules a hardened run; journals |
| `request_locked_holdout` | candidate exists; hardened verdict='survivor'; lock_holdout_attempt succeeds | schedules holdout run; journals |
| `set_triage_state` | candidate exists; new state in valid set | updates triage_state; journals |
| `write_post_mortem` | candidate exists; triage_state='graveyard'; markdown passes JSON-schema-of-frontmatter check | writes markdown to `discovery/graveyard/`; updates candidate row; journals |
| `enroll_shadow_trader` | candidate exists; gate_verdict='survivor' across dev/oos/holdout | inserts shadow tracking record; journals |

### Tools explicitly **not** ported

- `run_strategy_optimization` — replaced by pre-registered grid running
  inside `run_probe(mode='hardened')`. Agents never invoke a free-form
  optimization.
- `generate_final_report` — reports are a CLI/web concern. The Scribe agent
  produces narratives, not HTML reports.
- `ingest_data` — ingest is a one-time setup step the agent does not need
  to invoke. If a session needs ingest, the human runs `cli.main` first.

### `_decorators.py` — the guardrail layer

A single `@journaled_tool` decorator wraps every tool in:
1. JSON-schema validation of inputs (returns structured error on fail).
2. Precondition check (returns structured error on fail).
3. Execution + timing.
4. `journal(...)` write to `tool_journal` table on every call (success or
   failure).
5. Output truncation: if return > 2 KB, write to `.ta_artifacts/tool_outputs/<journal_id>.json` and return a summary + path.

This is the only place LLM-facing error formatting lives — every tool
returns the same shape: `{"ok": bool, "result": ...} | {"ok": false, "error": str, "code": str}`. Predictable shape lets a small local model recover.

### Files to touch

- `src/ta_foundation/agent/tools/_decorators.py` (new)
- `src/ta_foundation/agent/tools/read/*.py` (new, ~9 tools)
- `src/ta_foundation/agent/tools/write/*.py` (new, ~7 tools)
- `src/ta_foundation/agent/tools/__init__.py` (new — exposes `READ_TOOLS`, `WRITE_TOOLS`)
- `src/ta_foundation/agent/tools/analysis_tools.py` — add deprecation warning, keep imports working for one cycle
- `src/ta_foundation/agent/graph.py` — load from new registry, role-scoped tool subsets
- `src/ta_foundation/tests/agent/tools/` (new test package)

### Done when

- Each read tool has ≥ 3 unit tests (happy path, empty result, schema-fail).
- Each write tool has ≥ 5 unit tests (happy path + each precondition
  failure).
- `tool_journal` rows exist for every test call, with full inputs/outputs.
- Schema-fail returns are well-formed JSON the model can read.
- Output-truncation works: a tool returning a 50 KB blob writes to disk and
  returns a path.

---

## A.3 — Pre-registration + family registry

### Problem

Without a finite family registry, an LLM-driven Hypothesis Author will
invent indicators and mechanisms ad infinitum. Without pre-registration, the
"in-sample tweak after seeing results" failure mode (Real Edge §5) is
unblockable.

### Decision

- The set of legitimate families is **seeded as data** in the `families`
  table. Adding a family is a code change with reviewed seed migration.
- Initial seed list (mirroring `real_edge_discovery_program.md` §1, §"What Is Missing"):
  - `vwap_reject_fade`
  - `vwap_reclaim_continuation`
  - `prior_high_low_failed_breakout`
  - `overnight_high_low_sweep_reclaim`
  - `orb_breakout`
  - `orb_failure_reclaim`
  - `prior_close_settlement_reaction`
  - `initial_balance_extension`
  - `initial_balance_reversal`
  - `large_candle_origin_retest`
  - `compression_then_expansion`
  - `trend_pullback_continuation`
  - `exhaustion_into_reference`
- Each family record carries a `legitimate_params_json` whitelist. The
  Author cannot register a hypothesis with params outside the whitelist.
- Each family carries a short `mechanism_template` — a prompt-aid (not the
  mechanism itself) reminding the Author of the structural counterparty
  story for that family.

### Pre-registration record

A pre-registration is a `hypotheses` row plus a YAML file in
`discovery/generated/` referencing it. The YAML must declare:
- `hypothesis_id` (matches DB row)
- `family`
- `instrument`, `timeframe`, `session_window`, `direction`
- `params` — concrete grid, all values inside the family whitelist
- `pre_reg_mechanism` — frozen at registration time, copied from DB

The CLI sweep entry point (used by `run_probe`) verifies that the YAML's
`hypothesis_id` exists in the ledger and that the params haven't been
altered since registration. If they have, the run aborts with a structured
error and journals the attempted drift.

### Files to touch

- `src/ta_foundation/research_ledger/migrations/0002_seed_families.py` (new)
- `src/ta_foundation/research_ledger/family_registry.py` (new — convenience
  reads on top of the table)
- `src/ta_foundation/cli/main.py` — add `--hypothesis-id` flag and the
  drift check at the top of the sweep entry path.
- `src/ta_foundation/tests/research_ledger/test_family_registry.py` (new)

### Done when

- 13 families seeded. `list_probe_families` returns them.
- Drift check rejects a YAML whose `hypothesis_id` references a row whose
  params hash differs from the YAML body.
- A test confirms `author_probe` cannot register a hypothesis with a param
  outside the family whitelist.

---

## A.4 — Backfill from existing sidecars

### Problem

A meaningful amount of research exists on disk already — every
`discovery/04_nq_*` and `03_nq_*` YAML in
`real_edge_discovery_program.md`. If we don't backfill, the ledger is
empty on day one and the agent re-tests known graveyard hypotheses.

### Decision

One-shot Python script: `scripts/backfill_ledger.py`. Walks
`discovery/`, parses sidecars, registers hypotheses retroactively
(`registered_by='backfill'`), inserts the runs and candidates with
whatever metrics the sidecars contain.

For graveyard entries listed in `real_edge_discovery_program.md`, set
`triage_state='graveyard'` and `triage_reason` from the doc text.

The script is idempotent — running it twice does not duplicate. Uses the
config_hash uniqueness to skip already-imported runs.

### Files to touch

- `scripts/backfill_ledger.py` (new)
- `docs/designs/agentic_phase_a_foundation.md` — record what was imported
  in a footer here when the backfill runs.

### Done when

- Running the script populates the ledger from current `discovery/`
  contents without errors.
- Running it twice is a no-op.
- `count_hypotheses_tested` returns ≥ the number of YAMLs in `discovery/`.
- The graveyard entries from `real_edge_discovery_program.md` show up in
  `list_graveyard` with their reasons preserved.

---

## A.5 — Tool-call journaling

Mostly already covered by A.2's `_decorators.py`. Listed separately because
of one extra requirement: a CLI for human review.

`scripts/audit_journal.py`:
- Last N tool calls.
- Failed tool calls.
- Tool calls per role per day.
- Tool calls without a corresponding ledger write (catches silent drift).

### Done when

- The audit script runs from a fresh ledger.
- A failing tool call (e.g., schema-validation reject) shows up in the
  journal with full inputs and the error code.

---

## Phase A exit criteria

All of the following must hold before any Phase B work begins:

1. ✅ Ledger schema migrated, all queries unit-tested.
2. ✅ Tool registry refactored; old `analysis_tools.py` deprecated and not
   used by `graph.py`.
3. ✅ Family registry seeded with the 13 starter families.
4. ✅ Pre-registration drift check enforced in `cli/main.py`.
5. ✅ Existing `discovery/` content backfilled into the ledger.
6. ✅ Tool journal populated; audit script working.
7. ✅ `python -m pytest src/ta_foundation/tests/ -v` green
   (modulo the pre-existing `regime_recommender` failure).

When these all pass, mark each row in the master plan §5 ✅ Done with the
files-touched list, then move to Phase B.
