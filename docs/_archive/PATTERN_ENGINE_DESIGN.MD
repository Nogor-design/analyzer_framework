PatternEngine + Sweep Grids Specification (Architecture-Constrained)
0. Purpose

Add a scalable research capability that:

Detects many entry patterns from bar data and tick data already loaded in the project

Systematically expands each pattern into a parameter sweep grid

Evaluates each pattern instance consistently and produces comparable statistics

Renders results in new HTML report sections, without breaking existing pipeline flow

1. Non-negotiable architecture constraints

Parsers only ingest raw files → normalized data structures.

Analysis modules compute derived features + signals using already-ingested market data and AnalysisPackage objects.

Report sections are pure render functions; they do not reload data or do heavy computation.

All timestamps are already localized on ingest to America/Denver (tz-aware).

Market data is shared via the report context (ctx["market"] concept); do not re-load market data inside packages/sections.

If caching/precompute is introduced, it must be deterministic and attached as derived outputs in analysis context, not by modifying core data flow in ad-hoc ways.

2. Terminology

Pattern family: breakout, pullback, mean-reversion, etc.

Pattern definition: a rule template with parameter placeholders.

Pattern instance: a fully specified pattern definition after parameter expansion.

Signal instance: a single timestamped occurrence of a pattern instance with direction and features.

Outcome evaluation: how a signal instance is converted into PnL metrics (ticks), MAE/MFE, time-to-event, etc.

3. Pattern ID Taxonomy and Naming Standard
3.1 Canonical Pattern ID format
[Family].[Structure].[Q(param_block)].[Direction].[Regime]

Examples:

BRK.ORB.Q[w=5,rt=2,v=85,r=1.75,i=75].LONG.EXP

MNR.VWAPDEV.Q[d=2.0,wk=0.65,v=70].SHORT.CHOP

MIC.IMBREV.Q[tw=30,i=85,s=10].LONG.ALL

3.2 Required fields

Family: fixed 3-letter code (below)

Structure: fixed short code (below)

Q block: always present, even if minimal (use Q[] when no params)

Direction: LONG, SHORT, or BI (BI expands into long+short unless explicitly symmetric)

Regime: ALL, TREND, CHOP, EXP, SQZ

3.3 Family codes

BRK breakout

PBL pullback

MNR mean reversion

MOM momentum continuation

CMP compression / squeeze mechanics

VOL volume anomaly

WCK wick / rejection

MIC microstructure (tick-derived)

TOD time-of-day

RNG range-location within session

3.4 Structure codes (minimum set)

Breakouts:

ORB opening range break

PDH prior day high break

PDL prior day low break

IDH intraday high break (rolling)

IDL intraday low break (rolling)

IB inside-bar breakout

RBOX range-box breakout (n-bar range)

Pullbacks:

VWAPRET VWAP retest entry

EMA20RET EMA20 retest entry

EMA50RET EMA50 retest entry

FLAGBRK flag breakout after impulse

IMPRET impulse retracement entry (by depth)

Mean reversion:

VWAPDEV VWAP deviation fade

FAILBRK failed breakout fade

SEXT session extreme fade

Momentum:

LRNG large-range continuation bar

MRBZ marubozu continuation bar

CLUS multi-bar momentum cluster

STEP consecutive closes continuation

Compression:

COMP compression detector

COMPBRK compression break

COMPFAIL compression false-break

Volume:

VSPIKE volume spike continuation/exhaustion

ABSORB absorption bar (high vol, small range)

VDIV volume/price divergence proxy

Microstructure:

IMB tick imbalance

IMBREV imbalance reversal

SPRD spread expansion event

TDENS tick density anomaly

Time-of-day:

OPEN open window pattern

LUNCH lunch window pattern

POWER power hour

CLOSE close window

Range-location:

TOP top-of-session-range fade

BOT bottom-of-session-range fade

MID mid-range continuation

3.5 Parameter token standard (deterministic ordering)

The Q param block uses token=value pairs, sorted by token name for determinism.

Canonical token order:
b,c,d,i,n,r,rt,s,t,tw,v,w,wk

Tokens:

w = window minutes (e.g., ORB window)

rt = retest window bars (0 means no retest required)

n = lookback bars (levels / rolling extremes / boxes)

d = deviation threshold (VWAP band; units defined per pattern)

r = range multiple threshold (ATR-multiple)

v = volume percentile threshold (or max for contraction patterns; see semantics)

b = body fraction threshold (0–1)

wk = wick fraction threshold (0–1)

i = imbalance percentile threshold (tick-derived)

s = spread percentile max (tick-derived; lower is tighter)

tw = tick window seconds (microstructure aggregation window)

c = compression length bars

t = time bucket (open/lunch/power/close or enumerated)

Semantics note: some patterns interpret v as “volume must be >= v percentile”; pullback/compression may interpret v as “volume must be <= v percentile” (must be explicit in the pattern definition).

4. Systematic Parameter Sweep Strategy
4.1 Tiered sweeps (required)

All sweeps must support tiers:

Tier 0 (Smoke): smallest grid to validate output.

Tier 1 (Coarse Discovery): default production research grid.

Tier 2 (Refine Winners): narrower grids around top performers.

Tier 3 (Robustness): stress by slicing (regime/time/run splits); no new parameters needed.

4.2 Global shared filter grids (Tier 1 defaults)

These filter grids should be reused across families to keep results comparable:

Volume percentile (min): v ∈ {70, 85, 95}

Range multiple: r ∈ {1.25, 1.75, 2.25}

Imbalance percentile: i ∈ {60, 75, 85} (only for microstructure-enabled patterns)

Spread max percentile: s ∈ {20, 10} (only for tick-based filters)

Regime constraint: {ALL, TREND, CHOP, EXP, SQZ} (pattern-specific subset)

4.3 Tier 1 core grids by family
A) BRK (Breakout) core (Tier 1)

w ∈ {5, 15} (ORB only)

rt ∈ {0, 2, 5}

For IB/RBOX/IDH/IDL: n ∈ {10, 20, 50}

Filters: v, r always; optional i for “confirmed” variants

Regimes: {ALL, EXP, TREND}

B) PBL (Pullback) core (Tier 1)

Structures: VWAPRET, EMA20RET, EMA50RET, IMPRET, FLAGBRK

Impulse lookback: n ∈ {10, 20}

Retracement depth for IMPRET: d ∈ {0.25, 0.50, 0.75} (here d means retracement fraction)

Pullback volume contraction max: v_max ∈ {50, 65, 80} (encoded as v=50 etc but semantics are “<= v” for these patterns)

Regimes: {TREND, EXP}

C) MNR (Mean reversion) core (Tier 1)

Structures: VWAPDEV, FAILBRK, SEXT

VWAP deviation: d ∈ {1.0, 1.5, 2.0, 2.5} (units must be declared: ATR-multiple or stdev multiple)

Wick fraction: wk ∈ {0.50, 0.65, 0.80}

Optional volume: v ∈ {50, 70, 90} (mean reversion can work at lower v)

Regimes: {CHOP, ALL}

D) MOM (Momentum) core (Tier 1)

Structures: LRNG, MRBZ, CLUS, STEP

Body fraction: b ∈ {0.80, 0.90}

Consecutive bars: n ∈ {2, 3, 4} (for STEP/CLUS)

Range multiple: r ∈ {1.0, 1.5, 2.0} (LRNG/CLUS)

Volume: v ∈ {70, 85, 95}

Regimes: {TREND, EXP, ALL}

E) CMP (Compression) core (Tier 1)

Structures: COMP, COMPBRK, COMPFAIL

Compression length: c ∈ {5, 10, 20, 40}

Compression metric enum: {ATR_CONTRACT, RANGE_CONTRACT} (encoded in structure if needed)

Volume contraction max: v_max ∈ {40, 55, 70} (semantics “<= v”)

Break confirmation: r ∈ {1.25, 1.75, 2.25}

Regimes: {SQZ, ALL}

F) VOL (Volume anomaly) core (Tier 1)

Structures: VSPIKE, ABSORB, VDIV

Spike threshold: v ∈ {90, 95, 98}

Absorption range max (ATR multiple): r ∈ {0.5, 0.75, 1.0} (semantics “<= r”)

Wick fraction for absorption: wk ∈ {0.5, 0.7}

Regimes: {CHOP, ALL}

G) MIC (Microstructure) core (Tier 1)

Structures: IMB, IMBREV, SPRD, TDENS

Tick window: tw ∈ {15, 30, 60}

