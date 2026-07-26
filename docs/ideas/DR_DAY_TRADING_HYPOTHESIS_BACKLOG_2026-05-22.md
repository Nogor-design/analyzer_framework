# Deep Research Day-Trading Hypothesis Backlog - 2026-05-22

This file is a review artifact. It does not register hypotheses, mutate the
research ledger, or claim edge. Every item below still needs normal
pre-registration, duplicate/graveyard checks, fast probe, hardening,
slippage/delay stress, locked holdout, shadow observation, and Sim101 paper
testing before it can be treated as a candidate strategy.

## Run Notes

Requested task: run Deep Research to find possible day-trading hypotheses and
continue after analyzing results to discover additional hypotheses.

Local Deep Research status:

- Attempted command:
  `D:\local-deep-research\local-deep-research\scripts\ldr-research.py`
- Default OpenRouter path failed because `OPENROUTER_API_KEY` is not set.
- Local Ollama is available at `127.0.0.1:11434`.
- SearXNG is not running at `127.0.0.1:8080`.
- Ollama + DuckDuckGo LDR run was attempted and timed out after 15 minutes.
- This artifact therefore uses a direct web-research fallback, keeping the same
  conservative intake standard used by the Local Deep Research importer.

## Executive Summary

The most promising new directions are not brand-new "magic" entries. They are
testable structure around existing families:

- Opening-drive continuation after the first 5-15 minute impulse.
- Intraday momentum from early-session imbalance into late-session continuation.
- Overnight-gap and prior-settlement reaction variants.
- VWAP standard-deviation band behavior as a regime/filter layer.
- RVOL and time-of-day participation filters.
- ES/NQ confirmation or divergence filters.
- Market-internals filters for ES/NQ where external breadth data is available.
- Execution/risk policies for prop-firm constraints: one-and-done, stop-after-
  loss, volatility-scaled sizing, lunch pause, and late-day lockout.

Highest-confidence sources are academic or platform documentation. Retail
community sources are useful for mechanism vocabulary only and should not be
used as evidence of profitability.

## Source Trail

Higher-confidence anchors:

- IEEE Access ORB paper: `https://bip.imsi.athenarc.gr/site/details?id=10.1109%2Faccess.2019.2899177`
- Market Intraday Momentum SSRN: `https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866`
- Intraday momentum SPY SSRN paper: `https://papers.ssrn.com/sol3/Delivery.cfm/4824172.pdf?abstractid=4824172&mirid=1`
- Overnight returns and first/last 30-minute futures/ETF relation: `https://www.sciencedirect.com/science/article/pii/S1059056016301563`
- Intraday correlation patterns: `https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1686703_code817068.pdf?abstractid=1677915&mirid=1&type=2`
- NinjaTrader Order Flow VWAP docs: `https://ninjatrader.com/support/helpGuides/nt8/order_flow_vwap.htm`
- NinjaTrader Order Flow Cumulative Delta docs: `https://ninjatrader.com/support/helpGuides/nt8/order_flow_cumulative_delta.htm`
- NinjaTrader Order Flow Volume Profile docs: `https://ninjatrader.com/support/helpGuides/nt8/order_flow_volume_profile.htm`
- NinjaTrader Order Flow Volumetric Bars docs: `https://ninjatrader.com/support/helpGuides/nt8/order_flow_volumetric_bars2.htm`
- VWAP execution theory: `https://arxiv.org/abs/1605.03683`

Medium/low-confidence implementation vocabulary:

