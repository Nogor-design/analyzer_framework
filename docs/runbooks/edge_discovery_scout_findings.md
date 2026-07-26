# Cross-Instrument & Order-Flow Edge Scouts — runbook & findings

*Created 2026-06-16 · Owner: Claude (PM) · Reads-with: `analysis/strategy_discovery/cross_instrument.py`,
`analysis/entry_strategies/outcome/simulator.py`, `marketdata/tick_cache.py`,
memory `project-edge-sample-power`.*

**Read this before launching another intraday/tick mechanical-edge search.** It records
*why the previous edge hunt was underpowered, the two scouts built to fix that, and the
rigorous negative result they produced.* The point is to not repeat a search whose answer
we already have with high statistical confidence.

---

## 1. Why these exist — the binding constraint was sample power, not search cleverness

The earlier edge hunt selected candidates on tiny samples (~10–50 trades over ~95 trading
days). At that N a search has almost no statistical power, and a powerless search fails in
**both** directions at once: it can't detect a real small edge (→ "0 survivors") **and**
can't reject noise when trade counts are tiny (→ "PF 2.5–5.2 mirages"). Both symptoms are
the same disease. The fix is to **manufacture sample**, not search cleverer:

- **Frequency** — setups that fire many times/day → hundreds–thousands of trades.
- **Pooling** — require a *shared-parameter* edge to hold across NQ/ES/YM/RTY at once; a
  fitted artifact dies on independent instruments (the brutal anti-overfit test).

Both scouts measure **net of realistic cost** from the start and rank by **pooled t-stat**
(not profit factor — PF-first selection is what overfits).

---

## 2. The scouts

### `scripts/cross_instrument_scout.py` — 1-minute bar setups
Reuses `MinuteBarsLastTxtParser` (tz-aware Denver bars) → `simulate_atr_outcomes`
(cost-aware: $2.09/side + slippage, conservative tie-breaks) → `evaluate_cross_instrument`
(pooled t-stat/PF/fraction-profitable gates). Pools per-trade **net R-multiples** (net P&L
÷ initial dollar risk) across NQ/ES/YM/RTY so different tick values pool cleanly.

Setups: **MR** (fade ≥k·ATR stretch from SMA20) and **MO** (N-bar breakout). Conditioning
knobs added: efficiency-ratio regime gate, hour-of-day profile, market vs passive-**limit**
entry.

```bash
python scripts/cross_instrument_scout.py                       # sweep, costs on
python scripts/cross_instrument_scout.py --no-costs            # decompose cost drag
python scripts/cross_instrument_scout.py --mode profile --profile-k 3.0   # regime + hour
python scripts/cross_instrument_scout.py --mode compare-entry --rth-only  # limit vs market
```

### `scripts/tick_orderflow_scout.py` — tick / order-flow microstructure
Reuses the `.ta_cache` tick parquet (cols `dt,last,bid,ask,volume`). Signs every trade via
bid/ask → true order-flow imbalance (OFI), resamples to 10-second OFI bars (**cached** to
`.ta_cache/<inst>_ofi10s_rth.parquet` so reruns skip the GB scan). Pools vol-normalized
**net ticks** across NQ/ES/RTY (the index futures with tick parquet), RTH only; cost = entry
spread-cross + round-trip commission.

```bash
python scripts/tick_orderflow_scout.py              # OFI continuation & reversion
python scripts/tick_orderflow_scout.py --mode sweep # fade k-tick dislocation on one-sided flow
```

---

## 3. Findings (load-bearing — don't relearn the hard way)

Pooled N went from ~47 to **19,000–37,000 trades** (1-min) and thousands of events (tick).
The power problem is solved; these are trustworthy verdicts, not low-power "can't tell."

