# NT8 Execution Shell Hardening Test Matrix

This matrix is designed for **TaFoundationExecutionShell.cs** hardening validation in two phases:

- **Phase 1 — DryRunMode**: parser, validation, state gating, persistence, logging/outbox behavior.
- **Phase 2 — Sim Account Managed Orders**: actual order submit/update/fill/cancel/reject lifecycle in NT8 Simulation.

All tests use this mechanical schema:
- `test_id`
- `category`
- `phase`
- `scenario`
- `initial_state`
- `input`
- `expected_behavior`
- `expected_logs`
- `expected_final_shell_mode`
- `expected_position_state`
- `pass_criteria`

---

## A) Contract / Input Validation

### A-01
- **test_id:** A-01
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Valid entry message accepted.
- **initial_state:** Flat, `signalIntakeEnabled=true`, no lockout.
- **input:** `ENTER_LONG` with valid `message_id`, tz-aware `timestamp`, matching `instrument`, `template_name`, `quantity=1`, `stop_ticks=20`.
- **expected_behavior:** File archived, message deduped/persisted, instruction queued then executed in DryRun.
- **expected_logs:** `ACCEPT`, `DRYRUN_ENTRY`, `HEALTH`.
- **expected_final_shell_mode:** `InPosition` (DryRun simulation state).
- **expected_position_state:** Strategy account still flat (DryRun no real orders).
- **pass_criteria:** No reject; archive file exists; outbox has `ACCEPTED`.

### A-02
- **test_id:** A-02
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Missing `message_id`.
- **initial_state:** Flat.
- **input:** JSON missing `message_id`.
- **expected_behavior:** Reject and move file to rejected folder.
- **expected_logs:** `REJECT reason=missing message_id`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** File is in rejected directory; no dedupe write.

### A-03
- **test_id:** A-03
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Invalid/non-ISO timestamp.
- **initial_state:** Flat.
- **input:** `timestamp="bad-time"`.
- **expected_behavior:** Rejected.
- **expected_logs:** `REJECT reason=invalid timestamp`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** Rejected file and no queue execution.

### A-04
- **test_id:** A-04
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Future timestamp exceeds skew guard.
- **initial_state:** Flat.
- **input:** `timestamp` > now + 30 seconds.
- **expected_behavior:** Rejected with explicit reason.
- **expected_logs:** `REJECT reason=future timestamp`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** Rejected; no dedupe ID consumed.

### A-05
- **test_id:** A-05
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Duplicate `message_id` replay.
- **initial_state:** Previously accepted same message ID persisted in processed IDs.
- **input:** Re-send exact same payload.
- **expected_behavior:** Rejected as duplicate.
- **expected_logs:** `REJECT reason=duplicate message_id`.
- **expected_final_shell_mode:** unchanged.
- **expected_position_state:** unchanged.
- **pass_criteria:** Reject file exists and no secondary execution.

### A-06
- **test_id:** A-06
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** Entry missing template_name.
- **initial_state:** Flat.
- **input:** `ENTER_SHORT` without `template_name`.
- **expected_behavior:** Rejected before queue.
- **expected_logs:** `REJECT reason=missing template_name for entry`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** no archive entry.

### A-07
- **test_id:** A-07
- **category:** Contract / input validation
- **phase:** Phase 1
- **scenario:** MOVE_STOP missing stop price.
- **initial_state:** InPosition DryRun.
- **input:** `MOVE_STOP` with null `stop_price`.
- **expected_behavior:** Rejected.
- **expected_logs:** `REJECT reason=invalid stop price`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** unchanged.
- **pass_criteria:** no stop mutation.

---

## B) Entry / Order Lifecycle

### B-01
- **test_id:** B-01
- **category:** Entry lifecycle
- **phase:** Phase 2
- **scenario:** Flat -> ENTER_LONG -> fill -> protective stop attached.
- **initial_state:** Flat, Sim101.
- **input:** Valid `ENTER_LONG`.
- **expected_behavior:** Entry order submitted; on fill shell enters `InPosition`; stop attached.
- **expected_logs:** `ORDER`, `ORDER_WORKING`, `FILL`, `STOP_INIT`, `HEALTH`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** Long qty = requested.
- **pass_criteria:** exactly one live stop order with expected qty.