- Relative volume in futures: `https://nexusfi.com/a/concepts/relative-volume-rvol-futures-trading`
- Keltner futures pullback description: `https://www.tradingfutures.be/futures-trading/trading-strategieen/keltner-trend-pullback`
- Keltner channel description: `https://commodity.com/technical-analysis/keltner-channel/`
- Donchian channel strategy description: `https://www.systemtrader.co/stocks/channel`
- Market internals for futures: `https://traderverdict.com/traders-playbook/market-internals-futures/`
- Market internals overview: `https://united-daytraders.com/blog/market-internals-trading`
- ES market internals context: `https://www.mooretechllc.com/algorithmic-trading/es-day-trading-strategy-market-internals/`
- MNQ ORB OOS discussion, anecdotal only: `https://www.reddit.com/r/Daytrading/comments/1rrn609/i_tested_the_opening_range_breakout_on_mnq/`
- Slippage discussion, anecdotal only: `https://www.reddit.com/r/algotrading/comments/1szbixe/slippage_assumption_emini_backtesting/`

## Candidate Ideas

### 1. Opening Drive Pullback Continuation

- Category: opening range / pullback / trend following
- Family: `trend_pullback_continuation`
- Core hypothesis: a strong first 5-15 minute directional drive followed by a
  shallow pullback to VWAP, open, or EMA20 has continuation potential because
  late countertrend traders are trapped.
- Entry: after RTH open, require first 5-15 minute range directional close above
  VWAP/open for long or below for short; enter on first pullback within
  `pullback_max_ticks` of VWAP or EMA20 that closes back in drive direction.
- Exit/risk: stop beyond pullback swing or fixed 20-40 NQ ticks; target 1.5-3R;
  time exit by 09:30 Denver.
- Data: OHLCV, VWAP, EMA.
- Conditions: best on high RVOL trend opens; worst on two-sided chop.
- Timeframe: 1m, 2m, 5m.
- Source confidence: medium.
- Robustness concerns: overlaps with ORB; must prove pullback reference adds
  value.

### 2. First-Half-Hour To Last-Half-Hour Momentum

- Category: time-of-day / statistical
- Family: proposed `intraday_momentum_close_drive`
- Core hypothesis: early market imbalance predicts late-session continuation
  when institutional rebalancing or late-informed trading reinforces the move.
- Entry: compute return from prior close to 08:00 Denver or first 30 minutes of
  RTH; enter in same direction during final 30-45 minutes only when intraday
  realized volatility and RVOL are above baseline.
- Exit/risk: close all before session end; stop at 0.4-0.8x prior 30-minute ATR.
- Data: OHLCV; optionally ETF/cash index confirmation.
- Conditions: best on broad directional days; worst on mean-reverting close.
- Timeframe: 5m, 15m.
- Source confidence: high for pattern existence in SPY literature, medium for
  futures transfer.
- Robustness concerns: closing liquidity, contract roll, and event-day behavior.

### 3. Overnight Gap Fade / Follow-Through Classifier

- Category: time-of-day / prior close reaction
- Family: `prior_close_settlement_reaction`
- Core hypothesis: overnight gap direction has different first-30-minute and
  final-30-minute behavior depending on gap size, RVOL, and opening drive.
- Entry: if RTH open is above/below prior settlement by `distance_ticks`, trade
  fade when first 5m fails to extend and continuation when first 5m closes beyond
  open in gap direction with high RVOL.
- Exit/risk: fixed stop around open wick; target prior settlement for fade or
  morning measured move for continuation.
- Data: OHLCV, prior settlement/close, RVOL.
- Conditions: best on moderate gaps; worst on major macro/news days unless
  explicitly stratified.
- Timeframe: 1m-5m.
- Source confidence: medium-high.
- Robustness concerns: needs separate gap buckets and news filters.

### 4. VWAP Standard-Deviation Band Rejection

- Category: VWAP / mean reversion
- Family: `vwap_reject_fade`
- Core hypothesis: VWAP deviation bands act as dynamic auction extremes; a wick
  through 1-2 standard-deviation bands that closes back inside can identify
  failed continuation.
- Entry: price touches VWAP +1/+2 band and closes below band for short, or
  touches -1/-2 and closes above band for long; require bounded distance from
  VWAP and no strong trend-day filter.