Imbalance percentile: i ∈ {70, 80, 90}

Spread max percentile: s ∈ {20, 10, 5}

Regimes: {ALL, EXP}

H) TOD (Time-of-day) core (Tier 1)

Structures: OPEN, LUNCH, POWER, CLOSE

Bucket: t ∈ {open, lunch, power, close}

Window minutes: w ∈ {5, 15, 30}

Range multiple: r ∈ {1.0, 1.5, 2.0}

Volume: v ∈ {70, 85, 95}

Regimes: {ALL, CHOP, EXP} as appropriate

I) RNG (Range location) core (Tier 1)

Structures: TOP, BOT, MID

Session percentile bands: d ∈ {10, 20} (here d means percent band width)

Optional wick: wk ∈ {0.65, 0.80} for fades

Regimes: {ALL, CHOP}

5. Grid Pruning Rules (Required to avoid combinatorial blowups)
5.1 Hard caps (Tier 1)

Per family per direction, cap expanded pattern instances:

BRK: 300

PBL: 200

MNR: 250

MOM: 250

CMP: 200

VOL: 200

MIC: 250

TOD: 150

RNG: 100

If the grid exceeds cap:

Freeze one shared filter at neutral default (suggested defaults: v=85, r=1.75, i=75, s=10)

Prefer keeping core diversity over filter diversity in Tier 1

Defer filter expansion to Tier 2

5.2 Linked parameter constraints

Apply conditional pruning rules:

ORB retest should depend on OR window:

if w=5 then rt ∈ {0,2}

if w=15 then rt ∈ {0,2,5}

Microstructure confirmations require tick data availability; if no tick stream exists, MIC patterns are skipped and “micro-confirmed” variants of bar patterns should not be generated.

Absorption patterns require small range: only generate ABSORB with r <= 1.0.

5.3 Baseline inclusion (mandatory)

For every structure/direction/regime family set, include a baseline with minimal filters:

Q[] or Q[v=85] as baseline (depending on family)

Regime ALL

This allows measuring the incremental benefit of additional filters.

6. Pattern Definition Contract (What each pattern must specify)

Every pattern definition template must declare:

Family + Structure

Direction rule: LONG/SHORT/BI and how BI expands

Core parameters: tokens + grids + semantics (>= or <=)

Filter parameters: shared filter usage and semantics

Regime eligibility: which regimes are meaningful

Signal timestamp: exact bar/tick timestamp used

Entry reference price: close, next open, mid, bid/ask rule (must be deterministic)

Minimum spacing: optional “cooldown” bars to prevent overlapping duplicate signals

7. Signal Instance Output Schema (Analysis module output)

PatternEngine must output a list/table of signal instances with at least:

pattern_id (string, canonical format)

pattern_family (string)

pattern_structure (string)

dt (tz-aware timestamp)

instrument / contract

direction (LONG/SHORT)

regime (label at dt, if available else ALL)

entry_ref_price (float)

features (a flat mapping of numeric features used by filters, at minimum):

vol_pct (0–100)

range_atr_mult

body_frac

wick_frac

vwap_dev (if relevant)

imb_pct (if tick present)

spread_pct (if tick present)

tick_rate_pct (if tick present)

session_bucket / mins_from_open (if computed)

debug_flags (optional: which filters passed/failed, for validation)

8. Outcome Evaluation Contract (How to score signals)

PatternEngine must support an evaluation mode (choose one to start):

Option A — “Fixed Horizon” (recommended for initial research)

For each signal:

Evaluate returns at horizons H ∈ {1, 3, 5, 10, 20} bars

Record max favorable/adverse excursion over the horizon (MFE/MAE)

Output in ticks

Pros: fast, strategy-agnostic, stable.

Option B — “Policy-based exits” (later integration)

Route signals through existing exit policy simulation framework (if present), using shared tick stream for trigger realism.

Pros: closer to execution reality.
Cons: more compute; do Tier 1 with fixed horizon first.

Initial recommendation: implement Option A first. It’s the highest leverage foundation for ranking patterns.

Outcome fields per horizon:

ret_ticks_H

mfe_ticks_H

mae_ticks_H

time_to_mfe_H (optional)

time_to_mae_H (optional)

9. Aggregation Metrics (What report sections must compute)

For each pattern_id (and optionally by run_id):

signals (count)

avg_ret_ticks_H

net_ret_ticks_H

win_rate_H

p10/p50/p90 of ret_ticks_H

avg_mfe/avg_mae

worst_streak (by sign)

Breakdown slices:

by regime

by session_bucket

by tick_quality_decile (if computed)

Ranking score (choose one):

Primary: net_ret_ticks_10 with min signals constraint (e.g., >= 50)

Secondary: avg_ret_ticks_10 and p10 to penalize tail risk

Tertiary: robustness across regimes

10. Report Sections (Required set)
10.1 Pattern Sweep Leaderboard

Inputs:

expanded pattern_id set (Tier chosen)

min_signals

horizon H

Outputs:

Top N patterns by ranking metric

Columns: pattern_id, signals, net, avg, win_rate, p10, p90, avg_mfe, avg_mae

Filters: family, regime, direction, requires_ticks (bool)

10.2 Pattern Drilldown

For a selected pattern_id:

show example signals table with dt + entry_ref_price + outcomes

show slice stats by regime/time bucket

optionally show charts if already supported by framework

10.3 Diagnostics

count of patterns generated per family

count skipped due to missing tick data

distribution of signals per pattern (to detect overly rare patterns)

11. Default “Best” Pattern Set to implement first (Tier 1)

Implement families in this order (highest ROI first):

BRK breakouts (ORB/PDH/PDL/IB/RBOX)

MNR mean reversion (VWAPDEV/FAILBRK/SEXT)

PBL pullbacks (VWAPRET/EMA20RET/FLAGBRK/IMPRET)

CMP compression (COMP/COMPBRK/COMPFAIL)

MIC microstructure (IMB/IMBREV/SPRD/TDENS)

MOM, VOL, TOD, RNG

Reason: these cover the largest share of tradable edges in NQ-style products and interact well with regimes.

12. Determinism and Reproducibility Rules

Parameter expansion ordering must be stable (sorted tokens and stable iteration order).

Pattern IDs must be byte-identical between runs given the same options.

Signal timestamps must be defined unambiguously (e.g., “signal at bar close time”).

All percentiles must be computed using a fixed rolling lookback (declared per pattern or global default).

13. Options Surface (What the YAML options should control)

The system must be controllable from report configuration options:

tier: 0/1/2/3

families: list of families to include

structures: optional allowlist

directions: LONG/SHORT/BOTH

regimes: allowlist

horizons: list of H values

min_signals: integer

top_n: integer

requires_ticks: bool (if true, skip patterns needing ticks when ticks not available)

(Exact keys can be chosen by implementer, but must map cleanly to these concepts.)

14. Implementation Checklist (for ChatGPT / developer)

New analysis module responsibilities

Generate pattern templates

Expand grids by tier

Apply pruning rules and caps

Detect signals on market bars/ticks

Attach features at dt

Evaluate outcomes (Fixed Horizon initially)

Return standardized outputs keyed by pattern_id

New report section responsibilities

Render leaderboard, drilldowns, diagnostics

Never reload data

Use analysis outputs provided in context

Minor pipeline extension (optional)

Add a deterministic “precompute” hook so PatternEngine runs once per report build and attaches outputs to context.

15. Tier 0 Smoke Defaults (must exist)

Tier 0 should generate very few patterns to validate end-to-end:

BRK.ORB w=5 rt=0 v=85 r=1.75 LONG/SHORT ALL

MNR.VWAPDEV d=2.0 wk=0.65 v=70 LONG/SHORT CHOP

PBL.VWAPRET n=20 v=65 LONG/SHORT TREND

MIC.IMB tw=30 i=80 s=10 LONG/SHORT ALL (only if ticks exist)

End of Spec

Pattern Catalog Appendix (100 Templates)
Global conventions (applies to all templates)

Signal timestamp: default = bar close time of the trigger bar.
Entry reference price: default = trigger bar close (unless specified).
Direction: LONG triggers “buy”, SHORT triggers “sell”. BI expands into both if symmetric.
Regime: use ALL if regime labels not available; otherwise filter as specified.
Percentiles: all percentiles are rolling over a declared lookback (global default OK).
Feature keys used by filters (minimum):

vol_pct, range_atr_mult, body_frac, upper_wick_frac, lower_wick_frac, wick_frac_max

vwap_dev (pattern-specific units)

