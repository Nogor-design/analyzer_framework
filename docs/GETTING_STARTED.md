# Getting Started — TA Foundation Web App

A step-by-step walkthrough of the local web app: how to launch it and how to drive each capability.

> Looking for the architecture / data-model reference? See `USER_MANUAL.md` at the repo root. This guide is the user-facing tour.

---

## 1. One-time setup

You only do this once on a new machine.

### 1.1 Install Python and the project

You need Python 3.10 or newer.

From the repo root:

```bash
pip install -e .
```

This installs the `ta_foundation` package in editable mode plus its dependencies (`pandas`, `matplotlib`, `pyyaml`, `flask`, etc.).

Verify the install:

```bash
python -c "import ta_foundation; print('OK')"
```

You should see `OK`.

### 1.2 What you need on disk

The web app talks to three folders that you provide:

| Folder | What goes in it | Required for |
|---|---|---|
| **Input folder** | NinjaTrader exports: `*_Trades.csv`, `*_Analysis.csv`, optional `*_Optimization.csv` | Backtest Reports, Strategy Discovery (when ranking real runs) |
| **Output folder** | Where reports + sidecar JSON files land | Every capability that produces a report |
| **Market data folder** | Minute bars (`*.Last.txt`), optional ticks | Strategy Discovery, Prediction, Strategy Templates |

These can be anywhere on your machine. Quote the path if it has spaces.

---

## 2. Launching the web app

### 2.1 Start the server

From the repo root:

```bash
python -m ta_foundation.web.app --port 7734
```

You'll see a Flask startup line that tells you the actual URL:

```
 * Running on http://127.0.0.1:7734
```

> **Important:** if 7734 is already in use, the server will refuse to start. Pick a different port — `--port 7738`, `--port 8000`, etc. Look at the startup line to see which port you actually got. The rest of this guide assumes 7734 but **substitute your actual port everywhere**.

Leave this terminal open — closing it stops the web app.

> **If you change a Python file, restart the server** (Ctrl+C, then re-run the command). Templates auto-reload, but Python code does not. Running an old server against a freshly edited template can produce a 500 Internal Server Error if the template references a new variable the old route handler doesn't pass.

### 2.2 Open it in your browser

Visit:

```
http://localhost:7734
```

(Or `http://127.0.0.1:<your-port>` if you used a different port.)

You'll land on the **Workbench** — a single page with five tabs across the top:

```
[ Backtest Reports ] [ Prediction ] [ Strategy Templates ] [ Strategy Discovery ] [ System Map ]
                                                                    [ Discovery UI → ]
```

Each tab is a separate capability. The **Discovery UI →** link in the top-right opens the dedicated, beginner-friendly Discovery page (covered below).

---

## 3. Backtest Reports (the most common task)

Goal: pick up a folder of NinjaTrader exports and turn them into a self-contained HTML report.

### 3.1 Click the **Backtest Reports** tab

This is the default view when you open the workbench.

### 3.2 Pick a report template

Use the **Report template** dropdown. Each template is a curated YAML preset (e.g., a single-strategy report, a comparison report, a strategy-discovery report). When you pick one, a one-line description and an editable parameter list appear below.

### 3.3 Fill in the three folders

- **Input folder** — your NinjaTrader exports (e.g., `C:/Users/Owner/Downloads/B99`)
- **Output folder** — where the HTML report should be saved (e.g., `./outputs5-1`)
- **Market data folder** *(optional)* — needed only if the template uses market-data sections (e.g., `D:/MarketData`)

Tick **Recurse into subfolders** if your exports live under nested folders.

### 3.4 (Optional) Edit the template's parameters

Each template exposes a few knobs — section toggles, instrument hints, thresholds. Tweak them before running.

### 3.5 Click **Run report**

A job appears in the right-side **Jobs** panel. When it's green (`succeeded`), click the **Open report** link — your browser opens the generated HTML file. The report is fully self-contained: every chart is embedded, so you can email or zip it.

If the job goes red (`failed`), expand it to see stdout/stderr.

---

## 4. Strategy Discovery — the guided funnel

This is the most polished part of the app and the easiest place to find new edges. **Use this if you want the system to recommend setups for a market.**

### 4.1 Open the Discovery UI

Click **Discovery UI →** in the workbench top-right, or visit:

```
http://localhost:7734/discovery
```

On your **first visit** a 5-step welcome tour pops up. Read it through — it explains the funnel concept. You can re-open the tour any time with the **Tour** button in the header.

### 4.2 The four header buttons

```
Sessions   LCE   Tour   Glossary   ☐ Expert
```

- **Sessions** — manage saved sessions (rename, resume, delete). See §4.10.
- **LCE** — jump to the Large Candle Excursion sibling page (§4.11).
- **Tour** — replays the welcome tour.
- **Glossary** — slide-in panel with every term defined. Search box at top, click any term to expand.
- **Expert** — toggle on to reveal a raw-JSON overrides field on every stage form. Off by default for beginners.

