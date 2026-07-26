# StrategyDiscoveryFilter — repeatable exit verification (no hand-testing)

**Purpose.** Prove, hands-free, that `StrategyDiscoveryFilter` (SDF) actually
places its initial stop / target and (for trailing policies) moves the stop —
the same guarantee the Pantheon test harness gives PantheonMaster, but for SDF's
*entry + exit* wiring. Use this every time SDF's entry or exit code changes so we
never re-debug "is the stop even being placed?" by hand.

Companion: `pantheon_stop_engine_and_test_harness.md` (live stop plumbing for
PantheonMaster) and `atr_trail_parity.md` (backtest ATR parity).

---

## Why this exists (2026-06-15 incident)

SDF was reported "not placing the initial stop/TP, not moving the trail" while
`PantheonMasterBotV01TesterV2` worked perfectly. Root cause was **not** the live
logic: the **deployed** `…\NinjaTrader 8\bin\Custom\Strategies\StrategyDiscoveryFilter.cs`
was a **stale** copy (≈half the current file) whose entry block called
`EnterLong(…)` *before* `SetStopLoss(…)`. NinjaTrader requires the Set* call
**before** the entry order (setstoploss.md line 19), so the managed stop/target
never attached. The repo source already fixed this (`ApplyEntryExits()` arms the
stop/target before `EnterLong` in `PlaceEntry`), but the fix had never been
deployed or recompiled. **Lesson: always confirm the DLL was rebuilt from the
current source before drawing any conclusion about a strategy's behavior.**

---

## SDF's two exit mechanisms (never mixed on one position)

| ExitPolicy | Mechanism | What "working" looks like |
|---|---|---|
| FixedRR | managed `SetStopLoss` + `SetProfitTarget` (armed pre-entry) | trades exit on `Stop loss` / `Profit target`; loss ≈ StopTicks, win ≈ TargetTicks |
| FixedStop | managed `SetStopLoss` only | exits on `Stop loss` or session close |
| BreakEvenOnly | managed `SetStopLoss`, moved to BE after trigger | stop price jumps to entry±BreakEvenPlusTicks once +BreakEvenTriggerTicks reached |
| AtrTrail / Chandelier | **explicit** working `ExitLong/ShortStopMarket` submitted on fill (`OnExecutionUpdate→SubmitInitialStop`), ratcheted via `ChangeOrder` | a `SdfLongStop`/`SdfShortStop` order appears and its stop price ratchets monotonically toward price |
| Giveback | no hard stop; `ExitLong/Short` market when peak profit given back | exits on `Giveback` named exit |

---

## STEP 0 — SAFETY GATE (mandatory before any recompile/restart)

NinjaTrader only recompiles `bin/Custom` when a NinjaScript Editor is open or on
app startup. With no editor open and computer-use unable to click NT, the only
headless way to force a compile is an **NT restart** — and **a restart is unsafe
while a live/eval account has an open position or working orders.**

Disabling a strategy does **not** flatten it: our live strategies run with
`CancelExitsOnStrategyDisable=False`, so a disabled strategy leaves its position
and resting Stop/Target OCO orders working. A restart can disrupt those.

**Do not restart NT until you have confirmed, for every real account:**
- position is **flat**, and
- there are **no working orders**.

Quick check from the NT order log (look for unresolved `Working` stop/target with
no later `Filled`/`Cancelled`, and any non-flat `Position=` on the most recent
strategy enable):
```bash
LOG=$(ls -t "/c/Users/Owner/Documents/NinjaTrader 8/log/"*.txt | head -1)
grep -iE "New state='(Working|Filled|Cancelled)'|Position=|Disabling|Enabling" "$LOG" | tail -40
```
If in doubt, treat the account as live and **stop** — leave the source deployed
and finish verification in the next confirmed-safe window (or ask the operator).

> The repo source can be safely **deployed** (copied into place) at any time; it
> just sits inert until a recompile. Only the *recompile trigger* is gated.

---

## STEP 1 — Deploy current source

```bash
cp "D:/Backup/projects/PythonProject/ta_foundation/src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.cs" \
   "/c/Users/Owner/Documents/NinjaTrader 8/bin/Custom/Strategies/StrategyDiscoveryFilter.cs"
```
(Keep the previous deployed copy as a `.bak` so a bad compile is one `cp` to revert.)

## STEP 2 — Force a compile (only after STEP 0 passes)

