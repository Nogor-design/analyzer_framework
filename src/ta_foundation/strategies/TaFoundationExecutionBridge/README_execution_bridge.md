# NinjaTrader 8 Execution Shell + Python Signal Bridge

This folder provides a reusable deployment bridge from `ta_foundation` research outputs into NinjaTrader 8 execution.

## Delivered files
- `TaFoundationExecutionShell.cs` — generic NT8 execution shell strategy.
- `signal_contract.md` — normalized Python ↔ NT8 message contract.
- `templates/*.json` — template-driven behavior packs.
- `python_sender_example.py` — realistic Python sender stub.

## Architecture (three layers)
1. **Python Strategy Brain** (`ta_foundation` analytics/report outputs):
   - computes features and early-path classification,
   - chooses template,
   - emits normalized instruction messages.
2. **NT8 Execution Shell** (`TaFoundationExecutionShell.cs`):
   - owns position/order/account truth,
   - validates messages,
   - executes and manages orders,
   - enforces risk and kill-switch behavior.
3. **Template layer** (`templates/*.json`):
   - externalized management behavior (scalp/expansion/runner/hybrid),
   - keeps strategy behavior configurable without cloning many `.cs` files.

## Why this split
- NT8 is the broker/account execution runtime; it must remain source-of-truth for open position and active orders.
- Python remains the research/decision engine and can evolve independently without rewriting the execution core.
- Template configs absorb setup variance while keeping one audited execution shell.

## Safety model in shell
Implemented guardrails:
- stale message rejection (`StaleSignalSeconds`),
- duplicate message rejection (`message_id` set tracking),
- instrument mismatch rejection (`RequireInstrumentMatch`),
- max position cap (`MaxPositionSize`),
- stop cap (`MaxStopTicksCap`),
- daily realized-loss lockout (`MaxDailyLoss`),
- one-trade-at-a-time mode,
- heartbeat timeout detection + optional flatten (`FlattenOnHeartbeatLoss`),
- explicit emergency command: `FLATTEN_AND_DISABLE`.

## Logging model
Shell logs to both NT8 output (`Print`) and optional file (`LogFilePath`):
- instruction received / accepted / rejected,
- order placements,
- fills,
- stop moves,
- lockouts,
- heartbeat loss,
- flatten/disable events.

## Dry-run/simulation mode
`DryRunMode=true` allows complete parsing/validation/state transitions and logging without live order submission.
Use this first in Simulation account while validating bridge behavior.

## Manual NT8 setup required
1. Import/compile `TaFoundationExecutionShell.cs` in NinjaTrader 8.
2. Place template JSON files in strategy `TemplateDirectory`.
3. Configure bridge directories (`InboxDirectory`, `ArchiveDirectory`, `RejectDirectory`).
4. Start strategy on intended instrument chart/series.
5. Send messages from Python using `python_sender_example.py`.

## Research-to-execution answers
1. **Should Python send raw buy/sell or richer instructions?**
   - Send richer stateful instructions (action + template + risk hints) so execution can express scratch/partial/downgrade/runner transitions.
2. **Should shell be generic or setup-specific?**
   - Generic shell; setup behavior is selected by template.
3. **Templates vs core code split?**
   - Core code: validation, order state, safety, transport, lifecycle.
   - Templates: stop/target style, partial/runner/downgrade/scratch/session behavior.
4. **Safest first IPC method?**
   - File queue with atomic rename on localhost; easiest to inspect/replay and operationally robust.
5. **Failures to guard before live use?**
   - stale/duplicate/wrong-instrument, quantity or stop oversize, heartbeat loss, conflicting position actions, daily loss lockout.
6. **What to sim-test first?**
   - Contract validation + dedupe + stale checks + heartbeat fault response + dry-run action mapping, then managed-order path in Sim account.
7. **How future reports emit templates or `.cs` variants?**
   - Report outputs should emit JSON template/parameter packs directly matching `templates/*.json`; optional `.cs` variants should only wrap shell defaults, not duplicate core execution logic.

## Limitations (intentional first version)
- v1 transport is file-queue only (transport abstraction kept by isolating message parsing/validation path).
- Template JSON parsing inside shell currently reads only top-level fields required by execution core; nested fields remain available for staged enhancements.
- Daily lockout in v1 uses cumulative realized PnL from strategy performance stream; if account-level cross-strategy lockout is needed, add account-level aggregation.
