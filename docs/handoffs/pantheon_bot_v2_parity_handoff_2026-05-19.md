# PantheonBotV2 Parity Loop — Session Handoff (2026-05-19)

This is a complete state snapshot of an extended session that wired up
the Python ↔ NinjaTrader parity loop for PantheonBotV2 and built the
parameter-mapping registry. Use this to resume in a fresh conversation.

## TL;DR

PantheonBotV2 is now bit-faithful to its NinjaScript runtime on every
intermediate that matters. A live insight from `market_regime_discovery`
or `filter_discovery` can be translated mechanically to a PantheonBotV2
strategy XML through `param_map.py`. The next ticket (which we did NOT
start) is `template_writer.py` — the actual XML generator.

## What was built this session

### 1. Bar regime + classifier parity (tickets 2A, 2B)

Module: `src/ta_foundation/analysis/strategies/pantheon_bot_v2/`

| File | Role |
|---|---|
| `classifiers.py` | Pure-Python mirrors of NT `RegimeClassifier.ClassifyTrend/Vwap/VolatilityFromPercentile`. |
| `debug_parser.py` | Parses `EnableDebugPrint` output, both legacy (regime labels only) and current (with intermediates). |
| `settings_loader.py` | Loads NT-exported Strategy Analyzer CSV (display-name → property mapping) or hand-authored JSON. |
| `bar_regime.py` | Bit-faithful Python recompute of HTF EMA, HTF ATR, primary ATR, session VWAP, ATR percentile rank. |

### 2. Five real PantheonBotV2 parity bugs fixed

| # | Bug | Fix location |
|---|---|---|
| 1 | Python VWAP accumulated from calendar 15:00 MT but NT starts at first bar of backtest | `bar_regime.BarRegimeConfig.vwap_anchor_dt`, plumbed by the parity test from the first debug-log row |
| 2 | 5m resample used `closed='left', label='left'` but the 1m bars file is right-edge-labeled (close-times), causing a one-minute alignment shift | New helper `bar_regime.resample_nt_right_labeled` with `closed='right', label='right'` |
| 3 | Python VWAP accumulator used `close` when `UseCloseForVwapPrice=True`; NT's `UpdateSessionVwap` ALWAYS uses typical price `(H+L+C)/3` regardless of that flag (the flag only affects the dist_atr comparison) | `bar_regime._session_vwap` always uses typical price |
| 4 | Default `session_reset_hour=15` (15:00 MT) but NT's `IsFirstBarOfSession` for NQ ETH fires at 16:00 MT | Default changed to 16:00 MT |
| 5 | At primary T = HTF boundary, NT's primary `OnBarUpdate` fires before the same-timestamp HTF closes — `currentTrendSlopeValue` is cached from the previous HTF, but `emaTrendHtf[0]` reads the just-closed bar | Two separate `merge_asof` joins in `compute_bar_regime`: exact-match for ema/atr, no-exact-match for slope |

### 3. NinjaScript patch shipped to NT

`PantheonBotV2.cs` `EnableDebugPrint` block was extended to emit the
intermediate values (`ema_htf`, `atr_htf`, `atr_pri`, `vwap`, `atr_vol`)
alongside the original line. Format is backward-compatible — the
original prefix matches an existing fixture's regex; the appendix is
optional.

- In-repo source: `src/ta_foundation/strategies/PantheonBotV2/PantheonBotV2.cs:416-433`
- Deployed at: `C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\Strategies\PantheonBotV2.cs`
- Compiled and verified by operator.

### 4. AI repo index now covers .cs files

`scripts/build_ai_index.py` was Python-only. Extended to summarize C#
files: NinjaScript role detection (Indicator / Strategy / AddOn),
top-of-file `///` or block-comment doc extraction, class declarations,
and `[NinjaScriptProperty]` parameter names. Output renders alongside
Python summaries in each category.

`CLAUDE.md` now points sessions at `docs/AI_REPO_INDEX.md` as required
reading. The bridge .cs files (`TaFoundationMinuteBarExporter`,
`TaFoundationExecutionShell`, the Pantheon strategies) are now
discoverable from one place.

### 5. Param-mapping registry (ticket 1)

Module: `src/ta_foundation/analysis/strategies/pantheon_bot_v2/param_map.py`

Single declarative table — every `[NinjaScriptProperty]` in
PantheonBotV2.cs maps to a role (entry/exit/filter/classifier_input/
time/risk/core/display), an analysis key, and (for enums) bidirectional
NT-value ↔ analysis-value mappings.

Key API:
```python
analysis_value_to_nt_enum("RequiredTrendRegimeFilter", "up") == "Up"
nt_enum_to_analysis_value("RequiredVolatilityRegimeFilter", "High") == "high"
params_by_role("filter")  # the four Required*/Blocked* filters
get_param("UseCloseForVwapPrice").notes  # records the typical-price gotcha
```

The `UseCloseForVwapPrice` GOTCHA is recorded in the registry's `notes`
field so the typical-price bug can never silently reappear.

## Test inventory (final state of this session)

```
src/ta_foundation/tests/analysis/strategies/pantheon_bot_v2/
├── test_debug_parser.py        4  PASSED  no fixture needed
├── test_bar_regime_unit.py     9  PASSED  no fixture needed
├── test_classifier_parity.py   8  PASSED  2 fixtures × 4 tests (ticket 2A)
├── test_bar_regime_parity.py  11  PASSED  3 outputs + 5 intermediates (May), 3 outputs (Feb) (ticket 2B)
│                               5  SKIPPED intermediate tests on Feb fixture (captured before patch)
└── test_param_map.py          15  PASSED  registry consistency + .cs coverage + CSV round-trip

Total: 47 passed, 5 skipped
```

