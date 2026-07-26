# Handoff — Automated C#↔Python Parity Loop, Phase 1 (2026-06-12)

**Branch:** `CandleDiscovery`  ·  **Strategy:** PantheonMaster  ·  **Status:** Phase 1
built, tested (39 exits tests green), committed. Live NT dispatch validation pending.

Read alongside the memory note `project_parity_automation.md` and the related
`docs/runbooks/atr_trail_parity.md`, `docs/runbooks/pantheon_stop_engine_and_test_harness.md`.

---

## 1. Why this exists (the directive)

Eric (2026-06-12): stop hand-testing strategies in NinjaTrader — manual param entry,
clicking Playback, exporting CSVs — it won't scale across many strategies and is
error-prone. Build an **automated parity loop** that:
- hooks the **real strategy** (not a reimplementation),
- fires/monitors trades and runs backtests via the optimizer AddOn,
- verifies the **Python code matches the C#** so exit pre-selection / sims are trustworthy.

**Core flaw it replaces:** the external `PantheonTestHarness` AddOn reimplemented the
trail logic (a 3rd fork) → it validated the harness, not the strategy. We hook the
strategy itself instead.

### 3-phase plan (Eric chose "start with Phase 1")
- **P1 — backtest leg** (DONE): bar-close stop-audit trajectory vs the Python bar
  replica. Fully automatable now; no live NT control needed.
- **P2 — live-tick leg**: a generic strategy command-channel + **headless
  strategy-on-Playback control** (the one hard NT unknown — enabling a live strategy
  without a click). Reuses the P1 comparator against the **tick** replica.
- **P3 — generalize**: strategy-agnostic framework + per-strategy parity gate so a new
  strategy is a config entry, and any `.cs` change is parity-gated.

Guardrails: test hooks stay behind a `TestMode`/flag and never run in production
deploys; never re-grow trading logic into the AddOn.

---

## 2. What's built in Phase 1 (files + commits)

Commit chain on `CandleDiscovery` (newest last):
`142344b` → `7ecc439` → `8c196c6` → `e57e963` → `fb31c85` → `e543f9c`.

### Strategy hooks (C#) — `src/ta_foundation/strategies/PantheonMaster/PantheonMaster.cs`
- **`142344b`** live trail delegates to the shared pure `PantheonStopEngine`
  (`strategies/shared/PantheonStopEngine.cs`); verified behavior-preserving; fixed a
  compile-blocking `mkt`→`liveMarketPrice` typo.