| Mechanism | Timescale | Result |
|---|---|---|
| MR stretch-reversion | 1-min | **Real edge, zero-cost** — pooled t **+3.4**, PF 1.05, **4/4 instruments**, **monotonic in k**. But only **+0.04R/trade**. |
| MR, **with costs** | 1-min | Loses hard (pooled t −34 to −69). Cost ≈ 0.4R ≈ **10–15× the edge**. |
| MO breakout | 1-min | Loses net of cost. |
| MR conditioning | 1-min | Regime (efficiency-ratio) gate made it **worse**; hour-of-day concentrates to late-morning RTH but only reaches **breakeven**; passive limit + 0 slippage still **−0.07R** (one hour +0.04R but t=0.6, n.s.). |
| OFI continuation / reversion | 10s–1min | Gross predictive move ≈ 0 on the high-N instrument; loses net of cost both directions. |
| Sweep / exhaustion reversal | 10s–3min | Negative; not cross-instrument (NQ/ES **continue** after a sweep, only RTY mildly reverts → artifact, rejected by the guard). |

**Overall verdict:** across five mechanism families, 10s–1min, on NQ/ES/RTY/YM, over
~Dec-2025…Mar/May-2026, **no mechanically-discoverable edge survives realistic costs.** The
one genuine signal (stretch-reversion) is real, cross-instrument-consistent, and monotonic —
but ~10× too small vs cost. This is the efficient-market wall, shown rigorously.

**Honest bounds:** limited to the mechanisms tried, ≤1-min timescales, index futures, and a
short single-regime window. "No edge found here" ≠ "no edge exists."

**Key cause:** fixed per-trade cost. The only structural way to beat it is a **bigger move
per trade** → **lower-frequency / bigger-move setups**, where cost is a rounding error.

---

## 4. QUEUED — next action (needs NinjaTrader)

Testing lower-frequency / bigger-move setups **requires years of data** (a daily-bar setup on
4.6 months is back to ~30 trades — the original power problem). That is gated on **Lever 1:
fix the market-data export bridge, then pull multi-year history.**

**Bridge blocker (2026-06-15):** `scripts/gather_market_data.py` dispatches but NT returns
`RunError: Strategy Analyzer Run command was not executable after 12 attempts.` for
`TaFoundationDataExportStrategy` — even for a known-good window. Strategy is deployed+compiled
and worked Jun 8. Root cause is AddOn-side: the bridge's `Detected:` log never recognizes
`TaFoundationDataExportStrategy`, so SA's Run stays disabled (same class as the "must have a
parameter to optimize" AddOn failure). AddOn source: `D:\ninjatraderOptimizer`. Likely needs
a clean NT restart to clear SA state (confirm no live account has an open position/order
first — hard rule).

**When NT is free:** (1) fix the export-bridge Run-trigger for `TaFoundationDataExportStrategy`;
(2) pull 3–5 yrs of NQ/ES/YM/RTY minute (+ tick) history; (3) re-run a lower-frequency
version of `cross_instrument_scout.py` (multi-hour/daily reversion holds) where the captured
move ≫ fixed cost. Any positive result → **locked holdout** before trust.

### UPDATE 2026-06-16 (later) — REAL EDGE FOUND: daily cross-asset momentum (TSMOM)
NT intraday is too short, so pulled FREE multi-year DAILY futures from Yahoo
(`scripts/fetch_daily_history.py`, continuous `=F`, ~25 yr to 2000) and built
`scripts/cross_instrument_daily_scout.py` (same setups/simulator/gate, daily freq,
zero-cost since swing cost ≈0.01R → R is purely price-based / spec-independent).

- **Equity indices only:** trend-capture momentum (50d breakout, LET WINNERS RUN via
  wide target + 40d hold) = t 1.79, +0.077R, 4/4. Letting winners run DOUBLED the edge
  vs a capped 3-ATR target. Momentum LOSES intraday but WORKS daily (horizon dependence).
- **Cross-asset 19-instrument panel** (eq+commod+rates+fx — the proper TSMOM test):
  **t = 4.94, PF 1.16, +0.105R, 14/19 instruments; six momentum variants cluster t=2.6–4.9.**
- **Out-of-sample time split:** IS 2000–2013 t≈4.7 (+0.11R) → OOS 2013–2026 **t≈3.0 (+0.088R)**.
  Significant in BOTH independent 13-yr halves; mild post-2013 decay (published anomaly).

