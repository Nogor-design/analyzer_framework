# Handoff 005 — APEX 4.0 evaluation + PA rules verification (research)

**Owner:** Gemini (research draft) → Claude verifies → Eric confirms vs primary source.
**Why:** the account-risk engine + allocator's "trade-to-pass" (eval) vs "protect" (PA)
logic depends on APEX rules that are currently UNVERIFIED in
`src/ta_foundation/analysis/risk/firm_profiles/apex.yaml`. We have the trailing-DD
mechanics confirmed (50k = $2,500 intraday trail off peak equity incl. unrealized,
lock at start+$100). We do NOT have verified: the consistency rule, minimum trading
days, and payout rules. These change whether a backtest that "passes" the $3,000
target would actually pass a real APEX challenge.

## Questions (cite a source per answer; flag where sources disagree)
1. **Evaluation profit targets** per account size (confirm 50k=$3,000; 25k/100k/150k/etc).
2. **Consistency rule** on the EVALUATION (not just payout): is there a max-single-day
   share of total profit? (We have a guessed 30% payout-consistency for PA.) Exact %?
3. **Minimum trading days** to pass an evaluation (and any for PA payout).
4. **PA (funded) payout rules**: first-payout threshold, the 30% consistency rule scope,
   safety-net / buffer mechanics, how the trailing threshold behaves post-funding.
5. **Intraday vs EOD trailing**: confirm APEX 4.0 lets the trader choose ONE permanently,
   and that the intraday option trails off equity INCLUDING open positions and LOCKS at
   starting_balance + $100 once peak hits start + max_drawdown + $100.

## Output
- Markdown to `C:\Users\Owner\Downloads\apex_eval_rules_gemini_draft.md` (kept OUT of
  the repo — grounding URLs are weak/redirects; for verification only).
- Note primary-source URLs explicitly; mark anything inferred as UNVERIFIED.
- Do NOT edit apex.yaml — Claude reconciles after verifying citations.
