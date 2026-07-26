# TaFoundationExecutionShell — Phase 2 SIM Account Test Runbook

Phase 2 tests validate real managed-order behaviour on a Sim101 account.
Unlike Phase 1 (DryRun), Phase 2 submits actual orders to the NT8 simulator
and inspects fills, stop attachment, and position state via the outbox/log.

---

## Prerequisites

### 1. NinjaTrader 8 Setup

| Parameter | Required Value |
|---|---|
| `DryRunMode` | **false** |
| `Account` | **Sim101** (or match `phase2.sim_account` in config) |
| `EnableOutboxEvents` | true |
| `InboxDirectory` | `<bridge_root>/inbox` |
| `ArchiveDirectory` | `<bridge_root>/archive` |
| `RejectDirectory` | `<bridge_root>/rejected` |
| `OutboxDirectory` | `<bridge_root>/outbox` |
| `LogFilePath` | `<bridge_root>/logs/execution_shell.log` |
| `StateFilePath` | `<bridge_root>/state/shell_state.json` |
| `RequireInstrumentMatch` | true — set `instrument` in config to match |
| `HeartbeatTimeoutSeconds` | match `heartbeat_timeout_seconds` in config |

### 2. Shell Changes Required (Phase 2 observability)

The following changes must be deployed to `TaFoundationExecutionShell.cs`
before running Phase 2 tests:

- **SHELL_READY** outbox event written in `DataLoaded` after recovery
- **FILLED** event enriched with `remaining_qty` and `pos_side` fields
- **STOP_ATTACHED** event enriched with `pending_stop_price`
- **STOP_WORKING** outbox event written in `OnOrderUpdate` when a stop order becomes Working

These changes are implemented in the `CandleDiscovery` branch.

### 3. Market Data

The chart must be receiving live ticks (market hours) or running **Market Replay**
with tick data. Sim101 fills require price movement — a static chart will not fill.

### 4. Starting State

Sim101 account must be **flat** (no open positions or working orders) before running
the suite. Run `P2-G01` (EXIT_ALL cleanup) first if needed.

---

## Running the Suite

```bash
# From tests/execution_shell/
python run_phase2_tests.py

# Run specific groups only
python run_phase2_tests.py --groups A B C

# Disable human checkpoints (fully unattended)
python run_phase2_tests.py --no-checkpoints

# List all Phase 2 test IDs
python run_phase2_tests.py --list

# Skip directory pre-flight checks
python run_phase2_tests.py --no-preflight
```

---

## Test Groups and Execution Order

Groups always run in this fixed order: **E → A → B → C → D → F → G**

| Group | Label | Notes |
|---|---|---|
| E | Restart & Recovery | Run first — validates SHELL_READY before other tests |
| A | Entry / Fill Detection | Core fill path; all subsequent groups depend on fills |
| B | Stop Attachment & Movement | Depends on fills from Group A pattern |
| C | Exit Management | Depends on fills and stop attachment |
| D | Conflict / Sequencing | Tests gate behaviour on live fills |
| F | Heartbeat / Liveness | **Run last** — F01/F02 permanently disable intake |
| G | Cleanup / Teardown | Always runs last; flattens any residual position |

---

## Automation Tiers

### FULLY_AUTOMATED
No user interaction required. Tests pass/fail based on file system evidence
(outbox events, log records, state file).

### SEMI_AUTOMATED
The test pauses at checkpoint prompts. The user observes the NT8 UI and
presses Enter to confirm or `f` to fail. Use `--no-checkpoints` to skip all
prompts (checkpoints auto-pass — reduces coverage but allows unattended runs).

### MANUAL_REQUIRED
Always skipped by the runner. The procedure is printed to console. The operator
runs it manually and records the result outside the automated report.

Manual tests in Phase 2:
- **P2-E03**: Restart NT8 while in position — verifies recovery
- **P2-E04**: Post-recovery entry blocking
- **P2-F03**: Heartbeat loss with FlattenOnHeartbeatLoss=false