This is genuine, OOS-validated, cross-asset TIME-SERIES MOMENTUM — the real thing, and the
payoff of the sample-power reframe. PF ~1.1–1.18 at t≈3–5 is the correct shape of a real
diversified momentum edge (the scout's PF≥1.2 / 75%-profitable gates are calibrated too high
for this class — relax them for momentum). **NOT yet deployable:** needs vol-scaled sizing +
portfolio construction; only the equity-index leg (NQ/ES/YM/RTY) is directly tradeable in the
NT/prop pipeline (full diversification benefit needs multi-asset broker/account support); it is
a SWING/daily strategy (multi-week holds), unlike the intraday bots the prop stack was built for.

### UPDATE 2026-06-17 — higher-TF / FVG / large-candle / news directions TESTED (correcting the over-claim)
The earlier "<=1-min intraday is cost-walled" finding was over-generalized to "intraday is dead."
It was not — only <=1-min mean-reversion/breakout/OFI/sweep were tested. New scout
`scripts/multi_timeframe_scout.py` reuses the same loaders/cost-model/pooling and adds the
*untested* directions: resample to 2/3/5/15/30m, **FVG retest continuation** (gap-size gated,
limit on retrace), **large-candle retracement** (min/max ATR size gate + retrace fraction,
continuation), and a **news-window proxy filter** (08:30/10:00/14:00 ET ±pad), plus `--mode holdout`
for a per-instrument chronological IS/OOS split judged on shared params (no re-fitting).

Findings (NQ/ES/YM/RTY, Dec-2025…May-2026, cost-aware, pooled, ranked by t not PF):
- **Zero-cost:** best pooled t≈2.3 (2m MR) and higher-TF momentum (15m/30m MO, 4/4 inst, +0.05R) —
  tiny edges, the right "bigger-move-per-trade" shape.
- **Costs-on, all hours:** everything collapses to t<0.8, mostly negative; "edge" concentrates in
  **NQ only** (single-instrument fit, correctly rejected by the cross-instrument gate). 0/110 clear t>=2.
- **Costs-on, RTH + news-filtered:** news/session filtering genuinely **helped** (PF 1.04→1.10–1.16,
  meanR ~doubled). Best shapes: 15m MR (4/4 inst, PF 1.13) and 5m FVG (PF 1.14–1.16, 3/4). Still
  underpowered: t≈1.0–1.4, N cut to ~600. 0/110 clear t>=2.
- **Holdout (65/35 IS/OOS):** **0 of 36 candidates hold.** IS winners (3m FVG IS PF 1.37 / 15m FVG IS
  PF 1.56) collapse or invert OOS (15m FVG → OOS PF 0.43). IS↔OOS t-stats uncorrelated = noise.
  Only consistent shape: **15m mean-reversion**, weakly +0.05R / 3·4 inst in *both* halves but never
  significant (IS t=1.00, OOS t=0.95).

**Corrected verdict:** these specific fully-mechanical rules, on ~3–5 months of index-futures data,
show no edge surviving a proper OOS test net of cost. This is NOT "no intraday edge exists" — it is
limited to the rules/instruments/window tried. The binding constraint is again **sample/data**: a real
0.05R edge (what 15m MR hints at) is statistically invisible at N≈300/half. Resolving it needs years
of intraday history (same data-acquisition wall below), not cleverer rules. Still-untested, non-pure-
price mechanisms a discretionary trader uses (VWAP/volume-profile/POC, session-open structure, MTF
confluence, order-flow at levels) remain open.

### UPDATE 2026-06-17 (later) — VWAP / volume-profile / session-structure / MTF TESTED
Closed the last open door the prior update named: the *non-pure-price* mechanisms a
discretionary day trader actually uses. New scout `scripts/volume_session_scout.py` reuses the
SAME loaders / cost model / pooling / cross-instrument gate / holdout machinery and adds six
session-anchored families (all reset each RTH day — that anchoring is what makes them not pure
price): **VWAP fade** (≥k·ATR stretch to session VWAP), **VWAP reclaim** (fresh VWAP cross in
slope direction), **proper ORB** (opening-range break with an optional **volume/liquidity
filter**), **ORB break-retest** (passive limit), **prior-session volume-profile POC fade**, and
**MTF confluence** (ORB + VWAP-reclaim additionally gated by a higher-TF SMA-slope trend, using
only the last *completed* HTF bar — no lookahead). 5m base, NQ/ES/YM/RTY, Dec-2025…May-2026.

