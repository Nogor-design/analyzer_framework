# Handoff 003 — Prop-firm trailing-drawdown rules catalog (Phase 2a)

*Created 2026-06-08. PM: Claude. Executor: Gemini (web research). Verifier: Claude.*
*Roadmap: [strategy_business_roadmap.md](../designs/strategy_business_roadmap.md) Phase 2a —
"a web-research model (Grok/Gemini) drafts; Claude verifies; encode as per-firm risk profiles."*

## Why

Phase 2 (the account-risk layer) is the business differentiator, and its hard part is **not**
the NT hooks — it's encoding each prop firm's exact trailing-drawdown math so the allocator can
compute a per-account daily risk budget. This task produces the *research draft* of that catalog.
Claude then verifies every number and encodes the survivors as config risk profiles that extend
the existing DD primitives (`compute_true_max_loss`, `compute_effective_trades`,
`prop_max_daily_loss` in `template_naming`).

## What Gemini must produce

A markdown catalog, **APEX-first**, covering the major US futures prop firms. For each firm and
each account type (evaluation/challenge **and** funded/PA), capture these fields exactly:

1. **Trailing-DD type**: intraday/real-time trailing vs end-of-day (EOD) trailing vs static.
2. **Basis**: balance-based vs equity-based — i.e. does *unrealized/open* profit raise the
   trailing threshold, or only *closed* profit?
3. **High-water-mark lock**: does the trailing stop following once the buffer reaches a level
   (e.g. "trailing stops at starting balance + $X once funded")? Give the exact lock level.
4. **Drawdown amount** per account size (e.g. 50k/100k/150k) in dollars.
5. **Daily loss limit** (if any) and whether it's balance or equity based.
6. **Profit target** (eval) and **minimum trading days**.
7. **Consistency / 30%-day rule** or similar payout-eligibility constraints.
8. **Key eval-vs-funded difference** in one line (challenge = trade to pass; funded = protect).

## Output contract (strict)

- Output **only** GitHub-flavored markdown — no preamble, no chat.
- One **table per firm** with the fields above as rows or columns; a one-line firm summary above each.
- Every numeric rule gets an inline **source URL** and an **"as-of" note** — these rules change
  often, so unsourced numbers are worthless to us.
- End with a **"Conflicts / uncertain"** section listing anything you couldn't confirm from a
  primary/official source (firm site, official docs). Do NOT guess a number to fill a cell —
  write "UNVERIFIED" and cite where it was claimed.

## Firms (APEX-first, then the common ones)

Apex Trader Funding (first, most detail), Topstep, Take Profit Trader, MyFundedFutures,
Tradeify, Bulenox, Elite Trader Funding, and any ProjectX-platform firms you find material on.

## After Gemini (Claude)

- Verify each figure against the firm's official source; drop/flag anything UNVERIFIED.
- Reconcile the trailing-DD math with our primitives and note any model we don't yet support
  (e.g. equity-based intraday trailing that counts open profit).
- Encode the verified subset as per-firm **risk profiles** (config, not code) for the 2b engine.
- The client-specific firm list (open question in the roadmap) refines scope later; APEX-first
  is correct now regardless.
