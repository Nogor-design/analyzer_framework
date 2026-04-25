# LargeCandleReversal — Direction Policy Semantics

**Status:** DRAFT — needs human sign-off before any code change lands.
**Scope:** reconciles the research Strategy Card narrative (`reverse` /
`direction=-1`) with `LcrDirectionPolicy.CounterToFailedContinuation` in
`LargeCandleReversal.cs`.

---

## The ambiguity

Two narratives describe the same trade, and they appear to contradict each
other at the exporter boundary.

**Research narrative (Strategy Cards, Regime Findings digest):**

> Fade (reverse) a 50–75-tick large candle on the 2m chart after a failed
> continuation.

Colloquially: if a bullish 60-tick candle prints, **go short**.  The size of
the candle plus the failed-continuation context is evidence of exhaustion.

**Strategy narrative (`LargeCandleReversal.cs`):**

```csharp
private int ResolveTradeDirection(int signalDir)
{
    switch (DirectionPolicy)
    {
        case LcrDirectionPolicy.CounterToFailedContinuation:
            return signalDir;     // <── trades WITH the signal candle
        ...
    }
}
```

The C# comment on that switch says:

> The signal candle is itself the reversal of a prior failed move.
> `CounterToFailedContinuation` trades WITH that signal because the signal IS
> the reversal.

In this framing the research's "reverse" means "the signal candle is a
reversal of the earlier, failed continuation" — not "we fade the signal
candle."  The trade side is long-when-signal-is-bullish.

If the exporter emits `DirectionPolicy=CounterToFailedContinuation` for a
research row tagged `trade_mode=reverse, direction=-1`, the resulting trade
side depends entirely on which interpretation the research author had in mind.

---

## What the research actually stores

Walking `regime_discovery.py` and `reversal_size_analysis.py`:

- **`trade_mode`** is a pipeline-internal string — `"reverse"` or
  `"continuation"`.  It is assigned by whichever scanner produced the row.
- **`direction`** is ±1.  The convention is:
  - `+1` means "long trade"
  - `-1` means "short trade"

So `trade_mode="reverse", direction=-1` is the SHORT side of a reverse setup.
The word "reverse" is describing the *setup family* (it's a reversal setup,
not a trend-following setup), not a commitment to trade opposite to the
signal candle.

Under this reading, a reverse + direction=-1 row with a bullish signal candle
means: "we went short"; with a bearish signal candle it means: "we went
short" (because direction is absolute, not relative to the signal).

This is DIFFERENT from the colloquial "fade" interpretation.  If both
directions of the same signal setup appear in the ledger — one at
`direction=+1` and one at `direction=-1` — that's the research explicitly
keeping them as separate candidates, which is the current pipeline behaviour.

---

## Truth table

| signal_candle_dir | research.trade_mode | research.direction | `CounterToFailedContinuation` trades… | What the research intended |
|:-:|:-:|:-:|:-:|:-:|
| +1 (bull) | reverse       | +1 | long  | long  (match) |
| +1 (bull) | reverse       | -1 | long  | **short** (MISMATCH) |
| -1 (bear) | reverse       | +1 | short | **long**  (MISMATCH) |
| -1 (bear) | reverse       | -1 | short | short (match) |
| +1 (bull) | continuation  | +1 | long  | long  (match) |
| +1 (bull) | continuation  | -1 | long  | **short** (MISMATCH) |
| -1 (bear) | continuation  | +1 | short | **long**  (MISMATCH) |
| -1 (bear) | continuation  | -1 | short | short (match) |

`CounterToFailedContinuation` returns `signalDir` unconditionally.  The rows
where `research.direction == signalDir` are correctly traded; the rows where
they disagree are **inverted**.

In the current research output the `direction` field is dominated by rows
where `direction == signalDir` (because the setups that make the ledger are
the ones the signal candle pointed at).  That's why live testing hasn't
caught this — the MISMATCH rows are rare.  But they do exist, and every one
of them trades the wrong side.

---

## Options

### Option A — add `FadeSignalCandle` to `LcrDirectionPolicy`

Extend the enum and implement `ResolveTradeDirection`:

