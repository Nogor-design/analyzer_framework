# Market data refresh routine — keeping `D:\MarketData` current

*Written:* 2026-07-01 · *Status:* operational runbook
*Reads-with:* [`market_data_gathering.md`](market_data_gathering.md) (the underlying
gather capability), `CLAUDE.md` (MarketData section)

> **Why this exists:** the canonical local market data in `D:\MarketData` feeds
> the anchor engine, pattern engine, exit-sim, prediction, and every scout. It
> was going stale — old contracts lingered, the live front-month wasn't being
> topped up, and there was no single command to answer "what's missing and pull
> it." This routine closes that gap.

The one-time *pull* capability (`TaFoundationDataExportStrategy` → NT bridge →
bars + ticks for a window) already existed as
`scripts/gather_market_data.py`. What was missing was the **glue** that scans
the folder, decides what needs pulling, and drives the gather per gap. That glue
is **`scripts/refresh_market_data.py`**.

---

## The routine

### 1. Preview — what's current vs stale (safe, no NinjaTrader needed)

```bash
python scripts/refresh_market_data.py --dry-run
```

Prints a table, one row per `(instrument, contract)`, with each feed's last
bar/tick date + age and a decision: `ok`, `expired YYYY-MM-DD`, or
`REFRESH <from>-><to> [reason]`. Dispatches nothing.

Scope it while previewing:

```bash
python scripts/refresh_market_data.py --dry-run --instrument NQ
python scripts/refresh_market_data.py --dry-run --stale-days 2
```

### 2. Refresh — top up live contracts (drives NinjaTrader)

```bash
python scripts/refresh_market_data.py
```

Refreshes every contract flagged stale or missing a bars↔ticks partner, **bars +
ticks**, serially. Skips expired contracts. Prerequisites:

- NinjaTrader logged in (title `Control Center …`). It no longer has to be
  **warm**: a cold Strategy Analyzer refuses the first automated run, and the
  gather now recognises that specific refusal and re-dispatches with backoff
  (~20s, 45s, 90s) using a fresh `runId` each time. Nobody has to click RUN
  BATCH BACKTEST to prime it. A refusal that outlasts the budget fails loudly
  and pulls no data.
- **No optimizer batch running** — the NT command bridge is single-writer; the
  script aborts cleanly (exit 2) if a batch owns it. Rerun when free.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Plan only; dispatch nothing. |
| `--instrument NQ` / `--contract 09-26` | Restrict to one root / contract. |
| `--no-ticks` | Bars only (ticks are GB-scale — see sizes below). |
| `--stale-days N` | Flag a feed stale if older than N days (default 7). |
| `--only-missing` | Ignore staleness; fill only missing partners. |
| `--include-expired` | Also (re)pull contracts past expiry — one-off historical backfills, window capped at each contract's expiry. |

### 3. Seed a brand-new front-month (when a contract rolls)

The refresh script only maintains contracts that **already exist** on disk — it
can't conjure a new front-month. When NQ rolls (e.g. `09-26` → `12-26`), seed it
once with the gather script, then refresh keeps it current until its own expiry:

```bash
python scripts/gather_market_data.py "NQ 12-26" 2026-09-01 2026-10-01
# add --no-ticks to start light; widen the from-date for more history
```

---

## How the decision is made

For each contract:

1. **Expired?** Expiry = **3rd Friday of the contract month** (from the `MM-YY`
   code). If `today` is past it → skipped by default (`expired YYYY-MM-DD`).
   Expiry also **caps the pull window** (`to_date = min(today, expiry)`) so we
   never request data past expiry.
2. **Partner missing?** Bars exist but ticks don't (or vice versa) → refresh.
3. **Stale?** A feed's last bar/tick is older than `--stale-days`, measured
   against the expiry-capped end → refresh.
4. **Window:** `from_date` = earliest first-bar already on disk (coverage never
   shrinks), falling back to `end - lookback_days` when no file exists.

The policy lives in one function — `plan_refresh()` in the script — so it's the
single place to adjust if your cadence or contract cycles change.

---

## Worked example (2026-07-01): seeding NQ 09-26

NQ had rolled — live month `09-26`, but `D:\MarketData` held only the expired
`03-26` and `06-26`. Sequence:

