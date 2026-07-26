# Complete Capabilities Matrix — ta_foundation

**Quick Reference Table**  
**Last Updated:** May 24, 2026

---

## At a Glance

Use this table to find the right tool for your task.

| # | Capability | What It Does | Entry Point | Input | Output | Best For | Key Features |
|---|---|---|---|---|---|---|---|
| **1** | **Backtest Report Generation** | Ingest NinjaTrader exports, compute analytics, render HTML reports | `python -m ta_foundation.cli.main` | Trades CSV, daily CSV, summary CSV, settings CSV, minute bars, tick data | HTML report, manifest.json, PNG cards (optional) | Single/multi-run analysis, strategy comparison | 120+ report sections, YAML config, base64 embedded images |
| **2** | **Strategy Discovery Funnel** | 6-stage funnel to discover high-probability entry signals | `python -m ta_foundation.cli.main --report-config discovery/NN_*.yaml` | Backtest exports + market data (minute bars) | Ranked entry signals, IS/OOS validation, NinjaTrader templates | Finding and validating new trading signals | Quick scan → candle → levels → NY open → ORB → validate |
| **3** | **Daily Prediction** | Claude-driven next-day market forecast with proper scoring | `python -m ta_foundation.prediction.run_prediction --config prediction.yaml` | Market data (minute bars), economic calendar, prior context | Direction, confidence, key levels, breakout probability | End-of-day decision support | Claude Opus 4.7, ECE calibration, historical analogues |
| **4** | **Horizon Prediction** | Multi-agent, multi-timeframe, multi-horizon ensemble forecasting | `python -m ta_foundation.prediction.backtest_horizon_predictions` | Minute bars, config (timeframes, horizons, agents) | Probability distributions, tradable zones, Kelly sizing | Execution-side automation, walk-forward backtesting | 5 agents, stacking weights, per-bucket calibration |
| **5** | **Autonomous NinjaTrader Loop** | Author strategy → compile → repair → optimize end-to-end | `python -m ta_foundation.nt_strategy_loop.cli full-loop --spec strategy.json` | Strategy spec (JSON), NinjaTrader running, optional Ollama | Compiled .cs, optimizer results, decision record | Fully automated parameter optimization | Deterministic + optional LLM repair, seed template gen, guardrails |
| **6** | **Agentic Research Program** | Autonomous hypothesis testing with human-in-the-loop gates | `python -m ta_foundation.agent.cli <subcommand>` | Research ledger (SQLite), hypothesis specs, discovery results | Triage decisions, post-mortems, promotion records | Autonomous discovery program with researcher oversight | 5 phases, HITL inbox, graveyard refusal, persistent ledger |
| **7** | **Pattern Engine** | Sweep parameterized price patterns, cluster, Monte Carlo robustness | YAML config: `pattern_engine: { enabled: true }` | Backtest exports + market bars | Parquet artifacts, pattern matches, robustness scores | Explaining which patterns drove trades | Template registry, clustering, CV degradation scoring |
| **8** | **Entry Strategy Discovery** | Discover and rank entry signals across 8 strategy families | YAML config: `strategy_discovery: { enabled: true }` | Backtest exports + market data | Ranked entry rules, validation results, C# templates | Finding the best entry signals for an instrument | 8 families (candle, MA, BB, ORB, breakout, pullback, level, LCR) |
| **9** | **Execution Bridge** | Send algorithmic signals from Python → NinjaTrader execution | `python -m ta_foundation.cli.bridge_operator` | Signal JSON messages | Order fills, trade status, execution logs | Live trading automation | Inbox/archive/reject protocol, heartbeat, deduplication |
| **10** | **Web Workbench** | Interactive UI for all capabilities + job management | `python -m ta_foundation.web.app --port 7734` | Select folders, pick presets, configure YAML | Job status, report artifacts, live metrics | Interactive discovery, visual configuration, status tracking | 5 tabs (reports, prediction, templates, discovery, map) |

---

## How to Choose

### "I want to..."

**...analyze a backtest run**
→ **Capability 1: Backtest Report Generation**
```bash
python -m ta_foundation.cli.main \
  --input "C:/backtest_exports" \
  --output ./reports \
  --report-config report.yaml
```

**...discover new trading signals**
→ **Capability 2: Strategy Discovery Funnel**
```bash
# Start with quick scan
python -m ta_foundation.cli.main \
  --input "C:/exports" \
  --output ./discovery_out \
  --report-config discovery/01_quick_scan.yaml \
  --market-data "D:/MarketData" \
  --no-tick-data
```

**...get a next-day market forecast**
→ **Capability 3: Daily Prediction**
```bash
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --asof 2026-05-24
```

**...backtest a prediction model**
→ **Capability 4: Horizon Prediction**
```bash
python -m ta_foundation.prediction.backtest_horizon_predictions \
  --minute-bars-file "C:/NQ 06-26.Last.txt" \
  --store-dir .ta_artifacts/horizon \
  --timeframes 5m,15m \
  --horizons 3,5
```