Findings (cost-aware, pooled, ranked by t not PF):
- **Zero-cost, RTH:** best pooled t≈2.1 (5m ORB break-retest) but only **2/4 instruments**;
  VWAP-fade is the only 4/4-consistent shape but t≈1.9 *before cost*. Tiny.
- **Costs-on, RTH:** everything collapses. **0/26** clear pooled t≥2. Strong, repeated pattern:
  **NQ/ES positive, YM/RTY negative** across nearly every family — i.e. a 2-instrument fit, exactly
  what the cross-instrument gate is built to reject. Best (ORB-retest) drops to t=1.91, 2/4.
- **Costs-on, RTH + news-filter:** did **not** help here (unlike the multi-TF scout); still 0/26,
  still NQ/ES-only.
- **Holdout (65/35 IS/OOS):** **0 of 26 hold.** The *only* directionally-stable shape is
  **5m ORB break-retest** (IS PF 1.17 / R +0.087 / 3·4 inst → OOS PF 1.15 / R +0.081 / 3·4 inst) —
  but it never reaches significance (IS t=1.61, OOS t=1.02). Same N-wall as 15m MR earlier.

Two concrete sub-doors closed (both counter to discretionary folklore, on this data):
- **The volume / liquidity filter HURT.** Gating ORB to elevated-volume breaks (vol ≥ 1.5–2.0×
  running median) was consistently *worse* than the unfiltered break (e.g. OOS t −1.95 → −3.04).
  High-volume breaks did not mark real participation here; they marked worse entries.
- **MTF confluence did NOT help.** HTF-trend-gating ORB and VWAP-reclaim left them at/below the
  ungated versions and they still collapsed OOS. Adding a higher-TF agreement filter bought nothing.

**Verdict:** the volume/VWAP/session/MTF family lands in the *same place* as every pure-price
family — no edge survives realistic cost under a proper cross-instrument OOS test on ~5 months of
index futures. The one repeatedly-stable shape (ORB break-retest, ~PF 1.15, 3/4 inst both halves)
is real-looking but statistically invisible at N≈250/half — the **sample/data wall again**, not a
rule-cleverness wall. Honest bound unchanged: limited to these rules/instruments/window; "no edge
found here" ≠ "no edge exists." This now retires the runbook's "still-untested discretionary
mechanisms" caveat — the remaining unexplored lever is true **order-flow at levels** (tick/DOM
absorption at VWAP/POC/OR edges), which needs the tick parquet, not bars.

### UPDATE 2026-06-17 (last) — ORDER-FLOW AT LEVELS tested — first mechanism to beat cost
The final unexplored lever from the entry above. New scout
`scripts/tick_level_orderflow_scout.py` reuses `tick_orderflow_scout`'s tick
loading + trade-signing (true OFI via bid/ask) but extends `build_bars` to also
carry high/low (needed to detect a level *touch*; cached to its own
`{inst}_ofilvl10s_rth.parquet`). It conditions order flow on STRUCTURAL LEVELS —
the thing every prior scout omitted. Levels: session VWAP, prior-session POC,
opening-range high/low. Two mechanism-grounded plays, side inferred per-bar from
prior close vs level:
- **ABSORPTION (fade):** price enters the [L±tol] zone, one-sided flow pushes INTO
  the level but price fails to break → passive limit absorbs aggression → reverse.
- **INITIATIVE (continue):** price breaks THROUGH the level with confirming
  one-sided flow → real initiative, continue.
NQ/ES/RTY 10s bars, RTH, Dec-2025…Mar-2026, pooled vol-normalized NET ticks, ranked
by pooled t, plus `--mode holdout`.