```bash
# 1. Confirm nothing live is present
python scripts/refresh_market_data.py --dry-run --instrument NQ
#   NQ 03-26 → expired 2026-03-20
#   NQ 06-26 → expired 2026-06-19
#   (no 09-26 row — not on disk)

# 2. Seed the live contract with ticks
python scripts/gather_market_data.py "NQ 09-26" 2026-06-01 2026-07-01
#   bars:  NQ 09-26.Export.txt        31,417 lines   ~1.65 MB
#   ticks: NQ 09-26 Tick.Export.txt   12,299,706 lines   ~627 MB   (~2 min)

# 3. Verify it's now tracked and current
python scripts/refresh_market_data.py --dry-run --instrument NQ
#   NQ 09-26 → 2026-07-01 (0d) / 2026-07-01 (0d) → ok
```

**Tick volume reference:** one month of NQ ticks ≈ 12.3M lines / ~627 MB. Budget
disk/time accordingly before a multi-month or all-instrument tick refresh.

---

## Insights (why it's built this way)

- **Expiry does double duty — skip *and* window-cap.** Skipping historical
  contracts is obvious. The subtler win is capping the comparison/`to_date` at
  expiry: a freshly-pulled live contract then reads as `ok` (age 0 vs its capped
  end) and **won't be re-dumped next run**. Without the cap, every aged contract
  looks "stale vs today" forever and the loop thrashes. The cap is what makes
  the routine *converge*.

- **No roll-cycle knowledge required.** Because each contract carries its own
  expiry month in the `MM-YY` code, the skip never needs to know that index
  futures roll quarterly while GC/NG differ. That complexity only matters for
  picking the *active* month — which stays a human/upstream call (you tell it
  "NQ is 09-26 now" by seeding that contract).

- **Reuse over rebuild.** The pull itself is the shared `gather_market_data`
  (same NT batch bridge the `/optimizer` UI uses). The refresh script adds only
  a scan + planner. The freshness scan is tail-seeking — it reads ~64 KB from
  the end of each multi-GB tick file, not the whole file.

- **`.Export.txt` is deliberately non-colliding.** Gather writes `.Export.txt`;
  the live-streaming indicator writes `.Last.txt`. The parser accepts both and
  the store merges by timestamp (dedup keep-last), so fresh Export data wins on
  overlap without clobbering the live feed.

---

## Thoughts going forward

1. **Canonical-file question (open).** When both `.Last.txt` (old) and
   `.Export.txt` (fresh) exist for a contract, ingest merges them by timestamp,
   so fresh data wins — fine today. If we ever want a single canonical file per
   contract, revisit the ingest globbing / add a "prefer newest suffix" rule.
   Not urgent; flagged so it isn't rediscovered the hard way.

2. **The better long-term automation: `D:\MarketDataPipline`.** That sibling repo
   is a production Rithmic pipeline with **built-in gap detection + an
   APScheduler**, and **no NinjaTrader dependency** (no login/warm-up, no
   single-writer bridge). The catch: it writes its own partitioned Parquet/CSV
   store, *not* the `<INST> <CONTRACT>.Export.txt` format the engines read. Making
   it the refresher for `D:\MarketData` means either teaching it to emit that
   `.txt` format or teaching the engines to read its store — a real project, not
   a script. Until then, this NT-strategy routine keeps `D:\MarketData` current;
   the pipeline is the migration target when scheduled, NT-free freshness is
   worth the integration cost.

3. **Scheduling this routine.** Short of the full pipeline migration, the refresh
   script is a natural fit for a scheduled run (e.g. a daily task, or the
   optimizer spinning NT up already → dispatch a refresh pass for the run's
   instrument first, so downstream analysis always has fresh bars/ticks). Keep it
   serial and bridge-aware; never run it while an optimizer batch owns the bridge.

4. **Per-instrument staleness.** `--stale-days` is global. If some instruments
   need tighter freshness than others, that's the obvious next knob to add to
   `plan_refresh()`.

---

## Refresh record: 2026-08-03 index panel and rollover seed

With NinjaTrader already warm, the routine was used to remove the live-panel
gap that had blocked a sibling-contract replication:

- refreshed NQ 09-26 bars and ticks through 2026-08-03;
- acquired ES, RTY, YM, and MNQ 09-26 bars from the 2026-06-15 Strategy
  Analyzer session through 2026-08-03;
- seeded NQ, ES, RTY, YM, and MNQ 12-26 bars on 2026-08-01 through 2026-08-03;
  and
- filled every missing 09-26 and 12-26 tick partner.

The resulting 20 active/rollover files total 6.194 GB. The final verification
command was:

```bash
python scripts/refresh_market_data.py --dry-run --stale-days 3
```

It scanned 20 contracts, reported every non-expired 09-26/12-26 contract as
`ok`, skipped 10 expired contracts, and printed `Everything current -- nothing
to pull.` The December files are now discoverable by the normal routine; they
do not need to be seeded again when the roll approaches, only refreshed.
