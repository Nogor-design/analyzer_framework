# Design: Account Risk Engine + NT Account-Management Plugin (Phase 2)

*Created 2026-06-05. Part of [strategy_business_roadmap.md](strategy_business_roadmap.md) Phase 2 —
THE differentiator. Status: design for build. PM: Claude. APEX is the first (currently only) firm.*

## Problem

The pool and selector are **account-agnostic**. The product's real value is keeping each client's
account **alive** under its specific prop-firm drawdown rules, while pursuing the right objective
for that account: an **evaluation (challenge)** account trades to *pass*; a **funded (PA)** account
trades to *protect*. Clients run many accounts (some 20+), each at a different point on its trailing
drawdown. All clients trade through NinjaTrader (confirmed 2026-06-05), so an NT AddOn can see them.

> **The hard part is the per-firm drawdown rules engine, not the NT hooks.** A wrong "you're safe"
> signal is worse than no signal — it blows the account. Correctness > everything here.

## Firm rules are CONFIG, never code (non-negotiable)

Research finding (2026-06-05): APEX numbers and even mechanics differ by source and by APEX version
(3.0 vs 4.0), and APEX 4.0 offers **two drawdown types chosen permanently at signup**:
intraday-trailing vs EOD-trailing. Therefore firm rules live in a **verified, versioned profile**,
and the engine is fully parameterized. Every profile must be checked against the client's *actual*
account rulebook before it governs a live account.

### Firm profile schema (`analysis/risk/firm_profiles/apex.yaml`)

```yaml
firm: APEX
version: "4.0"                # profiles are version-stamped; rules change
drawdown_type: intraday_trailing | eod_trailing   # per-account choice, permanent
includes_unrealized: true     # intraday: true; eod: false
lock_buffer: 100              # threshold locks at starting_balance + lock_buffer (APEX = $100)
eod_recalc_time_et: "16:59:59"   # eod only
account_sizes:                # VERIFY against current APEX rulebook before live use
  "50000":  { max_drawdown: 2500, profit_target: 3000, max_contracts: 10 }
  "100000": { max_drawdown: 3000, profit_target: 6000 }   # Gemini research 2026-06-08
  "150000": { max_drawdown: 5000, profit_target: 9000 }   # Gemini research 2026-06-08
  # NOTE: the old "$2,000 vs $2,500" 50k conflict is RESOLVED (Phase-2a research, 2026-06-08,
  # docs/reference/prop_firm_dd_rules_catalog.md): APEX 50k DD = $2,500; the $2,000 figure was
  # TOPSTEP contamination. Still confirm vs a primary APEX source / Eric before live (help-center
  # 403s automated fetch); the real-account replay gate below remains mandatory regardless.
consistency_rules: {}         # APEX PA has consistency / min-days / payout rules — capture per profile
```

## Trailing-drawdown state machine (the core math)

State per account: `starting_balance`, `account_type` (evaluation|PA), `peak_balance` (high-water),
`current_balance`, `realized_today`, `current_threshold`, `locked` (bool).

**Intraday trailing (APEX):**
- `peak_balance` tracks the highest *intraday* account value **including unrealized PnL**.
- `current_threshold = peak_balance - max_drawdown`, trailing up as peak rises.
- **Lock condition:** once `peak_balance >= starting_balance + max_drawdown + lock_buffer`, the
  threshold freezes at `starting_balance + lock_buffer` and never moves again. (Both research
  sources agree once reconciled: "peak reaches start+DD+100" ⇔ "threshold locks at start+100".)
- **Violation:** account value (incl. unrealized) touches `current_threshold`.

**EOD trailing (APEX alt):**
- Threshold recalculated once at `eod_recalc_time_et` from the *closing realized* balance; enforced
  intraday next session. Has an additional **Daily Loss Limit**. `includes_unrealized: false`.

The engine implements both as a small, table-tested state machine selected by `drawdown_type`.

## What the engine outputs (per account, per day)

- `remaining_cushion = current_balance - current_threshold` (the live "room to lose")
- `distance_to_lock` (intraday) / `daily_loss_limit_remaining` (eod)
- `locked` status, `progress_to_target` (evaluation accounts)
- **`daily_risk_budget`** = cushion − safety margin → the max the account may risk today.

