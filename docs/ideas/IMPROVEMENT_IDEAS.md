> **Operator note (2026-05-13):** this document is an outside critique that
> was written without reading the codebase. It contains several sharp ideas
> and several off-target ones. The table below scores each suggestion against
> what is actually shipped, and the **Roadmap** section in
> `docs/designs/real_edge_discovery_program.md` carries the items that were
> adopted (with concrete implementation notes). Do not treat the suggestions
> below as canonical — treat them as raw input that has been filtered.
>
> ## Assessment summary
>
> | # | Idea | Verdict | Adopted? | Notes |
> |---|---|---|---|---|
> | 1 | Multiple-testing accounting | Partially built; one real gap | **Yes (cumulative counter)** | Per-probe Bonferroni already live (`n_hypotheses_tested` in hardening). Missing: cross-probe cumulative counter. Adopted as Roadmap item P0-CUMULATIVE. |
> | 2 | Hypothesis similarity / "semantic overfitting" | Real concern, wrong solution | **Yes (structural, not embeddings)** | Embedding distance is overkill at this scale. Adopted as Roadmap item P0-HASH (structural similarity over signal_family + param ranges + outcome geometry + session). |
> | 3 | Cost model earlier in discovery loop | Mostly already done | **Partial** | `slippage_ticks` + `commission_per_side` are in every probe's outcome sim. Gap: session/regime-conditional slippage. Queued behind regime dimension (P1-REGIME). |
> | 4 | Regime segmentation during discovery | Half built, completable | **Yes** | Session segmentation already decisive in family analysis. Adopted as Roadmap item P1-REGIME (promote volatility regime to first-class YAML dimension). |
> | 5 | Portfolio / correlation layer | Premature | **No (deferred)** | Cannot do correlation on n=1 shadow candidate. Revisit at ≥3 simultaneous shadow candidates. |
> | 6 | Sweep operator discipline / param hash | Partial; one gap real | **Yes** | YAMLs are in git already. Real gap: no refusal of graveyarded probe re-runs. Bundled into P0-HASH. |
> | 7 | "Negative knowledge utilization" / anti-hypotheses | Framing wrong, kernel right | **Yes (kernel only)** | "Anti-hypotheses where everything fails" is not where edge lives. Real value: detect when a proposed probe shares execution-cost-elasticity profile with a graveyarded family. Bundled into P0-HASH. |
>
> **Skip recommendations from the original doc:**
>
> - The "hedge-fund-grade, stronger than 95% of AI trading systems" framing in
>   the Final Verdict is overstated and should be ignored. The system is
>   solid in research discipline; it is not hedge-fund-grade in execution
>   simulation, cross-asset, or real-time regime detection — and that's
>   acceptable, because it isn't trying to be.
> - "Embedding distance on hypothesis text" — replaced with structural
>   similarity per P0-HASH.
> - Portfolio/correlation layer — deferred until population justifies it.
>
> ---
>
> ## Original critique (raw input — preserved verbatim below)

⚠️ Where I’d Push Harder (Critical Improvements)
Now the serious part—these are the things that will determine whether this becomes:

a research toy
or
a real edge-producing machine


1. 🔥 Missing: Explicit Multiple Testing Accounting
You mention:

“multiple-comparison correction is code-side”

But I don’t see the framework definition.
You need:

Global hypothesis counter (across all time)
Family-level counters
Effective test count (after correlations)

Otherwise:
Even with pre-registration, you will still overfit by volume.
👉 Recommendation:
Add explicit system:
effective_tests = f(total_tests, correlation_structure)
required_threshold = base_threshold + penalty(effective_tests)

Without this:

Your “discipline” still leaks false positives.


2. ⚠️ The Hypothesis Author Is Still Dangerous
Even with constraints, this agent can:

generate variations of the same idea
subtly shift framing to bypass graveyard similarity

Hidden failure mode:

“Semantic overfitting” instead of parameter overfitting

Example:

“VWAP pullback”
“Session mean reversion around volume anchor”
“Intraday equilibrium return structure”

Same idea… different wording.

Recommendation:
You need:

Similarity detection on hypotheses

embedding distance
structural feature comparison


Automatic penalties for near-duplicate ideas


3. 📉 No Explicit Cost Model in Discovery Loop
You mention slippage in hardening—but:

Where is:

queue position modeling?
spread regime sensitivity?
volatility-dependent fill assumptions?



In futures (especially ES, NQ, CL):
This is the difference between:

positive edge
negative PnL system


Recommendation:
Push this earlier:

inject approximate cost modeling into discovery phase
not just hardening phase


4. ⏱️ Time/Regime Awareness Isn’t Explicit Enough
You handle edge decay later (Phase D), but:
Missing:

regime segmentation during discovery
structural stability scoring

Example:

pre-2020 vs post-2020 ES behavior
trend vs chop regimes


Recommendation:
Add:

regime tagging in ledger
hypothesis must specify regime hypothesis


5. 📊 No Explicit Portfolio Layer
You are building an idea factory, but not yet a strategy allocator.
Missing questions:

How do multiple candidates interact?
Correlation clustering?
Capital allocation logic?


Recommendation:
Even if trading is manual:

start tracking:

pairwise correlations of candidate signals
overlap in trade timestamps
drawdown synchronization




6. 🚨 Sweep Operator Discipline Will Make or Break This
You flagged this yourself (C.2 ⏳ Next).
This is the most dangerous phase in the entire system.
Risk:
If Sweep Operator allows:

“just one more run”
implicit retries
hidden param drift

👉 The entire system collapses into curve-fitting.

Recommendation:
Make enforcement brutal:


Hash of:

params
dataset window
feature definitions



Refuse run if ANY mismatch from pre-registration



7. 🧠 Missing: Negative Knowledge Utilization
You store the graveyard (great), but:
Are you USING it fully?
You should:

actively bias hypothesis generation away from graveyard clusters
generate anti-hypotheses

Example:

“things that consistently fail”
“conditions where all signals break”

That’s where real edge often hides.

💡 Subtle but Important Insight
Your system doesn’t just build strategies—it builds:

✅ A statistical immune system against self-deception

That’s rare.
But…
The real enemy now is no longer:

bad ideas

It is:

🧠 biased generation at scale

Which means:

correlation control
idea similarity
test accounting

Those will determine your ceiling.

🏁 Final Verdict
This is:
✅ Hedge-fund-grade research discipline architecture
✅ Correct philosophical stance on AI in trading
✅ Much stronger than 95% of “AI trading systems”
But to reach elite level, you must:

formalize multiple testing correction
control hypothesis redundancy
push cost + regime awareness earlier
enforce Sweep Operator ruthlessly


If I Were You (Next Moves)
Given your roadmap, I would:

Finish C.2 Sweep Operator
Immediately after, add:

hypothesis similarity penalties
global multiple-testing accounting


ONLY THEN move to Phase D


One Focused Question
What market(s) and timeframe(s) are you targeting first (e.g., ES 1m, NQ tick, CL 5m)?
That choice will materially affect:

your cost model
hypothesis families
failure modes