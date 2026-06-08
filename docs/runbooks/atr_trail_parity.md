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

## Status & next

Parity A is sufficient to (a) fix the ATR definition (Wilder) for exit
pre-selection and (b) confirm the trail model is sound. **Before PantheonMaster
trades real money, run Parity B** (NT backtest vs NT live/replay) — that is an
NT-side run, pairing with the operator's "run it first" plan. Residual-divergence
work is optional and should not be chased into overfitting.

Code: `analysis/exits/nt_atr_trail_parity.py` (+ tests
`tests/analysis/exits/test_nt_atr_trail_parity.py`), driver
`scripts/atr_trail_parity.py`.
