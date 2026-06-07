# PantheonBotV2 ↔ PantheonMaster — exit-logic discovery pair

*Written:* 2026-06-06 · *Author:* Claude (acting PM) · *Status:* relationship audit + path
*Reads-with:* `docs/handoffs/pantheon_bot_v2_parity_handoff_2026-05-19.md`,
`docs/designs/ma_pool_enrichment_and_pantheonmaster_migration.md`, `analysis/exits/`

## The intent (operator, 2026-06-06)

`PantheonBotV2` and `PantheonMaster` are meant to be a **matched pair** that is
identical in entry/regime and differs **only in the trailing / stop-in-profit
exit logic** — so one can be tested in **NinjaTrader** and the other modeled in
**Python**, and comparing them isolates *which stop logic is best*. That is a
sound design: hold entries fixed, vary the exit, measure.

## Current reality (audited from the `.cs`, NT bin/Custom, 2026-06-06)

PantheonBotV2 = 53 params / 1175 lines · PantheonMaster = 59 params / 1758 lines
· **22 shared params.** The pieces line up like this:

| Layer | PantheonBotV2 | PantheonMaster | Match? |
|---|---|---|---|
| Entry signal (MA cross) | `averageFast/averageSlow/averageTrend` + UseTrend | `FastPeriod/SlowPeriod/TrendPeriod` + UseTrend | ✅ same concept, renamed |
| Direction | Long/Short/Reverse | Long/Short/Reverse | ✅ |
| Cross-profit bracket | MaxStop/UseMaxStop/MaxTPRatio/UseMaxTP | same | ✅ |
| Base risk | ProfitStop/LossStop/MaxTrades/Contracts | same (group "Legacy Daily Risk") | ✅ |
| Kill / Time | UseKill…, UseTimeFilter+Start/Duration | same | ✅ |
| **Exit / stop-in-profit** | **[Stops]: UseDynamicStop, UseLockIn, UseGiveback, UseTrail** (tick start+distance) | **[Discovery Exit]: DiscoveryExitPolicy = FixedRR/AtrTrail/BreakEven/Chandelier** + AtrTrailMultiple/ChandelierLookback/GivebackPct/BreakEven* | ⚠️ **differs — as intended** |
| **Regime / filter engine** | **VWAP-distance + HTF trend-slope + ATR-pct volatility + Required/Blocked filters** | **ADX + ATR-pct `RegimeMode` + UseTrendAlignment/EMA-confirm** | ❌ **differs — UNINTENDED drift** |
| Ops-only (Master) | — | Discovery Daily Risk (USD caps), Named Sessions, Live Stop Mgmt, Display/BotName | n/a |

**Verdict:** the operator is right about the *core* — entry, bracket, risk, time,
direction are shared, and the **exit logic differs as designed**. But the two have
**also** drifted in the **regime engine** (VWAP/HTF-slope vs ADX/ATR-pct). So today
there are **two** independent variables between them, not one — a head-to-head
would not cleanly attribute a result to the *exit* alone.

## Python coverage (asymmetric)

- **PantheonBotV2 has a full, verified Python twin** (`analysis/strategies/pantheon_bot_v2/`,
  47 tests, bit-for-bit parity loop — see the 2026-05-19 handoff). This is the
  "test in Python" half.
- **PantheonMaster has no full twin** — only the generic **exit-policy simulator**
  (`analysis/exits/policies.py` + `simulate.py`), which applies AtrTrail/BreakEven/
  Chandelier/Giveback/etc. to a *given trade set* on tick data. That simulator is
  actually the cleanest "vary the exit, hold entries fixed" tool we have.

## Path forward — two options (pick one)

**Option A — reuse the exit-sim on the verified twin (lean, mostly built).**
Run the **PantheonBotV2 Python twin** to generate entries/trades (parity-verified
vs NT), then rank exit policies with the **exit-sim** holding those entries fixed
— this is an apples-to-apples exit comparison *by construction*. Port the winning
policy into the deployment strategy and confirm once in NT. Treat regime as a
*separate* experiment, not mixed in. **Recommended** — it realizes "find the best
stop logic" today without first reconciling the C#.

**Option B — make them a true matched pair (cleaner, more C# work).**
Pick one canonical regime engine and align both strategies to it so they differ
**only** in exit. Then the NT(one)↔Python(other) head-to-head is valid. Bigger
effort (regime-engine reconciliation in two `.cs` files + re-verify the twin).

## Immediate next step (cheap, no NT)

Confirm the **entry-signal code** (OnBarUpdate MA-cross) is truly identical
between the two `.cs` (param-level it matches; verify the logic), and confirm the
exit mechanisms (managed SL/TP vs explicit stop + ChangeOrder — the live/backtest
parity trap noted in the ma-pool doc). Then decide Option A vs B.

> One-line: the pair is half-realized — same entry/bracket/risk, intended exit
> difference present, but an **unintended regime divergence** means it isn't yet
> a clean single-variable exit experiment. Option A gets the exit answer now;
> Option B makes the pair honest for the long run.
