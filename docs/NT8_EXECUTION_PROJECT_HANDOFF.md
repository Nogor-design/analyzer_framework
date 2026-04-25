# NT8 Execution Project Handoff

This document is the current handoff point for the NinjaTrader 8 + Python execution project.

Use this when starting a new conversation so the next thread can pick up quickly without re-discovering where the project stands.

## Project Goal

The goal is to run a simple, safe, deterministic first strategy loop on top of the already-validated NT8 execution shell.

Current priorities are:

1. Reliability
2. Safe operator control
3. Repeatable SIM/Playback behavior
4. Clean observability

Not current priorities:

1. GUI polish
2. Strategy complexity
3. Multi-instrument deployment
4. Profit optimization

## Core Operating Model

The system is intentionally simple.

- One trade at a time
- Operator reset means fresh flat start
- No mid-trade recovery/reattach required
- Existing bridge contract remains the source of truth
- Existing shell behavior should not be redesigned

## Current Architecture

### NT8 Shell

Main shell file:

- [TaFoundationExecutionShell.cs](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/TaFoundationExecutionShell.cs)

Bridge root in live use:

- `C:\ta_foundation\bridge`

Important live folders:

- `inbox`
- `archive`
- `outbox`
- `rejected`
- `logs`
- `state`

Important live files:

- `C:\ta_foundation\bridge\logs\execution_shell.log`
- `C:\ta_foundation\bridge\state\shell_state.json`

### Python Strategy Loop

Main strategy loop:

- [real_strategy_loop.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/real_strategy_loop.py)

This loop currently:

- reads NT-exported minute bars
- evaluates once per completed bar
- uses EMA(20) + strong candle body logic
- sends through the existing bridge sender contract
- enforces one-trade-at-a-time behavior
- writes a compact session summary

### Sender Contract

- [bridge_sender.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/bridge_sender.py)

### Minute Exporter

NT8 minute bar exporter:

- [TaFoundationMinuteBarExporter.cs](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/TaFoundationMinuteBarExporter.cs)

Live exported file path:

- `D:\MarketData\NQ 06-26.Last.txt`

### Monitoring

Lightweight soak/operator monitor:

- [soak_monitor.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/cli/soak_monitor.py)

### Operator Controls

Small operator CLI:

- [bridge_operator.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/cli/bridge_operator.py)

Control file:

- `C:\ta_foundation\bridge\state\python_strategy_loop_control.json`

Loop summary file:

- `C:\ta_foundation\bridge\logs\python_strategy_loop_summary.json`

## What Has Been Validated

### Execution Shell / Bridge Validation

The NT8 shell and bridge path were soak-validated for:

- single signal
- three sequential signals
- active-trade rejection
- burst handling
- restart while flat

This means the execution shell itself is not the main unknown at this point.

### Monitoring

The local monitor exists and works as a lightweight operator view.

It reads existing outputs only and does not change shell behavior.

Recent monitor improvements:

- deduplicated stale repeated `HEALTH` lines
- better `last_ready` semantics
- current-session feed emphasis
- shared-read file handling for `shell_state.json`

### Strategy Loop

The first real loop is implemented and tested.

Important behavior already in place:

- completed-bar-only evaluation
- bar-count cooldowns
- same-direction suppression
- unresolved pending hard hold
- strict shell readiness checks
- reject handling
- session-level loss controls
- stale market-data hold
- live summary output

### Live Playback / SIM Evidence

The loop has already taken real Playback trades and completed them through the existing shell.

Confirmed live behaviors:

- strategy-generated entry
- fill
- stop attachment
- target or stop exit
- return to `Idle / Flat`
- summary file updating correctly

A loop-side bug that misclassified normal `ORDER_CANCELLED` OCO cleanup as a reject was fixed.

## Current Strategy Logic

Current first-loop strategy shape:

- Instrument: `NQ`
- Timeframe: `1m`
- Evaluate on completed bar close only
- Long:
  - strong bullish candle
  - close above EMA(20)
  - EMA(20) slope positive
- Short:
  - strong bearish candle
  - close below EMA(20)
  - EMA(20) slope negative
- Strong candle:
  - body > `1.5 x` average body of previous 10 completed bars

This is intentionally simple and low-frequency.

## Current Guardrails

The loop currently enforces:

- shell must be clearly healthy before sends
- one evaluation per completed bar
- one send per completed bar max
- cooldown after send
- longer cooldown after reject
- same-direction suppression
- max signals per session
- max rejects per session
- max consecutive losses
- max session loss in ticks
- hard hold on unresolved lifecycle
- soft/hard hold on stale shell state or stale market data

