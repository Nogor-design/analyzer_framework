PantheonMasterBotV01TesterV2
User Behavior Document
1) Strategy overview

PantheonMasterBotV01TesterV2 is a moving-average crossover strategy that trades from the relationship between three simple moving averages:

Fast SMA
Slow SMA
Trend SMA

The strategy runs OnBarClose, meaning signals are evaluated when each bar closes, not intrabar.

At a high level:

a cross above of the Fast SMA over the Slow SMA is the bullish signal
a cross below of the Fast SMA under the Slow SMA is the bearish signal
the optional Trend filter controls whether those signals are allowed
the optional Reverse setting flips the direction of the actual trade
the strategy can use a fixed stop loss and fixed profit target
it can be restricted to a trading time window
it can stop itself after hitting daily/session profit, loss, or trade-count limits
it also has an optional kill switch based on current PnL
2) Core signal logic

The strategy calculates:

Fast SMA using averageFast
Slow SMA using averageSlow
Trend SMA using averageTrend

It then looks for two crossover events:

Bullish cross: CrossAbove(FAST, SLOW, 1)
Bearish cross: CrossBelow(FAST, SLOW, 1)

Those crossovers are the main events that drive entries and, in practice, can also cause reversals.

3) Direction settings: Long and Short

The strategy has two direction filters:

Long
Short

These determine which crossover signals are allowed to create trades.

If both Long and Short are enabled
bullish cross can create a long-side action
bearish cross can create a short-side action
If only Long is enabled
only bullish cross signals are allowed to trigger trades
bearish cross signals do not create new short entries
If only Short is enabled
only bearish cross signals are allowed to trigger trades
bullish cross signals do not create new long entries
4) Reverse setting

The Reverse checkbox flips the entry direction.

Normal mode: Reverse = false
bullish cross → long entry
bearish cross → short entry
Reverse mode: Reverse = true
bullish cross → short entry
bearish cross → long entry

This means the crossover signal itself stays the same, but the actual order submitted is reversed.

5) Trend filter behavior
written as if the Trend logic is fixed

The strategy includes:

UseTrend
UseTrendReverse

Assuming the Trend filter is fixed to compare price against the actual Trend SMA:

If UseTrend = true and UseTrendReverse = false
bullish trades are allowed only when price is on the bullish side of the Trend SMA
bearish trades are allowed only when price is on the bearish side of the Trend SMA

In practical terms:

long signals are allowed only when price is above the Trend SMA
short signals are allowed only when price is below the Trend SMA
If UseTrend = true and UseTrendReverse = true

the filter is inverted:

long signals are allowed only when price is below the Trend SMA
short signals are allowed only when price is above the Trend SMA
If UseTrend = false

the Trend filter is ignored and only the Fast/Slow crossover matters.

6) Fixed stop loss and fixed profit target

In State.DataLoaded, the strategy sets:

SetStopLoss("", CalculationMode.Ticks, MaxStop, false) when UseMaxStop = true
SetProfitTarget("", CalculationMode.Ticks, floor(MaxStop * MaxTPRatio)) when UseMaxTP = true
What that means

Each new trade can have:

a fixed stop loss in ticks
a fixed profit target in ticks

Example with defaults:

MaxStop = 200
MaxTPRatio = 0.5

So target = floor(200 × 0.5) = 100 ticks.

7) Important real-world behavior: crossover reversals can happen before stop or target

This is the key clarification you pointed out.

NinjaTrader strategies do not hold a long and short position at the same time on the same instrument/account in this setup. If the strategy is long and then submits a short entry, NinjaTrader will flatten the long and establish the short position. Likewise, if it is short and then submits a long entry, it will flatten the short and establish the long.

Because of that:

a trade does not have to reach its fixed stop loss
and it does not have to reach its fixed profit target

If an opposite valid entry signal occurs first, the current trade can be closed and reversed by the platform’s managed order handling. The strategy is explicitly set up with EntriesPerDirection = 1, so it is not stacking positions in the same direction; it is functioning as a one-position managed strategy.

Practical meaning

When both directions are enabled, the strategy often behaves like a reversing crossover system:

bullish cross can put it long
later bearish cross can take it out of long and put it short
later bullish cross can take it out of short and put it long again

So the stop and target are always active if enabled, but they are not guaranteed to be the reason the trade ends.

8) One-direction-only behavior

This is the unique behavior you described, and it matches how the logic behaves in practice.

Case A: only one direction selected, MaxTrades = 1

If only one direction is enabled:

Long = true, Short = false, or
Long = false, Short = true

