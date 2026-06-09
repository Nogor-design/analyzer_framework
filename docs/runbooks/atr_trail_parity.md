# AtrTrail Trail-Parity (Parity A) — runbook & findings

*Created 2026-06-08 · Owner: Claude (PM) · Reads-with:
`docs/designs/ma_pool_enrichment_and_pantheonmaster_migration.md` §6,
`docs/handoffs/pantheon_bot_v2_parity_handoff_2026-05-19.md` (Open Q #4),
`analysis/exits/`.*

**Read this before building any AtrTrail / exit-parity / "NT ATR" tooling — the
work is done; do not rebuild it.**

## What "trail parity" means (two distinct questions)

| | Question | Status |
|---|---|---|
| **Parity A** | Does a Python model of PantheonMaster's AtrTrail reproduce NT's **backtest** stop exits? | ✅ **BUILT** (this doc) |
| **Parity B** | Does NT's **managed-SL/TP backtest** match **live** explicit-stop + ChangeOrder trailing? | ⛔ open — needs an NT replay/paper run (operator/NT-side) |

Parity A is the prerequisite: there's no point comparing live to backtest if the
Python trail model doesn't even match the backtest. It also validates the ATR
definition used everywhere in exit pre-selection.

## Two exit models in the repo — NOT duplicates (do not merge)

- `analysis/exits/simulate.py` — trails the stop **continuously on ticks**. Right
  model for *ranking* hypothetical exit policies over the tick cache.
- `analysis/exits/nt_atr_trail_parity.py` — trails **once per bar close** off the
  *bar* high/low and a *bar* ATR, ratchets, rounds to tick, fills intrabar on
  touch. This mirrors NT's PantheonMaster mechanics so a per-trade price
  comparison is meaningful. `simulate.py` would diverge by construction.

## NT mechanics replicated (PantheonMaster.cs, audited 2026-06-08)

- Initial protective stop = `StopTicks` (default **60**) ticks from entry
  (`SetupBacktestExit`, L792-811).
- Each bar close, AtrTrail proposes `highSinceEntry − AtrTrailMultiple·currentAtr`
  (long) / `lowSinceEntry + AtrTrailMultiple·currentAtr` (short)
  (`ManageHistoricalOrBarCloseDynamicStops`, L1128-1132).
- Move only if it improves by > ½ tick, after `RoundToTick`
  (`MoveHistoricalStopIfImproved`, L1076-1098) — a one-way ratchet.
- `currentAtr = ATR(AtrPeriod)[0]`, `AtrPeriod` default **14** (L990, L400).
- Managed stop fills at the stop price on intrabar touch (Low≤stop long).
- Defaults: `AtrTrailMultiple=2.0`, `TickSize=0.25` (NQ).

Minute bars suffice (no tick file): the stop price is fixed at bar close and the
managed stop fills *at* that price on touch, so bar high/low reproduces the exit
price. Ticks would only refine sub-bar fill *timing*.

## How to run

```bash
python scripts/atr_trail_parity.py [session_id] [bars_file]
# default: opt_a09359e6b60b  +  D:\MarketData\NQ 06-26.Export.txt
```

Pools every stop-exit trade across the session's final backtests (AtrTrail params
are pinned identically across all templates) and grades the replica's exit price
vs NT's, under **both** ATR smoothings.

## Findings (opt_a09359e6b60b, NQ 06-26, 2026-06-08)

8,786 trades pooled (7,372 "stop loss" exits graded).

| ATR smoothing | match rate (±1 tick) | median diff |
|---|---|---|
| **Wilder** | **70.8 %** | **0.0 ticks** |
| SMA | 26.0 % | 5.0 ticks |

**Conclusion 1 — NT's `ATR()` indicator is Wilder, not SMA.** This **corrects**
the "NT ATR is SMA-based" claim that was in `CLAUDE.md`: SMA matches only 26 %
with a 5-tick median error; Wilder matches 70.8 % with an *exact* median. Exit
pre-selection (which keys on PantheonMaster's `currentAtr = ATR(AtrPeriod)`) must
use **Wilder** ATR.

> **Two-ATR caveat — do NOT over-correct the discovery engine.** This finding is
> about PantheonMaster's **exit** ATR (NT's `ATR()` indicator = Wilder). It is a
> *different* ATR from the **discovery candle-feature** ATR (`size_vs_atr` in
> `analysis/entry_strategies/candle/features.py`), which is a *simple rolling
> mean of TR* matched on purpose by the C# `SdfCandleFeatureEngine`
> ([[project-discovery-nt-entry-parity]]). Leave that one as a simple mean —
> switching it to Wilder would break entry parity. Right ATR depends on which NT
> code path you are mirroring.

