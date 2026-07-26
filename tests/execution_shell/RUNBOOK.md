# NT8 Bridge Test Suite — Runbook

## Overview

This test suite validates `TaFoundationExecutionShell.cs` through its filesystem bridge
without mocking any NinjaTrader internals. NT8 is treated as a black box. The harness
injects JSON messages into the inbox directory, waits for the shell to process them, and
inspects the archive, rejected, outbox, state, and log files for expected outcomes.

---

## Prerequisites

### 1. NinjaTrader 8 Running with Strategy Loaded

- NT8 must be open and connected to a data feed (live or replay).
- `TaFoundationExecutionShell` must be compiled and importable in NT8.
- Load the strategy on a chart of the instrument you are testing (e.g. NQ 06-26 on a 1m chart).

### 2. Strategy Parameters

In the NT8 strategy properties panel, set these values to match `test_config.json`:

| Parameter | Required Value | Notes |
|---|---|---|
| `InboxDirectory` | `C:\ta_foundation\bridge\inbox` | Must match `bridge_root` in config |
| `ArchiveDirectory` | `C:\ta_foundation\bridge\archive` | |
| `RejectDirectory` | `C:\ta_foundation\bridge\rejected` | |
| `OutboxDirectory` | `C:\ta_foundation\bridge\outbox` | |
| `TemplateDirectory` | `C:\ta_foundation\bridge\templates` | Copy template JSON files here |
| `LogFilePath` | `C:\ta_foundation\bridge\logs\execution_shell.log` | |
| `StateFilePath` | `C:\ta_foundation\bridge\state\shell_state.json` | |
| `ProcessedIdsFilePath` | `C:\ta_foundation\bridge\state\processed_ids.log` | |
| `DryRunMode` | `true` | **Required for Phase 1 tests** |
| `EnableOutboxEvents` | `true` | Required for outbox assertions |
| `RequireInstrumentMatch` | `true` | Set instrument in config to match chart |
| `HeartbeatTimeoutSeconds` | `20` | Match `heartbeat_timeout_seconds` in config |
| `PollIntervalSeconds` | `1` | |

### 3. Live Chart Required

The shell's `OnBarUpdate` drives all processing. It must be receiving ticks.
During non-market hours, add a replay session or switch to a tick replay.
Playback speed should be at least 1x to ensure timely polling.

### 4. Templates

Copy the `templates/` directory from the bridge folder into the configured
`TemplateDirectory`. The `runner_reversal_template.json` must be present since
test messages reference it by default.

---

## Running the Tests

### Full Suite

```bash
cd tests/execution_shell
python run_bridge_tests.py
```

### Skip Heartbeat Tests (faster, no intake disruption)

```bash
python run_bridge_tests.py --skip-heartbeat
```

### Run Specific Groups

```bash
python run_bridge_tests.py --groups A B        # contract validation + state machine only
python run_bridge_tests.py --groups A          # contract validation only
python run_bridge_tests.py --groups C          # heartbeat tests only
```

### List All Tests

```bash
python run_bridge_tests.py --list
```

### Custom Config Path

```bash
python run_bridge_tests.py --config /path/to/my_config.json
```

---

## Test Group Summary

| Group | ID Range | Category | Duration |
|---|---|---|---|
| A | A-01..A-07 | Contract / Input Validation | ~30s |
| B | B-01..B-05 | DryRun State Machine | ~60s |
| D | D-01..D-03 | Sequencing / Gating | ~40s |
| E | E-01..E-04 | Persistence / Recovery | ~30s (E-03/E-04 skip) |
| C | C-01..C-02 | Heartbeat / Liveness | ~60s + heartbeat_timeout |

**Default full run (skip C): ~2-3 minutes**  
**Full run with heartbeat: ~4-5 minutes**

---

## After Heartbeat Tests

Group C tests (specifically C-02) will leave `signalIntakeEnabled=false` in the shell.
No further messages will be processed until the strategy is restarted.

