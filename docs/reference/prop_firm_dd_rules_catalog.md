# Prop-firm trailing-drawdown rules catalog (Phase 2a)

*Drafted by Gemini (web research) 2026-06-08 from handoff
[`003-prop-dd-rules-research.md`](../handoffs/003-prop-dd-rules-research.md); curated &
partially verified by Claude. Feeds the Phase-2 account-risk engine
([`account_risk_engine.md`](../designs/account_risk_engine.md)).*

> **VERIFICATION STATUS — read first.** These numbers are a research starting point, **not**
> yet trustworthy config. Two caveats:
> 1. **Sourcing is weak.** Gemini's citations are Google *grounding-redirect* URLs, not primary
>    firm pages — they can't be click-verified and may expire. The raw draft (with those URLs)
>    is at `C:\Users\Owner\Downloads\prop_dd_rules_gemini_draft.md` for provenance.
> 2. **Prop rules change often** and differ by platform version / promotion. Every figure needs
>    primary-source (official firm doc) confirmation before the engine trusts it. The Phase-2
>    gate still stands: the engine must replay a **real anonymized account history exactly**
>    before any client relies on it.
>
> **APEX (the only firm currently in live use) is reconciled below and corroborated on its key
> disputed number; all other firms are UNVERIFIED Gemini draft.**

## APEX Trader Funding — ✅ reconciled (still pending primary-source/Eric confirmation)

Real-time **intraday** trailing drawdown, **equity-based** (unrealized profit raises the
threshold), that **locks permanently at starting balance + $100** once the buffer is cleared.

| Field | Value | Status |
|---|---|---|
| Trailing-DD type | Intraday / real-time | ✅ matches design doc (APEX 4.0 intraday vs EOD chosen permanently) |
| Basis | Equity-based (unrealized counts) | ✅ matches |
| HWM lock | Threshold freezes at **start + $100** | ✅ matches (`lock_buffer: 100`) |
| Drawdown 50k/100k/150k | **$2,500 / $3,000 / $5,000** | ✅ **resolves the conflict** — see note |
| Daily loss limit | None (intraday plan) | plausible; confirm for EOD plan |
| Profit target (eval) | $3,000 / $6,000 / $9,000 | ✅ matches design doc |
| Min trading days | 7 (eval) — **disputed** (1-day-pass promos contradict) | ⚠ see Conflicts |
| Consistency | 30% rule on funded payout requests | ⚠ unverified, plausible |
| Eval vs funded | Eval trails continuously; funded DD permanently stops trailing once buffer cleared | ✅ matches |

> **Conflict RESOLVED — `$2,000` vs `$2,500` for APEX 50k.** `account_risk_engine.md` flagged this
> and said "do not trust." The independent research disambiguates it: **APEX 50k DD = $2,500**;
> the **$2,000** figure is **Topstep's** 50k DD (see Topstep row below). The earlier conflict was
> APEX-vs-Topstep contamination. The design doc's tentative `2500` was correct. **Action taken:**
> design-doc NOTE updated; engine config still gated on a real-account replay.

## Other firms — ⚠ UNVERIFIED Gemini draft (future expansion; APEX is the only live firm)

Drawdowns are 50k / 100k / 150k. All figures below are unverified and must be primary-sourced
before use. Captured for when the client base expands beyond APEX.

| Firm | DD type | Basis | HWM lock | Drawdown $ | Daily loss limit | Targets / min days | Consistency |
|---|---|---|---|---|---|---|---|
| **Topstep** | EOD | Balance (closed) | Start + $0 | $2,000 / $3,000 / $4,500 | $1,000 / $2,000 / $3,000 (intraday equity, soft-lock) | $3k/$6k/$9k · 2 days | 50% (Combine) |
| **Take Profit Trader** | Eval EOD / PRO intraday | Eval balance / PRO equity | PRO: start balance | $2,000 / $4,000 / $4,500 | None (removed Jan 2025) | $3k/$6k/$9k · 5 days | 50% (eval) |
| **MyFundedFutures** | EOD (Core/Pro) or intraday (Rapid) | EOD balance / Rapid equity | Start + $100 | $2,000 / $3,000 / $4,500 | Generally none | $3k/$6k/$9k · 2 days | 50% (eval) |
| **Tradeify** | EOD | Balance (5pm ET close) | Start + $100 | $2,000 / $3,000 / $4,500 (Select); $2,000 / $3,500 / $5,000 (Growth) | Growth: $1,250 / $2,500 / $3,750 soft; Select: none | $2.5k/$6k/$9k · 1–3 days | 40% eval / 35% funded |
| **Bulenox** | Opt1 intraday / Opt2 EOD | Opt1 equity / Opt2 balance | Start + $100 | $2,500 / $3,000 / $4,500 | Opt2: $1,100 / $2,200 / $3,300 | $3k/$6k/$9k · 5 days | 40% (Master payout) |
| **Elite Trader Funding** | 1-Step intraday / EOD plan | 1-Step equity / EOD balance | Start + $100 (when realized = maxDD+$100) | $2,000 / $3,000 / $5,000 | 1-Step none; EOD yes (amounts UNVERIFIED) | $3k/$6k/$9k · 5 days | 23% or 40%-max-day |
| **ProjectX (e.g. Fast Track)** | Intraday | Equity (unrealized) | Start + $100 | $2,500 / UNVERIFIED / $7,500 | Customizable soft | Instant fund · 0 to pass, 5 to payout | 20% | 

*(Fast Track Trading reported insolvent — do not onboard; row kept only as a ProjectX-platform data point.)*

## Conflicts / uncertain (carried from the research, must resolve before encoding)

- **APEX min trading days (7 vs 1):** "1-day-pass" promotions contradict the stated 5–7 day
  minimums in compliance docs. Confirm the *current* APEX 4.0 eval + payout minimums.
- **Elite Trader Funding EOD daily-loss amounts:** UNVERIFIED; vary dynamically by prior-day close.
- **Take Profit Trader daily-loss limit:** officially removed Jan 2025, but legacy docs may still
  list $1,100/50k — confirm which applies to current accounts.
- **ProjectX/FTT 100k tier:** UNVERIFIED (FTT used 50k/150k/300k, skipping 100k).

## Next (Claude)

1. Confirm APEX figures against a primary source (help-center pages 403 automated fetch — needs
   Eric's authoritative APEX 4.0 numbers or a manual paste) before encoding the profile.
2. Encode the verified APEX profile as versioned config for the 2b engine; do **not** encode the
   other firms until a client uses them and the numbers are primary-sourced.
3. Engine still gated on the real-account replay test regardless of catalog confidence.