- Exit/risk: target VWAP or half-distance to VWAP; stop beyond rejection wick.
- Data: VWAP and standard-deviation bands; NT Order Flow VWAP can provide this.
- Conditions: best in range or balanced days; worst in clean trend days.
- Timeframe: 1m-5m.
- Source confidence: medium.
- Robustness concerns: requires exact VWAP reset/session parity.

### 5. VWAP Band Compression Then Expansion

- Category: VWAP / volatility expansion
- Family: `compression_then_expansion`
- Core hypothesis: when price spends several bars inside VWAP +/-1 stdev, a
  close outside the band with RVOL expansion may mark transition from balance to
  initiative trade.
- Entry: require `n` bars contained inside VWAP band and narrow realized range;
  enter on close outside band with RVOL above threshold.
- Exit/risk: stop back inside band or at VWAP; target 1.5-3x band width.
- Data: OHLCV, VWAP bands, RVOL.
- Conditions: best after morning balance; worst before scheduled news.
- Timeframe: 2m-5m.
- Source confidence: medium.
- Robustness concerns: may duplicate generic compression breakout; test VWAP
  containment versus price-only compression.

### 6. Keltner Pullback In ADX Trend

- Category: pullback / trend following
- Family: `trend_pullback_continuation`
- Core hypothesis: in high-ADX trend regimes, pullbacks to Keltner midline/EMA
  resume because countertrend liquidity is absorbed.
- Entry: ADX above threshold; channel slope aligned; enter when price pulls back
  to midline/EMA and closes back toward outer band.
- Exit/risk: stop beyond opposite side of pullback; target outer Keltner band or
  ATR multiple.
- Data: OHLCV, EMA, ATR, ADX.
- Conditions: best on trend days; worst on low-ADX chop.
- Timeframe: 5m, 15m.
- Source confidence: medium.
- Robustness concerns: ADX threshold optimization risk.

### 7. Keltner Squeeze Breakout

- Category: volatility contraction / breakout
- Family: `compression_then_expansion`
- Core hypothesis: unusually narrow ATR bands followed by a close outside the
  channel marks transition from contraction to expansion.
- Entry: Keltner width percentile below threshold for `n` bars; enter on close
  above upper or below lower band with range expansion.
- Exit/risk: stop at midline; target 1.5-2.5x ATR.
- Data: OHLCV, EMA, ATR.
- Conditions: best after compression; worst during lunch drift without RVOL.
- Timeframe: 5m.
- Source confidence: medium.
- Robustness concerns: many false breaks; requires random-time breakout
  baseline.

### 8. Donchian Re-Entry Breakout

- Category: breakout / volatility expansion
- Family: proposed `donchian_reentry_breakout`
- Core hypothesis: requiring price to re-enter a Donchian channel before taking
  a new breakout reduces repeated entries in already mature trends.
- Entry: close above `N`-bar high after at least one close back inside channel
  since prior breakout; optional RVOL filter.
- Exit/risk: stop at channel midline or `N`-bar opposite low; time stop after
  6-12 bars.
- Data: OHLCV.
- Conditions: best on initiative trend days; worst in narrow ranges.
- Timeframe: 5m, 15m.
- Source confidence: medium.
- Robustness concerns: lookback sensitivity.

### 9. RVOL-Confirmed ORB

- Category: opening range / filter
- Family: `orb_breakout`
- Core hypothesis: ORB signals with abnormal opening participation are more
  likely to follow through than low-participation breaks.
- Entry: standard ORB break close; require opening range volume divided by
  rolling same-time baseline above threshold.
- Exit/risk: existing ORB stop/target; add no-second-trade-after-loss rule.
- Data: OHLCV with same-time volume baselines.
- Conditions: best on high participation opens; worst on low-volume holidays.
- Timeframe: 1m-5m.
- Source confidence: medium.
- Robustness concerns: volume baselines must be time-of-day normalized.

### 10. Pullback Volume Contraction Resumption

- Category: pullback / volume
- Family: `trend_pullback_continuation`
- Core hypothesis: strong impulse on high RVOL followed by pullback on declining
  volume and resumption bar on renewed RVOL separates trend continuation from
  chop.