---

## Test Configuration (`test_config.json`)

```json
"phase2": {
  "sim_account": "Sim101",
  "entry_qty": 1,
  "default_stop_ticks": 50,
  "default_target_ticks": 200,
  "move_stop_ticks": 10,
  "partial_qty": 1,
  "fill_wait_seconds": 30,
  "stop_attach_wait_seconds": 15,
  "show_checkpoints": true
}
```

**Key settings:**
- `default_stop_ticks`: Keep this at or below the active template hard-stop cap. The readiness path now uses `50` for the runner template.
- `default_target_ticks`: Keep this wide (`200`) so tests do not depend on short-window market travel after the fill.
- `entry_qty`: Set to **2** to enable partial-exit tests (P2-C02, P2-C03, P2-C04)
- `fill_wait_seconds`: Increase to 60 if fills are slow on replay
- `show_checkpoints`: Set to `false` for fully unattended runs

---

## Heartbeat Warning

**Group F tests permanently disable `signalIntakeEnabled`.**

After running F01 or F02, NinjaTrader must be restarted before further testing.
This is the same limitation as Phase 1 Group C.

Run Group F as the last automated group (before G cleanup). The runner enforces
this order automatically.

---

## Partial Exit Tests

Tests P2-C02, P2-C03, and P2-C04 require `entry_qty >= 2`. With the default
`entry_qty=1`, these tests automatically skip. To enable them:

```json
"phase2": {
  "entry_qty": 2,
  "partial_qty": 1
}
```

---

## Evidence Model

Phase 2 assertions rely on four evidence sources:

| Source | What it proves |
|---|---|
| Outbox `.evt.json` files | Shell processed the message; order lifecycle events (FILLED, STOP_ATTACHED, STOP_WORKING) |
| Log `pos=`/`qty=` fields | Live NT8 position state on every log line |
| HEALTH snapshot log events | Stop order ID nullity; pending stop price; shell mode |
| User checkpoints (SEMI_AUTOMATED) | Visual confirmation of NT8 UI state |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| No FILLED event after ENTER_LONG | Chart not receiving ticks | Start Market Replay or run during market hours |
| ENTER_LONG rejected immediately | Instrument mismatch | Set `instrument` in config to match chart |
| No STOP_ATTACHED after fill | Shell changes not deployed | Redeploy `TaFoundationExecutionShell.cs` with Phase 2 changes |
| SHELL_READY not in outbox (P2-E01 fails) | Old shell without SHELL_READY change | Deploy Phase 2 shell changes |
| P2-B03 (unsafe stop) not rejected | Stop safety check disabled in shell | Verify `EnableStopSafetyCheck=true` in NT8 parameters |
| All tests fail with GATE | Strategy still in Disabled/Faulted mode | Restart NT8 strategy (previous run left intake=false) |
| fill_wait_seconds timeout | Slow Sim fills on replay | Increase `fill_wait_seconds` to 60+ in config |
| Checkpoint prompt times out | Non-interactive terminal | Pass `--no-checkpoints` flag |

---

## Relationship to Phase 1

Phase 2 uses the same `test_config.json`, `bridge_io.py`, `log_parser.py`,
and `outbox_parser.py` as Phase 1. Key differences:

| Aspect | Phase 1 | Phase 2 |
|---|---|---|
| `DryRunMode` | true | false |
| Position state | Always Flat | Real Sim101 fills |
| Stop orders | Never placed | Placed and tracked |
| Fill detection | Not applicable | FILLED outbox event |
| Evidence model | File-only | File + user checkpoints |
| Human interaction | None | SEMI_AUTOMATED checkpoints |

---

## Output

Reports are written to `test_reports/` (configurable via `report_output_dir`):

- `phase2_results_{timestamp}.json` — machine-readable structured results
- `phase2_results_{timestamp}.md` — human-readable Markdown summary

---

*See `RUNBOOK.md` for Phase 1 (DryRun) test instructions.*
