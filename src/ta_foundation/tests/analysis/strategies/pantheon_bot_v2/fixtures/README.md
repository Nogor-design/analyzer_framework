# PantheonBotV2 parity-test fixtures

To populate this directory, capture a short NinjaTrader Strategy Analyzer run:

1. Open Strategy Analyzer, select **PantheonBotV2** on NQ ~5m for one trading day.
2. In the strategy settings set:
   - `EnableDebugPrint = true`
   - `BarsRequiredToTrade = 20`
   - All `Required*RegimeFilter` and `BlockedVolatilityRegimeFilter` set to `Any`
     (we want the regime print every primary bar, not only on trading bars).
3. Run the backtest.
4. Open the NinjaTrader **Output window**, select all, copy.
5. Save to this directory as `debug_<YYYY-MM-DD>_<INSTRUMENT>_<TF>.txt`.
6. Export the strategy settings as a CSV (NT Strategy Analyzer → Save → CSV).
   The settings_loader handles NT's display-name format. Save it next to the
   debug log with a matching stem, e.g.
   `debug_2026-02-26_NQ_2D.txt` + `debug_2026-02-26_NQ_2D.csv`.

A hand-authored JSON is also supported (canonical property names):

```json
{
  "TrendEmaPeriod": 34,
  "TrendFlatThreshold": 0.03,
  "VwapNearThresholdAtr": 0.20,
  "VolatilityLowPercentile": 0.33,
  "VolatilityHighPercentile": 0.67
}
```

The test_classifier_parity test auto-discovers fixture pairs by stem match;
drop multiple `debug_*.txt` + settings file pairs in this directory and the
tests parametrize across all of them.

## Bar-level parity (ticket 2B)

To enable `test_bar_regime_parity`, also export the minute bars
NinjaTrader used during that same backtest:

1. Open a 1-minute chart of the same contract (e.g. `NQ 03-26`).
2. Load enough history. The volatility-percentile window is 100 primary
   bars by default and the EMA/ATR seeds need ~2× their period to
   stabilise. For a 5m primary + 15m HTF, a week of history before the
   debug window is plenty; a month is bulletproof.
3. Chart menu → **File → Export Historical Data → Minute** → save as
   `bars_<stem>.Last.txt` where `<stem>` matches your debug log stem
   without the `debug_` prefix. Example:
   `debug_2026-02-26_NQ_2D.txt` → `bars_2026-02-26_NQ_2D.Last.txt`.
4. Rerun pytest. With the bars file present the parity test runs;
   without it the test skips with a directive that mirrors these steps.

The exporter writes the format the existing
`MinuteBarsLastTxtParser` understands (`yyyyMMdd HHmmss;O;H;L;C;V`).
