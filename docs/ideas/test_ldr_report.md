# Local Deep Research Report: Opening Range Breakout (ORB) Failure Reclaim Analysis

## Executive Summary
This report analyzes the behavior of Opening Range Breakouts (ORBs) on high-beta equity index futures, focusing specifically on failure-reclaim mechanics. In fast-moving regimes like Nasdaq (NQ) index futures, early breakout attempts often fail to attract sufficient continuation volume, trapping breakout buyers or sellers.

## Strategy Description: ORB Failure Reclaim
When an initial breakout above or below the 5-minute opening range is rejected and price reclaims back inside the opening range boundaries, it indicates trapped momentum. This reclaim forces late-entry breakout chasers to exit their positions, creating a rapid acceleration/squeeze in the opposite direction. 

To improve entry prices and avoid chasing the immediate reversal, a pullback target is set to the body-midpoint fill of the breakout bar.

- **Instrument**: NQ (Nasdaq Futures)
- **Timeframe**: 5m
- **Session Window**: Denver Time 07:30 to 08:30 (New York Session Open)
- **Direction**: Both (Long/Short)

### Proposed Parameters
- `orb_minutes`: 5
- `sweep_min_ticks`: 4
- `reclaim_within_bars`: 2
- `fill_mode`: "body_midpoint"
- `stop_ticks`: 24
- `target_ticks`: 80

## Mechanism and Falsification
The core market-microstructure theory is that opening-hour breakouts are heavily populated by retail momentum participants. A failure-reclaim event creates a liquidity vacuum. 

To falsify this strategy, we will test whether this body-midpoint fill pullback variant produces positive expectancy against a naive one-bar delayed entry or unconditional breakout strategy after accounting for realistic execution friction and slip on NQ.

## Appendix: Source Notes
- Standard Profile profiles show resting liquidity pools right above/below the early opening range.
- Prior retail-centric TradingView scripts or Reddit threads claim >55% win rates, but those calculations ignore transaction costs and execution delay.