imb_pct, spread_pct, tick_rate_pct (tick-dependent)

mins_from_open, session_bucket

session_range_pos_pct (0–100, location within session range)

Filter semantics encoding:

v>=X means vol_pct >= X

v<=X means vol_pct <= X (contraction filters)
Same for other tokens where applicable.

A) BREAKOUT (BRK) — 24 Templates
ORB (Opening Range Break)

BRK.ORB.Q[w,rt,v,r].LONG.{ALL|EXP|TREND}

Core: w opening range minutes; break above OR high

Core: rt retest window bars (0=no retest; >0 means price returns to OR boundary within rt bars and re-breaks)

Filters: v>=, r>= (break bar range ATR-multiple)

Regimes: ALL/EXP/TREND

Entry: close of breakout bar (or close of re-break bar if retest)

Linked pruning: if w=5 → rt ∈ {0,2}; if w=15 → rt ∈ {0,2,5}

BRK.ORB.Q[w,rt,v,r].SHORT.{ALL|EXP|TREND}

Mirror: break below OR low

Prior Day High/Low break

BRK.PDH.Q[rt,v,r].LONG.{ALL|EXP|TREND}

Trigger: break above prior day high

Core: rt optional retest to PDH

Filters: v>=, r>= on break bar

Entry: break bar close or re-break close

BRK.PDL.Q[rt,v,r].SHORT.{ALL|EXP|TREND}

Trigger: break below prior day low

Rolling Intraday High/Low break

BRK.IDH.Q[n,rt,v,r].LONG.{ALL|EXP|TREND}

Core: n lookback bars to define rolling high

Core: rt retest of the level

Filters: v>=, r>=

BRK.IDL.Q[n,rt,v,r].SHORT.{ALL|EXP|TREND}

Inside Bar breakout (IB)

BRK.IB.Q[n,v,r].LONG.{ALL|EXP}

Core: n inside-bar sequence length (e.g., 1 means current bar inside previous; n means last n bars inside a “mother bar”)

Trigger: break above mother bar high

Filters: v>=, r>= on break bar

BRK.IB.Q[n,v,r].SHORT.{ALL|EXP}

Range box breakout (RBOX)

BRK.RBOX.Q[n,rt,v,r].LONG.{ALL|EXP}

Core: n lookback bars define range box high/low

Trigger: close above box high

Core: rt optional retest of box boundary

Filters: v>=, r>=

BRK.RBOX.Q[n,rt,v,r].SHORT.{ALL|EXP}

Close outside range (simple breakout confirmation)

BRK.CLOSEOUT.Q[n,v].LONG.ALL

Core: n lookback to define range; signal when close is above range high AND body_frac >= 0.6

Filters: v>=

BRK.CLOSEOUT.Q[n,v].SHORT.ALL

(That’s 12 breakout structures × directions = 24 templates.)

B) PULLBACK (PBL) — 20 Templates
VWAP retest (trend continuation)

PBL.VWAPRET.Q[n,v,r].LONG.{TREND|EXP}

Trigger: regime TREND/EXP; price above VWAP (for long) then pulls back to VWAP band and prints rejection (close > open, or lower wick dominance)

Core: n impulse lookback bars (defines “trend leg” strength)

Filter: v<= (pullback volume contraction max)

Filter: r>= (rejection bar range multiple OR body_frac threshold; implementer chooses but must be consistent)

Entry: rejection bar close

PBL.VWAPRET.Q[n,v,r].SHORT.{TREND|EXP}

Mirror with price below VWAP and pullback up to VWAP

EMA20 retest

PBL.EMA20RET.Q[n,v].LONG.{TREND|EXP}

Trigger: pullback to EMA20 after impulse

Filter: v<= (contraction)

Entry: rejection bar close

PBL.EMA20RET.Q[n,v].SHORT.{TREND|EXP}

EMA50 retest

PBL.EMA50RET.Q[n,v].LONG.{TREND|EXP}
PBL.EMA50RET.Q[n,v].SHORT.{TREND|EXP}

Impulse retracement entry (depth-based)

PBL.IMPRET.Q[n,d,v].LONG.TREND

Core: n impulse lookback

Core: d retracement fraction ∈ (0,1) of impulse range

Filter: v<= during pullback

Trigger: price retraces to d-level and prints bullish reversal bar

PBL.IMPRET.Q[n,d,v].SHORT.TREND

Flag breakout after pullback

PBL.FLAGBRK.Q[n,c,v,r].LONG.{TREND|EXP}

Core: n impulse lookback

Core: c flag length bars

Filter: v<= during flag

Filter: r>= on breakout bar

Trigger: break above flag high

PBL.FLAGBRK.Q[n,c,v,r].SHORT.{TREND|EXP}

Higher-low / lower-high structural pullback

PBL.HL.Q[n,v].LONG.TREND

Core: n lookback for swing detection (lightweight pivot)

Filter: v<= on pullback

Trigger: higher-low formed + close breaks prior bar high

PBL.LH.Q[n,v].SHORT.TREND

(10 pullback structures × directions = 20 templates.)

C) MEAN REVERSION (MNR) — 20 Templates
VWAP deviation fade

MNR.VWAPDEV.Q[d,wk,v].SHORT.{CHOP|ALL}

Core: d deviation from VWAP (units declared: ATR-multiple or stdev multiple)

Filter: wk>= (upper wick fraction threshold OR wick_frac_max)

Filter: v>= optional confirmation (often not too strict)

Trigger: price extended above VWAP by d and prints rejection candle

MNR.VWAPDEV.Q[d,wk,v].LONG.{CHOP|ALL}

Mirror below VWAP

Session extreme fade

MNR.SEXT.Q[d,wk].SHORT.{CHOP|ALL}

Core: d = band size in session range percent (e.g., top d% zone)

Filter: wk>= upper wick fraction

Trigger: session_range_pos_pct >= (100-d)

MNR.SEXT.Q[d,wk].LONG.{CHOP|ALL}

Mirror bottom

Failed breakout fade (levels)

MNR.FAILBRK.PDH.Q[rt,v,wk].SHORT.ALL

Trigger: breaks above PDH but closes back below within rt bars

Filter: v>= optional; wk>= optional

Entry: close of failure confirmation bar (the close back inside)

MNR.FAILBRK.PDL.Q[rt,v,wk].LONG.ALL

Mirror

MNR.FAILBRK.IDH.Q[n,rt,v].SHORT.ALL

Rolling high false break (n lookback)

MNR.FAILBRK.IDL.Q[n,rt,v].LONG.ALL

Exhaustion large-range candle reversal

MNR.EXHAUST.Q[r,v,wk].SHORT.ALL

Trigger: range_atr_mult >= r and upper wick dominance; “climax”

Filter: v>= (but allow low-volume climax test as separate variant if desired)

MNR.EXHAUST.Q[r,v,wk].LONG.ALL

Mirror

2-step reversal (spike then opposite close)

MNR.TWOSTEP.Q[r,v].SHORT.ALL

Trigger: bar1 large up (r), bar2 closes below bar1 mid with volume confirmation (v)

MNR.TWOSTEP.Q[r,v].LONG.ALL

(10 mean-reversion structures × directions = 20 templates.)

D) MOMENTUM (MOM) — 16 Templates
Large-range continuation

MOM.LRNG.Q[r,v].LONG.{ALL|TREND|EXP}

Trigger: range_atr_mult >= r, close near high, vol_pct >= v

MOM.LRNG.Q[r,v].SHORT.{ALL|TREND|EXP}

Marubozu continuation

MOM.MRBZ.Q[b,v].LONG.{ALL|TREND|EXP}

Trigger: body_frac >= b, small wick, vol_pct >= v

MOM.MRBZ.Q[b,v].SHORT.{ALL|TREND|EXP}

Momentum cluster

MOM.CLUS.Q[n,v].LONG.{TREND|EXP}

Core: n consecutive bars with higher closes + increasing body or range

Filter: v>= on cluster aggregate or final bar

MOM.CLUS.Q[n,v].SHORT.{TREND|EXP}

Step pattern (consecutive closes)

MOM.STEP.Q[n].LONG.{TREND|EXP}

Core: n consecutive higher closes and closes above EMA20

MOM.STEP.Q[n].SHORT.{TREND|EXP}

Breakout + imbalance confirmation (hybrid)

MOM.BRKIMB.Q[i,v].LONG.EXP

Trigger: breakout bar + tick imbalance >= i

Filter: vol_pct >= v

MOM.BRKIMB.Q[i,v].SHORT.EXP