```csharp
public enum LcrDirectionPolicy
{
    CounterToFailedContinuation = 0,
    ...
    SignalDirection             = 5,
    FadeSignalCandle            = 6,   // NEW
}

private int ResolveTradeDirection(int signalDir)
{
    switch (DirectionPolicy)
    {
        case LcrDirectionPolicy.FadeSignalCandle:
            return -signalDir;
        ...
    }
}
```

The exporter then picks between `CounterToFailedContinuation` (current
behaviour) and `FadeSignalCandle` based on whether
`research.direction == signalDir` for the candidate's representative events.

**Pros**
- Handles every row in the truth table without changing the semantics of the
  existing `CounterToFailedContinuation` policy.
- No research-label changes; no digest rename.
- The C# enum stays self-describing: `CounterTo*` trades with the signal,
  `Fade*` trades against it.

**Cons**
- Requires a matching change to `LargeCandleReversal.cs`.
- Adds a branch to the exporter that must decide per-blueprint which policy
  to emit.

### Option B — change the research label semantics

Rename `trade_mode="reverse"` to `trade_mode="confirmed_reversal"` (or
similar) everywhere in the research pipeline.  Keep the exporter's single
`CounterToFailedContinuation` policy mapping.

- Strategy Cards and Regime Findings rows that would have said
  `reverse | direction=-1` now say `confirmed_reversal | direction=-1`.
- The word "fade" is removed from the narrative text.
- Any setup that requires trading OPPOSITE to the signal candle is no longer
  representable — the pipeline would need a new `trade_mode` to express it.

**Pros**
- No C# change.
- Eliminates the dual interpretation of "reverse" in the research output.

**Cons**
- The research intent (fade a big candle) is a real, commonly-described idea
  in discretionary trading; removing the vocabulary is a loss.
- Any MISMATCH row that exists today is quietly reclassified as "wrong label"
  rather than "wrong side" — we may drop legitimate candidates.
- Digest output changes; downstream consumers of the digest need to be
  notified.

---

## Recommendation

**Option A (add `FadeSignalCandle`) is preferred.**

Rationale:
1. The research clearly intends both interpretations to exist — the
   `direction` column already encodes them.  Option B flattens that into a
   single orientation.
2. The cost of Option A is a one-time C# enum addition.  The cost of Option
   B is a pipeline-wide rename and a vocabulary loss.
3. Option A's exporter rule is mechanical: emit `FadeSignalCandle` when the
   research row's `direction` disagrees with the signal candle direction for
   its representative events (stored in `events_sample` or inferable from the
   candidate's `dominant_signal_direction`).

### Implementation sketch (Option A)

1. `LargeCandleReversal.cs`:
   - Add `LcrDirectionPolicy.FadeSignalCandle = 6`.
   - Update `ResolveTradeDirection` to return `-signalDir` for it.

2. `strategy_blueprint_exporter.py`:
   - For each candidate, compute `signal_dir_consistency` — the fraction of
     the candidate's events where `event.direction == event.signal_direction`.
   - If `consistency >= 0.80` → emit `CounterToFailedContinuation` (current
     behaviour).  If `consistency <= 0.20` → emit `FadeSignalCandle`.
     Mixed candidates (0.20 < c < 0.80) get a warning and default to
     `CounterToFailedContinuation` for backwards compatibility.

3. `generate_nt8_template.py`:
   - Add `"fade_signal_candle"` to `DIRECTION_POLICY_ENUM`.

4. `test_blueprint_roundtrip.py`:
   - Assert every template emits a `DirectionPolicy` that produces the trade
     side the research intended for that candidate.

### Work NOT included in this note

- Actual code changes.  Nothing is committed until this note is signed off.
- Backtesting the MISMATCH rows to quantify how much edge they represent.
  That should happen once Option A is implemented — if the MISMATCH rows are
  profitable on `FadeSignalCandle`, the current `CounterToFailedContinuation`
  templates are systematically losing trades.

---

## Sign-off

- [ ] Research lead confirms Option A preserves intended semantics.
- [ ] Strategy author confirms the C# change is acceptable.
- [ ] Digest consumers notified of any rename (Option B only; N/A for A).

Date signed off: _________________
