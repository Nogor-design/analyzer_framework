# Agentic Research + NinjaTrader Strategy Knowledge Base

This note captures the current working map for the autonomous research,
NinjaTrader validation, and execution-bridge ecosystem. Read this before
building new strategy-generation, optimizer, shadow, or execution automation so
existing pieces are reused instead of recreated.

## Core Rule

LLMs and agents may propose hypotheses, summarize evidence, inspect artifacts,
and recommend next actions. Deterministic Python/C#/NinjaTrader code must own
gates, risk, validation, execution, and promotion decisions. Live or paper
deployment remains a human-approved step.

## Current Local Components

### Agentic Research Program

Primary docs:

- `docs/designs/agentic_research_program.md`
- `docs/designs/agentic_phase_a_foundation.md`
- `docs/designs/agentic_phase_b_read_only_agents.md`
- `docs/designs/agentic_phase_c_authoring_agents.md`
- `docs/designs/agentic_phase_d_forward_observation.md`

Implemented source areas:

- `src/ta_foundation/agent/scheduler.py`
- `src/ta_foundation/agent/roles/hypothesis_author.py`
- `src/ta_foundation/agent/roles/sweep_operator.py`
- `src/ta_foundation/agent/tools/write/promote.py`
- `src/ta_foundation/agent/tools/write/shadow.py`
- `src/ta_foundation/research_ledger/`
- `src/ta_foundation/shadow/`

Known state:

- Hypothesis Author exists and writes structured proposals under
  `runs/inbox/proposals`.
- Sweep Operator exists and runs accepted hypotheses through `fast_probe` and
  optionally `hardened`; it does not spend the locked holdout.
- Promotion tooling includes one-shot locked holdout and shadow enrollment.
- Shadow runner, health reporting, and CUSUM edge-decay detection exist.
- Phase C.5 multiple-testing denominator work is still pending.
- Phase D slippage realism and portfolio correlation tracking are still open.
- A known Phase D limitation remains: decayed candidates with open positions
  need careful handling so position tracking is not silently lost after
  auto-disable.

### NinjaTrader Strategy Loop

Primary doc:

- `docs/designs/autonomous_ninjatrader_strategy_loop.md`

Implemented source area:

- `src/ta_foundation/nt_strategy_loop/`

Useful commands:

- `ensure-nt-ready`
- `observe-compile`
- `repair-loop`
- `optimizer-bridge`
- `full-loop`
- `smoke-loop`
- `generate-seed-template`

Known state:

- `full-loop` chains compile/repair and optimizer/backtest evidence collection.
- Current in-tree authoring renderers are narrow, mainly `sma_cross` and
  `sma_cross_smoke`.
- Unknown strategy families should be handed to a factory/generator layer, not
  reimplemented ad hoc in `nt_strategy_loop.authoring`.
- `optimizer_bridge.py` uses the existing optimizer web/AddOn machinery to
  create sessions, generate XML templates, run Strategy Analyzer batches, ingest
  CSV, and apply guardrails.
- `analyzer.py` has basic guardrails for drawdown, trade count, and profit
  factor.
- `policy.py` owns stop reasons such as compile clean, repeated error
  signature, max attempts, repair declined, stale assembly, and worker errors.

### StrategyDiscoveryFilter

Primary files:

- `src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.md`
- `src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.cs`

Use:

- Quick NinjaTrader validation harness for discovery-report conditions.
- Good for testing whether discovered filters, regimes, sessions, direction
  policies, stop/target settings, exit policy, and daily risk constraints
  improve a generic entry.

Limit:

- The current strategy uses an EMA crossover entry plus discovery filters. It
  validates filter/regime value, not an exact clone of every discovered strategy
  family.

### TaFoundationExecutionBridge

Primary files:

- `src/ta_foundation/strategies/TaFoundationExecutionBridge/README_execution_bridge.md`
- `src/ta_foundation/strategies/TaFoundationExecutionBridge/signal_contract.md`
- `src/ta_foundation/strategies/TaFoundationExecutionBridge/TaFoundationExecutionShell.cs`
- `src/ta_foundation/strategies/TaFoundationExecutionBridge/bridge_sender.py`
- `src/ta_foundation/strategies/TaFoundationExecutionBridge/real_strategy_loop.py`
- `src/ta_foundation/strategies/TaFoundationExecutionBridge/tests/execution_shell/RUNBOOK.md`

Use:

- Later-stage dry-run, Sim101, paper, or supervised execution after research,
  NT validation, locked holdout, and shadow evidence.