Preferred while NT is open with the **NinjaScript Editor open**: drop the file and
fire the optimizer AddOn's `ObserveCompile` IPC; it calls the editor's `OnCompile`.
```bash
RUNID="sdf_$(date +%H%M%S)"
cat > /c/temp/nt8_command.json <<EOF
{"action":"ObserveCompile","runId":"$RUNID","sourceFile":"C:\\\\Users\\\\Owner\\\\Documents\\\\NinjaTrader 8\\\\bin\\\\Custom\\\\Strategies\\\\StrategyDiscoveryFilter.cs","strategyName":"StrategyDiscoveryFilter","outputDir":"C:\\\\temp\\\\sdf_compile","timeoutSeconds":120}
EOF
```
If no editor is open, `ObserveCompile` returns `timed_out` / `stale_assembly`
("NinjaTrader.Custom.dll was not rewritten…"). In that case the compile trigger
is a **clean NT restart** (gated by STEP 0):
restart per `NINJATRADER_INTEGRATION_RUNBOOK.md` §3 (login automation), then
verify readiness. NT recompiles changed sources on startup.

## STEP 3 — Verify the compile actually happened

```bash
DLL="/c/Users/Owner/Documents/NinjaTrader 8/bin/Custom/NinjaTrader.Custom.dll"
echo "DLL mtime: $(date -r "$DLL" '+%F %T')   source: $(date -r "/c/Users/Owner/Documents/NinjaTrader 8/bin/Custom/Strategies/StrategyDiscoveryFilter.cs" '+%F %T')"
cat /c/temp/sdf_compile/${RUNID}_compile_result.json 2>/dev/null   # state must be "succeeded", errorCount 0
```
DLL mtime must be **newer than** the source mtime, and `state":"succeeded"`. Any
`CS####` rows in `${RUNID}_errors.csv` are real compile errors — fix and redeploy.

## STEP 4 — Headless backtest that produces trades

**Automated (one command):** `python scripts/verify_sdf_backtest.py [--instrument "NQ 06-26"]`
stamps both fixed Backtest templates (FixedRR + AtrTrail) from a seed off the live source with
filters loosened to guarantee trades, dispatches ONE RunBatch, parses each Trades.csv, and prints
the exit-name breakdown + stop/target bounds + PASS/FAIL. Proven 2026-06-15: FixedRR 1039 trades
(avg loss −$201 ≈ 40 StopTicks, avg win +$300 ≈ 60 TargetTicks); AtrTrail 1123 trades (exits
SdfLong/ShortStop, avg loss −$156 = ratcheted tighter than the initial, best win +$3050).

**GOTCHA:** every pinned value must respect that NinjaScriptProperty's `[Range]` or NT pops a
blocking "not in valid range" modal that wedges the batch (e.g. `MaxDailyLossUsd` ≤ 10000,
`MaxDailyProfitUsd` ≤ 50000). If a modal appears: dismiss its OK via UIAutomation InvokePattern,
then cancel the batch by deleting `C:\temp\nt8_command.json` (cancel-on-delete), fix the value, re-run.

**Manual equivalent:** use the optimizer RunBatch bridge with a single-combination fixed template whose
params **guarantee** trades (loosen filters): `RegimeMode=Any`, `AllowLong=true`,
`AllowShort=true`, `UseTrendAlignment=false`, `EntrySignal=EmaCross` (or `SmaCross`),
`AllowRTH=true`, a window with local data (e.g. `NQ 06-26`, 2024-09..2024-12).
Run it twice: once `ExitPolicy=FixedRR` (managed), once `ExitPolicy=AtrTrail`
(explicit trail). See `nt_strategy_loop/optimizer_bridge.py` /
`web/optimizer_runner.py` for the RunBatch payload shape.

## STEP 5 — Confirm exits fired (the actual proof)

- **Aggregate (always available from the Optimization.csv):** trades > 0, and for
  FixedRR the average loss ≈ `StopTicks` and average win ≈ `TargetTicks` (no runaway
  excursions to session close). A run that produced the stale-binary bug shows
  losses far larger than `StopTicks`.
- **Per-trade (gold standard):** export the SA Trades list (or enable
  `EnableDebugPrint` and capture the Output window) and confirm exit names:
  `Stop loss`/`Profit target` for FixedRR, `SdfLongStop`/`SdfShortStop` for AtrTrail
  with a ratcheting stop price, `Giveback` for Giveback.

Offline pre-check (no NT): the shared exit logic battery must be green —
`python -m pytest src/ta_foundation/tests/analysis/exits/test_pantheon_stop_engine.py -q`.

---

## Open improvement

SDF currently forks its own trailing machinery (`SubmitInitialStop` /
`ManageExplicitTrail` / `MoveStopIfImproved`) instead of delegating to the proven
`PantheonStopEngine` the way PantheonMaster does. Converging SDF's trail onto that
single engine would let the existing Python battery + AddOn harness cover SDF's
exits directly. Tracked as a follow-up, not done here.