and MaxTrades = 1, then the strategy will typically take one trade in that enabled direction only. After that first qualifying trade, it will not continue taking additional entries because the max-trades guard will stop further trade actions after the trade count threshold is reached. The trade then behaves like a more traditional fixed-stop / fixed-target trade unless another strategy control forces an exit. The cross-count guard is checked on crossover events and can disable trading once the threshold is reached.

If Reverse is checked in this one-direction / one-trade setup, then that single allowed signal still occurs only from the enabled side’s crossover logic, but the actual order submitted is reversed. So it still becomes a one-trade fixed stop / target style setup, just inverted in direction.

Case B: only one direction selected, MaxTrades > 1

This is where the behavior becomes less obvious.

If only one direction is enabled, and MaxTrades is greater than 1, then:

each qualifying crossover for that enabled side increments the internal trades counter
once the internal trade count reaches the maximum, the next crossover event triggers the max-trades guard
at that point the strategy calls ExitShort() and ExitLong(), disables trading, and stops processing further entries
Practical result

In a one-direction-only setup with multiple allowed trades:

the strategy keeps counting valid crossover events for that enabled side
while a position is open, if another crossover event occurs before stop or target is hit, the strategy can be forced flat when the max-trade threshold is reached
so the final exit may happen on a crossover/max-trade event instead of at the fixed stop or fixed target

That is why this mode does not always behave like “enter once and wait only for stop or target.”
Instead, with one direction enabled and MaxTrades > 1, the crossover count becomes part of the exit behavior.

9) How MaxTrades actually works

The strategy tracks trades in two ways:

session trade count from SystemPerformance.AllTrades.Count
internal counter trades++ when an entry signal is taken

There is a specific guard:

whenever a crossover happens, if MaxTrades <= trades
the strategy exits any open position
prints a message
sets allowTrading = false
returns without taking more trades

So MaxTrades is not just a passive limit on future entries. In crossover situations, it can actively cause the strategy to flatten.

10) Time filter behavior

The strategy has:

UseTimeFilter
start hour/minute
duration hour/minute

It calculates a start time and end time, and if the current bar is outside that range:

it does not evaluate new entries
if a position is already open, it checks for a fresh crossover
if a crossover occurs while outside the time window, it exits the position and returns
Practical meaning

Outside the time window:

no new trades are opened
an existing trade is not automatically flattened immediately
but if a fresh crossover appears while outside the window, the strategy exits

So this is a signal-sensitive time filter, not a strict hard-flat end-of-window rule.

11) Daily/session guardrails

The strategy resets session tracking on the first bar of the session and stores:

prior cumulative profit
prior trade count
resets trades = 0
sets allowTrading = true

Then it monitors for:

session profit >= ProfitStop
session loss <= -LossStop
session trades >= MaxTrades

If any of those are hit:

it exits open positions
disables trading for the rest of the session/day
no more entries are allowed
12) Kill switch behavior

The optional kill logic uses:

UseKill
KillProfitStop
KillLossStop

It monitors current PnL and, when enabled, can stop the strategy and flatten positions if current profit or loss reaches those thresholds. This is separate from the normal session risk logic.

13) Visual behavior on the chart

When the strategy takes a signal, it also:

draws a vertical line
colors the bar yellow

It also plots:

FAST
SLOW
TREND

There are also display features for:

current PnL
stats box
background images

These are visual only and do not change the trading logic.

14) Best way to think about the strategy

The cleanest mental model is:

When both Long and Short are enabled

This is primarily a reversing crossover strategy with optional fixed stop/target protection.

That means a trade can end in three main ways:

fixed stop loss hit
fixed profit target hit
opposite crossover causes a reversal before stop/target are reached
When only one direction is enabled and MaxTrades is very large > 20

This acts more like a single-direction fixed bracket trade:

one qualifying signal
one trade
then effectively done for that session/rule set unless reset
When only one direction is enabled and MaxTrades is small.

This becomes a single-direction crossover-counted trade model:

valid same-side cross signals keep incrementing count
once the count threshold is reached, the strategy can flatten on a crossover event even if stop/target have not been hit
15) Short-form behavior summary

PantheonMasterBotV01TesterV2 trades Fast/Slow SMA crossovers, optionally filtered by a Trend SMA. It can trade both directions or only one direction, and it can optionally reverse the actual order direction. It uses fixed stops and fixed profit targets when enabled, but those are not always the trade exit because opposite crossover logic and max-trade logic can flatten or reverse the position first. With both directions enabled, it behaves like a managed reversing crossover strategy. With only one direction enabled and MaxTrades = 1, it behaves more like a one-shot fixed bracket trade. With only one direction enabled and MaxTrades > 1, repeated qualifying crossovers increment the trade counter, and when that threshold is reached the strategy can flatten on a crossover even if stop or target have not yet been touched.