- Entry: impulse bar/range over threshold with RVOL high; 2-5 bar pullback with
  lower volume; enter on close breaking pullback high/low.
- Exit/risk: stop behind pullback; target impulse measured move.
- Data: OHLCV, RVOL.
- Conditions: best in trend morning; worst on news spike exhaustion.
- Timeframe: 1m-5m.
- Source confidence: medium.
- Robustness concerns: impulse/pullback definitions can overfit.

### 11. ES/NQ Confirmation Filter

- Category: intermarket / filter
- Family: filter-only
- Core hypothesis: index-futures breakouts are more robust when ES and NQ move
  together; failed confirmation may identify fragile moves.
- Entry: no standalone entry. Permit NQ long breakout only if ES also closes
  above its comparable reference within `m` bars, and vice versa.
- Exit/risk: tighten or skip when divergence persists.
- Data: synchronized ES and NQ OHLCV.
- Conditions: best during RTH equity index participation; worst during
  tech-specific news.
- Timeframe: 1m-5m.
- Source confidence: low-medium.
- Robustness concerns: can filter out legitimate leadership days.

### 12. ES/NQ Divergence Reversion

- Category: intermarket / mean reversion
- Family: proposed `index_pair_divergence_reversion`
- Core hypothesis: short-horizon ES/NQ divergence after a shared reference break
  can revert when one market overextends without broad index confirmation.
- Entry: ES and NQ start aligned; one makes new session high/low while the other
  fails; fade the leader after reclaim back inside reference.
- Exit/risk: target pair spread mean or VWAP; stop if laggard confirms.
- Data: synchronized ES/NQ OHLCV.
- Conditions: best outside single-sector news; worst when Nasdaq-specific
  catalyst drives true leadership.
- Timeframe: 1m-5m.
- Source confidence: low-medium.
- Robustness concerns: requires careful correlation-regime tagging.

### 13. Market Internals Breadth Confirmation

- Category: market internals / filter
- Family: filter-only
- Core hypothesis: ES/NQ trend trades need breadth confirmation; price breakouts
  against ADD/VOLD/TICK are more fragile.
- Entry: no standalone entry. Permit long ES/NQ breakout when TICK regime,
  advance/decline slope, and up/down volume breadth align.
- Exit/risk: flatten or reduce when breadth diverges sharply.
- Data: external market internals symbols.
- Conditions: best on broad equity sessions; worst when mega-cap concentration
  drives NQ alone.
- Timeframe: 1m-5m.
- Source confidence: medium for context, low for exact thresholds.
- Robustness concerns: external data dependency and symbol mapping.

### 14. TICK Exhaustion Fade At Reference

- Category: market internals / exhaustion
- Family: `exhaustion_into_reference`
- Core hypothesis: extreme NYSE TICK into prior high/low, VWAP band, or OR
  boundary can mark short-horizon exhaustion when price fails to close through.
- Entry: TICK extreme above/below threshold at reference; enter opposite after
  rejection close.
- Exit/risk: target VWAP/reference midpoint; stop beyond extreme.
- Data: OHLCV plus TICK.
- Conditions: best for ES; less direct for NQ.
- Timeframe: 1m.
- Source confidence: low-medium.
- Robustness concerns: TICK threshold and data vendor differences.

### 15. Volume Profile Value-Area Rejection

- Category: volume profile / mean reversion
- Family: proposed `volume_profile_value_area_rejection`
- Core hypothesis: prior session value-area high/low attracts response when
  price probes outside value and fails to accept.
- Entry: price trades above prior VAH then closes back inside for short, or
  below VAL then closes back inside for long.
- Exit/risk: target prior POC or opposite value edge; stop outside probe wick.
- Data: session volume profile.
- Conditions: best on balanced days; worst on initiative trend days.
- Timeframe: 5m.
- Source confidence: medium for platform availability, low for edge evidence.
- Robustness concerns: needs exact profile construction parity.