**...auto-optimize a strategy in NinjaTrader**
→ **Capability 5: Autonomous NinjaTrader Loop**
```bash
python -m ta_foundation.nt_strategy_loop.cli full-loop \
  --spec my_strategy.json \
  --instrument "NQ 06-26" \
  --max-drawdown 2500
```

**...run autonomous discovery with HITL gates**
→ **Capability 6: Agentic Research Program**
```bash
python -m ta_foundation.agent.cli daily-pass     # Triage + scribe
python -m ta_foundation.agent.cli authoring-pass # Hypothesis author
python -m ta_foundation.agent.cli operator-pass  # Execute probes
```

**...find which patterns explain my trades**
→ **Capability 7: Pattern Engine**
In YAML:
```yaml
pattern_engine:
  enabled: true
```

**...discover the best entry signals**
→ **Capability 8: Entry Strategy Discovery**
In YAML:
```yaml
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
```

**...send live trading signals to NinjaTrader**
→ **Capability 9: Execution Bridge**
```python
from ta_foundation.strategies.TaFoundationExecutionBridge.bridge_sender import send_message
send_message(
    signal_type="enter_long",
    entry_price=5450.00,
    stop_price=5440.00,
    target_price=5470.00
)
```

**...use the interactive workbench**
→ **Capability 10: Web Workbench**
```bash
python -m ta_foundation.web.app --port 7734
# Visit http://localhost:7734
```

---

## Capability Comparison Matrix

### Scale: Effort to Set Up (1=easy, 5=complex)

| Capability | Setup Effort | Runtime | Data Required | Output Type |
|---|---|---|---|---|
| 1. Backtest Report | ⭐ (1) | 5-10 min | CSV exports | HTML |
| 2. Strategy Discovery | ⭐⭐ (2) | 3-60 min (per stage) | Exports + bars | HTML + CSV |
| 3. Daily Prediction | ⭐⭐ (2) | 1-2 min | Market bars | JSON |
| 4. Horizon Prediction | ⭐⭐⭐ (3) | 5-30 min | Bars + config | JSON + reports |
| 5. NinjaTrader Loop | ⭐⭐⭐⭐ (4) | 10-60 min | Spec + NT | .cs + CSV |
| 6. Agentic Research | ⭐⭐⭐ (3) | Nightly | Ledger + probes | Markdown + JSON |
| 7. Pattern Engine | ⭐⭐⭐ (3) | 5-20 min | Exports + bars | Parquet |
| 8. Entry Strategy | ⭐⭐⭐ (3) | 5-15 min | Exports + bars | HTML + CSV |
| 9. Execution Bridge | ⭐⭐⭐⭐⭐ (5) | Real-time | Live signals | Log files |
| 10. Web Workbench | ⭐ (1) | Instant | Folders selected | Web UI |

### Frequency of Use

| Capability | Typical Frequency | Typical User |
|---|---|---|
| 1. Backtest Report | Weekly | Researcher, trader |
| 2. Strategy Discovery | 2-4 weeks | Researcher |
| 3. Daily Prediction | Daily (after close) | Trader |
| 4. Horizon Prediction | Weekly/monthly | Researcher |
| 5. NinjaTrader Loop | Weekly | Researcher, quant |
| 6. Agentic Research | Nightly (scheduled) | Researcher |
| 7. Pattern Engine | Monthly | Researcher |
| 8. Entry Strategy | 2-4 weeks | Researcher |
| 9. Execution Bridge | Real-time (live trading) | Trader, operations |
| 10. Web Workbench | Daily | All users |

---

## Capability Dependencies

```
Backtest Report
├─ Requires: NinjaTrader exports (CSV)
└─ Optional: market data (minute bars)

Strategy Discovery Funnel
├─ Requires: backtest exports (runs)
├─ Requires: market data (minute bars)
└─ Produces: entry signals → feed to Capability 6 or 5

Daily Prediction
├─ Requires: market data (minute bars)
├─ Requires: Anthropic API key
└─ Optional: economic calendar CSV

Horizon Prediction
├─ Requires: market data (minute bars)
└─ Produces: signals → feed to Capability 9

Autonomous NinjaTrater Loop
├─ Requires: NinjaTrader running + AddOn authorized
├─ Requires: D:\NinjaAccountManager integration
├─ Optional: Ollama (local code repair model)
└─ Produces: optimized strategy → live trading

Agentic Research Program
├─ Requires: research ledger (SQLite)
├─ Requires: discovery probes (from Capability 2)
├─ Requires: Claude API key
└─ Produces: hypothesis records → feed to Capability 5 or 6

Pattern Engine
├─ Requires: backtest exports + market data
└─ Produces: pattern artifacts → diagnostic insights

Entry Strategy Discovery
├─ Requires: backtest exports + market data
└─ Produces: entry rules + C# templates → feed to Capability 5

Execution Bridge
├─ Requires: NinjaTrader running + shell installed
├─ Consumes: signals from Python code
└─ Produces: order fills → live P&L

Web Workbench
├─ Integrates: all above capabilities
├─ Requires: Python Flask server
└─ Produces: interactive UI for discovery & config
```