Acceleration (slope increase)

MOM.ACCEL.Q[n,r].LONG.TREND

Core: n lookback for slope; trigger when slope increases and range expands >= r

MOM.ACCEL.Q[n,r].SHORT.TREND

(8 momentum structures × directions = 16 templates.)

E) COMPRESSION (CMP) — 12 Templates
Compression detector (pre-break alert)

CMP.COMP.Q[c,v].BI.SQZ

Core: c bars show ATR contraction

Filter: v<= volume contraction

Signal: “setup” event at end of compression (direction BI)

Compression break

CMP.COMPBRK.Q[c,r,v].LONG.EXP

Core: c compression length

Trigger: breakout up + range_atr_mult >= r

Filter: v>= on breakout bar

CMP.COMPBRK.Q[c,r,v].SHORT.EXP

Compression false break

CMP.COMPFAIL.Q[c,rt].SHORT.ALL

Trigger: compression break up then close back inside within rt bars

CMP.COMPFAIL.Q[c,rt].LONG.ALL

Range contraction metric variant

CMP.RCOMP.Q[c,v].BI.SQZ

Uses range (high-low) contraction rather than ATR

(6 templates + direction expansions = 12 “usable” patterns; BI counts as one template but expands downstream.)

F) VOLUME ANOMALY (VOL) — 12 Templates
Volume spike continuation

VOL.VSPIKE.Q[v,r].LONG.ALL

Trigger: vol_pct >= v and range_atr_mult >= r and close near high

VOL.VSPIKE.Q[v,r].SHORT.ALL

Volume spike exhaustion reversal

VOL.VCLIMAX.Q[v,r,wk].SHORT.ALL

Trigger: vol_pct >= v, range >= r, upper wick >= wk

VOL.VCLIMAX.Q[v,r,wk].LONG.ALL

Absorption bar

VOL.ABSORB.Q[v,r,wk].LONG.{CHOP|ALL}

Trigger: vol_pct >= v, range_atr_mult <= r, lower wick dominance >= wk

VOL.ABSORB.Q[v,r,wk].SHORT.{CHOP|ALL}

upper wick dominance

Volume-price divergence proxy

VOL.VDIV.Q[v,n].SHORT.ALL

Core: n lookback

Trigger: price makes higher high but vol_pct at high is below rolling median while prior swing high had higher vol (divergence)

VOL.VDIV.Q[v,n].LONG.ALL

(6 structures × directions = 12 templates.)

G) WICK / REJECTION (WCK) — 12 Templates
Pure wick rejection (context-free)

WCK.UP.Q[wk,v].SHORT.ALL

Trigger: upper_wick_frac >= wk

Filter: vol_pct >= v (optional)

WCK.DN.Q[wk,v].LONG.ALL

Wick at level (PDH/PDL)

WCK.UP.PDH.Q[wk].SHORT.ALL

Trigger: bar tags/exceeds PDH and prints upper wick rejection

WCK.DN.PDL.Q[wk].LONG.ALL

Wick at VWAP deviation

WCK.UP.VWAPDEV.Q[d,wk].SHORT.{CHOP|ALL}
WCK.DN.VWAPDEV.Q[d,wk].LONG.{CHOP|ALL}

(6 structures × directions = 12 templates.)

H) MICROSTRUCTURE (MIC) — 16 Templates

All MIC patterns require tick data; skip if no tick stream.

Imbalance threshold

MIC.IMB.Q[tw,i,s].LONG.ALL

Core: tw seconds window for imbalance

Filter: imbalance_pct >= i

Filter: spread_pct <= s (tight spreads)

MIC.IMB.Q[tw,i,s].SHORT.ALL

Mirror imbalance negative (implementation can map to “signed imbalance”)

Imbalance reversal

MIC.IMBREV.Q[tw,i].LONG.ALL

Trigger: signed imbalance flips from <= -i to >= +i within 2*tw window

MIC.IMBREV.Q[tw,i].SHORT.ALL

Spread expansion event

MIC.SPRD.Q[tw,s].BI.ALL

Trigger: spread_pct rises above (100-s) percentile threshold

BI because follow-through vs reversal is what you test; direction may be derived from next bar sign in evaluation or expanded into both

Tick density anomaly

MIC.TDENS.HI.Q[tw].BI.ALL

Trigger: tick_rate_pct >= 90 over tw

MIC.TDENS.LO.Q[tw].BI.ALL

Trigger: tick_rate_pct <= 10 over tw

Liquidity vacuum displacement (tick sparse + price jump)

MIC.VAC.Q[tw,r].BI.ALL

Trigger: tick density low AND range_atr_mult >= r in same window

(8 templates with BI expansions + 4 directional = 16 “usable” patterns.)

I) TIME-OF-DAY (TOD) — 12 Templates
Open burst continuation

TOD.OPEN.BURST.Q[w,r,v].LONG.ALL

Trigger: within first w minutes; range >= r and vol>=v and close near high

TOD.OPEN.BURST.Q[w,r,v].SHORT.ALL

Lunch fade

TOD.LUNCH.FADE.Q[w,d].SHORT.CHOP

Core: w minutes window inside lunch bucket

Core: d session range pos threshold (top d% zone)

Trigger: in lunch and in top zone → fade

TOD.LUNCH.FADE.Q[w,d].LONG.CHOP

Power hour breakout

TOD.POWER.BRK.Q[w,v].LONG.EXP
TOD.POWER.BRK.Q[w,v].SHORT.EXP

Close reversal

TOD.CLOSE.REV.Q[w,wk].LONG.ALL

Trigger: last w minutes, rejection wick >= wk at session extreme

TOD.CLOSE.REV.Q[w,wk].SHORT.ALL

(6 structures × directions = 12 templates.)

J) RANGE LOCATION (RNG) — 8 Templates
Top/bottom zone fade

RNG.TOP.Q[d,wk].SHORT.{ALL|CHOP}

Core: d zone width percent (10 or 20)

Filter: wk>= optional rejection requirement

RNG.BOT.Q[d,wk].LONG.{ALL|CHOP}

Mid-zone continuation

RNG.MID.Q[d,v].LONG.ALL

Core: d mid-band width (e.g., ±d around 50%)

Filter: v>= (momentum confirmation)

RNG.MID.Q[d,v].SHORT.ALL

(4 structures × directions = 8 templates.)

Template Count Check (100)

BRK: 24

PBL: 20 → 44

MNR: 20 → 64

MOM: 16 → 80

CMP: 12 → 92

VOL: 12 → 104

WCK: 12 → 116

MIC: 16 → 132

TOD: 12 → 144

RNG: 8 → 152

This appendix defines 152 templates if you count every directional mirror explicitly and BI expansions as separate evaluations.

If you want exactly “100 patterns” in practice, use this selection rule:

“Top-100 Practical Set” Selection Rule

Include all of:

BRK (24)

PBL (20) → 44

MNR (20) → 64

MOM (16) → 80

CMP (12) → 92
Then add 8 from MIC/TOD/RNG (choose 8 highest leverage below) to reach 100:

Recommended 8 add-ons:

MIC.IMB (LONG/SHORT) = 2

MIC.IMBREV (LONG/SHORT) = 2

TOD.OPEN.BURST (LONG/SHORT) = 2

RNG.TOP + RNG.BOT = 2
Total add-ons = 8 → 100

Everything else remains available as “extended set”.

Per-Template Parameter Semantics Summary

To remove ambiguity, here are the semantics by token per family:

BRK: v>=, r>=, rt is retest bars, w minutes, n lookback bars

PBL: v<= (contraction), n impulse lookback, d retracement fraction (IMPRET)

MNR: d deviation threshold, wk>= rejection wick fraction, v optional (usually v>= but allow lower in CHOP)

MOM: b>=, v>=, r>=, n consecutive bars

CMP: v<= contraction, c compression length, r>= on breakout bar, rt false-break window

VOL: v>= spike, ABSORB uses r<= (small range), wk>=

WCK: wk>=, v optional v>=

MIC: i>=, s<=, tw seconds, tick-derived

TOD: time bucket via t and w; v>=, r>=

RNG: d band width percent; wk>= optional; MID uses v>=

Implementation Notes (Critical)

Do not implement these as 152 functions.
They must be data-driven templates expanded by tier grids.

BI patterns: treat as a template that generates either:

two directional instances (LONG + SHORT), or

a single “BI” instance that is evaluated both ways (recommended: expand to both for consistent metrics).

Retest logic must be standardized:

“break then retest then re-break within rt bars”