- Bridge health, outbox events, rejected messages, slippage realism, duplicate
  detection, heartbeat status, daily lockout, stale-message handling, and
  operator controls are useful manager-monitoring inputs.

Limit:

- Do not let LLM agents send trade commands directly.
- The documented/tested contract is the file-queue inbox/outbox path under
  `C:\ta_foundation\bridge`. Some Python helpers also reference socket/runtime
  submission. `D:\NinjaAccountManager` appears to be the direct socket runtime
  that this path should target; standardize the transport contract before
  relying on live routing.

## External Components Already Available

### NinjaTrader Optimizer AddOn

External repo:

- `D:\ninjatraderOptimizer`

Known state:

- Batch Strategy Analyzer AddOn works against NinjaTrader 8.1.6.3.
- RunBatch IPC watches `C:\temp\nt8_command.json` and writes
  `C:\temp\nt8_status.json`.
- Compile observation reports `compileBlockReason` and `workerKind`.
- Revalidate optimizer-template loading after NT restart.
- Keep the NT version pinned unless the AddOn is revalidated.

### NinjatraderDocScrapper Strategy Factory

External repo:

- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper`

Primary files:

- `README.md`
- `strategy_factory/README.md`
- `strategy_factory/factory.py`
- `strategy_factory/specs/validator.py`
- `strategy_factory/generators/python_template_generator.py`
- `strategy_factory/generators/ninjascript_generator.py`
- `strategy_factory/generators/ninjascript_template_generator.py`
- `strategy_factory/install_ninjatrader_outputs.py`

Use:

- Best fit for the deterministic strategy-realization layer between research
  candidates and `ta_foundation.nt_strategy_loop`.
- Starts from a canonical JSON spec and emits:
  - ta_foundation StrategyTemplate JSON
  - NinjaScript `.cs`
  - NinjaTrader StrategyTemplate XML
  - manifest/parity artifacts
- Safer default than freeform RAG/code generation because it can constrain
  modules, parameters, risk cards, and parity checks.

Limits:

- The validator lists several entry types, including MA cross, opening range
  breakout, RSI threshold, price level, and large candle reversal.
- Do not assume every validator-supported entry has production-ready
  deterministic NinjaScript generation. Confirm generator support before routing
  an autonomous candidate through a family.
- Use freeform docs/RAG generation only as a fallback to create or repair new
  factory modules, not as the default autonomous path.

### NinjaAccountManager

External repo:

- `D:\NinjaAccountManager`

Primary files:

- `README.md`
- `AGENTS.md`
- `main.py`
- `core/config.py`
- `core/strategy_api.py`
- `core/strategy_bridge.py`
- `core/nt_client.py`
- `ninjascript/NinjaAccountManager.cs`
- `tools/strategy_live_smoke.py`
- `tools/strategy_live_suite.py`
- `tests/test_strategy_api.py`
- `tests/test_strategy_bridge.py`

Use:

- Real-time NinjaTrader 8 desktop account monitor and execution runtime.
- Python runs a WebSocket server on `ws://127.0.0.1:8765/ws`.
- The bundled NinjaScript indicator connects as a WebSocket client and streams
  account, position, order, market-data, and bar events into Python.
- Python exposes a direct localhost strategy API on `tcp://127.0.0.1:8766` for
  `ta_foundation` strategy commands.
- The strategy API uses one JSON command per line from client to server and one
  JSON event per line from server to client.
- On connect, the server immediately pushes `STATE_SNAPSHOT` and later pushes
  state snapshots after material state changes.
- Supported strategy commands include `HEARTBEAT`, `ENTER_LONG`, `ENTER_SHORT`,
  `EXIT_ALL`, `SCRATCH`, `MOVE_STOP`, `CANCEL_WORKING`, and
  `FLATTEN_AND_DISABLE`.
- Runtime events include `ACCEPTED`, `REJECTED`, `ENTRY_SUBMITTED`, `FILLED`,
  `PARTIAL_FILL`, `STOP_ATTACHED`, `TARGET_ATTACHED`, `STOP_WORKING`,
  `TARGET_WORKING`, `EXIT_FILLED`, `ERROR`, `HEARTBEAT_TIMEOUT`, and
  `STATE_SNAPSHOT`.
- Legacy file-bridge compatibility exists behind `legacy_file_bridge_enabled`,
  but it is disabled by default and should be treated as a fallback only.

Fit with `ta_foundation`:

- This is the likely runtime endpoint for
  `src/ta_foundation/strategies/TaFoundationExecutionBridge/bridge_sender.py`
  and `real_strategy_loop.py` when they use socket submission.