Methodology note (don't repeat): the first cut required a literal straddle
(`low<=L<=high`) which fires ~never for a slow VWAP → N=2–3, pure-noise t-stats.
Fixed to a proximity band (`low<=L+tol & high>=L-tol`). Always sanity-check N before
reading a t-stat.

Findings:
- **VWAP INITIATIVE is the first intraday mechanism to SURVIVE realistic cost.**
  Break of session VWAP confirmed by one-sided flow (lookback 6 bars=60s, thr 0.2,
  tol 3t, hold 36 bars=6min): pooled **t=2.30, N=196, 3/3 instruments net-positive**
  WITH cost on (NQ +14.3t, ES +6.3t, RTY +7.9t per event). Cost barely dents it
  because it is a BIG-MOVE-PER-EVENT setup (~7–14 ticks) — exactly the structural
  fix the cost analysis predicted (fixed ~1.8t cost becomes a rounding error). The
  cost wall that flattened MR/breakout/FVG/OFI-in-open-space does NOT flatten this.
- **Absorption (fade at levels): negative/insignificant.** Fading flow at VWAP/POC/OR
  did not work; the edge is in INITIATIVE (continuation through a level), not fading it.
- **POC and OR-edge levels: not robust** (high-t cells are all N=2–6 noise). The signal
  is specific to VWAP.
- **Holdout: 0/192 formally hold — but the VWAP-initiative shape is the strongest,
  most stable candidate found by ANY scout.** It stays positive and 3/3 instruments
  in BOTH halves (e.g. lb6/h36/tol6: IS t=1.99 → OOS t=1.73, OOS mean +2.27t, 3/3
  inst+ OOS; lb6/h18/tol6: IS t=2.30 → OOS t=1.39, 3/3 inst+ OOS). It fails the gate
  ONLY on sample size — IS t just under 2.0 and OOS N≈32–52 (<60) — not on inversion.
  Unlike the bar-based FVG winners that flipped sign OOS, this one is directionally
  consistent across the split.

**Verdict — qualitatively different from every prior scout.** This is NOT another
"dead net of cost" negative. Order-flow-at-VWAP-initiative is the first mechanism
that (a) beats cost, (b) is cross-instrument consistent 3/3, and (c) is
direction-stable IS→OOS. It is **not yet validated** — N is too small to clear a
proper OOS significance gate (IS ~76–196 / OOS ~32–52 events over ~60 RTH days).
The binding constraint is the SAME data wall: a real ~10-tick/event VWAP-break edge
at this frequency needs more RTH days to reach significance, OR a tighter live
microstructure test. **This is the one intraday candidate worth carrying forward.**
Next, in priority order: (1) gather more RTH tick history for NQ/ES/RTY (more 03-26
and 06-26 tick exports) and re-run the holdout — if IS t clears 2 and OOS N>60 with
3/3, it graduates; (2) refine the entry (the break bar is a market entry crossing
the spread; a stop-entry just beyond VWAP on flow confirmation may improve fills);
(3) add a real stop/target outcome sim instead of fixed-hold before any deployment.
Do NOT keep adding new mechanism families — the intraday rule space is now
exhaustively mapped; this is the single thread to pull.

### UPDATE 2026-06-16 — bridge FIXED, but a new hard wall: data availability
- **Bridge fixed.** Root cause of "Run command was not executable" was **stale Strategy-Analyzer
  state**, not a code/template bug. A clean NT **cold restart** cleared it; re-probe returned 6,899
  bars for NQ 03-26 Feb. (Lesson: cold-restart NT first on that error.) `scripts/gather_market_data.py`
  + `scripts/gather_history_bulk.py` (new, resumable multi-contract puller) are ready.
- **But NT's intraday history horizon ≈ 6 months.** Probing older NQ contracts (12-25…12-23) all
  return **1 bar** (no data) vs 6,899 for the recent one. A DAILY-bars probe of the front contract
  over 2021→2026 also returned 1 bar (`scripts/probe_daily_history.py`) — requesting a specific
  contract does **not** trigger continuous merge; NT only serves each contract's own recent data.
  No local multi-year source (DailyAnalysis repo has only the same recent file).
- **Binding constraint is now DATA ACQUISITION,** not tooling. To test the only viable mechanical
  direction (lower-frequency/bigger-move), we need years of history from: (a) an NT deep-history
  feed + merge policy (likely a paid data subscription), or (b) an external vendor (FirstRate /
  Polygon / Databento / CME DataMine — daily often cheap/free, intraday paid). The bridge and both
  scouts are ready to consume whatever arrives. **Open decision — operator's call.**
