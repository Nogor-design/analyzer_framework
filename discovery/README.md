# Strategy Discovery Workflow

## The Core Problem

Every YAML parameter you add multiplies the runtime:
- 3 multipliers × 2 lookbacks × 4 TP × 3 SL = **72 combos** (fast)
- 9 patterns × 3 entries × 2 TF × 4 TP × 4 SL = **3,584 combos** (hangs)

The solution is a **funnel**: run cheap questions first, drill into winners only.

---

## Always Use `--no-tick-data` for Discovery

Discovery only needs minute bars. Tick data loading adds 5-15 minutes and is
never needed here. Add `--no-tick-data` to every discovery run command.

```powershell
python -m ta_foundation.cli.main `
  --input  "C:/path/to/exports" `
  --output ./outputs_discovery `
  --report-config ./discovery/01_quick_scan.yaml `
  --market-data "D:/MarketData" `
  --recursive `
  --no-tick-data
```

---

## The Funnel — Run in Order

```
01_quick_scan.yaml      ~3-5 min    Which signal families have ANY edge?
        ↓  (take top 2-3 families)
02_candle_patterns.yaml ~8-12 min   Which candle patterns win? Best TP/SL?
03_levels_regions.yaml  ~5-8 min    LCR / breakout / level patterns?
04_ny_open.yaml         ~3-5 min    NY open scalp — does the bell trade?
05_orb_momentum.yaml    ~3-5 min    Opening range, BB, MA signals?
        ↓  (take top 5-10 combos across files)
06_validate.yaml        ~5 min      IS/OOS check — is the edge real?
```

---

## File-by-File Guide

### `01_quick_scan.yaml` — Run This First
**Question:** Does any signal family have edge on this instrument?
**What to look at:** `strategy_discovery_unified` section — the cross-family
  ranking table. If nothing shows PF ≥ 1.2, stop. You don't have signal.
**Combos:** ~250 | **Runtime:** ~3-5 min

### `02_candle_patterns.yaml` — Best Candle Setup
**Question:** Which specific candle pattern + TP/SL combination is best?
**What to look at:** `candle_discovery_ranking` — 5-tier table.
  The "Most Robust" tier (IS/OOS degradation < 10%) are real signals.
**Combos:** ~600 | **Runtime:** ~8-12 min

### `03_levels_regions.yaml` — Zone-Based Strategies
**Question:** Do large candle regions (LCR/FVG) and swing levels produce edge?
**What to look at:** `lcr_discovery_overview` — retrace rate and R2R reach rate.
  88%+ retrace rate = strong magnet. `break_retrace` signal type is typically best.
**Combos:** ~400 | **Runtime:** ~5-8 min

### `04_ny_open.yaml` — NY Bell Scalp
**Question:** Does the last premarket candle predict the first NY open move?
**What to look at:** `candle_discovery_overview` filtered to 07:29 bar.
  Look for patterns with PF > 1.3 on `next_open` entry only.
**Combos:** ~300 | **Runtime:** ~3-5 min

### `05_orb_momentum.yaml` — Opening Range + Trend Signals
**Question:** Is ORB breakout or MA crossover the better opening range play?
**What to look at:** `orb_discovery_overview` and `ma_discovery_overview`.
**Combos:** ~350 | **Runtime:** ~3-5 min

### `06_validate.yaml` — Confirm Before Trading
**Question:** Does the edge survive walk-forward IS/OOS validation?
**What to look at:** `strategy_discovery_validation` — IS/OOS degradation.
  Only trade signals with degradation < 0.15 (15% drop from IS to OOS PF).
**Combos:** ~100 (just the top combos from above) | **Runtime:** ~2-3 min

---

## How to Narrow Between Stages

After `01_quick_scan`, open the HTML report and note:
1. Which signal family ranked #1 in the unified table?
2. What timeframe (1m or 5m) had the better average PF?
3. Was PF ≥ 1.2? If not, the instrument/period has no detectable edge.

Take those answers and edit the relevant deep-dive YAML:
- Disable the losing signal families
- Lock the TF to the winner
- Expand the TP/SL sweep for the winner only

---

## Editing Tips

**To cut runtime in half:** remove one value from `tp_ticks` or `sl_ticks`.
**To double signal count:** lower `min_trades` from 20 to 10 (but validate more carefully).
**To focus on a time window:** use `session_filter` in candle_discovery patterns (if supported).
**To focus on direction:** set `direction: long` or `direction: short` instead of `both`.

---

## What the Tiers Mean

| Tier | Criteria | Action |
|---|---|---|
| Most Robust | PF ≥ 1.5, IS/OOS degradation ≤ 10%, n ≥ 30 | Trade it |
| High Quality | PF ≥ 1.3, n ≥ 20 | Paper trade, then live |
| Solid | PF ≥ 1.1, n ≥ 15 | Needs more data before trading |
| Marginal | PF ≥ 1.0 | Probably noise, skip |
| (below) | PF < 1.0 | Filtered out by min_trades |

**IS/OOS degradation** = how much PF drops from the first 70% of bars to the last 30%.
A degradation of 0.05 means PF dropped 5% — excellent.
A degradation of 0.50 means PF dropped 50% — likely overfit.

---

## Quick Command Reference

```powershell
# Stage 1 — broad scan
python -m ta_foundation.cli.main --input "C:/exports" --output ./out_scan `
  --report-config ./discovery/01_quick_scan.yaml `
  --market-data "D:/MarketData" --recursive --no-tick-data

# Stage 2 — candle deep dive
python -m ta_foundation.cli.main --input "C:/exports" --output ./out_candle `
  --report-config ./discovery/02_candle_patterns.yaml `
  --market-data "D:/MarketData" --recursive --no-tick-data

# Stage 3 — zones and levels
python -m ta_foundation.cli.main --input "C:/exports" --output ./out_zones `
  --report-config ./discovery/03_levels_regions.yaml `
  --market-data "D:/MarketData" --recursive --no-tick-data

# Stage 4 — NY open
python -m ta_foundation.cli.main --input "C:/exports" --output ./out_ny `
  --report-config ./discovery/04_ny_open.yaml `
  --market-data "D:/MarketData" --recursive --no-tick-data

# Stage 5 — ORB + momentum
python -m ta_foundation.cli.main --input "C:/exports" --output ./out_orb `
  --report-config ./discovery/05_orb_momentum.yaml `
  --market-data "D:/MarketData" --recursive --no-tick-data
```