**Conclusion 2 — the bar-close AtrTrail model is faithful** (exact price when it
matches). The ~29 % residual is NOT the smoothing; the candidate sources, to
investigate only if tighter parity is needed (do not overfit the replica):
- **Gap-through fills:** when a bar gaps past the stop, NT fills at the bar's
  actual path (≈ open), not the stop price; the replica fills at the stop.
- **ATR warm-up seeding:** NT seeds ATR from chart warm-up bars outside the
  exported file; early-window trades carry a small ATR offset.
- **Entry-bar high init:** the replica seeds `highSinceEntry` at the entry price,
  not the entry bar's High.
- **Fill-timing off-by-one** between bar-close stop update and intrabar touch.

Code: `analysis/exits/nt_atr_trail_parity.py` (+ tests
`tests/analysis/exits/test_nt_atr_trail_parity.py`), driver
`scripts/atr_trail_parity.py`.

---

# Parity B — NT backtest ↔ NT live (the gate before real money)

**Parity A validated the Python model against the NT *backtest*. Parity B asks
whether the NT *backtest* matches NT *live* — because PantheonMaster runs two
different stop engines** (`PantheonMaster.cs` header `[DESIGN-2]`, "correct
architecture", do not mix the paths):

| | Backtest | Live |
|---|---|---|
| Stop API | `SetStopLoss` / `SetProfitTarget` (managed) | `ExitLong/ShortStopMarket` + `ChangeOrder` (unmanaged) |
| Trail reference | `highSinceEntry` = **bar** high, updated **per bar close** (`Calculate.OnBarClose`) | `liveHighestFavorablePrice` = **tick** high, updated **every tick** (`OnMarketData` → `ManageLiveDynamicStop`, L1171-1219) |
| ATR offset | `AtrTrailMultiple · ATR(14)` bar-close | same (ATR is bar-close in both) |

## The structural divergence (predict before measuring)

Same ATR offset, **different favorable-price reference**: live trails off the
intrabar **tick** high, backtest off the **bar-close** high. The tick high leads
the bar-close high, so **the live stop ratchets up sooner and tighter** within a
bar. Expected consequence: **live exits runners earlier than backtest** → the
backtest likely **overstates** AtrTrail trend-trade profit (and gives back less
on reversals). The magnitude is the empirical question; the *direction* is known.

Our two Python models conveniently bracket the two NT engines:
`nt_atr_trail_parity.py` (bar-close) ≈ NT **backtest**; `analysis/exits/simulate.py`
(tick-continuous) ≈ NT **live**.

## Step 1 (do first, no NT) — offline pre-estimate of the gap ✅ DONE

`scripts/atr_trail_live_estimate.py` runs the **tick-trail** replica
(`replicate_nt_atr_trail_tick`, ≈ live: trails off tick highs, ATR bar-close,
Wilder per Parity A) on the NQ tick cache and compares its P&L to NT's **actual**
backtest P&L, per pooled trail trade.

**Result (opt_a09359e6b60b, 5,734 trail trades in tick coverage ≤ 2026-05-27):**

| | total P&L |
|---|--:|
| NT backtest (actual) | **$81,275** |
| tick "live" estimate | **$61,870** |
| **Haircut (live − backtest)** | **−$19,405 = −23.9%** |

Per-trade: median Δ **$0** (most trades exit identically), but **19% are worse
live** and those runners carry the loss. This **confirms the predicted
direction**: live trails tighter → exits trend runners earlier → **the
backtested AtrTrail edge is ~24% optimistic.**

> **Treat −24% as a floor, not the final number.** The tick model fills *at* the
> stop price; real live stop-market fills add slippage, and ChangeOrder latency/
> rejections can only make live *worse*. Coverage also misses the May 28–Jun 3
> tail. **Action: haircut the pool's backtested AtrTrail PF/net by ≥~24% for
> sizing/expectations until Parity B Step 2 measures the real live gap.**

## Step 2 — first-attempt findings (2026-06-09): two bugs caught before live $

The first replay attempts surfaced two real problems — exactly what this gate exists for.
**No haircut measured yet**; fix both, then re-run.

1. **Template `DiscoveryExitPolicy=AtrTrail` does NOT apply on chart/playback template-load —
   it silently falls back to the `FixedRR` default** (`PantheonMaster.cs` L373). Proof: chart runs
   exited at clean ±$300 / +$450 with a `Profit target`, and the Output printed `Target=…`
   (`ShouldUseTarget()` returns true only for FixedRR, L915). The **optimizer pool ran AtrTrail
   correctly** (F_065 = 281 `Stop loss` + 224 `Close position`, no targets, 186 distinct P&Ls), so
   the pool/analysis is valid — only chart-load drops the enum. **Workaround:** after loading the
   template, **manually set `DiscoveryExitPolicy = AtrTrail` in the strategy dialog**, and confirm
   via Output `Protective orders: Stop=… (no target)` and exits never named `Profit target`.
   *(Consequence: the "slow run matched backtest" result was FixedRR↔FixedRR — trivially parity-clean,
   measures nothing about AtrTrail.)*

2. **Live AtrTrail short-stop crash — FIXED 2026-06-09 (needs recompile).** Once AtrTrail was active
   live, the first short submitted `BuyToCover StopMarket @ 27271.75` BELOW the market → NT rejected
   it ("buy stop … can't be placed below the market") → strategy terminated. Cause: a short trail stop
   is `lowSinceEntry + AtrTrailMultiple·ATR`; when price retraces up past that level, `MoveStopIfImproved`
   submitted a buy-stop below the current market without a side check. **Fix:** added a current-market
   guard in `MoveStopIfImproved` (skip the `ChangeOrder` if the proposed stop is on the illegal side of
   `GetCurrentBid/Ask`; the existing valid resting stop stays). Protects all live trailing policies
   (AtrTrail/Chandelier/Giveback/BreakEven). **Recompile PantheonMaster in NinjaScript Editor (F5)
   before re-running.**

**Corrected re-run recipe:** recompile → load template → **manually set DiscoveryExitPolicy=AtrTrail** →
confirm Output shows `(no target)` and no `Profit target` exits → run a SHORT window at MODERATE speed
(not max) → export per-trade `Trades.csv` for BOTH the live replay and a matching backtest → diff.

## Step 2 — READY-MADE single-template run (use this; built 2026-06-08)

The pooled "whole session" diff isn't runnable as one Market Replay (124 templates, one
param set per run). So Parity B Step 2 runs on **one representative template, F_065** (the
highest-trade-count final backtest: 505 trades / 244 round-trips, Overlap session, NQ 06-26).