### 16. Naked POC Magnet

- Category: volume profile / reference reaction
- Family: proposed `naked_poc_magnet`
- Core hypothesis: untested prior high-volume nodes can act as intraday magnets
  when price enters their attraction zone.
- Entry: when price enters within `x` ticks of untested prior POC and current
  session is balanced, enter toward POC.
- Exit/risk: target POC; stop outside attraction zone; no trade on trend day.
- Data: prior session volume profile.
- Conditions: best in balance; worst in news/trend days.
- Timeframe: 5m-15m.
- Source confidence: low-medium.
- Robustness concerns: strong selection bias in choosing POCs.

### 17. Prior Settlement Reclaim Continuation

- Category: prior close reaction / continuation
- Family: `prior_close_settlement_reaction`
- Core hypothesis: after opening away from settlement, reclaiming settlement
  and holding it may trigger continuation as overnight inventory is forced out.
- Entry: gap away from settlement; price crosses settlement; enter on retest
  hold in reclaim direction.
- Exit/risk: stop on failed retest; target opening range opposite side or ATR.
- Data: OHLCV, settlement/prior close.
- Conditions: best on moderate gaps; worst on flat opens.
- Timeframe: 1m-5m.
- Source confidence: medium.
- Robustness concerns: settlement versus prior RTH close semantics.

### 18. Lunch Lull No-Trade Filter

- Category: time-of-day / filter
- Family: filter-only
- Core hypothesis: midday low participation degrades breakout and pullback
  expectancy.
- Entry: no standalone entry. Suppress new trades during low-RVOL lunch window
  unless volatility expansion threshold is met.
- Exit/risk: force time exit before low-liquidity window for scalps.
- Data: time, RVOL.
- Conditions: useful as a global filter.
- Timeframe: all intraday.
- Source confidence: medium.
- Robustness concerns: can remove good trend-day continuation.

### 19. Closing-Hour Continuation Only Filter

- Category: time-of-day / filter
- Family: filter-only
- Core hypothesis: late-day volatility is more directional when early imbalance
  and current breadth agree; otherwise late entries are noise.
- Entry: permit closing-hour trades only if early direction, VWAP side, and
  realized volatility align.
- Exit/risk: hard flat before session close.
- Data: OHLCV, VWAP; optional internals.
- Conditions: best on trend days; worst on option-expiry chop.
- Timeframe: 5m.
- Source confidence: medium.
- Robustness concerns: close auction dynamics do not map perfectly to futures.

### 20. One-And-Done Prop Constraint

- Category: risk management
- Family: risk-only
- Core hypothesis: for prop-firm constraints, stopping after first qualified
  win or after one loss may improve drawdown/consistency even if raw expectancy
  falls.
- Entry: no standalone entry. Apply to ORB/opening-drive families.
- Exit/risk: if first trade reaches `min_win_ticks`, disable for day; if first
  trade loses, disable for day.
- Data: trade outcomes.
- Conditions: best with high-quality morning setups.
- Timeframe: session policy.
- Source confidence: low-medium.
- Robustness concerns: may underuse real edge; test net drawdown and
  consistency not just PnL.

### 21. Volatility-Scaled Position Size

- Category: risk management
- Family: risk-only
- Core hypothesis: sizing by realized ATR/range reduces NQ drawdown spikes and
  makes ES/NQ risk comparable.
- Entry: no standalone entry.
- Exit/risk: size = target dollar risk / stop distance; cap contracts; skip if
  stop distance exceeds policy.
- Data: ATR, instrument tick value.
- Conditions: global.
- Timeframe: all.
- Source confidence: medium.
- Robustness concerns: micro contracts may be required for smooth sizing.

### 22. Slippage-Aware Limit-To-Market Fallback

- Category: execution / risk management
- Family: risk-only
- Core hypothesis: some candidates fail because passive limit fills are
  unrealistic; require trade-through for limits or convert stale limits to
  market only when adverse selection is bounded.
