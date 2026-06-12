# Pantheon Stop Engine + Live Test Harness

**Purpose:** one shared, pure-C# stop/trail engine (`PantheonStopEngine`) driving
PantheonMaster's live exits, a fast Python validation battery, and a NinjaTrader
AddOn that fires on-demand stop/trail tests on a Sim account — plus a **headless
file-driven loop** so an AI agent can deploy, compile, run, and read results
without clicking in NinjaTrader.

**Status:** Live-validated 2026-06-11 — all six exit policies ran end-to-end on
Sim101/NQ, long and short, with the trail ratcheting correctly and **zero
side-of-market rejections**. See [Live validation](#live-validation-2026-06-11).

Read this before changing PantheonMaster's exit logic, the stop engine, the
trail battery, or the test-harness AddOn. Related: `atr_trail_parity.md`
(backtest-parity replica), `docs/designs/ma_pool_enrichment_and_pantheonmaster_migration.md`
(why PantheonMaster's exits matter for the pool), `NINJATRADER_INTEGRATION_RUNBOOK.md`.

---

## Why this exists

PantheonMaster's stop-in-profit / trail logic used to live **inside the
Strategy**, so it could only be exercised through a live or Playback Strategy —
one position at a time, slow, and impossible to batch. Worse, the live trail
silently never fired in Market Replay (see [the Replay bug](#the-replay-bidask-trail-bug)).

The fix was to **lift the math out into a pure class with zero NinjaTrader
dependencies** so the *same* logic can be: (1) validated in Python over hundreds
of synthetic paths in ~1 s, (2) executed live by a button/command-driven AddOn on
a Sim account, and (3) run by the Strategy itself. Logic and plumbing become two
separable test problems.

## Two-tier test model

| Tier | Question | Tool | Speed |
|---|---|---|---|
| **Logic** | Given a price path, does the stop move correctly? | Python battery (`pantheon_trail_battery.py`) | ~1 s, hundreds of paths, no NT |
| **Plumbing** | Does NinjaTrader actually submit/Change the order? | AddOn harness on Sim101 | real-time per trade, a few trades |

The Python battery is the fast loop; the AddOn is the final live-plumbing proof.

---

## Components (one engine, four surfaces)

| Surface | Path | Role |
|---|---|---|
| **Engine** | `src/ta_foundation/strategies/shared/PantheonStopEngine.cs` | Pure C#, **zero NT deps**. All 5 policies + breakeven. Single source of truth. |
| **Python mirror** | `src/ta_foundation/analysis/exits/pantheon_stop_engine.py` | 1:1 port of the engine (same method names/branches). |
| **Python battery** | `src/ta_foundation/analysis/exits/pantheon_trail_battery.py` | Synthetic adversarial paths + driver + invariant checks + report. |
| **Tests** | `src/ta_foundation/tests/analysis/exits/test_pantheon_stop_engine.py` | Per-policy behaviour + cross-check vs `nt_atr_trail_parity.replicate_nt_atr_trail_tick`. |
| **AddOn harness** | `D:\NinjatraderAddons\PantheonTestHarness\` (external) | NT AddOn: window buttons + headless command channel; drives Sim orders via the Account API. |
| **Strategy** | `src/ta_foundation/strategies/PantheonMaster/PantheonMaster.cs` | Live path (`ManageLiveDynamicStop`) delegates to the engine via `BuildStopConfig()`/`MapExitPolicy()`. |

**Linking:** `PantheonStopEngine.cs` has no NT references, so it is dropped into
NinjaTrader `bin\Custom\AddOns\` (compiled with both the Strategy and the AddOn)
and `<Compile Include>`-linked into the AddOn `.csproj`. **Do not fork it.**

The five policies: `FixedRR`, `AtrTrail`, `Chandelier`, `Giveback`, `FixedStop`,
plus `BreakEvenOnly`. Engine semantics mirror PantheonMaster's discovery-exit path
exactly (initial stop = `StopTicks`; ratchet = move only if it improves by
> 0.5 tick; Giveback protects `(1-GivebackPct)` of peak open profit; etc.).

---

## The Replay Bid/Ask trail bug

PantheonMaster's live trail only ran off **Bid** (long) / **Ask** (short) ticks
in `GetLiveTriggerPrice`. Market Replay frequently streams **only Last**, so
`ManageLiveDynamicStop` was never reached — the initial stop was placed but the
trail **never moved** ("I can't see it move the stop"). **Fix:** fall back to
`Last` when the side-specific tick is absent. Last is a faithful trail trigger and
matches the Python tick replica's single price series.

## The side-of-market guard fix

A protective stop must rest on the valid side of the book or the broker rejects it
(`"Stop price can't be changed above the market"`), and `RealtimeErrorHandling`
escalates that to terminating the strategy. Referencing **Last** is too
optimistic — a long's sell-stop is checked against the **bid**, which sits below
Last. **Fix:** guard against the conservative side (bid for long-exit, ask for
short-exit) plus a `StopBufferTicks` margin (default 2). Applied in **both**
`PantheonTestController.MoveStop` and `PantheonMaster.MoveStopIfImproved`.

---

## Python battery (the fast loop)

```bash
# run the battery (synthetic adversarial paths, all policies, both directions)
python -m ta_foundation.analysis.exits.pantheon_trail_battery
# -> tidy DataFrame: policy × path × direction, exit, n_stop_moves, invariant flags

# the durable tests (incl. cross-check vs the parity replica)
python -m pytest src/ta_foundation/tests/analysis/exits/test_pantheon_stop_engine.py -q
```

Invariants checked every run: the stop only ratchets favourably (never loosens),
and at each move it sat on the valid side of that tick's price. Paths:
`run_up_then_retrace`, `trend_then_reverse`, `gap_through`, `chop`, `steady_trend`.

---

## Headless drive loop (the agent-operable capability)

The AddOn ships a **command channel** (`PantheonHarnessIpc`) that polls a file and
writes a log, so the whole deploy→compile→run→read cycle is file-driven. An agent
runs everything below from the shell; **the only thing requiring a human is
clicking NinjaTrader's trust dialogs / a true app restart** (see [gotchas](#operational-gotchas)).

### 1. Deploy
Copy the harness `.cs` + the engine into NinjaTrader's custom folder:
```powershell
D:\NinjatraderAddons\PantheonTestHarness\deploy_and_check.ps1            # harness + engine
D:\NinjatraderAddons\PantheonTestHarness\deploy_and_check.ps1 -Strategy  # also PantheonMaster.cs
```
Targets `…\Documents\NinjaTrader 8\bin\Custom\AddOns\` (and `\Strategies\`).

### 2. Compile (NT does NOT auto-compile on copy alone)
A NinjaScript Editor **must be open**. Trigger a compile via the optimizer AddOn's
global `ObserveCompile` IPC:
```powershell
# unique runId each time (it dedups on exact content)
@{ action='ObserveCompile'; runId=("pmh_"+(Get-Date -Format HHmmss));
   sourceFile='C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\AddOns\PantheonHarnessIpc.cs';
   strategyName='PantheonHarnessIpc'; outputDir='C:\temp\pmh_compile'; timeoutSeconds=60
} | ConvertTo-Json -Compress | Set-Content C:\temp\nt8_command.json -Encoding UTF8
```

### 3. Verify
- `…\bin\Custom\NinjaTrader.Custom.dll` mtime advances = recompiled (it is briefly
  *deleted* mid-compile).
- Scan `…\Documents\NinjaTrader 8\log\*.txt` and `\trace\*.txt` for fresh
  `CS\d{4}` lines mentioning the changed files.

### 4. Drive + read
Write one command per line to `C:\temp\pantheon_cmd.txt`; read `C:\temp\pantheon_harness_log.txt`.

| Command | Form |
|---|---|
| configure | `configure;<account>;<instrument>;<qty>;<atr>;<Policy>;<stopTicks>;<targetTicks>;<atrMult>;<dryRun true/false>` |
| set policy | `policy;<PolicyName>` |
| enter long | `testlong` |
| enter short | `testshort` |
| flatten | `flatten` |

Example (live AtrTrail, NQ):
```powershell
Set-Content C:\temp\pantheon_cmd.txt 'configure;Sim101;NQ 06-26;1;4;AtrTrail;40;90;2.0;false' -Encoding ASCII
Start-Sleep 2; Set-Content C:\temp\pantheon_cmd.txt 'testlong' -Encoding ASCII
# watch: TRAIL stop X -> Y (bid .. ask .. last ..), STOP FILLED, FLATTEN
```
Tip: append a unique `;<nonce>` token to force re-processing of an identical
command (the poller dedups on file content). `dryRun=true` logs intended orders
without submitting.

---

## Operational gotchas

1. **NinjaTrader is read-only for computer-use.** The sandbox grants NT at tier
   `read` (it's a trading platform): an agent can screenshot it but **cannot click
   or type**. NinjaTrader's "authorize new add-ons?" trust dialogs and any true
   application restart **must be done by the operator**. Everything else is
   file-driven and needs no NT interaction.
2. **NT leaks an AddOn instance + DispatcherTimer on every hot-reload.** Each
   recompile can leave an old timer polling the command file → a single command
   executes **N times** (e.g. `testlong` → N Sim orders). `PantheonHarnessIpc` has
   a **generation guard** (newest gen in `C:\temp\pantheon_ipc_gen.txt` wins; older
   instances self-stop), but **pre-guard leaked timers are immortal until a full NT
   process restart.** Always confirm exactly-once with a dry-run `configure` (count
   the `Configured:` lines) **before** firing a real `testlong`.
3. **Auto-compile needs an open editor.** Pure file copy and app-focus do **not**
   trigger compilation; the `ObserveCompile` force-compile is a no-op with no
   NinjaScript Editor open.

---

## Live validation (2026-06-11)

All on Sim101 / NQ 06-26, driven entirely via the command file (single instance
confirmed first). **No side-of-market rejections on any policy, either side.**

| Policy | Side | Result |
|---|---|---|
| AtrTrail | long | trailed up, stop always below bid |
| Giveback | long | locked ~60% of peak (+9 ticks) on the pullback |
| Chandelier | long | trailed up (tracks favorable extreme — see limitation) |
| FixedStop | long | static stop, never moved, filled at fixed level |
| BreakEvenOnly | long | armed **once** to entry+4 at +30 ticks, then held |
| AtrTrail | short | trailed **down**, stop always above ask |
| FixedRR | long | static stop (target not placed — see limitation) |

---

## Known harness limitations (intentional / open)

- **Fixed ATR:** the AddOn uses the operator-set `atr` value, not a live indicator
  (the Python battery validates ATR-driven behaviour; the rig proves order
  plumbing). ATR-parity is the Strategy's job — see `atr_trail_parity.md`.
- **Chandelier** uses the favorable extreme as its reference (no bar `MAX`/`MIN`),
  so in the harness it behaves like AtrTrail. Use the Strategy / Python battery for
  true Chandelier.
- **No target order:** `PantheonTestController` places only the protective stop, so
  `FixedRR`/`MaxTP` exits are not live-tested by the harness yet.
- **Hardcoded params:** BreakEven trigger/plus (30/4) and Giveback % (0.40) are
  fixed in the IPC `configure`; exposing them as command params is an easy follow-up.
- The historical/bar-close path in PantheonMaster is **not yet unified** onto the
  engine (left to preserve backtest parity).

---

## Source-of-truth invariant

If you change a stop/trail branch, change it in **all three**: `PantheonStopEngine.cs`,
`pantheon_stop_engine.py`, and the tests — and re-run the battery. The cross-check
test against `nt_atr_trail_parity.replicate_nt_atr_trail_tick` is what proves the
C# engine and the Python mirror remain one logic.