**Template installed:** `Documents/NinjaTrader 8/templates/Strategy/PantheonMaster/ParityB_F065_LIVE.xml`
(NT8 stores strategy templates as `templates/Strategy/<StrategyName>/<name>.xml` — load via
PantheonMaster → **Template → Load → ParityB_F065_LIVE**; reopen the strategy dialog if NT was
already running). It is F_065's
exact backtest config with **only two changes**, which are the whole point of Parity B:
- `UseLiveStopManagement = true` — engages the LIVE tick-trail (`ChangeOrder` via `OnMarketData`)
  instead of the backtest bar-close managed stop. **Without this the replay just reproduces the
  backtest and measures nothing** (path switch: `UseLiveStopManagement && State==Realtime`,
  PantheonMaster.cs L485/523/763).
- `EnableDebugPrint = true` — emits the `[PantheonMaster] ChangeOrder stop -> {price}` trail prints.

**Run it:**
1. Ensure NT has **Market Replay data** for **NQ 06-26** over ~**2026-05-01 → 2026-06-03** (Tools →
   Historical Data → Market Replay, download if missing).
2. Connect via the **Playback** connection; add **PantheonMaster** to an **NQ 06-26, 1-min** chart
   (or Strategy Analyzer on the Playback feed); load the template above. The strategy's time filter
   (16:00 Denver +8h) gates to the Overlap session automatically — replay the full date range.
3. Let it replay to the end; **export the replay `Trades.csv`** (e.g. `C:\temp\replay\F065_live_Trades.csv`).