## Allocator (2c) — pool/selector × account budget → per-account daily plan

Given today's selector lineup + each account's `daily_risk_budget` + objective:
- **Evaluation/challenge accounts**: objective = maximize P(reach `profit_target` before violation).
  May size up / take more of the lineup, bounded by `max_contracts` and budget.
- **Funded/PA accounts**: objective = protect cushion + satisfy consistency rules. Conservative
  sizing, tighter daily-loss cap, prefer high-survival lineup entries.
- Output per account: which templates to run, contract size, daily-loss cap. This is the deliverable
  the client receives (Phase 3).

## NT AddOn (2d) — thin data pipe only

- Reads NT-connected account state: equity, open positions, realized daily P&L, balance.
- Client enters at onboarding (one time): firm + profile version + account_size + account_type +
  drawdown_type + current threshold/balance (or we infer peak from history).
- **All logic stays in Python.** The AddOn pushes account state to the risk engine and renders the
  returned daily plan. Built from NT's sample AddOn hooks; reuses existing NT-internals experience.

## Module layout (additive)

```
src/ta_foundation/analysis/risk/
  firm_profiles/apex.yaml        # verified, versioned config                    [built 2026-06-08]
  account_state.py               # FirmProfile + AccountState models             [built 2026-06-08]
  dd_engine.py                   # state machine: intraday + eod; cushion/budget/lock/violation [built 2026-06-08]
  allocator.py                   # lineup x account budget x objective -> per-account daily plan [TODO 2c]
bin/Custom/AddOns/<TaAccountManager>.cs   # NT data pipe (later sub-phase 2d)    [TODO 2d]
```

**2b status (2026-06-08):** engine + APEX profile + 9 table tests built and green
(`tests/analysis/risk/test_dd_engine.py`): intraday trail→lock-at-start+buffer→violation,
EOD daily-recalc + daily-loss-limit, eval/PA progress branching, cushion→daily-risk-budget.
The math is firm-agnostic; APEX numbers are config (corroborated, see catalog). **The replay
gate below is still mandatory before any client relies on it** — table tests prove the math is
self-consistent, not that it matches APEX's actual trajectory. Next: 2c allocator (needs the
selector lineup + this budget), then the real-account replay validation when Eric provides a history.

## Risks to validate before any live account relies on this

1. **DD math correctness** — validate the engine against *real APEX account histories* (replay a
   known account's day-by-day balances and assert threshold/lock/violation match APEX's actual
   numbers) before trusting it. This is the gate for Phase 2.
2. **Trail parity** — managed-SL/TP backtest vs explicit-stop + ChangeOrder live can diverge
   (documented trap). Validate before live.
3. **ATR Wilder-vs-SMA parity** — Python sim and NT must match or pre-selection is wrong.
4. **APEX rule changes** — profiles are versioned; never assume a number is still current.

## Tests & exit criteria

- Table tests: intraday trailing (trail up, lock at start+buffer, violation), EOD (daily recalc +
  DLL), evaluation vs PA objective branching.
- Replay test against a real (anonymized) APEX account history → threshold/lock/violation match.
- **Exit criterion:** engine reproduces a real APEX account's drawdown trajectory exactly; allocator
  produces a per-account plan within budget; reviewed by Claude for off-by-one / unrealized-PnL bugs.

## Executor guidance (handoff)

- **Web research (Grok when available; for now Claude/Gemini-with-web):** assemble the *authoritative*
  APEX 4.0 profile from APEX's own rulebook (the help-center pages 403 to automated fetch — may need
  manual paste from Eric or an authenticated grab). Claude verifies before it lands.
- **Codex:** build `dd_engine.py` + `account_state.py` from this spec as a pure, exhaustively
  table-tested state machine. No NT, no web.
- **Claude:** own the lock/violation edge cases and the allocator objective logic; review for the
  off-by-one and unrealized-PnL handling that distinguishes "safe" from "blown".
- **Eric:** provide one real (anonymized) APEX account history for the replay validation test, and
  confirm the verified profile numbers.
