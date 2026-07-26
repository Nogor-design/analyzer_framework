# Large Candle Reversal — Blueprint-Parameterized Strategy

An NT8 strategy whose every input is filled from a **blueprint JSON** emitted by
the ta_foundation findings pipeline.  Swap candidates (e.g.
`midpoint_reclaim_yes` → `rebreak_no` → `explosive_start`) by loading a
different template XML — no code change.

## Files

| File | Purpose |
|---|---|
| `LargeCandleReversal.cs` | The NinjaTrader 8 strategy (install to `Documents\NinjaTrader 8\bin\Custom\Strategies\`) |
| `generate_nt8_template.py` | Reads a `*_blueprints.json` and emits one NT8 template XML per blueprint |
| `templates/` | Generated XML template files |
| `__init__.py` | Python package marker |

## End-to-end workflow

1. **Run the analysis** against your data:
   ```bash
   python -m ta_foundation.cli.main \
     --input  "C:/path/to/nt_exports" \
     --output ./outputs_discovery2 \
     --report-config discovery/large_candle_excursion_findings_report.yaml
   ```
   Outputs include `large_candle_excursion_findings_blueprints.json` alongside the HTML.

2. **Generate NT8 templates** from that JSON:
   ```bash
   python -m ta_foundation.strategies.LargeCandleReversal.generate_nt8_template \
     --input  outputs_discovery2/large_candle_excursion_findings_blueprints.json \
     --output src/ta_foundation/strategies/LargeCandleReversal/templates
   ```
   One `.xml` file per emitted blueprint.

3. **Install to NinjaTrader**:
   - Copy `LargeCandleReversal.cs` to `Documents\NinjaTrader 8\bin\Custom\Strategies\` and compile (F5 in the NT editor).
   - Copy the generated `.xml` files to `Documents\NinjaTrader 8\templates\Strategy\LargeCandleReversal\`.

4. **Load on a chart**:
   - Open a 1-minute NQ chart on the contract you ran the analysis against.
   - Strategies → Select `LargeCandleReversal` → Template → pick the blueprint (e.g. `flc_x_midpoint_reclaim_yes_1m`).
   - Enable, backtest, paper-trade.

## Blueprint → input mapping

Every field in the blueprint maps to a strategy input, grouped by category in
the NT Strategy dialog:

| Group | Blueprint section |
|---|---|
| **A: Blueprint** | `blueprint_id`, `provenance.onset_condition`, `direction_policy` |
| **B: Onset Detection** | `onset_detection.candle_size.*`, `onset_detection.failed_continuation.*`, `onset_detection.atr_period` |
| **C: Context Gates** | `onset_detection.context_gates.*` (off by default — discovery did not require them) |
| **D: Quiet Filter** | `onset_detection.quiet_filter.*` |
| **E: Session Filter** | `session_filter.*` |
| **F: Entry** | `entry_rule.*` |
| **G: Stop** | `stop_rule.*` |
| **H: Post-Entry Management** | `post_entry_management.*` (hold rules evaluated at bar+2) |
| **I: Exit / Targets** | `exit_rule.*` |
| **J: Risk** | `risk_and_friction.*` |
| **K: Debug** | `DrawMarkers`, `EnableDebugPrint` |

## Notes & limits

- **Onset support**: the full implementation covers
  `first_large_after_failed_continuation` (where all top-3 candidates come from).
  The other four onset enum values are reserved so future blueprints plug in
  without structural change, but their detection logic is not yet implemented.
- **Session labels** match the YAML's `time_segment_analysis` bucket names.  The
  strategy classifies times in the chart's time zone — make sure the chart is
  on `America/New_York` if the blueprint's `dominant_session` was derived in
  that zone.
- **Hold-rule evaluation** runs at `EvaluationBar` (default 2).  Under
  `HoldRuleMode = AnyOf` the trade holds if *any* of the three rules pass —
  mirroring the union-of-top-3 that the discovery found.  Under `PrimaryOnly`
  only the `PrimaryHoldRule` is evaluated.
- **Post-entry targets** are a % of the signal candle's range in ticks.  With a
  40-tick signal candle and `RunnerTargetPct=125`, runner target = 50 ticks
  from entry.

## Not a set-and-forget strategy

These blueprints come from historical discovery.  Paper-trade any blueprint
before enabling live — the executive summary warnings (fast_decay,
session_concentration, low OOS sample) remain relevant after template
generation.