**Diff (the measurement):**
```bash
python scripts/atr_trail_live_diff.py \
  --bt ".ta_artifacts/web_optimizer/sessions/opt_a09359e6b60b/deployment_package/final_backtest_handoff/nt8_backtest_results/F_065/Trades.csv" \
  --live "C:\temp\replay\F065_live_Trades.csv" --trail-only
```
It prints matched count, both-sides P&L, the **measured haircut %**, and compares to the Step-1
prediction (−23.9%). Expectation: live ≈ 24%+ worse (live trails tighter → exits runners earlier).
*(Optional rigor: also replay once with `UseLiveStopManagement=false` to confirm Market Replay itself
reproduces the backtest, isolating the stop-engine delta from any replay-fill delta.)*

## Step 2 — generic capture sheet (params reference)

The diff harness is ready: `scripts/atr_trail_live_diff.py` (core
`diff_backtest_vs_live_trades` in `nt_atr_trail_parity.py`, tested). It matches
the backtest and replay `Trades.csv` **per trade on entry time** and prints the
measured aggregate haircut vs the Step 1 prediction. **The realized `Trades.csv`
P&L is the source of truth — the `ChangeOrder` Output prints carry no timestamp
and are diagnostic only (use them to eyeball the trail path, not to compute the
number).** Do all of this on the *same instrument/window as a known backtest*.

**A. Configure the strategy (must match the backtest exactly):**
- `UseDiscoveryExitPolicy = true`, `DiscoveryExitPolicy = AtrTrail`
- `AtrTrailMultiple = 2.0`, `StopTicks = 60`, `AtrPeriod = 14`
- `EnableDiscoveryFilters = false`, `RegimeMode = Any` (regime off — isolate the trail)
- `EnableDebugPrint = true`
- Confirm the loaded build is the **`[BUG-3]`-fixed** one (see Gotchas) before trusting live behavior.

**B. Run** PantheonMaster in **Market Replay** (tick replay), same date range and
contract as the backtest session you'll diff against (default `opt_a09359e6b60b`,
NQ 06-26, ≤ 2026-05-27 for tick coverage).

**C. Export** the replay results as NinjaTrader **`Trades.csv`** (Strategy
Performance → Trades → right-click → Export, or the grid export). Drop it in a
folder, e.g. `C:\temp\replay\Trades.csv`. (Optionally save the Output window text
alongside for the `ChangeOrder` trail-path sanity check.)

**D. Run the diff:**
```bash
python scripts/atr_trail_live_diff.py --bt opt_a09359e6b60b --live C:\temp\replay
# add --trail-only to grade just trailed-stop exits (the population the haircut is about)
```
It prints matched count, both-sides aggregate P&L, the **measured haircut $/%**,
worse-live trade count, median per-trade and exit-time deltas, compares to the
Step 1 floor (−23.9%), states the decision, and writes
`atr_trail_live_diff_detail.csv` (per-trade rows for inspection).

**E. Decision rule** (printed automatically): if live aggregate P&L is within
~10% of backtest with no systematic worse-fill bias, the backtested pool is
trustworthy for sizing. If live is materially worse (expected: tighter trail →
earlier exits, and Step 1 says ≥~24%), apply the **measured** haircut to
backtested PF/net before promotion/sizing — and treat the live number, not the
bar backtest, as the planning figure.

## Gotchas

- `[BUG-3]` (`.cs` header): `ResetDynamicTrackingOnEntry` initialised
  `liveHighest/LowestFavorablePrice` from `Position.AveragePrice` at a point that
  was too early — verify it is the *fixed* build before trusting live prints.
- ATR is bar-close in **both** engines, so the offset is identical; only the
  reference differs. Don't "fix" live to use bar ATR.
- `ChangeOrder` can be rejected/latent live; Market Replay won't show broker
  latency — true live may diverge a bit more than replay.
- Two daily-risk systems can both be active (`[DESIGN-3]`); keep Legacy vs
  Discovery risk caps from conflicting in the live config.

## Status

- **Parity A:** ✅ done (Wilder confirmed, model faithful).
- **Parity B Step 1 (offline pre-estimate):** ✅ done — **backtest overstates
  AtrTrail P&L by ~24%** (live trails tighter); haircut the pool before sizing.
- **Parity B Step 2 (live/replay diff):** harness ✅ built & tested
  (`scripts/atr_trail_live_diff.py`); ⬜ awaiting the operator's NT Market Replay
  `Trades.csv` to produce the measured number. Gates real money — confirms/refines
  the ~24% (expected to widen with real fill slippage). Capture sheet above.