entry signal timestamp is the re-break bar close

Deviations must declare units:

VWAPDEV d measured in ATR-multiples OR rolling stdev multiples

Choose one globally for consistency.

Feature Definitions Glossary Appendix
0) Scope and invariants

All datetimes (dt) are tz-aware and in America/Denver.

All “bar features” are computed from the bar at dt plus rolling windows on prior bars.

All “tick features” are computed from ticks within a deterministic window around dt (see tw) and must use bid/ask/last fields consistently.

All percentiles must specify:

a lookback window length (bars or time)

whether current observation is included (recommended: include current for signal bar features, exclude current for threshold baseline—pick one and keep it consistent)

Recommendation for percentiles: compute rolling distribution on the prior N observations and compare current value to that distribution (prevents leakage).

1) Core Bar Geometry Features

Assume a bar has fields: open, high, low, close, volume.

1.1 Range

range = high - low

1.2 Body

body = abs(close - open)

1.3 Upper/Lower Wick

upper_wick = high - max(open, close)

lower_wick = min(open, close) - low

1.4 Body Fraction (0–1)

body_frac = body / range if range > 0 else 0

1.5 Wick Fractions (0–1)

upper_wick_frac = upper_wick / range if range > 0 else 0

lower_wick_frac = lower_wick / range if range > 0 else 0

wick_frac_max = max(upper_wick_frac, lower_wick_frac)

1.6 Close Location Value (CLV) (-1 to +1)

Used for “close near extreme” logic.

clv = ((close - low) - (high - close)) / range if range > 0 else 0
Interpretation:

+1 close at high

-1 close at low

0 close midrange

Convenience:

close_near_high = clv >= +0.6 (tunable)

close_near_low = clv <= -0.6

2) ATR and Range Multiples
2.1 True Range (TR)

tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

2.2 ATR

Choose one method and keep it consistent:

Wilder’s ATR (RMA) OR simple moving average of TR.

Default:

atr_n = 14 bars (or configurable per market timeframe)

2.3 Range as ATR multiple

range_atr_mult = range / atr if atr > 0 else 0

3) Rolling Percentiles (Volume, Range, Tick features)

Percentile values should be expressed 0–100.

3.1 Rolling percentile definition

Given current value x_t and baseline sample S = {x_{t-N}, …, x_{t-1}}:

pct = 100 * rank(x_t within S)

Define rank method:

rank = (# of values in S <= x_t) / len(S)
(Alternative tie handling is okay if deterministic, but must be specified.)

3.2 Volume percentile

vol_pct = rolling_percentile(volume_t, volume_{t-N..t-1})

3.3 Range percentile (optional)

range_pct = rolling_percentile(range_t, range_{t-N..t-1})

4) VWAP and VWAP Deviation
4.1 Session VWAP

Define VWAP per trading session (e.g., RTH session, or full day session—pick one and keep it consistent).

At time t, session VWAP:

vwap_t = sum(price_i * volume_i) / sum(volume_i) over bars i in session up to t
Use typical price:

price_i = (high_i + low_i + close_i)/3
(or use close only; choose one and document it; typical price is recommended)

4.2 VWAP deviation vwap_dev

Choose one unit system globally:

Option A (ATR units, recommended for futures)

vwap_dev = (close - vwap) / atr
So d=2.0 means “2 ATRs away from VWAP”.

Option B (stdev units)

compute rolling stdev of (close - vwap) within session or lookback

vwap_dev = (close - vwap) / stdev

Pick one and enforce it across all VWAPDEV patterns.

Also define signed versions:

vwap_dev_signed = vwap_dev

vwap_dev_abs = abs(vwap_dev)

5) Trend and Regime Features

These are used for regime gating (TREND/CHOP/EXP/SQZ).

5.1 HTF slope proxy (bar-based)

If you compute HTF slope, define:

timeframe aggregation method (e.g., 5m from 1m bars)

MA type and length (e.g., EMA(20) on HTF closes)

slope measure: difference over k bars or regression slope

A simple deterministic slope:

slope = ema_t - ema_{t-k}

5.2 Realized volatility

rv = stddev(returns over last N bars)
Where return can be close-to-close difference in ticks.

5.3 Overlap / Chop proxy

Define overlap ratio:

overlap = avg( min(high_t, high_{t-1}) - max(low_t, low_{t-1}) ) / avg(range) over N bars
Clamp overlap contribution at >=0.

Chop tends to have higher overlap.

5.4 Regime labels (deterministic rule set)

You need a consistent mapping to {TREND, CHOP, EXP, SQZ}.

One recommended rule set:

SQZ (squeeze): atr_pct <= 25th percentile over long lookback AND rv_pct <= 25th

EXP (expansion): atr_pct >= 75th OR a breakout bar with range_atr_mult >= 1.75

TREND: slope magnitude above threshold AND overlap below threshold

CHOP: overlap above threshold AND slope below threshold

fallback: ALL

Exact thresholds can be tuned, but must be explicit and consistent.

6) Session / Time Features
6.1 Session boundaries

Define session type used (RTH or full day). Must be consistent across:

VWAP

session high/low

session range position features

TOD buckets

6.2 Minutes from session open

mins_from_open = (dt - session_open_dt) in minutes

6.3 Session bucket (categorical)

Define buckets by mins_from_open (example; tune to your preference but keep fixed):

open: 0–30

mid: 30–180

lunch: 180–300

power: 300–390

close: last 15–30 mins of session

Emit:

session_bucket ∈ {open, mid, lunch, power, close}

6.4 Session high/low to time t

sess_high_t = max(highs from session start to t)

sess_low_t = min(lows from session start to t)

sess_range_t = sess_high_t - sess_low_t

6.5 Position within session range (0–100)

session_range_pos_pct = 100 * (close - sess_low_t) / sess_range_t if sess_range_t > 0 else 50

Used by RNG and SEXT patterns.

7) Level Definitions (PDH/PDL, IDH/IDL, ORB)
7.1 Prior day high/low

Define “day” in same session calendar you use for VWAP (RTH day recommended).

PDH = max(high) over prior day session

PDL = min(low) over prior day session

7.2 Rolling intraday high/low (IDH/IDL)

IDH_n = max(high over last n bars)

IDL_n = min(low over last n bars)
Ensure IDH/IDL are computed on bars prior to the trigger bar (avoid lookahead).

7.3 Opening range (ORB)

For w minutes:

OR start = session open

OR end = session open + w minutes

ORH = max(high within OR window)

ORL = min(low within OR window)
Trigger after OR window completes.

8) Retest Logic (rt)

Retest is used in ORB/PDH/PDL/IDH/IDL/RBOX.

8.1 Definitions

A “break” occurs when close exceeds the level in direction:

LONG break: close > level

SHORT break: close < level

A “retest” occurs when within rt bars after the break:

price touches the level boundary within a tolerance band

and then closes back in breakout direction.

8.2 Tolerance band

Define a deterministic tolerance in ticks:

tol_ticks (e.g., 1–2 ticks) OR

use ATR-based tolerance (e.g., 0.05 ATR)

Pick one.

8.3 Signal timestamp with retest

Signal dt = close time of the “re-break” confirmation bar.

9) Tick-Based Feature Definitions (MIC and micro-confirmations)

Assume ticks include:

dt, last, bid, ask, volume
and tick size is known.

9.1 Tick window

For a bar time dt, define tick window:

W = (dt - tw seconds, dt]
(or (dt - tw, dt] inclusive end). Keep it consistent.

9.2 Mid price

mid = (bid + ask) / 2 if both exist
If bid/ask missing, use last but record a diagnostic flag.

9.3 Spread

spread = ask - bid

spread_ticks = spread / tick_size

9.4 Spread percentile

Compute rolling percentile of spread_ticks over a baseline window (e.g., last M tick windows or last K bars of tick windows):

spread_pct = rolling percentile of average spread in W

Semantics:

filter s means spread_pct <= s (tight market)

9.5 Tick rate