## Current Operator Controls

The operator CLI was added as the next step after the monitor.

Supported commands:

- `status`
- `summary`
- `pause`
- `resume`
- `stop`
- `run-loop`

The intent is:

- `pause`: keep heartbeats and supervision alive, but block new entries
- `stop`: wait for flat/idle and then stop gracefully
- `status`: quick operational snapshot
- `summary`: raw loop session summary

This is the preferred control path for now instead of building a trading GUI.

## Important Operational Lessons

### 1. Heartbeat Timeout Misconfiguration Caused Noise

At one point the NT8 strategy timeout was set to `20` seconds instead of the intended `60`.

That caused repeated `HEARTBEAT_LOST` noise and unnecessary resets.

Interpret past heartbeat fault noise with that in mind.

### 2. Market Data Export Can Stall

The loop already protects against stale minute-bar export.

If `D:\MarketData\NQ 06-26.Last.txt` stops updating, the loop should hold instead of trading blind.

### 3. `shell_state.json` File Access Needed Hardening

The monitor and loop were updated to use Windows shared-read semantics so they are less likely to interfere with NT8 writes.

This directly targeted warnings like:

- `failed to save persistent state ... shell_state.json is being used by another process`

### 4. GUI Is Not The Current Bottleneck

The main project bottlenecks have been:

- state-file contention
- stale market data
- shell readiness / liveness
- operator control during forward sessions

So the correct next work is control and reliability, not a larger frontend.

## Current Recommended Path Forward

### Phase A: Use the New Operator Controls Live

Run the loop through the operator CLI and verify:

- `pause` blocks new entries cleanly
- `resume` clears the block cleanly
- `stop` drains safely while flat
- `status` and `summary` are enough for the operator

This should be validated in Playback and Sim.

### Phase B: Forward-Sim Reliability Sessions

Run longer sessions with:

- monitor running
- operator CLI available
- minute exporter running
- intended heartbeat timeout configured in NT8

Watch for:

- stale export holds
- stale shell-state holds
- repeated state-save warnings
- unexpected hard holds
- summary correctness across multiple trades

### Phase C: Only Then Consider a Slightly Nicer Operator Surface

If the operator workflow is solid, then consider:

- a tiny local control panel
- or a slightly more integrated terminal workflow

Do not jump to a large trading GUI yet.

## What Not To Do Next

- Do not redesign `TaFoundationExecutionShell.cs`
- Do not replace the bridge contract
- Do not add a database
- Do not add a heavy web frontend
- Do not add tick-driven live decision logic yet
- Do not broaden into multi-instrument deployment yet
- Do not optimize for profitability before operational stability is proven

## Most Relevant Files

Primary implementation files:

- [real_strategy_loop.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/real_strategy_loop.py)
- [bridge_operator.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/cli/bridge_operator.py)
- [soak_monitor.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/cli/soak_monitor.py)
- [bridge_sender.py](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/bridge_sender.py)
- [TaFoundationMinuteBarExporter.cs](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/TaFoundationMinuteBarExporter.cs)
- [TaFoundationExecutionShell.cs](/D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/TaFoundationExecutionBridge/TaFoundationExecutionShell.cs)

Relevant tests:

- [test_python_strategy_loop.py](/D:/Backup/projects/PythonProject/ta_foundation/tests/execution_shell/test_python_strategy_loop.py)
- [test_bridge_operator.py](/D:/Backup/projects/PythonProject/ta_foundation/tests/test_bridge_operator.py)
- [test_soak_monitor.py](/D:/Backup/projects/PythonProject/ta_foundation/tests/test_soak_monitor.py)

Existing runbooks:

- [RUNBOOK.md](/D:/Backup/projects/PythonProject/ta_foundation/tests/execution_shell/RUNBOOK.md)
- [RUNBOOK_PHASE2.md](/D:/Backup/projects/PythonProject/ta_foundation/tests/execution_shell/RUNBOOK_PHASE2.md)

## Suggested Prompt For A New Conversation

If starting a new thread, point it to this file and say something like:

```text
Read docs/NT8_EXECUTION_PROJECT_HANDOFF.md first.
We are continuing the NinjaTrader 8 + Python execution project from that exact state.
Use the existing bridge/shell/monitor/operator architecture.
Do not redesign the system.
First confirm the current status from the handoff doc, then help me with the next implementation step.
```

## Current Recommendation

If a new conversation starts today, the best next implementation step is:

1. Live-validate the new operator control flow with the real loop
2. Run a longer forward-sim session with monitor + operator CLI + exporter together
3. Only then decide whether a slightly nicer operator surface is worth adding