### B-02
- **test_id:** B-02
- **category:** Entry lifecycle
- **phase:** Phase 2
- **scenario:** Entry rejected by broker simulator.
- **initial_state:** Flat.
- **input:** Force rejection conditions (invalid quantity/instrument config).
- **expected_behavior:** mode returns to `Idle`, entry order cleared.
- **expected_logs:** `ORDER_REJECT`, outbox `ORDER_REJECTED`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** no orphaned pending state.

### B-03
- **test_id:** B-03
- **category:** Entry lifecycle
- **phase:** Phase 2
- **scenario:** Entry submitted then canceled.
- **initial_state:** Flat.
- **input:** `ENTER_LONG` then `CANCEL_WORKING` before fill.
- **expected_behavior:** working entry canceled; no fill.
- **expected_logs:** `CANCEL`, `ORDER_CANCEL`.
- **expected_final_shell_mode:** `Idle` or `EntryPending`->`Idle` after cancel.
- **expected_position_state:** Flat.
- **pass_criteria:** no entry or stop orders remaining.

---

## C) Stop / Target Management

### C-01
- **test_id:** C-01
- **category:** Stop management
- **phase:** Phase 2
- **scenario:** MOVE_STOP while long with valid lower stop price.
- **initial_state:** Long in position with active stop.
- **input:** `MOVE_STOP` stop below market.
- **expected_behavior:** Replace/update protective stop; single active stop remains.
- **expected_logs:** `MOVE_STOP`, `ORDER_WORKING`, outbox `STOP_MOVED`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** Long unchanged.
- **pass_criteria:** only one active stop order, no stacking duplicates.

### C-02
- **test_id:** C-02
- **category:** Stop management
- **phase:** Phase 2
- **scenario:** Unsafe stop move rejected.
- **initial_state:** Long position.
- **input:** `MOVE_STOP` stop above/beyond current market for long.
- **expected_behavior:** rejected as unsafe.
- **expected_logs:** `REJECT reason=unsafe stop for current side`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** unchanged.
- **pass_criteria:** previous stop order remains unchanged.

### C-03
- **test_id:** C-03
- **category:** Stop + partial
- **phase:** Phase 2
- **scenario:** TAKE_PARTIAL then stop remains valid for remainder.
- **initial_state:** Long qty 2 with stop.
- **input:** `TAKE_PARTIAL quantity=1`.
- **expected_behavior:** partial exit fills; stop still protects remaining qty.
- **expected_logs:** `PARTIAL`, `FILL`, `HEALTH`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** Long qty 1.
- **pass_criteria:** stop qty equals remaining position qty.

---

## D) Restart / Recovery

### D-01
- **test_id:** D-01
- **category:** Restart/recovery
- **phase:** Phase 1 + Phase 2
- **scenario:** Restart while flat.
- **initial_state:** Persisted state exists, account flat.
- **input:** disable/re-enable strategy.
- **expected_behavior:** mode resolves to `Idle`/`Disabled` per intake flag, logs reconciliation snapshot.
- **expected_logs:** `RECOVERY_FLAT` health snapshot.
- **expected_final_shell_mode:** `Idle` or `Disabled`.
- **expected_position_state:** Flat.
- **pass_criteria:** no new orders emitted on startup.

### D-02
- **test_id:** D-02
- **category:** Restart/recovery
- **phase:** Phase 2
- **scenario:** Restart while in position with persisted stop.
- **initial_state:** live Sim position + saved `pending_stop_price`.
- **input:** restart strategy.
- **expected_behavior:** stop restored via recovery path.
- **expected_logs:** `RECOVERY`, `STOP_INIT`, `RECOVERY ... restored`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** unchanged live position.
- **pass_criteria:** protective stop working shortly after startup.

### D-03
- **test_id:** D-03
- **category:** Restart/recovery
- **phase:** Phase 2
- **scenario:** Restart in position with missing stop state.
- **initial_state:** position exists; state file has `pending_stop_price=0`.
- **input:** restart.
- **expected_behavior:** fallback stop synthesized using `RecoveryFallbackStopTicks`.
- **expected_logs:** `RECOVERY_WARN persisted stop missing; applying fallback stop ...`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** unchanged side/qty.
- **pass_criteria:** stop order exists at synthesized price.

