# StrategyDiscoveryFilter — NinjaTrader Strategy

Translates every condition discovered by the ta_foundation Strategy Discovery
engine into an optimizable NinjaTrader 8 parameter.

---

## Installation

1. Copy `StrategyDiscoveryFilter.cs` to:
   ```
   Documents\NinjaTrader 8\bin\Custom\Strategies\
   ```
2. In NinjaTrader → Tools → Edit NinjaScript → Strategy → compile.
3. The strategy will appear in the strategy list as **StrategyDiscoveryFilter**.

---

## How to Set Parameters from the Report

### Step 1 — Regime Mode (Section A)

Open the report → **Market Regime Distribution** section.

| What the report shows | Set RegimeMode to |
|---|---|
| Most winning days are trending (ADX feature is #1) | `TrendingOnly` (1) |
| Losing trades cluster in high-vol sessions | `NoHighVol` (5) |
| Winning trades concentrate in ranging days | `RangingOnly` (2) |
| Direction is consistently long bias | `TrendingUp` (3) |
| No clear regime preference | `Any` (0) — use as baseline |

Then open **Entry Rule Discovery**. Find the `adx >= XX` condition in the top rules.
Set **ADX Threshold** to that value (commonly 20, 25, or 30).

### Step 2 — Session Filter (Section B)

Open the report → **Trade Evaluation** → session breakdown table.

- If RTH P&L is positive and ONH P&L is negative: set `AllowRTH=true`, `AllowONH=false`.
- If ETH shows a meaningful positive P&L: set `AllowETH=true` and adjust ETH hours.
- NQ/ES RTH hours: 8:30 AM – 3:00 PM Central Time (already the default).

### Step 3 — Direction Filter (Section C)

Open the report → **Trade Evaluation** → direction breakdown.

- If Long PF > 1.3 and Short PF < 1.0: set `AllowShort=false`.
- If both are positive: leave both `true`.
- `UseTrendAlignment=true` means: in a trending_up regime, only longs are taken;
  in trending_down, only shorts. This is the safest default for trend strategies.

### Step 4 — Stop and Target (Section E)

Open the report → **MAE/MFE Profile**.

```
StopTicks  = p75 MAE in ticks  (protects 75% of trades from stopping out on noise)
TargetTicks = p50 or p75 MFE in ticks  (captures the "natural" move)
```

Example: if p75 MAE = 15 ticks and p50 MFE = 22 ticks, set Stop=15, Target=22.

### Step 5 — Exit Policy (Section E)

Open the report → **Exit Policy Sweep** → top-ranked policy.

| Report top policy | Set ExitPolicy to |
|---|---|
| FixedRR 1:1.5 or 1:2 | `FixedRR` (0) |
| ATR_Trail 1.5x or 2.0x | `AtrTrail` (1) |
| Chandelier 20-bar | `Chandelier` (2) |
| Giveback 40% | `Giveback` (3), GivebackPct=0.40 |

### Step 6 — Daily Risk Limits (Section F)

Open the report → **Drawdown Analysis**.

```
MaxDailyLossUsd = avg_winning_trade × 1.0  (stop after one average winner lost)
MaxDailyTrades  = max_consecutive_losses + 2
```

---

## Optimizer Workflow

Once the basic backtest looks reasonable, use the NinjaTrader optimizer to
confirm which parameters from the report actually improve results.

### Optimizer ranges based on the report

**Regime — if ADX was the top feature:**
```
AdxThreshold:  min=18, max=32, step=2
RegimeMode:    try values 0,1,5 (Any, TrendingOnly, NoHighVol)
```

**Exit — based on MAE/MFE percentile ranges:**
```
StopTicks:   p50_mae to p90_mae in 4-tick steps
TargetTicks: p25_mfe to p75_mfe in 4-tick steps
```

**ATR trail (if AtrTrail was the top exit policy):**
```
AtrTrailMultiple: min=1.0, max=3.5, step=0.5
```

**Session (if session was a top feature):**
```
AllowRTH: true/false
AllowONH: true/false
```

### What to look for in optimizer results

1. **Consistent improvement across a range of parameters** — not a single spike.
   A parameter value that only works at exactly 25 and degrades at 24 and 26 is
   fragile (matches FRAGILE in the Parameter Sensitivity section of the report).

2. **Cross-validate with the report's robustness verdict** — parameters marked
   ROBUST in the Parameter Sensitivity section should show flat or gradual
   improvement curves in the optimizer, not sharp peaks.

3. **Cluster the results** — optimizer output showing many near-identical
   parameter sets in the top 20 results means you have found a plateau, which
   is healthy.

---

## Debug Output

Set `EnableDebugPrint=true` to see every entry and exit reason in the
NinjaTrader Output window:

```
[SDF] ENTRY Long | bar=1234 regime=trending_up vol=mid_vol session=RTH
      ADX=28.4 ATR=18.50 EMA(fast)=19820.50 EMA(slow)=19810.00
      stop=60t target=90t exit=FixedRR

[SDF] EXIT Long | ATR trail hit | bar=1248
```

This lets you verify that the regime and session labels match what the report
identified as the winning conditions.

---

## Mapping to Report Sections

| Parameter group | Report section to read |
|---|---|
| Regime Mode, ADX Threshold | Feature Importance + Market Regime Distribution |
| Session Filter | Trade Evaluation (session breakdown) |
| Direction | Trade Evaluation (direction breakdown) |
| Stop/Target Ticks | MAE/MFE Profile |
| Exit Policy + ATR Multiple | Exit Policy Sweep |
| Daily Loss/Profit/Trades | Drawdown Analysis + Position Sizing |
