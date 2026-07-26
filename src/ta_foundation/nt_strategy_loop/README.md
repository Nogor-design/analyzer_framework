# Autonomous NinjaTrader Strategy Loop

`ta_foundation.nt_strategy_loop` removes the manual babysitting from NinjaTrader
strategy development. It can author a strategy, install it, let NinjaTrader
auto-compile it, read the compile errors, repair until clean, then run the
optimizer and analyze the results — all from one command.

> **Design reference:** `docs/designs/autonomous_ninjatrader_strategy_loop.md`
> **Status reference:** `docs/handoffs/observe_compile_strategy_analyzer_parity_2026-05-19.md`

---

## What it does

```
StrategySpec ─► author .cs ─► install into NinjaTrader ─► observe auto-compile
                                                              │
                              ┌── compile errors ◄────────────┤
                              ▼                                │
                      repair (.cs)  ──────────────────────────►┘   (loop)
                              │
                       compile-clean
                              │
                              ▼
            generate seed template ─► optimizer RunBatch ─► ingest results
                              │
                              ▼
                  analyze vs. guardrails ─► candidate / archive
```

The guiding rule: **NinjaTrader is the worker; TA Foundation is the memory and
conductor.** NinjaTrader owns compilation and Strategy Analyzer; this package
owns intent, durable artifacts, repair policy, and the decision record.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| NinjaTrader 8 | Installed at the standard `Documents\NinjaTrader 8` location. |
| `BatchStrategyOptimizerAddOn` | The C# AddOn that handles the `ObserveCompile` and `RunBatch` IPC actions. Must be compiled and authorized in NinjaTrader. |
| `ta_foundation` installed | `pip install -e .` from the repo root. |
| Ollama (optional) | Only needed for `--repair-llm`. A local `ollama serve` with a code model such as `qwen3-coder:30b`. |

NinjaTrader cold-starts slowly — allow **1–2 minutes** after launch/authorization
before sending compile or optimizer commands. The `ensure-nt-ready` command
handles the login, AddOn-authorization prompt, and the wait for you.

---

## Quick start

```powershell
# 1. Make sure NinjaTrader is up, logged in, and the AddOn is authorized.
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready

# 2. Run the whole loop from a strategy spec.
python -m ta_foundation.nt_strategy_loop.cli full-loop --spec my_strategy.json
```