tick_rate = (# ticks in W) / tw

tick_rate_pct = rolling percentile

9.6 Tick imbalance (signed)

Need a deterministic proxy (since true aggressor delta may not exist).

A robust proxy:

Compute tick-to-tick last price change: Δ = last_t - last_{t-1}

Classify:

uptick if Δ > 0

downtick if Δ < 0

unchanged if Δ == 0

Weight by tick volume if available.

Let:

up_vol = sum(volume_t for upticks in W)

dn_vol = sum(volume_t for downticks in W)

tot = up_vol + dn_vol

Signed imbalance:

imb_signed = (up_vol - dn_vol) / tot if tot > 0 else 0
Range: [-1, +1]

Percentile:

imb_pct = rolling percentile of imb_signed (or of abs(imb_signed); choose one)
For directional patterns, percentiling signed values is okay if you define:

LONG condition uses imb_signed and compares to a signed threshold derived from i (e.g., top i-th percentile of signed distribution)
Simpler approach:

compute imb_abs_pct percentile of abs imbalance

and also use sign separately:

LONG requires imb_signed > 0 and imb_abs_pct >= i

Pick one and document it.

9.7 Imbalance reversal (IMBREV)

Define a reversal event if within a 2*tw lookback:

imbalance was below -thr and is now above +thr
Where thr is derived from i.

10) Derived Pattern-Specific Constructs
10.1 Range box (RBOX)

For lookback n bars:

box_high = max(high over last n bars)

box_low = min(low over last n bars)
Break:

LONG if close > box_high

SHORT if close < box_low

10.2 Compression (COMP)

Compression signal occurs if over last c bars:

atr is declining and current ATR is below a percentile threshold relative to a longer baseline
AND/OR

average range is below percentile threshold
Optionally combine with volume contraction:

vol_pct <= v meaning volume is low relative to baseline.

10.3 Absorption (ABSORB)

Absorption bar for long:

vol_pct >= v

range_atr_mult <= r

lower_wick_frac >= wk

close above open (optional)

Short absorption:

upper_wick_frac >= wk

11) Evaluation Metrics (Fixed Horizon)

For each signal at dt:

Define horizons in bars: H ∈ {1,3,5,10,20}.

11.1 Return in ticks

For LONG: ret_ticks_H = (close_{t+H} - entry_ref_price) / tick_size

For SHORT: ret_ticks_H = (entry_ref_price - close_{t+H}) / tick_size

11.2 MFE/MAE in ticks (within next H bars)

Compute max favorable and max adverse excursions relative to entry_ref_price.

For LONG within bars t+1..t+H:

mfe_ticks_H = (max(high) - entry_ref_price) / tick_size

mae_ticks_H = (min(low) - entry_ref_price) / tick_size (will be <= 0)

For SHORT:

mfe_ticks_H = (entry_ref_price - min(low)) / tick_size

mae_ticks_H = (entry_ref_price - max(high)) / tick_size (will be <= 0)

11.3 Time-to-MFE/MAE (optional)

time_to_mfe_H = argmax(high) index for long; argmin(low) for short
Express as bars or minutes.

12) Diagnostics Flags (recommended)

Emit boolean flags for:

has_ticks (tick stream present for instrument/contract)

missing_bid_ask (if bid/ask absent in tick window)

session_defined (if session calendar matched)

atr_valid

vwap_valid

pct_valid (enough history for percentiles)

End of Feature Glossary Appendix

Validation Playbook Appendix (PatternEngine + Sweeps)

This playbook is a deterministic checklist to confirm the PatternEngine, sweep expansion, feature math, and outcome evaluation are correct before you trust any leaderboard.

It’s organized as: what to validate, how to validate, and pass/fail criteria. It assumes your report framework can render diagnostic tables.

0) Principles

Validate data integrity → features → signals → outcomes → aggregation in that order.

Every validation artifact must be reproducible given the same inputs and tier.

Prefer small Tier 0 validations with known expectations before Tier 1 sweeps.

When something fails, fix the earliest layer, not the downstream metric.

1) Data Integrity Validation (Bars + Ticks)
1.1 Bar continuity and monotonic time

What: Bar timestamps must be strictly increasing per instrument/contract, no duplicates.

How:

For each instrument/contract: count duplicates, count non-increasing dt.

Show first/last dt and number of bars.

Pass:

duplicates = 0, non-increasing = 0.

1.2 Tick continuity (if ticks present)

What: Tick timestamps monotonic and within the report’s coverage window.

How:

For each tick stream: duplicates count, non-increasing count.

Count ticks per day/session bucket.

Pass:

non-increasing = 0 (or explain allowable rare duplicates if vendor does it; then must be de-duplicated deterministically).

1.3 Tick ↔ bar alignment sanity

What: Tick windows used for a bar must contain ticks for that time (when market is active).

How:

Random sample 20 bars during active hours:

show bar dt

tick window [dt-tw, dt]

tick count

Also sample 10 bars overnight (if applicable) for expected lower counts.

Pass:

Active hours have nonzero ticks in most windows.

Low activity windows are explainable (lunch/overnight).

2) Feature Math Validation (Bar Geometry, ATR, VWAP, Percentiles)
2.1 Bar geometry invariants

What: body/wick fractions should be within [0,1] and sum logically.

How:

On a sample of 100 bars:

compute range, body, upper_wick, lower_wick

show min/max of body_frac, upper_wick_frac, lower_wick_frac

show distribution summary

Check: upper_wick + lower_wick + body == range (within float tolerance)

Pass:

fractions in [0,1], and reconstruction error near 0.

2.2 ATR sanity

What: ATR should be positive and stable; range_atr_mult should be plausible.

How:

Show ATR min/median/max per run

Show range_atr_mult distribution:

p50 ~ ~0.5–1.5

rare tails > 3–5 depending on instrument

Pass:

ATR > 0 after warmup, no large NaN gaps, range_atr_mult not absurd.

2.3 VWAP sanity (session-based)

What: VWAP should move smoothly and remain within session price envelope most of the time.

How:

For a single session:

table every N bars: close, vwap, (close-vwap), cumulative volume

Count how often VWAP is outside [sess_low, sess_high] (should be extremely rare unless bad definition).

Pass:

VWAP mostly inside session range; monotonic cumulative volume.

2.4 Rolling percentile leakage test

What: Percentiles must use prior-window baseline (no lookahead).

How:

Pick a bar t with extreme volume.

Display baseline window values used for percentile.

Confirm t’s volume is not included in the baseline set (or if included by design, it must be consistent everywhere and documented).

Pass:

Baseline window matches declared rule.

Identical methodology used for vol_pct, spread_pct, tick_rate_pct, etc.

3) Regime Label Validation (if regimes enabled)
3.1 Regime counts and transitions

What: Regime labels should not be degenerate (e.g., 99% TREND).

How:

Count bars in each regime per session and per run

Show average duration of contiguous regime segments

Pass:

Regimes appear with reasonable frequency.

Segment durations not mostly 1-bar flicker (unless that’s intended).

3.2 Spot-check regime correctness

What: Manually spot-check a small number of periods:

SQZ should align with low ATR/rv

EXP should align with breakouts / high range bars

CHOP should align with high overlap

How:

Create a 1-page diagnostic for 3 sessions:

summary metrics per segment (ATR pct, slope, overlap)

list of segments with start/end dt and label

Pass:

Qualitatively plausible and matches metric definitions.

4) Pattern Template Expansion Validation (Sweep Engine)
4.1 Deterministic ID generation

What: Same config produces same set of pattern_ids, ordered deterministically.

How:

Run twice (same inputs) and compare:

count of pattern instances

first 20 IDs

hash of sorted ID list

Pass:

Identical results.

4.2 Cap & pruning rule enforcement

What: Family caps and linked-parameter constraints must be applied.

How:

For Tier 1:

show generated_count per family per direction pre- and post-pruning

show which parameter was frozen (if cap exceeded)

For ORB:

confirm w=5 never generates rt=5 (linked constraint)

Pass:

Counts match caps, linked constraints satisfied.

4.3 Baseline inclusion verification

What: Every major structure/direction includes a baseline variant.

How:

For each family/structure/direction:

confirm at least one Q[] or baseline Q exists (as specified)

Pass:

No missing baselines.

5) Signal Detection Validation (Correctness and Duplicates)
5.1 Signal count plausibility

What: Signal counts should be in plausible ranges; no pattern triggers on nearly every bar unless intended.

How:

For each pattern_id: show signals count distribution:

top 10 by count

bottom 10 nonzero

number of zero-signal patterns

Flag patterns with signals > X% of bars (e.g., > 20%)

Pass:

Over-triggering patterns are understood or fixed.

5.2 Duplicate signal suppression (cooldown)

What: If cooldown is defined, verify it works.

How:

For one pattern_id, list signals dt gaps.

Confirm min gap >= cooldown bars.

Pass:

No violations.

5.3 Retest logic verification (critical)

What: Retest patterns must truly break → retest → re-break.

How:

For 10 sampled retest signals:

show:

