# Market data gathering (bars + ticks) — capability & automation

*Written:* 2026-06-06 · *Status:* capability doc + automation plan
*Reads-with:* CLAUDE.md (MarketData section), `docs/AI_REPO_INDEX.md`,
`src/ta_foundation/strategies/TaFoundationExecutionBridge/`

> **Why this exists:** the canonical local market data in `D:\MarketData` (bars +
> ticks per `(instrument contract)`) is what feeds the anchor engine, pattern
> engine, exit-sim, prediction, and any scout. When a needed window/instrument
> isn't present (e.g. **NQ 06-26 ticks** for the exit-sim), we must NOT make the
> operator the long pole by hand-downloading from NinjaTrader. NT can dump any
> window it has data for **programmatically**. This doc captures that capability
> and the path to automate it.

## Two exporters — use the right one

| Component | Type | Trigger | Use for |
|---|---|---|---|
| **`TaFoundationDataExportStrategy.cs`** | **Strategy** | **Strategy Analyzer with explicit From/To** (or the optimizer bridge with a 1-combo seed) | **On-demand historical dump of any window** — bars + optional ticks. *This is the automation target.* |
| `TaFoundationMinuteBarExporter.cs` | Indicator | Streams from whatever chart it's attached to (real-time + `BackfillOnce`) | Keeping live bars fresh on attached charts; not for arbitrary historical windows |

Both live in `src/ta_foundation/strategies/TaFoundationExecutionBridge/` and are
deployed in `Documents\NinjaTrader 8\bin\Custom\`.

## TaFoundationDataExportStrategy — what it does

- Writes **every closed bar** to `<OutputDirectory>\<instrument>.<suffix>.txt` and,
  when `ExportTicks=true`, **every tick** to `<instrument> Tick.<suffix>.txt`.
- Places **no orders**, O(1) memory (StreamWriter), crash-safe periodic flush —
  safe for multi-year dumps.
- Timestamps normalized to **UTC**. Formats:
  - Bars: `yyyyMMdd HHmmss;open;high;low;close;volume`
  - Ticks: `yyyyMMdd HHmmss <frac7>;last;last;last;volume` (sub-second 100ns)
- **Parameters:** `OutputDirectory` (default `D:\MarketData`), `FilenameSuffix`
  (default `Export`), `ExportTicks` (default false), `OverwriteIfExists` (default
  true; false = append).
- **Filename suffix convention:** default `Export` → `.Export.txt`, deliberately
  distinct from the streaming `.Last.txt` (indicator) and `.Full.txt`
  (indicator BackfillOnce) so the three feeds never collide. **The Python parser
  accepts all three suffixes** (`.Last` / `.Full` / `.Export`), so an `Export`
  dump is consumed transparently and never clobbers the live `.Last.txt`.

## Manual run (today)

Strategy Analyzer → select `TaFoundationDataExportStrategy` on the instrument →
set From/To → set `ExportTicks=true` if ticks needed → Backtest. Output lands in
`D:\MarketData`. Console prints `EXPORT_OPEN …` / `EXPORT_DONE bars_written=… ticks_written=…`.

## Automation plan (the deliverable) — "gather data" via the optimizer bridge

> **Shipped:** the one-time gather is `scripts/gather_market_data.py` (engine:
> `web/market_data_export.py`). The scan-and-refresh *routine* built on top of it
> — "what's stale/missing, pull it, skip expired" — is
> [`market_data_refresh_routine.md`](market_data_refresh_routine.md)
> (`scripts/refresh_market_data.py`). Start there for day-to-day upkeep.

The strategy's own header notes it can be *"triggered via the existing
optimizer-bridge with a 1-combo seed template."* That is the automation: reuse
the machinery we already have (catalog → seed-gen → bridge dispatch → status
poll) to dump data with **no manual NT interaction**.

**Proposed module `src/ta_foundation/web/market_data_export.py`** (or a CLI under
`scripts/`):

```
gather_market_data(instrument="NQ 06-26", from_date, to_date, *, export_ticks=True,
                   suffix="Export", output_dir=r"D:\MarketData") -> GatherResult
```

Steps (all reuse existing code):
1. **Seed:** generate a 1-combo *fixed-backtest* template for
   `TaFoundationDataExportStrategy` via `regenerate_recipe_seed` /
   `generate_fixed_backtest_template`, pinning `OutputDirectory`, `FilenameSuffix`,
   `ExportTicks`, `OverwriteIfExists`, `From`, `To`, and the instrument. (No swept
   params — it's a single backtest, not an optimization.)
2. **Dispatch:** write the RunBatch IPC command (`ensure_bridge_available` first,
   `closeTempTabs:true`) the same way `optimizer_runner` does, pointing at a temp
   source/dest folder.
3. **Wait:** poll `C:\temp\nt8_status.json` to terminal (reuse the driver poll).
4. **Verify:** assert `D:\MarketData\<instrument>.<suffix>.txt` (and `… Tick.<suffix>.txt`)
   exist and grew; surface bars/ticks line counts.

**Guardrails:** ticks are GB-scale for multi-year windows — default to the
specific window needed, not "all history". Respect the single-writer bridge lock
(don't run while an optimizer batch owns the bridge). NT must be logged in and
warm (see [[project_ninjatrader_startup]] — post-8.1.7.1 login requirement).

**Bridge-aware scheduling idea (operator's framing):** when the optimizer spins
NT up for a run, it can first dispatch a data-export pass for the run's
instrument/window so the bars+ticks the downstream analysis (exit-sim, scout)
needs are present before the optimization starts — making data-gathering a
self-service step of the pipeline rather than an operator chore.

## Immediate use case

The **exit-sim (Option A of the PantheonBotV2↔PantheonMaster exit discovery)**
needs ticks for the target window. `D:\MarketData` currently has **NQ 03-26**
ticks but not the **NQ 06-26** May–June deployment window. This automation dumps
the missing NQ 06-26 ticks on demand instead of a manual download. (Until built,
run exit discovery on the NQ 03-26 window where ticks already exist.)
