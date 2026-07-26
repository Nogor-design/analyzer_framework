# ta_foundation Python ↔ NinjaTrader 8 Signal Contract

## Transport (v1)
- **Method:** filesystem queue (JSON files dropped into inbox directory).
- **Atomic write requirement:** sender writes to `*.tmp`, then renames to `*.json`.
- **Consumer:** `TaFoundationExecutionShell.cs` polls `InboxDirectory` every `PollIntervalSeconds`.

## Message schema

```json
{
  "message_id": "abc-123",
  "timestamp": "2026-04-10T10:05:00-06:00",
  "instrument": "NQ 06-26",
  "timeframe": "1m",
  "action": "ENTER_LONG",
  "side": "LONG",
  "template_name": "runner_reversal_template",
  "confidence": 0.87,
  "entry_mode": "market",
  "quantity": 1,
  "stop_mode": "signal_extreme_capped",
  "stop_ticks": 42,
  "stop_price": null,
  "target_mode": "partial_then_runner",
  "target_ticks": 72,
  "partial_target_ticks": 18,
  "runner_mode": "trail_structure",
  "max_hold_bars": 20,
  "thesis_id": "explosive_start_extended_vwap",
  "notes": "optional"
}
```

## Required fields
- `message_id` (string, unique globally at least for session/day).
- `timestamp` (ISO-8601 with timezone offset; naive datetime is invalid).
- `instrument` (string; must match NT8 chart instrument when `RequireInstrumentMatch=true`).
- `action` (enum string).
- `template_name` (string for entry actions; should map to loaded template file).
- `quantity` (int > 0 for entry/partial actions).
- `thesis_id` (string for traceability back to research decision path).

## Optional fields + defaults
- `timeframe` default: informational only.
- `side` default: inferred from `action` for `ENTER_LONG`/`ENTER_SHORT`.
- `confidence` default: `0.0` if omitted.
- `entry_mode` default: `market`.
- `stop_mode` default: template stop mode.
- `stop_ticks` default: 12 (then clamped by template hard cap and shell max cap).
- `stop_price` default: null; required for `MOVE_STOP` action.
- `target_mode` default: template initial target mode.
- `target_ticks` default: equals stop ticks if omitted.
- `partial_target_ticks` default: null.
- `runner_mode` default: template runner mode behavior.
- `max_hold_bars` default: template max hold.
- `notes` default: empty.

## Supported actions
- `HEARTBEAT`
- `ENTER_LONG`
- `ENTER_SHORT`
- `EXIT_ALL`
- `SCRATCH`
- `TAKE_PARTIAL`
- `MOVE_STOP`
- `HOLD_FOR_RUNNER`
- `DOWNGRADE_TO_SCALP`
- `CANCEL_WORKING`
- `FLATTEN_AND_DISABLE`

## Validation rules (shell-side)
1. Parse JSON payload.
2. Reject if `message_id` missing.
3. Reject duplicate `message_id`.
4. Reject if `timestamp` missing/invalid/non-timezone-aware.
5. Reject stale messages older than `StaleSignalSeconds`.
6. Reject messages with timestamps too far in the future (clock-skew guard).
7. Reject instrument mismatch if strict instrument matching enabled.
8. Reject unsupported action.
9. Reject entry quantity above `MaxPositionSize`.
10. Reject stop ticks above `MaxStopTicksCap`.
11. Reject new entries while daily loss lockout is active.
12. Reject entry without `template_name`.
13. Reject `MOVE_STOP` without a positive `stop_price`.

## Rejection reasons emitted in log
- `payload parse failed`
- `missing message_id`
- `duplicate message_id`
- `invalid timestamp`
- `stale signal`
- `instrument mismatch`
- `unsupported action`
- `future timestamp`
- `quantity above max position size`
- `stop ticks above configured cap`
- `daily loss lockout active`
- `invalid stop price`
- `one_trade_at_a_time`

## Optional NT8 outbox events (v1.1 hardening)
When `EnableOutboxEvents=true`, the shell writes status files to `OutboxDirectory`:
- `ACCEPTED`
- `REJECTED`
- `ENTRY_SUBMITTED`
- `PARTIAL_SUBMITTED`
- `FILLED`
- `STOP_ATTACHED`
- `STOP_MOVED`
- `EXIT_SUBMITTED`
- `FLATTENED`
- `HEARTBEAT_LOST`

## Message lifecycle
1. Python writes JSON to inbox.
2. Shell validates + queues.
3. Shell archives accepted files into `ArchiveDirectory`.
4. Shell moves rejected files into `RejectDirectory`.
5. Shell logs acceptance/rejection + execution events.

## Future transport compatibility
Contract is transport-agnostic. You can replace file queue with localhost TCP or named pipes as long as JSON message shape and validation semantics remain unchanged.
