# Parity Phase 2 — live-tick leg: design + research findings (2026-06-12)

**Owner:** Claude (PM). **Status:** research spike done; AddOn work is Claude's,
two delegable tasks specced (`docs/handoffs/004-*.md`, `005-*.md`).
**Context:** Phase 1 (backtest leg) PASSED 2026-06-12 — one command drives
template → RunBatch → SA backtest → stop audit → verdict (100% stop match,
29/29 trades, Wilder ATR confirmed per-event). Phase 2 re-uses the SAME
comparator (`analysis/exits/stop_audit_parity.py`, deliberately path-agnostic)
against the **live tick path**: `ManageLiveDynamicStop` + explicit
`ExitLongStopMarket`/`ChangeOrder`, audited by the same `StopAuditCsvPath`
hook (INIT + TRAIL rows) on a Playback connection.

## The two halves of "headless strategy-on-Playback"

Research spike findings (reflected from `NinjaTrader.Core.dll` 8.1.7.1,
2026-06-12 — these are PUBLIC members of the readable, non-obfuscated Core
assembly, NOT the obfuscated Gui assembly):

### 1. Headless Playback connect
```
NinjaTrader.Cbi.Connection.Connect(NinjaTrader.Cbi.ConnectOptions)   // public static
NinjaTrader.Cbi.Connection.PlaybackConnection                        // static getter
NinjaTrader.Cbi.Connection.ConnectionStatusUpdate                    // static event
```
The configured connections' `ConnectOptions` live in NT's options store
(enumerate `Connection.Connections` / Globals options to find the Playback
entry). Connect, then await `ConnectionStatusUpdate` → Connected.