level value (e.g., ORH/PDH)

break bar dt/close

retest bar dt/low/high relative to level±tol

re-break bar dt/close

Provide pass/fail per sample.

Pass:

Every sample meets formal definitions.

6) Tick Feature Validation (MIC patterns + confirmations)
6.1 Spread computation sanity

What: Spread ticks must be non-negative and usually small for liquid futures.

How:

Show spread_ticks min/median/p95

Show spread_pct distribution

Pass:

No negative spreads; typical values plausible.

6.2 Imbalance sanity

What: Imbalance values should be in [-1,1] and not all zeros.

How:

Show imb_signed distribution summary

Show relationship to 1-bar return on average (not necessarily strong, but not nonsense)

Pass:

Distribution looks sensible, percentiles not degenerate.

6.3 MIC window tick counts

What: MIC patterns should not trigger when tick windows are empty.

How:

For 20 MIC signals:

show tick_count in window

show imb_signed, spread_ticks

Pass:

tick_count > 0 (or your design explicitly handles zero windows and flags it).

7) Outcome Evaluation Validation (Fixed Horizon)
7.1 Directional sign correctness (must-pass)

What: Returns for LONG vs SHORT must be signed correctly.

How:

Pick 10 signals (5 long, 5 short) with known future movement:

show entry_ref_price

show close at H

show computed ret_ticks_H

Manually verify sign.

Pass:

All signs correct.

7.2 MFE/MAE correctness (must-pass)

What: MFE >= 0 and MAE <= 0 by definition (for both directions under your formula).

How:

For 50 signals:

compute mfe_ticks_H, mae_ticks_H

show min/max

Spot-check a handful by comparing to high/low in the forward window.

Pass:

No MFE negative, no MAE positive (unless you represent MAE as absolute; then document it and enforce non-negative).

7.3 Horizon boundary correctness

What: H bars forward must not exceed dataset length; last bars should be dropped or marked invalid.

How:

Count signals excluded due to insufficient forward data per horizon.

Ensure exclusions are consistent across patterns.

Pass:

No out-of-bounds; exclusions explicitly tracked.

8) Aggregation Validation (Leaderboard Correctness)
8.1 Aggregation identity test

What: net_ret = sum(ret) and avg_ret = net_ret / signals.

How:

For 5 random pattern_ids:

show signals table with ret_ticks_10

sum it manually in the diagnostic output

compare to leaderboard row

Pass:

Exact match (within rounding).

8.2 Min-signals constraint enforcement

What: Patterns below min_signals should be excluded or marked.

How:

Set min_signals to a high number and confirm fewer rows.

Confirm excluded rows count is reported.

Pass:

Filter behaves as configured.

8.3 Slice totals consistency

What: Regime slice sums should equal ALL totals.

How:

For one pattern_id:

show signals count per regime

sum counts and compare to total signals

Pass:

Totals align.

9) End-to-End Smoke Suite (Tier 0) — Required

Create a dedicated “Tier 0 Validation” report mode that runs:

Patterns:

BRK.ORB Q[w=5,rt=0,v=85,r=1.75] LONG/SHORT ALL

MNR.VWAPDEV Q[d=2.0,wk=0.65,v=70] LONG/SHORT CHOP

PBL.VWAPRET Q[n=20,v=65] LONG/SHORT TREND

MIC.IMB Q[tw=30,i=80,s=10] LONG/SHORT ALL (only if ticks exist)

Outputs:

Generated pattern_id list

Signal counts and a 20-row sample of signals

A 10-signal manual audit table (entry, forward close, computed ret)

A small leaderboard for H=10

Pass:

Deterministic IDs and stable counts

Signs correct

No invalid feature ranges

No missing tick-window data for MIC signals

10) Common Failure Modes and What They Look Like
A) Lookahead leakage

Symptoms:

Unrealistically high win rates across many patterns

Sharp performance that disappears when you shift timestamps

Fix:

Ensure percentiles, IDH/IDL, boxes use prior data only.

B) Wrong short return sign

Symptoms:

SHORT patterns all look terrible or inverted relative to market direction

Fix:

Verify ret_ticks formula for SHORT and MFE/MAE windows.

C) Retest logic too permissive

Symptoms:

retest patterns produce almost same signal count as non-retest

Fix:

Enforce explicit “touch level within tolerance” and “re-break” constraints.

D) Session boundary mismatch

Symptoms:

VWAP weirdness, ORB triggers outside intended time

Fix:

Confirm session calendar, mins_from_open, and day definitions.

E) Tick-window mismatch

Symptoms:

MIC signals with zero ticks, spread_pct nonsensical

Fix:

Validate tick alignment and tick window extraction.

11) “Ready to Trust” Gate

Do not proceed to Tier 1 broad sweeps until:

✅ All must-pass validations in sections 7.1, 7.2, 5.3 pass

✅ Tier 0 smoke suite passes end-to-end

✅ Aggregation identity test passes for multiple pattern_ids

✅ Regime and session features produce plausible distributions

Validation Report Outline (Single-Page Standard)

This is a fixed layout you can implement as one or more report sections. It’s designed so you can open one HTML report and quickly answer: is the PatternEngine correct?

It’s intentionally redundant: multiple cross-checks catch the same bug from different angles.

1) Header Block

Title: PatternEngine Validation Report
Run scope: list run_id(s) + instrument/contract(s) included
Timezone: America/Denver
Tier: 0/1/2/3
Horizon(s): e.g., {1,3,5,10,20}
Min signals: integer
Tick availability: per instrument/contract: YES/NO and tick row counts
Build hash (optional): deterministic hash of:

sorted pattern_ids

key settings (tier, grids, pruning caps)

2) Data Integrity Summary (Bars)
Table: Bar Coverage

Columns:

instrument

contract

bars

first_dt

last_dt

duplicate_dt_count

non_increasing_dt_count

missing_volume_count (if any)

missing_ohlc_count (if any)

Red flags shown inline:

duplicates > 0

non_increasing > 0

3) Data Integrity Summary (Ticks) — Only if tick streams exist
Table: Tick Coverage

Columns:

instrument

contract

ticks

first_dt

last_dt

duplicate_dt_count

non_increasing_dt_count

missing_bid_count

missing_ask_count

spread_negative_count

Table: Tick Window Alignment (Sample)

Show 20 sampled bar dts during active hours:

bar_dt

tw_seconds

window_start

window_end

tick_count_in_window

avg_spread_ticks

imb_signed

Red flag:

tick_count_in_window == 0 in active windows (unless expected)

4) Feature Sanity Dashboards (Bars)
Table: Bar Geometry Summary

Per instrument/contract (or overall):

range_min / range_p50 / range_p99

body_frac_min / p50 / p99

upper_wick_frac_min / p50 / p99

lower_wick_frac_min / p50 / p99

wick_frac_max_min / p50 / p99

clv_min / p50 / p99

Hard validity checks displayed:

any body_frac < 0 or > 1

any wick frac < 0 or > 1

Table: ATR + Range Multiples

atr_min / p50 / p99

range_atr_mult_min / p50 / p99

NaN ATR count (after warmup)

NaN range_atr_mult count

5) VWAP + Session Feature Sanity
Table: VWAP Quick Scan (Sample Session)

Pick 1–3 sessions (configurable) and show rows every N minutes:

dt

close

vwap

close_minus_vwap

vwap_dev (as defined)

cumulative_volume

session_high_so_far

session_low_so_far

session_range_pos_pct

mins_from_open

session_bucket

Red flags:

cumulative_volume decreases

vwap outside [session_low, session_high] frequently

6) Regime Label Diagnostics (if regimes enabled)
Table: Regime Counts

Per run_id/instrument:

TREND bars

CHOP bars

EXP bars

SQZ bars

ALL/fallback bars

avg_segment_length per regime

median_segment_length per regime

Table: Regime Segment List (Top 20 segments)

start_dt

end_dt

regime

duration_bars

atr_pct (avg)

overlap_metric (avg)

slope_metric (avg)

7) Pattern Expansion & Pruning Diagnostics
Table: Pattern Instance Counts (Pre/Post)

Per family (and per direction if you want):

generated_pre_prune

generated_post_prune

cap

pruned_count

frozen_param (if cap exceeded)

baseline_included (YES/NO)

Table: ORB Linked-Constraint Check

count of pattern instances where (w=5 AND rt=5)

count where (w=5 AND rt=0/2)

count where (w=15 AND rt=0/2/5)

Pass condition: invalid combos count = 0.

Table: Baseline Coverage

