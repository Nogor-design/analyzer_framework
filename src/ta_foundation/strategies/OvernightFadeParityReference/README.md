# OvernightFadeParityReference

Order-free NinjaScript reference for Layer 1 of the overnight-range fade audit.
It is intentionally separate from the managed-order strategy renderer.

The strategy requires a primary 2-minute series and adds a 1-minute series. It
binds to the frozen source manifest and an immutable per-segment binding by
SHA-256. It also verifies the runtime instrument, NinjaTrader application time
zone, and primary trading-hours template, and it refuses real-time execution.
It reconstructs the source's
600-one-minute-bar overnight range and exclusive Wilder ATR, emits independent
first-break events for both sides, opens virtual entries at the next available
one-minute bar open, and measures continuous 2×ATR stop/target races for up to
240 available one-minute rows. A same-row target/stop touch is emitted as
`tied_bar_requires_ticks` for deterministic reconciliation against the source
tick verdict.

It contains no entry, exit, order, account, or position calls. Compiling or
running it cannot enable a strategy or place an order. Output is three
per-segment CSV files: the signal and outcome ledgers keyed by the same 24-hex
event ID used by
`D:\strategy-analysis\large-candle\ninjascript\freeze_overnight_fade_ledger.py`,
and a complete native-primary two-minute OHLC/true-range/prior-ATR telemetry
ledger. The latter proves the recursive input path, not merely the 466 event
rows.

Generate the ten immutable segment binding files once with
`build_overnight_fade_layer1_bindings.py`. For each Strategy Analyzer run,
configure the matching binding path and SHA-256 from that package. The binding
line includes the instrument, contract, inclusive date window, and frozen
source-manifest hash, so relabeling a run cannot pass validation.

Do not optimize this reference. Run one frozen contract segment at a time,
with `OverwriteIfExists=false`, and reconcile the CSVs against
`fixed_2r2r_source_v1` before any managed-order backtest.

This layer intentionally emits `tied_bar_requires_ticks` for the two ambiguous
one-minute bars. It can independently reconcile 464 outcomes and detection of
both ties; a separate tick-resolution layer is required for full 466-outcome
parity. Primary 2-minute OHLC/ATR must also reconcile exactly against a Layer 0
export because NinjaTrader constructs that series independently from the added
one-minute series.

`OvernightFadeParityReference.compile.csproj` is an isolated compatibility
check against the installed NinjaTrader assemblies. Building it does not copy
the strategy into NinjaTrader or invoke the platform compiler.