Every run writes a durable, append-only session folder under
`.ta_artifacts/nt_strategy_lab/sessions/<session_id>/` — see
[Session folder layout](#session-folder-layout).

---

## The strategy spec

A `StrategySpec` is a small JSON file. `repair-loop` and `full-loop` take one
via `--spec`:

```json
{
  "strategy_name": "MyCrossBot",
  "family": "sma_cross",
  "intent": "9/21 SMA cross on NQ with a fixed tick stop and target.",
  "parameters": {
    "FastPeriod": 9,
    "SlowPeriod": 21,
    "ProfitTargetTicks": 24,
    "StopLossTicks": 16,
    "Reverse": false
  },
  "risk_note": "Backtest only — not cleared for live."
}
```

| Field | Required | Meaning |
|---|---|---|
| `strategy_name` | yes | The C# class name **and** the `.cs` filename. |
| `family` | yes | Selects the in-tree source renderer. Built in: `sma_cross`, `sma_cross_smoke`. |
| `intent` | no | Free text; included in the repair prompt. |
| `parameters` | no | Family-specific knobs (see the family's renderer in `authoring.py`). |
| `risk_note` | no | Free text carried into the session record. |

An unknown `family` raises a clear error — register a renderer with
`authoring.register_family(...)` or hand off to the NinjatraderDocScrapper
strategy factory.

---

## Commands

Run any command with `--help` for its full flag list.

### `ensure-nt-ready`
Starts/logs into NinjaTrader, clicks the AddOn authorization prompt, and waits
for the Control Center. Run this before any live compile/optimize command.

```powershell
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready [--restart]
```

### `observe-compile`
Installs one `.cs` file, lets NinjaTrader auto-compile it, and reports the
parsed compile status/errors. The lowest-level building block.

```powershell
python -m ta_foundation.nt_strategy_loop.cli observe-compile `
  --source "D:\path\MyStrategy.cs" --strategy-name MyStrategy --out result.json
```

### `repair-loop`
Authors a strategy from a spec, then installs → observes → repairs in a loop
until compile-clean or a [stop reason](#decisions-and-stop-reasons) fires.

```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json --max-repair-attempts 5
```

### `optimizer-bridge`
Runs the optimizer control plane against an existing **compile-clean** `.cs`:
generates a seed template, runs `RunBatch`, ingests the optimization CSV, and
scores it against the guardrails.

```powershell
python -m ta_foundation.nt_strategy_loop.cli optimizer-bridge `
  --session-dir ".ta_artifacts\nt_strategy_lab\sessions\<id>" `
  --compile-clean-source "<id>\compile_clean\MyStrategy.cs"
```

### `full-loop`
`repair-loop` + `optimizer-bridge` end to end. The usual entry point.

```powershell
python -m ta_foundation.nt_strategy_loop.cli full-loop `
  --spec my_strategy.json `
  --instrument "NQ 06-26" --max-drawdown 2500 --min-trades 10 --min-profit-factor 1.5
```

### `generate-seed-template`
Builds a provisional Strategy Analyzer seed XML from a compile-clean `.cs`
(used internally by the optimizer bridge; also runnable standalone).

### `smoke-loop`
A tiny author/compile/analyze loop for verifying the plumbing end to end.

---

## Compile modes: `live` vs `fixture`

`repair-loop`, `full-loop`, and `smoke-loop` accept `--compile-mode`:

- **`live`** (default) — talks to a running NinjaTrader through the AddOn's
  `ObserveCompile` IPC. This is the real loop.
- **`fixture`** — compile observations are supplied programmatically. Used by
  the test suite and for dry runs without NinjaTrader.

---

## LLM-assisted repair

By default the repair loop uses **deterministic heuristics only** — it fixes
class-name/filename mismatches and missing `using` directives. That covers the
common compile errors with no network calls and no surprises.

For errors the heuristics cannot fix, opt in to an **LLM repair pass** backed by
a local Ollama model:

```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json `
  --repair-llm `
  --repair-llm-model qwen3-coder:30b `
  --repair-llm-url http://localhost:11434
```

How it works:

1. The deterministic heuristics always run **first**.
2. If they decline, the loop sends the spec, the current source, and the
   NinjaTrader compiler errors to the Ollama model and asks for a corrected
   `.cs` file.
3. The model's response is extracted, sanity-checked (must look like a
   NinjaScript strategy), and used as the next repair attempt.

It is **fail-soft**: if Ollama is unreachable or the model returns something
that is not NinjaScript, the callback declines the repair and the loop halts
with a clear `repair_declined` stop reason rather than crashing. The same flags
are available on `full-loop`.

> Requires a running `ollama serve`. A code-tuned model is strongly recommended.

---

## Session folder layout

Each run produces an append-only session under
`.ta_artifacts/nt_strategy_lab/sessions/<session_id>/`:

```
<session_id>/
  session.json              # session metadata
  strategy_spec.json        # the spec this run was built from
  source_request.md         # human-readable intent
  attempts/
    attempt_001/
      <Strategy>.cs         # the source for this attempt
      compile_status.json   # parsed ObserveCompile result
      repair_prompt.md      # what the repair step was asked to fix
      repair_summary.md     # what the repair step changed
    attempt_002/ ...
  compile_clean/            # the first compile-clean source + status
  optimizer/
    optimizer_analysis.json # guardrail verdict
    nt_output/              # ingested *_Optimization.csv
  decisions/
    STRATEGY_LOOP_SUMMARY.md
    NEXT_ACTION.md
  manifest.json             # index of everything above
```

Every repair attempt keeps its own folder, so a bad loop can always be
inspected afterward. Start any review at `decisions/STRATEGY_LOOP_SUMMARY.md`.

---

## Decisions and stop reasons

`full-loop` ends with one **decision**:

| Decision | Meaning | Exit code |
|---|---|---|
| `candidate` | Compile-clean and at least one optimizer row cleared the guardrails. | 0 |
| `archive` | Compile-clean but no optimizer row passed. | 2 |
| `incomplete` | Optimizer did not reach a terminal state. | 2 |
| `halted` | Repair never reached compile-clean. | 3 |

The repair loop halts on one of these **stop reasons** (`stop_reason.code`):

| Code | Meaning |
|---|---|
| `compile_clean` | Success — NinjaTrader auto-compile succeeded. |
| `max_attempts` | Exhausted `--max-repair-attempts`. |
| `repeated_signature` | The same compiler error came back; repair changed nothing. |
| `repair_declined` | No heuristic fix applied and no LLM repair was produced. |
| `peer_compile_block` | Another strategy file is blocking the whole `bin\Custom` rebuild — fix that file. |
| `stale_assembly` | `NinjaTrader.Custom.dll` was not rewritten; auto-compile likely failed silently. |
| `worker_error` / `stale_observation` | The compile observer timed out or went quiet. |

---

## Human gates and safety

The loop is autonomous for **mechanical** work — generating code, installing,
compiling, repairing, running Strategy Analyzer, ingesting and analyzing
results. It is **not** autonomous for risk decisions.

A human still owns:

- overwriting an existing strategy file the loop does not own
  (`--overwrite` is required and should be used deliberately),
- accepting a major behavioral rewrite after optimization analysis,
- copying final templates into a deployment folder,
- enabling real-time / paper / live trading,
- marking a strategy as approved.

A `candidate` decision means *worth reviewing*, not *ready to trade*. Run
robustness, walk-forward, and neighborhood-stability checks before any live
exposure.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Commands hang or time out right after launch | NinjaTrader still cold-starting. Wait 1–2 minutes, or run `ensure-nt-ready` first. |
| `peer_compile_block` stop reason | A different `.cs` in `bin\Custom` has compile errors and blocks the whole assembly. Open the file named in the message and fix or quarantine it. |
| `stale_assembly` stop reason | NinjaTrader did not rebuild `NinjaTrader.Custom.dll`. Confirm the AddOn is authorized and NinjaTrader is responsive. |
| `--repair-llm` always declines | `ollama serve` is not running, or the model name is wrong. The warning prints to stderr. |
| `optimizer-bridge` reports `incomplete` | NinjaTrader/AddOn not running or not authorized. Re-run `ensure-nt-ready`, then retry the bridge against the same session. |
| `no in-tree renderer for family ...` | The spec's `family` has no registered source renderer. Use a built-in family or register one. |