- It should be the preferred supervised Sim101/paper execution runtime after a
  strategy has passed research, NinjaTrader validation, locked holdout, and
  shadow monitoring.
- It gives the manager process live account, position, order, heartbeat, fill,
  protective-order, and runtime-state events that can be compared with the
  research ledger and shadow expectations.
- Its tests and smoke tools can be reused for execution-path validation instead
  of creating a new bridge test harness.

Limits:

- This is an execution/runtime monitor, not a research or optimizer engine.
- It should not be used to skip research hardening, locked holdout, Strategy
  Analyzer validation, or human approval.
- Port ownership matters: `8765` is the NT WebSocket server and `8766` is the
  direct strategy API. Avoid starting duplicate runtimes on the same ports.
- Follow `AGENTS.md` when editing NinjaScript. Important existing fixes include
  using `System.Web.Script.Serialization.JavaScriptSerializer`, submitting
  orders on the NT thread, using `Globals.RandomDispatcher`, and not relying on
  Newtonsoft.Json.

## Recommended Combined Pipeline

```text
Agentic Research
  -> deterministic discovery fast_probe/hardening
  -> promising candidate in research ledger
  -> optional StrategyDiscoveryFilter quick NT validation
  -> Strategy Factory spec generation for exact strategy realization
  -> generated Python template + NinjaScript + XML
  -> nt_strategy_loop observe-compile / repair-loop / optimizer-bridge
  -> Strategy Analyzer evidence and guardrail analysis
  -> locked holdout request
  -> shadow enrollment and health/decay monitoring
  -> NinjaAccountManager / ExecutionBridge dry-run or Sim101 only after approval
  -> manager report: promote, refine, retire, or request human review
```

The build plan for turning this map into an autonomous state machine is
`docs/designs/autonomous_research_to_paper_trade_loop_build_plan.md`.

## Integration Work Still Needed

Add a Strategy Factory bridge in `ta_foundation`, probably under
`src/ta_foundation/nt_strategy_loop/`:

- Convert `candidate_id` or hypothesis payloads into Strategy Factory spec JSON.
- Call `strategy_factory.factory.build_strategy_outputs(...)` by dependency,
  submodule, vendored package, or controlled subprocess with explicit
  `PYTHONPATH`.
- Route generated `.cs` into `observe-compile` / `repair-loop`.
- Route generated XML into `optimizer-bridge` / RunBatch.
- Route generated `.ta_template.json` into Python parity/backtest/shadow checks.
- Persist spec path, factory manifest, generated NinjaScript path, XML path,
  optimizer session id, result CSVs, parity report, and manager decision in the
  research ledger.
- Add a runtime adapter that standardizes `ta_foundation` socket execution
  commands/events against `D:\NinjaAccountManager`'s JSON-lines API on
  `tcp://127.0.0.1:8766`.
- Capture `STATE_SNAPSHOT`, fills, rejected commands, heartbeat faults,
  protective-order events, and flatten/disable events as manager evidence.

Start with the narrowest reliable family, then expand module cards:

1. MA cross / trend-filter strategy realization.
2. Opening range breakout once generator parity is confirmed.
3. RSI / price-level / large-candle reversal only after deterministic
   NinjaScript and Python-template parity are verified.

## Manager Oversight Checks

The manager process should watch for:

- Candidate spends locked holdout only once.
- No strategy moves to shadow without passing locked holdout.
- Generated artifacts are linked back to the candidate ledger.
- Compile repair stops on repeated signatures instead of looping forever.
- Optimizer runs satisfy minimum trades, max drawdown, and profit-factor gates.
- Python template and NinjaScript parameters remain in parity.
- Strategy Analyzer results and Python expectations are materially consistent.
- NinjaAccountManager runtime state, fill events, rejected commands, and
  heartbeat/protective-order faults are reviewed before dry-run/paper escalation.
- Slippage and execution events are reconciled with research-ledger and shadow
  expectations.
- Retired ideas go to the graveyard with reasons so future agents avoid repeats.

## Do Not Rebuild

- Do not create a second research ledger.
- Do not create a second locked-holdout mechanism.
- Do not create a second shadow runner before checking `src/ta_foundation/shadow`.
- Do not create another NT compile observer before checking `nt_strategy_loop`.
- Do not create a new generic execution bridge before checking
  `TaFoundationExecutionBridge`.
- Do not create a new account/order/position monitor or direct socket strategy
  runtime before checking `D:\NinjaAccountManager`.
- Do not build freeform NinjaScript generation as the first path when Strategy
  Factory can represent the candidate deterministically.