### D-04
- **test_id:** D-04
- **category:** Restart/recovery
- **phase:** Phase 1
- **scenario:** Corrupt state file.
- **initial_state:** malformed `shell_state.json`.
- **input:** strategy startup.
- **expected_behavior:** warn and continue with defaults; no crash.
- **expected_logs:** `WARN failed to load persistent state`.
- **expected_final_shell_mode:** safe default from runtime context.
- **expected_position_state:** unchanged.
- **pass_criteria:** strategy remains running and responsive.

### D-05
- **test_id:** D-05
- **category:** Restart/recovery
- **phase:** Phase 1
- **scenario:** Corrupt processed ID file.
- **initial_state:** malformed processed IDs file.
- **input:** startup.
- **expected_behavior:** warn and continue; dedupe still available from in-memory and optional state fallback.
- **expected_logs:** `WARN failed to load processed ids` and/or `STATE processed_id_count`.
- **expected_final_shell_mode:** unchanged.
- **expected_position_state:** unchanged.
- **pass_criteria:** strategy does not terminate, accepts new non-duplicate IDs.

---

## E) Heartbeat / Liveness

### E-01
- **test_id:** E-01
- **category:** Heartbeat
- **phase:** Phase 1
- **scenario:** Regular heartbeat keeps intake alive.
- **initial_state:** `signalIntakeEnabled=true`.
- **input:** `HEARTBEAT` every < timeout.
- **expected_behavior:** no heartbeat fault.
- **expected_logs:** repeated `HEARTBEAT`; no `HEARTBEAT_LOST`.
- **expected_final_shell_mode:** unchanged.
- **expected_position_state:** unchanged.
- **pass_criteria:** intake remains enabled.

### E-02
- **test_id:** E-02
- **category:** Heartbeat
- **phase:** Phase 1/2
- **scenario:** Timeout with flatten disabled.
- **initial_state:** in position, `FlattenOnHeartbeatLoss=false`.
- **input:** stop sender; wait > timeout.
- **expected_behavior:** intake disabled, position untouched.
- **expected_logs:** `HEARTBEAT_LOST`, outbox `HEARTBEAT_LOST`.
- **expected_final_shell_mode:** remains current mode but intake false.
- **expected_position_state:** unchanged open position.
- **pass_criteria:** no forced exit generated.

### E-03
- **test_id:** E-03
- **category:** Heartbeat
- **phase:** Phase 2
- **scenario:** Timeout with flatten enabled.
- **initial_state:** in position, `FlattenOnHeartbeatLoss=true`.
- **input:** stop sender; wait > timeout.
- **expected_behavior:** `FlattenAndDisable` executes.
- **expected_logs:** `HEARTBEAT_LOST`, `EXIT`, `DISABLE`.
- **expected_final_shell_mode:** `Disabled`/`ExitPending` transition to `Disabled` when flat.
- **expected_position_state:** Flat.
- **pass_criteria:** position flattened and intake disabled.

---

## F) Conflict / Sequencing

### F-01
- **test_id:** F-01
- **category:** Sequencing conflict
- **phase:** Phase 1 + Phase 2
- **scenario:** `ENTER_LONG -> TAKE_PARTIAL -> MOVE_STOP` before fill.
- **initial_state:** Flat.
- **input:** send burst in quick succession.
- **expected_behavior:** while `EntryPending`, partial and move-stop are gate-rejected.
- **expected_logs:** `REJECT_GATE reason=entry pending` for non-allowed actions.
- **expected_final_shell_mode:** `EntryPending` then `InPosition` after fill.
- **expected_position_state:** valid entry only.
- **pass_criteria:** no partial/stop mutation prior to fill.