### 4.3 Pick your instrument

Top-right of the header: **Instrument** dropdown. Pick `NQ`, `ES`, `CL`, `GC`, etc. Whatever you pick is reflected into every stage's tick_size, tick_value, and session filter automatically. Switching instruments re-saves the choice on the current session.

### 4.4 The 6-stage funnel

Down the left sidebar:

```
1  Quick Scan
2  Candle Pattern Deep Dive
3  Zones & Levels
4  NY Open Scalp
5  ORB & Momentum
6  Validate
```

Click any stage to open its **Configure** tab. The detail panel shows:

- A pill ("Stage 3" / "Event study") and runtime estimate
- "What this stage does" — plain-English explanation
- "What to look at" — what matters in the results
- "Read this first" — gotchas for this stage
- A **Stuck?** collapsible with stage tips, "where to go next" links, and key glossary terms
- The **Configure** form below

### 4.5 Run Stage 1 — Quick Scan

**Always start here.** Stage 1 tests every signal family on your instrument with one parameter set per family. It tells you where edge actually lives.

1. Fill in **Input folder**, **Output folder**, **Market data folder**.
2. Leave **Skip tick data** ticked — discovery never needs ticks.
3. Look at the **Generated YAML** preview pane below — it updates live as you edit.
4. Click **Run this stage**.
5. The **Run & Watch** panel mounts: live log tail, **Cancel** button, and a green **View results** button when finished (~3-5 min).

### 4.6 Read the Stage 1 results

Click **View results** (or click the run row in the right-side **Session runs** rail). You see:

- **Setup Cards** grouped by tier (Most Robust → High Quality → Solid → Marginal)
- A **Promote to next stage** button on each card
- Recommended next stages based on the verdict

Look at which family ranked first. That's your Stage 2/3/4/5 target.

### 4.7 Promote a setup

Click **Promote to next stage** on any card. The combo's exact YAML overrides are saved into the session's promotions list. Stage 6 will validate exactly what you promoted.

### 4.8 Run a deep-dive stage (2-5)

Pick the stage that matches what won Stage 1:

- Candle ranked first → **Stage 2: Candle Pattern Deep Dive**
- LCR / Levels ranked first → **Stage 3: Zones & Levels**
- ORB / BB / MA / Pullback ranked first → **Stage 5: ORB & Momentum**
- Want to test the NY-open thesis specifically → **Stage 4: NY Open Scalp**

Each stage has more knobs than Stage 1: per-family sub-signal pickers, common-parameter overrides (min_trades, timeframes, session filter, TP/SL ticks).

Promote winners again from each deep-dive's results.

### 4.9 Run Stage 6 — Validate

Stage 6 has no family/sub-signal form. Instead it shows a **Promoted Combos to Validate** checklist with everything you promoted from earlier stages. Tick the ones to include and click **Run this stage**.

The walk-forward report shows IS/OOS degradation per combo. Your gates:

| Degradation | Verdict |
|---|---|
| `< 0.15` | Real edge — paper trade for a week, then go live |
| `0.15 – 0.40` | Marginal — needs more history or different parameters |
| `> 0.40` | Overfit — discard |

### 4.10 Sessions index page

Click **Sessions** in the header (or visit `/discovery/sessions`). Every session you've worked on appears as a row with:

- Label (rename inline)
- Instrument, current stage, run count, last-updated time
- **Resume** — sets the cookie and drops you back into that session
- **Delete** — wipes the session directory

This is the place to clean up after experiments.

### 4.11 Large Candle Excursion sibling page

Click **LCE** in the header (or visit `/discovery/lce`). Same widget toolkit, different layout: only the LCE event-study stage is shown. LCE is **not** a trade-entry sweep — it measures how far price travels after an unusually large candle. Use it for stop/target calibration.

### 4.12 Expert mode

Tick the **Expert** checkbox in the header. A new section appears in every stage's Configure form: **Expert overrides (raw JSON)**. Paste any JSON object — it's deep-merged on top of whatever the form widgets produced. Use it for tweaks the form doesn't expose (custom threshold lists, alternate signal types, block-specific keys).

The textbox shows ✓ when the JSON parses, red error when it doesn't. Dispatch is blocked on bad JSON.

---

## 5. Prediction

The web app exposes a thin wrapper around the prediction CLIs. **Most users run prediction from the terminal** — see §7.

### 5.1 In the web app

Click the **Prediction** tab.

1. Pick a **Job type**:
   - **Single prediction config** — run `run_prediction` with one YAML
   - **Multi-agent** — run `run_multi_agent`
   - **Horizon backtest** — replay walk-forward predictions
2. Fill in the **Config path** (path to your prediction YAML file).
3. For horizon backtest, also fill **Minute bars file**, **Store dir**, optional **As-of date**.
4. Click **Run prediction**.