- Entry: no standalone entry.
- Exit/risk: limit must trade through by `x` ticks; cancel after `n` bars; never
  assume queue fill at touched price.
- Data: tick or high-resolution intrabar data preferred.
- Conditions: global.
- Timeframe: execution policy.
- Source confidence: medium.
- Robustness concerns: needs NT high order fill resolution/tick data.

### 23. News/Event Avoidance Filter

- Category: news / filter
- Family: filter-only
- Core hypothesis: scheduled macro events create discontinuous volatility that
  invalidates OHLCV-derived intraday patterns.
- Entry: suppress entries `x` minutes before/after CPI, FOMC, NFP, Powell, and
  major rate events.
- Exit/risk: flatten or widen only under explicit test; default is avoid.
- Data: event calendar.
- Conditions: global for NQ/ES.
- Timeframe: all.
- Source confidence: high as risk control, low as edge.
- Robustness concerns: external calendar dependency.

### 24. Contract-Rollover Liquidity Filter

- Category: data quality / filter
- Family: filter-only
- Core hypothesis: strategy behavior changes around rollover and low-liquidity
  contracts; avoiding or normalizing rollover windows reduces false evidence.
- Entry: suppress trading near rollover unless active contract volume dominates.
- Exit/risk: global data policy.
- Data: contract volume/open interest if available.
- Conditions: futures only.
- Timeframe: all.
- Source confidence: medium.
- Robustness concerns: requires accurate continuous-contract rules.

### 25. Consecutive Adverse Excursion Pause

- Category: risk management / edge decay
- Family: risk-only
- Core hypothesis: repeated early adverse excursion on a candidate indicates
  local regime mismatch before full stop-outs accumulate.
- Entry: no standalone entry. Pause candidate for day/week after `k` trades with
  MAE above threshold even if final outcomes are mixed.
- Exit/risk: automatic disable and manager review.
- Data: trade path MAE/MFE.
- Conditions: paper/shadow/live monitoring.
- Timeframe: runtime policy.
- Source confidence: medium as risk control.
- Robustness concerns: can pause strategies that recover late by design.

## Additional Hypotheses Discovered After Analysis

These are second-order ideas created by combining the research trail with the
existing ta_foundation loop.

1. ORB quality score: combine OR width percentile, opening RVOL, VWAP side, and
   ES/NQ confirmation to decide whether ORB candidates should be breakout,
   failure-reclaim, or no-trade.
2. VWAP regime switch: classify session as VWAP mean-reversion, VWAP
   continuation, or VWAP irrelevant based on first-hour VWAP slope, band width,
   and number of crosses.
3. Reference-level hierarchy: when prior high/low, overnight high/low, OR
   boundary, settlement, and VWAP cluster within `x` ticks, test whether
   reactions are stronger but also more crowded/slippage-sensitive.
4. Trend-day no-fade rule: disable all reference fades when price holds one side
   of VWAP for `n` bars and opening RVOL remains high.
5. Balanced-day no-breakout rule: disable breakouts when first-hour range is
   narrow, VWAP crosses are frequent, and RVOL decays below baseline.
6. Paper-trade health gate: promote to longer Sim101 only when rejects,
   slippage, and protective-order events remain inside policy for `n` sessions.

## Top 12 Pre-Registration Shortlist

1. Opening Drive Pullback Continuation
   - Family: `trend_pullback_continuation`
   - Claim: first impulse plus shallow VWAP/EMA pullback beats unconditional
     pullback entries after cost.
   - Adverse tests: no-RVOL baseline, delayed entry, first 15 vs 30 minutes,
     ES vs NQ split.

2. RVOL-Confirmed ORB
   - Family: `orb_breakout`
   - Claim: same-time normalized opening RVOL improves ORB follow-through.
   - Adverse tests: RVOL threshold grid, low-volume days, news exclusion,
     1-bar delay.