### 2. Programmatic strategy enable
```
NinjaTrader.Cbi.Account.Strategies      // Collection<StrategyBase>, public
NinjaTrader.NinjaScript.StrategyBase:
    Account                  { get; set; }   // public
    Instrument / Instruments { get; set; }
    InstrumentOrInstrumentList { get; set; }
    State                    { get; set; }
    SetState(State)                          // public method
    StartBehavior            { get; set; }
```
So the enable path is: instantiate the strategy type (LAST-match assembly
resolution — see gotcha below), apply the template XML's `<Strategy>` block
(the AddOn's `ApplySimpleXmlProperties` already does exactly this), set
`Account` (Playback101) + instrument + `BarsPeriod`, then drive `SetState`.
The exact `State` progression NT expects from an external caller
(`Configure` first vs setting `State` then letting the engine pump) is the
ONE remaining empirical question — answered by a guarded experiment, below.

**`NTAddOnIdea.txt`'s `Strategy.Configurator.ProcessNewStrategy` does not
exist** in the docs mirror (0/1,152 pages) and was not found in Core's public
surface. Use the members above instead.

## Delivery shape (decided)

No new AddOn/window. **Two new IPC commands on the existing optimizer batch
AddOn** (`D:\ninjatraderOptimizer`), same `C:\temp\nt8_command.json` /
`nt8_status.json` contract:

- `ConnectPlayback` — `{ "action": "ConnectPlayback" }` → find Playback
  ConnectOptions → `Connection.Connect` → status heartbeat reports
  connection state. (Playback date-range/speed control is a stretch goal;
  first iteration can require the operator to have playback data downloaded.)
- `EnableStrategy` — `{ "action": "EnableStrategy", "templatePath": ...,
  "account": "Playback101" }` → instantiate from template (reuse
  `ApplySimpleXmlProperties` + last-match `ResolveType`), bind account/
  instrument/bars, `SetState` progression, report per-state transitions to
  the status file. `DisableStrategy` mirrors it via
  `SetState(State.Terminated)`.

The parity run template pins `UseLiveStopManagement=true` (live/explicit
path), `ForceEntry=Long` (fires once on the first realtime bar — the live
test primitive added in 8c196c6), and a unique `StopAuditCsvPath`.

## Guardrails (unchanged from the Phase-1 directive)
- Test hooks stay behind flags; never in production deploys.
- NEVER re-grow trading logic into the AddOn — it only configures/enables
  the real strategy.
- All AddOn type resolution must be LAST-match (stale-assembly gotcha,
  fixed 2026-06-12 in `ResolveType` — commit `b6bba0e`). Any new reflection
  helper must reuse `ResolveType`, not `Type.GetType` first-match patterns.

## Experiment plan (Claude, after the delegated graders land)
1. Build `EnableStrategy` with verbose per-state logging; deploy; restart NT
   (login via `ensure-nt-ready`, see CLAUDE.md NT section).
2. Dry experiment on Sim101 with a 1-min chartless enable; observe which
   `SetState` sequence reaches Realtime; log NT's Strategies-tab row as
   ground truth.
3. `ConnectPlayback` + enable on Playback101 + `ForceEntry=Long` →
   first live-tick audit → grade with the live-leg CLI (handoff 004).
4. Expect divergences; iterate exactly like Phase 1 (the comparator
   pinpoints first-divergence events).

## EXPERIMENT RESULTS (2026-06-12, 5 iterations, live NT 8.1.7.1)

**`ConnectPlayback` WORKS** (proven repeatedly): finds the configured Playback
ConnectOptions, connects, polls to Connected in seconds. One constraint learned:
**every NT-object call (`Connection.*`, `Account.All`, …) must run on a WPF
dispatcher** — worker-thread calls trip NT-internal `Debug.Assert` dialogs
(`#32770 "Assertion Failed…"`) that block until dismissed (dismissable
headlessly via `SendMessage(hwnd, WM_COMMAND, IDIGNORE)`).

**`EnableStrategy` via `SetState` is BLOCKED at Configure.** What works
externally: `Activator` create → `SetState(SetDefaults)` → template-XML apply →
`Category.NinjaScript` → `Workspace`/`SetUniqueId` → `Account`/`Instrument`
bind → `SetState(Configure)` (verified by the private `doneConfigureState=True`
— `OnStateChange(Configure)` genuinely runs). What is refused, silently, with
no exception and no state change — `SetState(State.Active)` — under every
combination tried:
main-UI dispatcher / the strategy's OWN `Dispatcher` / paced retries in
separate frames / after `account.Strategies.Add()` / with Category, Workspace,
UniqueId set. `SetState(State.DataLoaded)` directly **kills the object**
(→ Finalized). The Active gate sits in NT's stripped-IL `SetState`/
`SetStatePart` internals (Core.dll metadata is readable but method bodies are
removed — same protection family as the Gui obfuscation). Conclusion: NT 8.1.7.1
does not honor an external Configure→Active request on a strategy it did not
host itself; the missing wiring is not discoverable from the public surface.

## Pivot (next step): drive NT's OWN Strategies grid via UIAutomation

Use the exact technique that already automates the Welcome login and the
AddOn-auth prompt (`nt_strategy_loop/nt_startup.py`):

1. **One-time rig setup** — a `PantheonMaster` row in Control Center →
   Strategies, configured with the parity pins (template `load` button in the
   strategy dialog can import our generated XML). Manual (2 minutes, operator)
   or UIA-scripted later; the row persists in the workspace across restarts.
2. **Per-run headless enable/disable** — UIA toggle of the row's Enabled cell
   (+ auto-click of any confirm `NTMessageBox`). This goes through NT's own
   hosting path, so every internal flag is set the way NT wants.
3. The AddOn keeps `ConnectPlayback` (works) and the audit collection; the
   grader (`scripts/stop_audit_parity.py --ticks`) is already built and tested.

## Risks / open questions
- Playback position/speed control may be Gui-side (obfuscated); first
  iteration tolerates manual playback start, automating only connect+enable.
- The Strategies-grid UIA element names need one inspection pass (same
  inspect-style scripts as `D:\ninjatraderOptimizer\inspect_*.ps1`).