### F-02
- **test_id:** F-02
- **category:** Sequencing conflict
- **phase:** Phase 1
- **scenario:** `ENTER_LONG -> EXIT_ALL -> MOVE_STOP`.
- **initial_state:** Flat DryRun.
- **input:** send sequence.
- **expected_behavior:** exit accepted (allowed), later move-stop rejected due no position.
- **expected_logs:** `DRYRUN_EXIT`, `REJECT_GATE reason=no open position`.
- **expected_final_shell_mode:** `Idle`.
- **expected_position_state:** Flat.
- **pass_criteria:** deterministic behavior, no undefined state.

### F-03
- **test_id:** F-03
- **category:** Sequencing conflict
- **phase:** Phase 2
- **scenario:** `ENTER_LONG` then `ENTER_SHORT` while long.
- **initial_state:** Long.
- **input:** short entry message.
- **expected_behavior:** reject opposite entry.
- **expected_logs:** `REJECT_GATE reason=already in position` or `REJECT reason=non_flat_position`.
- **expected_final_shell_mode:** `InPosition`.
- **expected_position_state:** Long unchanged.
- **pass_criteria:** no unintended reversal order.

---

## G) Persistence / Replay

### G-01
- **test_id:** G-01
- **category:** Persistence/replay
- **phase:** Phase 1
- **scenario:** Crash simulation after archive move and before next poll.
- **initial_state:** new valid file ready.
- **input:** process accepted message then restart strategy.
- **expected_behavior:** no duplicate execution because ID persisted and file already archived.
- **expected_logs:** single `ACCEPT` for ID.
- **expected_final_shell_mode:** deterministic unchanged after restart.
- **expected_position_state:** unchanged per DryRun/Sim context.
- **pass_criteria:** exactly-once behavior for that instruction.

### G-02
- **test_id:** G-02
- **category:** Persistence/replay
- **phase:** Phase 1
- **scenario:** processed-id retention trimming.
- **initial_state:** retention low (e.g., 100), inject 150+ messages.
- **input:** batch unique heartbeat IDs.
- **expected_behavior:** in-memory queue trimmed; processed IDs file rewritten atomically.
- **expected_logs:** no errors; continued acceptance.
- **expected_final_shell_mode:** unchanged.
- **expected_position_state:** unchanged.
- **pass_criteria:** processed ID file line count ~= retention and contains newest IDs.

### G-03
- **test_id:** G-03
- **category:** Persistence/replay
- **phase:** Phase 1
- **scenario:** state save failure handling.
- **initial_state:** make state directory read-only/unavailable.
- **input:** any message requiring save.
- **expected_behavior:** strategy continues running with warning logs.
- **expected_logs:** `WARN failed to save persistent state`.
- **expected_final_shell_mode:** operational, no crash.
- **expected_position_state:** unchanged.
- **pass_criteria:** strategy survives repeated save failures.

---

## Phase Mapping Summary

- **Phase 1 (DryRunMode required):** A-01..A-07, D-01, D-04, D-05, E-01, F-02, G-01..G-03.
- **Phase 2 (Sim Account managed orders):** B-01..B-03, C-01..C-03, D-02, D-03, E-02, E-03, F-01, F-03.

---

## Remaining High-Risk Areas Before Live Use

1. **Account-level risk aggregation:** current daily lockout is strategy-performance scoped, not full-account across strategies.
2. **Multi-strategy coordination:** no shared account risk arbiter to prevent conflicting strategies trading same instrument/account.
3. **Transport limits:** filesystem queue lacks guaranteed ordering across producers and has no built-in ACK/NACK channel semantics.
4. **Broker routing edge cases:** exchange/broker specific reject states and partial-cancel transitions still require venue-specific simulation.
5. **Slippage / fast market behavior:** stop migration logic must be validated under jump/slip conditions in replay/sim stress.
6. **Outbox reliability contract:** outbox is best-effort file emission; Python consumer retry/compaction policy still required.

## Must Validate Before Any Real-Money Deployment

- Complete **all Phase 1 and Phase 2 tests with evidence logs**.
- Run at least one **restart-mid-position scenario per side (long/short)** and verify stop continuity.
- Validate **heartbeat-loss behavior** under both flatten enabled/disabled policies.
- Validate **dedupe/replay durability** across forced restarts and message replays.
- Verify **operational runbook** for corrupt state files, disabled intake, and manual intervention paths.