Output appears in the Jobs panel. Predictions land in the configured store directory (typically `.ta_artifacts/horizon/` or wherever your YAML points).

---

## 6. Strategy Templates

LLM-assisted strategy template builder. Use this when you want to design a strategy by editing a structured template and then backtest it locally.

### 6.1 Click the **Strategy Templates** tab

The page has three panes:

- **Template editor** (Ace editor with JSON syntax highlighting) — pre-loaded with a starter template
- **Generate / Backtest / Validate** buttons on the right
- **Results** below

### 6.2 Common flow

1. Edit the template JSON (or paste one in).
2. (Optional) Override **Instrument** and **Contract** if your template's defaults aren't what you want.
3. Click **Validate** to lint the template.
4. Click **Backtest** to run it against loaded market data. A bar chart and per-trade table appear in Results.
5. Iterate.

The "Generate" button asks an LLM to draft a template from a natural-language prompt — only available if you've configured an LLM endpoint.

---

## 7. Command-line capabilities (for reference)

Some things are easier from the terminal. Each capability has a CLI entry point:

| Capability | Command |
|---|---|
| Backtest report | `python -m ta_foundation.cli.main --input <folder> --output <folder> --report-config report.yaml` |
| Strategy discovery (manual) | Same CLI, with a `strategy_discovery:` block in the YAML |
| Prediction (single) | `python -m ta_foundation.prediction.run_prediction --config prediction.yaml` |
| Prediction (multi-agent) | `python -m ta_foundation.prediction.run_multi_agent --config multi_agent.yaml` |
| Horizon backtest | `python -m ta_foundation.prediction.backtest_horizon_predictions --minute-bars-file <file> --store-dir <dir>` |
| Execution bridge operator | `python -m ta_foundation.cli.bridge_operator …` |
| Soak monitor | `python -m ta_foundation.cli.soak_monitor …` |

Run any of them with `--help` for the full flag list.

---

## 8. Common pitfalls

### "No edge found" on Stage 1

That's a real result, not a bug. The instrument or date range you chose has no signal-family edge worth chasing. Try a longer history, a different instrument, or skip to the LCE event study to understand the market's character first.

### Web app won't start: `Address already in use`

Another process is on port 7734. Either kill it or pick a different port:

```bash
python -m ta_foundation.web.app --port 7800
```

### Clicking **Discovery UI →** gives a 500 Internal Server Error

This usually means the running server is out of date — the Python process started before a code change, but the template on disk has been updated since. Stop the server (Ctrl+C in the terminal where you launched it) and start it again. Templates auto-reload, but Python code only loads on startup.

### "No market-data files found" during discovery

Check that your **Market data folder** points at a directory containing `*.Last.txt` files (or contract-named subfolders). Discovery uses minute bars, not ticks — leave **Skip tick data** ticked.

### Paths with spaces

Always quote them on the command line:

```bash
python -m ta_foundation.cli.main --input "C:/Users/Bob/My Exports" --output "./out"
```

In the web app, just paste — no quotes needed.

### My Discovery session disappeared

Sessions are scoped to the cookie `ta_discovery_session_id`. Clearing browser cookies wipes that link. The session **directory** is still on disk under `.ta_artifacts/web_discovery/sessions/<id>/` — visit `/discovery/sessions` to find it and click **Resume**.

### Generated YAML preview looks empty

The preview only fills in once the form has enough info to validate. If validation is failing (red errors below the YAML pane), fill in the missing fields and the preview repopulates.

### A run is stuck "running" forever

Hit **Cancel** in the Run & Watch panel. The job's process is killed and the run row turns red. You can then fix the config and re-run.

---

## 9. Where stuff lives on disk

| Path | What |
|---|---|
| `.ta_artifacts/web_discovery/sessions/<id>/` | Discovery session dir: `session.json`, `stage_runs.json`, `promotions.json`, generated stage YAMLs |
| `.ta_artifacts/pattern_engine/<run_id>/` | Pattern engine parquet artifacts |
| `.ta_artifacts/horizon/` | Horizon prediction store |
| `<your output folder>/<report>.html` | Generated reports |
| `<your output folder>/discovery_summary.json` | Discovery sidecar JSON (the UI reads this for Setup Cards) |
| `<your output folder>/manifest.json` | Manifest of inputs + warnings |

The `.ta_artifacts/` directory is gitignored — feel free to delete it whenever you want a clean slate.

---

## 10. What to read next

- `USER_MANUAL.md` — system reference, data model, parser contracts, architectural diagrams
- `CLAUDE.md` — the contract for code changes (4-layer architecture, JSON-safety rules, section purity)
- `discovery/README.md` — the canonical discovery YAMLs the web UI generates from
- `docs/AI_CAPABILITY_MAP.md` — capability boundaries (which CLI command maps to which web tab)
- `docs/designs/discovery_web_ui.md` — the discovery web UI design doc and build progress