Per family/structure/direction:

baseline_present (YES/NO)

baseline_pattern_id

8) Signal Detection Diagnostics
Table: Signal Count Distribution (Top 20 by count)

pattern_id

signals

pct_of_bars

first_signal_dt

last_signal_dt

Table: Signal Count Distribution (Bottom 20 nonzero)

Same columns.

Table: Zero-Signal Patterns Summary

family

structure

zero_signal_count

total_patterns_in_family

Table: Cooldown Gap Check (optional)

For a selected pattern_id:

consecutive_signal_dt_gap_bars (min/median/p05)

9) Retest Logic Audit (Critical)
Table: Retest Sample Audit (10 rows)

Randomly sample 10 retest signals across retest-enabled patterns.

Columns:

pattern_id

level_type (ORH/PDH/box_high/etc.)

level_value

break_dt

break_close

retest_dt

retest_touch (YES/NO; shows min_low/max_high vs level±tol)

rebreak_dt

rebreak_close

PASS/FAIL

Pass condition: all PASS.

10) Outcome Calculation Audit (Critical)
Table: Manual Return Sign Audit (10 rows)

Random sample (5 LONG, 5 SHORT) at a chosen horizon (e.g., H=10).

Columns:

pattern_id

dt

direction

entry_ref_price

close_H

computed_ret_ticks_H

expected_sign (derived from prices)

PASS/FAIL

Table: MFE/MAE Audit (10 rows)

Random sample at same horizon.

Columns:

pattern_id

dt

direction

entry_ref_price

max_high_window

min_low_window

mfe_ticks_H

mae_ticks_H

PASS/FAIL (checks MFE>=0 and MAE<=0, or absolute conventions)

11) Aggregation Identity Tests (Must-Pass)
Table: Aggregation Identity (5 patterns)

For 5 random pattern_ids:

pattern_id

signals

sum(ret_ticks_H) from raw

reported_net_ret_ticks_H

diff

PASS/FAIL

Pass condition: diff ~ 0 (within rounding).

12) Tier 0 Smoke Suite (Dedicated Block)
Table: Tier 0 Pattern List

pattern_id (exact)

signals

included/excluded reason (if excluded)

Mini Leaderboard (H=10, min_signals small)

Top rows:

pattern_id

signals

net_ret_ticks_10

avg_ret_ticks_10

win_rate_10

p10/p90

13) Final “Readiness Gate” Checklist

Render a checklist with explicit status:

 Bars monotonic, no duplicates

 Tick windows non-empty in active hours (if ticks used)

 Feature ranges valid (fractions in [0,1], ATR>0)

 VWAP sane (cum vol monotonic, mostly within range)

 Regime distribution non-degenerate (if enabled)

 Pattern IDs deterministic (hash stable)

 Retest audit passes

 Return sign audit passes

 MFE/MAE audit passes

 Aggregation identity passes

Only if all checked → “Approved for Tier 1 sweeps”.

Optional Add-On: “Single Pattern Deep Dive” Panel

For a user-selected pattern_id:

list first 20 signals with dt + features + outcomes

histogram-style summary (p10/p50/p90)

slice by regime and session bucket

This is invaluable for debugging one pattern end-to-end.

Below is a stable naming convention + section ID registry for all PatternEngine + Sweep + Validation components.

This is designed to:

Respect your existing section registry model

Avoid collisions with existing report sections

Be predictable across projects

Group logically in the UI

Scale cleanly as you add ML / Monte Carlo later

🧭 Section Naming Convention Standard
Global Prefix Rule

All PatternEngine-related sections must start with:

pattern_

All validation-only sections must start with:

pattern_validation_

All sweep/leaderboard sections must start with:

pattern_sweep_

This ensures:

Clear registry grouping

No accidental name overlap

Easy filtering in report.yaml

📘 Canonical Section IDs (Stable Across Projects)

Below is the official section list.

I. Core Pattern Sections
1️⃣ Pattern Sweep Leaderboard
pattern_sweep_leaderboard

Title:

Pattern Sweep Leaderboard

Purpose:

Ranks expanded pattern instances

Supports min_signals, top_n, horizon selection

2️⃣ Pattern Drilldown
pattern_sweep_drilldown

Title:

Pattern Drilldown

Purpose:

Deep inspection of one pattern_id

Shows signals + slice breakdowns

3️⃣ Pattern Diagnostics (Expansion Summary)
pattern_sweep_diagnostics

Title:

Pattern Sweep Diagnostics

Purpose:

Shows pattern counts pre/post pruning

Cap enforcement

Baseline coverage

Linked constraint verification

II. Validation Suite Sections

These are strictly for correctness.

4️⃣ Bar Data Integrity
pattern_validation_bars

Title:

Pattern Validation — Bar Data Integrity
5️⃣ Tick Data Integrity
pattern_validation_ticks

Title:

Pattern Validation — Tick Data Integrity
6️⃣ Feature Sanity (Bar Geometry + ATR)
pattern_validation_features

Title:

Pattern Validation — Feature Sanity
7️⃣ VWAP + Session Sanity
pattern_validation_vwap_session

Title:

Pattern Validation — VWAP & Session Features
8️⃣ Regime Diagnostics
pattern_validation_regimes

Title:

Pattern Validation — Regime Diagnostics
9️⃣ Retest Audit
pattern_validation_retest

Title:

Pattern Validation — Retest Audit
🔟 Outcome Audit (Returns + MFE/MAE)
pattern_validation_outcomes

Title:

Pattern Validation — Outcome Calculations
11️⃣ Aggregation Identity Check
pattern_validation_aggregation

Title:

Pattern Validation — Aggregation Identity
12️⃣ Tier 0 Smoke Suite
pattern_validation_tier0

Title:

Pattern Validation — Tier 0 Smoke Suite
13️⃣ Final Readiness Gate
pattern_validation_readiness_gate

Title:

Pattern Validation — Readiness Gate

Purpose:

Checklist summary

Pass/Fail indicators

III. Optional Advanced Sections (Future-Safe)

These are optional but recommended for later.

Pattern Robustness by Regime
pattern_sweep_regime_breakdown
Pattern Robustness by Time-of-Day
pattern_sweep_time_breakdown
Pattern Robustness Across Runs
pattern_sweep_run_comparison
Pattern Stability (Parameter Neighborhood Stability)
pattern_sweep_stability

Purpose:

Show performance sensitivity to adjacent parameter values

🧱 Section Ordering Standard

In report.yaml (or equivalent config), sections should appear in this order:

1. Validation Block (Top)

pattern_validation_bars

pattern_validation_ticks

pattern_validation_features

pattern_validation_vwap_session

pattern_validation_regimes

pattern_validation_retest

pattern_validation_outcomes

pattern_validation_aggregation

pattern_validation_tier0

pattern_validation_readiness_gate

2. Sweep Diagnostics

pattern_sweep_diagnostics

3. Sweep Results

pattern_sweep_leaderboard

pattern_sweep_regime_breakdown

pattern_sweep_time_breakdown

pattern_sweep_run_comparison

pattern_sweep_stability

pattern_sweep_drilldown

This gives a clean logical progression:

Data → Features → Signals → Outcomes → Aggregation → Results

🧩 Option Key Naming Standard (for report options)

To keep option names stable across sessions, use these canonical keys:

tier
families
structures
directions
regimes
horizons
min_signals
top_n
requires_ticks
pattern_id   # for drilldown
sample_size  # for validation random sampling
tw_seconds   # tick window override if needed

Never invent new option names unless extending the spec.

🧠 Deterministic ID Rule Reminder

Pattern IDs must always:

Sort parameters alphabetically inside Q[]

Use consistent float formatting (e.g., 2 decimal places for d, r)

Use uppercase family/structure/direction/regime

Never include whitespace

Example canonical form:

BRK.ORB.Q[r=1.75,rt=2,v=85,w=5].LONG.EXP

Not:

BRK.ORB.Q[w=5,v=85,rt=2,r=1.75].LONG.EXP   ❌

Parameter order must follow token ordering rule defined earlier.

🚦 Recommended Implementation Sequence

If a new ChatGPT session is implementing this:

Implement Tier 0 only.

Implement validation sections.

Verify readiness gate passes.

Expand to Tier 1.

Only then enable large grids.

🔒 Why This Matters

Without stable section IDs and ordering:

Future ChatGPT sessions may:

Recompute features in sections

Break determinism

Misplace validation blocks

Overlook pruning rules

Re-introduce lookahead bias

This naming registry prevents architectural drift.
