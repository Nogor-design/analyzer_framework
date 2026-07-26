# Strategy Lifecycle Risk Loop

The goal is not to prove a strategy works forever. The goal is to decide
whether a strategy is currently in favor, whether its risk is acceptable, and
when it should be reduced or paused.

## Operating Model

1. Run the normal report ingest over current NinjaTrader exports.
2. Render `strategy_lifecycle_board`.
3. Use the structured queue:
   `*_strategy_lifecycle.json`.
4. Trade only rows whose `tradability` and `risk_category` fit the operator's
   account rules.
5. Re-run the board after each new export batch. A strategy can move from
   `trade_candidate` to `pause_or_reduce` without being permanently
   graveyarded.

## Actions

- `trade_candidate`: Recent 2w and 3w windows are positive with acceptable
  profit factor and activity. Eligible for live or sim deployment if the risk
  bucket is acceptable.
- `small_size_watch`: Recent 2w window is improving but 3w/4w support is not
  yet strong. Use reduced size, paper, or very limited account allocation.
- `paper_or_research`: Longer window is profitable but not timely enough for
  live deployment. Keep watching or refine.
- `pause_or_reduce`: Longer window may still look good, but the most recent
  2w window cooled. Reduce allocation or stop adding new accounts.
- `do_not_trade`: Risk budget is exceeded, activity is too sparse, or recent
  performance is blocked.

## Risk Categories

The board compares realized window risk against the configured `risk_budget`
using the larger of max drawdown and worst day.

- `conservative`: risk <= 20% of budget.
- `balanced`: risk <= 40% of budget.
- `tactical`: risk <= 70% of budget.
- `aggressive`: risk <= 100% of budget.
- `blocked`: risk exceeds the budget or no profitable active window exists.

The default budget is `$2,500`, matching a common trailing drawdown style
constraint. Change `risk_budget` in the section options for other accounts.

## Recommended Deployment Rule

For funded-account style use, start with:

- `trade_candidate` + `conservative`: eligible for primary rotation.
- `trade_candidate` + `balanced`: eligible with smaller allocation or fewer
  accounts.
- `trade_candidate` + `tactical`: manual review only.
- `small_size_watch`: sim, paper, or one-account scout.
- Any `blocked` risk category: do not trade, even if recent PnL is high.

## Decay Rule

The lifecycle state is meant to move both ways.

- If a live strategy becomes `pause_or_reduce`, reduce allocation immediately.
- If it remains `pause_or_reduce` for two consecutive refreshes, remove it from
  the active rotation.
- If it returns to `trade_candidate`, it can re-enter without pretending the
  whole historical backtest changed.

This loop treats strategies as seasonal tools under risk control, not as
permanent money machines.