To re-enable:
1. In NT8: right-click chart → Strategy → Disable
2. Wait 2 seconds
3. Right-click chart → Strategy → Enable

---

## Interpreting Results

### Console Output

```
── Contract / Input Validation ──

  PASS  [A-01] Valid ENTER_LONG accepted → archive + DRYRUN_ENTRY...  (3.2s)
  PASS  [A-02] Missing message_id → rejected directory...             (1.1s)
  FAIL  [A-03] Invalid timestamp...                                   (10.0s)
         ✗ file_in_rejected: No rejected file containing 'abc...' found after 10s
             Evidence: Rejected contents (0 files): []
```

### What to inspect when a test fails

**`file_in_archive` failure** — the message was not processed in time:
- Check if NT8 chart is receiving ticks (OnBarUpdate must fire)
- Verify `InboxDirectory` path matches exactly
- Check NT8 output tab for compile/runtime errors

**`file_in_rejected` failure** — expected rejection did not happen:
- Open the log file and search for the message_id
- The message may have been routed differently (instrument mismatch, etc.)
- Check `RequireInstrumentMatch` setting vs `instrument` in config

**`log:DRYRUN_ENTRY` failure** — entry accepted but not executed:
- Check if `DryRunMode=true` in strategy parameters
- Open log: look for the ACCEPT line with the message_id, then check what follows

**`outbox:ACCEPTED` failure** — outbox file not written:
- Verify `EnableOutboxEvents=true`
- Check write permissions on the outbox directory
- Open log and look for `outbox write failed`

**`state:shell_mode=InPosition` failure** — state file not updated:
- Check write permissions on the state directory
- Open log and look for `failed to save persistent state`

### Log file location

`C:\ta_foundation\bridge\logs\execution_shell.log`

Log line format:
```
{timestamp}|{EVENT_TYPE}|instr={msg_id}|template={template}|mode={shell_mode}|pos={position}|qty={qty}|msg={detail}
```

---

## Semi-Manual Tests (E-03, E-04)

These tests write corrupt files to the state directory and print instructions.

**E-03 procedure:**
1. Run the suite (E-03 will write a corrupt state file and skip)
2. Note the corrupt file path printed in console output
3. In NT8: disable strategy, wait 2s, re-enable
4. Open the log file and verify: `WARN ... failed to load persistent state`
5. Send a HEARTBEAT via `python_sender_example.py` and verify it is still accepted

**E-04 procedure:**
1. Same as E-03 but for `processed_ids.log`
2. Verify log shows either `failed to load processed ids` or graceful ID count log

---

## Phase 2 Tests (SIM Account — Not Yet Automated)

The following require a Sim101 account with managed orders and cannot be
validated through the bridge filesystem alone:

- B-01: Fill → protective stop attached
- B-02: Broker-rejected entry handling
- B-03: Cancel working entry
- C-01: MOVE_STOP with live position
- C-02: TAKE_PARTIAL → stop protects remainder
- D-02: Restart in position with persisted stop
- D-03: Restart in position with missing stop
- E-02: Heartbeat timeout with FlattenOnHeartbeatLoss=true
- F-01: Burst sequence with live fills
- F-03: Opposite entry while in live position

These tests will be added in Phase 2 of the harness once DryRun hardening is complete.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| All tests timeout | Shell not receiving ticks | Check chart has live/replay data |
| All validation tests fail | Wrong bridge path | Verify bridge_root in config matches NT8 parameters |
| Instrument mismatch reject | Wrong instrument in config | Set `instrument` to match chart instrument exactly |
| C-02 never triggers | HeartbeatTimeoutSeconds mismatch | Match `heartbeat_timeout_seconds` in config to NT8 setting |
| State file empty | DryRun entry not saving state | Check write perms on state dir; look for save warnings in log |
| Outbox events missing | `EnableOutboxEvents=false` | Set to true in NT8 strategy parameters |
| Duplicate test fails on first run | Previous processed_ids.log present | Normal — IDs from previous manual tests persist; not a defect |