---

## Common Workflows

### Workflow 1: "Find and Trade a New Signal" (2-4 weeks)

```
Week 1: Backtest Existing Strategy (Capability 1)
  └─ Run CLI with --include-run-images
  └─ Generate HTML report to understand current edge

Week 1-2: Discover New Signals (Capability 2)
  └─ Run 01_quick_scan.yaml
  └─ Identify top signal families
  └─ Run 02_candle_patterns.yaml (or other stages)
  └─ Pick top signals with PF ≥ 1.2

Week 2-3: Validate Signals (Capability 2 continued)
  └─ Run 06_validate.yaml on top signals
  └─ Check IS/OOS degradation < 15%

Week 3-4: Deploy to NinjaTrader (Capability 5)
  └─ Copy signal parameters into StrategySpec
  └─ Run full-loop to author, compile, optimize
  └─ Analyze guardrails scoring
  └─ If pass: move to Capability 9 for live trading

Week 4+: Live Trading (Capability 9)
  └─ Send signals via bridge_sender
  └─ Monitor via soak_monitor
  └─ Track P&L daily
```

### Workflow 2: "Daily Trading with ML Predictions" (Ongoing)

```
Daily (before market open):
  └─ Run Capability 3 (Daily Prediction)
  └─ Get direction + key levels + confidence
  └─ Use output in trading decision

Daily (after close):
  └─ Run outcome measurement (Capability 3)
  └─ Score prediction against actuals
  └─ Update calibration for next day

Weekly (Monday):
  └─ Run Capability 4 (Horizon Prediction backtest)
  └─ Check agent performance by timeframe/regime
  └─ Detect any calibration drift

Monthly:
  └─ Run Capability 7 & 8 (Pattern + Strategy Discovery)
  └─ Identify new signals
  └─ Feed to Capability 5 for optimization
```

### Workflow 3: "Autonomous Research Loop" (Nightly, Scheduled)

```
Every Night (11 PM, after market close):
  └─ Capability 6 Phase B: Triage Pass
  └─ Classify untriaged candidates
  └─ Scribe generates post-mortems

Every Week (Monday):
  └─ Capability 6 Phase B: Weekly Letter
  └─ Narrative summary of week's research

Every 2 Weeks:
  └─ Capability 6 Phase C: Authoring Pass
  └─ Hypothesis Author proposes new probes
  └─ Operator runs Discovery → Operator Pass

Outcome:
  └─ Persistent research ledger (SQLite)
  └─ Drafts queued in inbox (HITL review)
  └─ Graveyarded hypotheses prevent redundant work
```

---

## Quick Troubleshooting

### "I don't know which capability I need"

Use this decision tree:

```
Do you have NinjaTrader backtest exports?
├─ YES
│  ├─ Do you want to analyze what you traded?
│  │  └─ Capability 1: Backtest Report
│  ├─ Do you want to find NEW signals?
│  │  └─ Capability 2: Strategy Discovery (+ 7, 8)
│  └─ Do you want to automate optimization?
│     └─ Capability 5: NinjaTrader Loop
│
└─ NO
   ├─ Do you want to predict next day?
   │  └─ Capability 3: Daily Prediction
   ├─ Do you want to backtest predictions?
   │  └─ Capability 4: Horizon Prediction
   ├─ Do you want to send live signals?
   │  └─ Capability 9: Execution Bridge
   └─ Do you want interactive UI?
      └─ Capability 10: Web Workbench
```

### "Where do I start?"

**If you're new:**
1. Launch **Capability 10** (Web Workbench) — interactive and visual
2. Explore **Capability 1** (Backtest Report) with sample data
3. Read the relevant subsection above

**If you have backtest data:**
1. Start with **Capability 1** (Backtest Report) to understand your current edge
2. Move to **Capability 2** (Strategy Discovery) to find new signals
3. Use **Capability 5** or **9** to deploy to live trading

**If you want automation:**
1. Set up **Capability 6** (Agentic Research) for nightly runs
2. Integrate **Capability 3** (Daily Prediction) for daily forecasts
3. Use **Capability 5** for optimizer automation

---

## Next Steps

1. **Pick a capability** from the matrix above
2. **Read the corresponding section** in COMPLETE_SYSTEM_MAP.md
3. **Find the entry point** and command in this document
4. **Check prerequisites** (NinjaTrader? API key? Market data?)
5. **Run the command** with sample data first
6. **Refer back to this matrix** when you need the next capability

---

## See Also

- **COMPLETE_SYSTEM_MAP.md** — Detailed explanation of each capability
- **GETTING_STARTED.md** — Step-by-step walkthrough
- **DISCOVERY_SUMMARY.md** — System overview and findings
- **Specific capability docs** (NinjaTrader, Prediction, Agentic, etc.) — Coming soon