- **`7ecc439`** fixed managed/explicit stop **mixing in Playback** (the "trail never
  moved" bug): `UseLiveStopManagement` is now a **HARD mode switch** — `true` =
  live/explicit (`ExitLongStopMarket`+`ChangeOrder`), `false` = backtest/managed
  (`SetStopLoss`). Both managed gates now key off the flag, not `State`.
- **`8c196c6`** `StopAuditCsvPath` param + `AppendStopAudit` writes one row per stop
  event from BOTH paths. Schema:
  `time,event,policy,dir,entry,market,favorable,atr,peakProfit,oldStop,newStop`.
  `ForceEntry` param fires one filter-bypassing entry (live test primitive).

### Python comparator core — `src/ta_foundation/analysis/exits/`
- **`nt_atr_trail_parity.py`** (`e57e963`): refactored the bar-close trail loop into
  ONE shared `_replay_bar_trail`; added `replicate_nt_atr_trail_trajectory` (full
  per-ratchet stop trajectory + exit). `replicate_nt_atr_trail`'s contract unchanged
  (its 13 tests still pass).
- **`stop_audit_parity.py`** (NEW, `e57e963`): `parse_stop_audit` (+ trade segmentation
  for live INIT-row and bar-close TRAIL-only audits), `diff_trade_stop_trajectory`
  (asof active-stop diff over the union of event bars → match rate, median/max tick
  diff, **first-divergence bar**), `diff_trade_atr` (runtime Wilder-vs-SMA: the audit
  logs NT's own `currentAtr` per event), `parity_report` (pass/fail + thresholds).
  Pure Python.

### CLI + runner — `scripts/`
- **`stop_audit_parity.py`** (`fb31c85`): the verdict CLI. bars + Trades.csv (entry
  anchors) + audit → `parity_report` → exit 0 (PASS) / 1 (FAIL). ASCII-only output
  (Windows cp1252 console). Validated on real 82k NQ bars (100% on a clean audit;
  +4-tick injected drift → 94.4%, exit 1, divergence bar pinpointed).
- **`run_parity_backtest.py`** (`e543f9c`): headless runner. Drives PantheonMaster
  through the optimizer batch bridge (1-combo fixed backtest, same path as
  `web/market_data_export.py`), pinned for the bar-close leg, then auto-grades.
  `build_parity_backtest_template` is pure + tested vs the real `.cs`.

### Tests (all green): `src/ta_foundation/tests/analysis/exits/`
`test_nt_atr_trail_parity.py` (13), `test_stop_audit_parity.py` (8),
`test_stop_audit_parity_cli.py` (2), `test_run_parity_backtest.py` (1), plus
`test_pantheon_stop_engine.py` (16). `python -m pytest src/ta_foundation/tests/analysis/exits/ -q` → 39 pass (+16 engine).

---

## 3. How to run it

**Dry-run the template (no NT):**
```bash
python scripts/run_parity_backtest.py --from-date 2026-03-12 --to-date 2026-03-20 --no-dispatch
# prints the generated template path; inspect the pins
```

**Full headless run (needs NT logged in + the optimizer batch AddOn authorized):**
```bash
python scripts/run_parity_backtest.py --instrument "NQ 06-26" \
    --from-date 2026-03-12 --to-date 2026-03-20 --bars "D:\MarketData\NQ 06-26.Export.txt"
# build template -> dispatch RunBatch -> poll -> grade -> PASS/FAIL
```

**Grade an audit you already have (e.g. from a manual SA backtest):**
```bash
python scripts/stop_audit_parity.py --audit C:\temp\pm_audit.csv \
    --trades "<...>/Trades.csv" --bars "D:\MarketData\NQ 06-26.Export.txt" --atr-mode wilder
```
For a **manual** SA backtest set on PantheonMaster: `StopAuditCsvPath=C:\temp\pm_audit.csv`,
`UseLiveStopManagement=false`, `UseDiscoveryExitPolicy=true`, `DiscoveryExitPolicy=AtrTrail`,
`EnableDiscoveryFilters=false`. Delete the audit file first (it's append-only).

---

## 4. Key design decisions / invariants (don't relitigate)
- **Entry anchors come from Trades.csv**, not a new C# INIT row — kept the `.cs`
  stable. The comparator matches audit trades to Trades.csv entries by time order.
- **`UseLiveStopManagement` is the mode switch**: run SA/parity **backtests with it
  FALSE** (managed path), or entries get NO stop. Live/Playback = TRUE.
- **bar-close audit ↔ bar replica** is P1; **tick audit ↔ tick replica** is P2, same
  comparator (`stop_audit_parity` is path-agnostic).
- Audit `time` and bars `dt` are both **naive-Denver** (NT-local). `MinuteBarsLastTxtParser`
  → `.dt.tz_localize(None)`.
- Parity backtest is a **fixed** backtest → **no `OptimizationParameters`**, so enum
  params (`DiscoveryExitPolicy=AtrTrail`) can't crash the NT optimizer.
- **NT auto-compiles** on a `.cs` change under `bin\Custom` (no F5). `ObserveCompile`
  IPC is only a headless fallback.
- ASCII-only script output (Windows console is cp1252).

---

## 5. THE NEXT STEP (start here in the new chat)

**Validate the live dispatch — it's the only untested piece** (no running NT was
available). The first real `run_parity_backtest`:
1. confirms the **RunBatch Trades.csv output layout** (the runner globs `dest_folder`
   recursively for `Trades.csv` — adjust if the AddOn writes elsewhere),
2. confirms the **`StopAuditCsvPath` round-trip** (the strategy actually writes it
   during a batch SA run),
3. yields the **first REAL C#↔Python trajectory parity read** — including whether
   Wilder or SMA matches at the per-event level (run both `--atr-mode wilder` and `sma`).

Recommended: `--no-dispatch` first to eyeball the template, then a short live window.
Expect to tweak the `_find_trades_csv` path or audit-collection if the AddOn layout
differs. If parity PASSES → the backtest leg is trustworthy and we move to **Phase 2**
(generic command-channel hooks + headless Playback control for the live-tick leg).

---

## 6. Open items / notes
- The PantheonMaster live trail itself is **already live-validated** in Playback
  (2026-06-11/12): explicit `PantheonLongStop` trailed cleanly, no managed-mix, no
  side-of-market rejections (see `project_pantheon_stop_engine.md`).
- Uncommitted in the tree: unrelated sample CSVs/PNGs under `docs/samples/`, IDE files,
  auto-gen docs — leave them or commit separately; not part of this work.
- Bigger business arc (selector, account-risk engine, deployment pool) is unchanged and
  tracked in `project_business_roadmap.md` / `project_ma_pool_pantheonmaster.md`. The
  parity loop is the **trail-parity gate** that protects the first live dollar.