To reproduce:
```bash
cd D:\Backup\projects\PythonProject\ta_foundation
python -m pytest src/ta_foundation/tests/analysis/strategies/pantheon_bot_v2/ -v
```

## Fixtures

```
src/ta_foundation/tests/analysis/strategies/pantheon_bot_v2/fixtures/
├── README.md
├── bars_paths.local.json   (gitignored — points at D:/MarketData/*.txt)
├── debug_2026-02-26_NQ_2D.txt + .csv   (Feb backtest, legacy format)
├── debug_2026-05-04_NQ.txt + .csv      (May backtest, with intermediates)
└── NinjaTrader Grid 2026-05-19 11-57 AM.csv  (cosmetic dup, ok to delete)
```

Bars come from `D:\MarketData\` which is kept current by the
`TaFoundationMinuteBarExporter` indicator (running on a live chart per
attached contract).

## Convention for future captures

To add a new parity fixture: capture an NT backtest debug log + settings
CSV with the SAME stem, both starting `debug_`. E.g.
`debug_2026-06-10_NQ.txt` + `debug_2026-06-10_NQ.csv`. Discovery
parametrizes the existing tests automatically. If the bars need
external data, add an entry to `bars_paths.local.json` pointing at the
right file in `D:\MarketData\`.

## Memory notes touched

- `project_bridge_components.md` — kept as behavioral notes (gotchas
  the index can't capture). Defers inventory to `docs/AI_REPO_INDEX.md`.

## Next ticket — promote-to-template (NOT started)

Goal: take a discovery output and emit a PantheonBotV2 NinjaTrader
strategy XML that an operator can drop into
`C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\` and load
into Strategy Analyzer.

### What to build

Module: `src/ta_foundation/analysis/strategies/pantheon_bot_v2/template_writer.py`

API sketch:
```python
def write_pantheon_template(
    *,
    discovery_payload: dict,        # one row of market_regime_discovery recommendations OR filter_discovery insight
    seed_template: Path,            # existing XML to patch (preserves machine-specific fields)
    output_path: Path,
    baseline_overrides: dict = {},  # any operator-set defaults
) -> dict:                          # manifest: written path, settings dict, source insight
    ...
```

The translation logic is now mechanical:
- Filter values → `analysis_value_to_nt_enum(...)` for each `Required*/Blocked*` enum
- Exit policy choice → `MaxStop` / `MaxTPRatio` / `UseLockIn` / `LockInTriggerTicks` etc., driven by which `exits/policies.py` dataclass the discovery picked
- Session window from `market_regime.session_window_optimizer.start_hour/start_minute/...`
- MA periods from `ma_discovery` top result

### Reference implementation

`src/ta_foundation/strategies/LargeCandleReversal/generate_nt8_template.py`
already does seed-template XML patching for that strategy. Mirror its
patterns: load seed XML, walk to `<Strategy>` node, patch named values,
preserve everything else. Don't synthesize XML from scratch — NT serializes
type-specific machine details that breaks if you regenerate them.

### Tests to ship with it

1. **Round-trip**: write a template with known overrides, read it back through NT's expected XPath, assert each `Required*` enum has the right NT-enum value.
2. **No-op patch**: writing a template with no overrides equals the seed template (within whitespace).
3. **Coverage**: every operator-tunable property in `params_by_role("filter")` + `params_by_role("exit")` + `params_by_role("time")` has a code path that can set it.
4. **End-to-end**: load a small `market_regime_discovery` payload from a fixture, write the template, and assert the resulting XML has the expected enum strings.

### Estimated scope

~200 LOC + ~150 LOC tests. Half a day of focused work.

## Open questions for next session

1. **Seed template source.** Where does the canonical PantheonBotV2 seed XML live? Check `src/ta_foundation/strategies/PantheonBotV2/templates/` (saw `sampleTemplate.xml` in an earlier ls). If absent, the operator needs to save one from NT first.

2. **Session reset hour for non-NQ instruments.** Default of 16:00 MT is hardcoded as right for NQ ETH. ES, GC, etc. may differ. The template writer might need an instrument-aware override.

3. **EMA seed drift on HTF.** Python's HTF EMA drifts by up to 0.19 against NT (because NT seeded from chart warm-up data outside our 1m bars file). For the loop this is fine (classifier labels still agree), but if anyone wants tighter parity, the fix is loading more warm-up bars before resampling.

4. **Tick exporter not yet exercised.** `TaFoundationMinuteBarExporter` can also write Tick.Last.txt files (the file at D:\MarketData\ for NQ exists). The `analysis/exits/simulate.py` tick-level engine could use these for true tick-faithful exit validation, but we haven't wired it.

## How to start the next session

1. Read this handoff.
2. Read `docs/AI_REPO_INDEX.md` — bridge components are listed under "Execution Bridge" and "Configuration And Prompts".
3. Check `MEMORY.md` for the bridge-components memory note.
4. Run `python -m pytest src/ta_foundation/tests/analysis/strategies/pantheon_bot_v2/` to confirm 47 passed + 5 skipped is still the baseline.
5. Open `src/ta_foundation/strategies/LargeCandleReversal/generate_nt8_template.py` as the reference pattern.
6. Confirm with the operator what discovery payload shape feeds the writer (one row from `market_regime_discovery` recommendations is the simplest first target).
7. Begin `template_writer.py`.

## Commits during this session

None. All work is staged in the working tree. The operator should review
and commit before starting the next ticket. Suggested commit boundaries:

- C# debug-print extension + Python parser back-compat + intermediate parity tests (one PR)
- Five bar_regime parity bug fixes + `resample_nt_right_labeled` + `vwap_anchor_dt` (one PR)
- `build_ai_index.py` C# support + CLAUDE.md pointer + memory note slim-down (one PR)
- Param-mapping registry + tests (one PR)