3. VWAP Band Compression Then Expansion
   - Family: `compression_then_expansion`
   - Claim: VWAP-band containment adds value beyond generic narrow range.
   - Adverse tests: price-only compression baseline, lunch-only split,
     RVOL ablation.

4. VWAP Standard-Deviation Band Rejection
   - Family: `vwap_reject_fade`
   - Claim: band rejection outperforms unbounded VWAP fade after slippage.
   - Adverse tests: trend-day exclusion, band level split, distance bucket,
     side split.

5. Overnight Gap Fade / Follow-Through Classifier
   - Family: `prior_close_settlement_reaction`
   - Claim: gap behavior separates into fade/continuation by first 5m extension
     and RVOL.
   - Adverse tests: gap buckets, macro-day exclusion, settlement semantics,
     ES/NQ split.

6. ES/NQ Confirmation Filter
   - Family: filter-only
   - Claim: requiring peer-index confirmation reduces failed breakout rate.
   - Adverse tests: leadership-day split, tech-news days, confirmation lag grid.

7. Keltner Pullback In ADX Trend
   - Family: `trend_pullback_continuation`
   - Claim: trend-regime pullbacks to Keltner midline/EMA outperform generic
     pullbacks.
   - Adverse tests: ADX threshold grid, no-ADX baseline, lunch exclusion.

8. Prior Settlement Reclaim Continuation
   - Family: `prior_close_settlement_reaction`
   - Claim: reclaim/hold of settlement after gap has continuation behavior.
   - Adverse tests: fade baseline, gap direction split, no-retest baseline.

9. Market Internals Breadth Confirmation
   - Family: filter-only
   - Claim: breadth-aligned ES/NQ trades outperform price-only trades.
   - Adverse tests: no-internals baseline, ES-only vs NQ-only, data-vendor
     sensitivity.

10. One-And-Done Prop Constraint
    - Family: risk-only
    - Claim: morning strategy with first-win/first-loss stop improves drawdown
      and consistency metrics.
    - Adverse tests: raw expectancy loss, missed trend days, account-rule
      variants.

11. Slippage-Aware Limit-To-Market Fallback
    - Family: risk-only
    - Claim: trade-through limit accounting and stale-limit cancellation improve
      research/live parity.
    - Adverse tests: touched-limit baseline, tick-data availability, market
      fallback slippage ladder.

12. Volume Profile Value-Area Rejection
    - Family: proposed `volume_profile_value_area_rejection`
    - Claim: prior value edge rejection adds reference value beyond generic
      prior high/low fades.
    - Adverse tests: profile construction parity, trend-day exclusion, POC
      target vs fixed target.

## Intake Recommendation

Immediate queue:

1. Run duplicate/graveyard checks on the first six shortlist items.
2. Author only two or three registry-compatible hypotheses in the next session
   quota.
3. Treat filter/risk ideas as overlays or manager-policy experiments before
   making new families.
4. Defer order-flow, volume-profile, and market-internals candidates until data
   availability and NinjaTrader parity are explicitly confirmed.

Suggested first pass:

- Opening Drive Pullback Continuation
- RVOL-Confirmed ORB
- VWAP Band Compression Then Expansion

## Guarded Authoring Dry-Run

Prepared proposal artifact:

- `docs/ideas/author_proposals_dr_20260522.json`

Dry-run command:

```bash
python -m ta_foundation.research_intake.author_from_intake \
  --author-json docs\ideas\author_proposals_dr_20260522.json \
  --max-proposals 3 \
  --dry-run \
  --write-report-json output\research_intake\DR_20260522_author_dry_run.json
```

Result:

- `requested=3`
- `parsed=3`
- `accepted=3`
- `rejected=0`
- `quota_remaining_week=17`

Accepted in dry-run only:

- `trend_pullback_continuation` / NQ / 5m
- `compression_then_expansion` / NQ / 5m
- `vwap_reject_fade` / NQ / 5m

No real ledger rows were created. The dry-run used a temporary copied ledger and
discarded generated inbox drafts after reporting.
