# NinjaTrader Integration Runbook

**Status:** Complete guide to the Autonomous Strategy Loop  
**Last Updated:** May 24, 2026  
**Audience:** Traders, researchers, and system administrators  

---

## Overview

The **Autonomous NinjaTrader Strategy Loop** is a closed-loop system that removes manual babysitting from strategy development. It automates the entire workflow:

```
Strategy Intent (JSON)
  ↓
Generate NinjaScript (.cs)
  ↓
Install into NinjaTrader
  ↓
Observe auto-compile
  ↓
Repair errors (deterministic + optional LLM)
  ↓
Compile-clean strategy
  ↓
Generate optimizer template
  ↓
Run Strategy Analyzer
  ↓
Ingest & analyze results
  ↓
Candidate or Archive decision
```

**The guiding principle:** NinjaTrader is the worker. ta_foundation is the memory and conductor.

---

## Prerequisites: What You Need

### 1. Software & Installations

| Requirement | Version | Location | Notes |
|---|---|---|---|
| NinjaTrader | 8.x | `C:\Users\Owner\Documents\NinjaTrader 8` | Must be installed at standard location. |
| BatchStrategyOptimizerAddOn | Latest | `D:\ninjatraderOptimizer` (external) | C# AddOn compiled + authorized. |
| ta_foundation | Latest | Repository | `pip install -e .` |
| Ollama (optional) | Latest | Local service | Only for `--repair-llm` feature. |
| Python | 3.9+ | System | From `pip install -e .` environment. |

### 2. NinjaTrader Configuration

#### Required: AddOn Authorization
The `BatchStrategyOptimizerAddOn` must be compiled and authorized in NinjaTrader:

1. **Locate the AddOn project:** `D:\ninjatraderOptimizer`
2. **Compile it** (using Visual Studio or command line)
3. **Copy the compiled DLL** to:
   ```
   C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\AddOns
   ```
4. **Restart NinjaTrader**
5. **Authorize when prompted:** A dialog appears asking to update server definitions and authorize the new AddOn. Click **Yes**.
6. **Verify:** The Control Center should show the AddOn loaded in the output/trace.

#### Required: IPC Paths
The AddOn uses these paths for command/status communication:

- **Command file:** `C:\temp\nt8_command.json` (Python writes, AddOn reads)
- **Status file:** `C:\temp\nt8_status.json` (AddOn writes, Python reads)
- **Compile output:** `C:\ta_foundation\nt_compile_loop\compiler_errors\` (AddOn writes)

**Ensure these directories exist** before running any commands.

#### Required: Compile Loop Root
Python needs write access to a compile-loop workspace:

- **Default:** `C:\ta_foundation\nt_compile_loop`
- **Must exist** (or --compile-root flag points to it)

#### Optional: Strategy Analyzer
For the optimizer phase, NinjaTrader Strategy Analyzer must be accessible. It comes with NinjaTrader 8. No extra installation needed.

### 3. Credentials & Secrets

#### NinjaTrader Login
Store your NinjaTrader password in a local file:

```
C:\Users\Owner\Downloads\P.txt
```

⚠️ **NEVER:**
- Echo this file to console
- Include it in logs
- Share it via git
- Print it in generated documentation
- Store it in source code

The system reads it **only in memory** for automated login.

#### API Keys (if using LLM repair)
If using `--repair-llm`, Ollama must be running locally. No credentials needed — just local network access.

### 4. Ollama Setup (Optional, for LLM Repair)

If you want AI-assisted repair for compiler errors the heuristics can't fix:

1. **Install Ollama:** https://ollama.ai
2. **Start the service:**
   ```bash
   ollama serve
   ```
3. **Pull a code model:**
   ```bash
   ollama pull qwen3-coder:30b
   ```
   (Or another code-tuned model. Larger = better repairs.)
4. **Verify it's running:**
   ```bash
   curl http://localhost:11434/api/tags
   ```

The default flags assume `qwen3-coder:30b` on `http://localhost:11434`. Override with `--repair-llm-model` and `--repair-llm-url` if needed.

### 5. Network & Timing

- **Startup wait:** NinjaTrader cold-starts slowly. Allow **1–2 minutes** after launch before sending compile/optimizer commands.
- **AddOn latency:** After clicking the authorization prompt, wait **60–150 seconds** for full AddOn startup.
- **File I/O delays:** Poll timeouts default to 120 seconds for compile, 3600 seconds for optimizer.

---

## Quick Start: Your First Strategy Loop

### Step 1: Prepare NinjaTrader

```powershell
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready
```

This command:
- Starts NinjaTrader (or connects to a running instance)
- Logs in (reads password from `P.txt`)
- Clicks the AddOn authorization prompt
- Waits for the Control Center to be ready
- Returns when safe to proceed

**Output:**
```json
{
  "state": "ready",
  "message": "Control Center is responding",
  "prompt_clicked": true,
  "login_submitted": true
}
```

### Step 2: Create a Strategy Spec

A strategy spec is a small JSON file describing what you want to build:

```json
{
  "strategy_name": "MyCrossBot",
  "family": "sma_cross",
  "intent": "9/21 SMA cross on NQ with fixed tick stop and target. Target 24 ticks, stop 16 ticks.",
  "parameters": {
    "FastPeriod": 9,
    "SlowPeriod": 21,
    "ProfitTargetTicks": 24,
    "StopLossTicks": 16,
    "Reverse": false
  },
  "risk_note": "Backtest only. Not cleared for live trading."
}
```

Save this as `my_strategy.json`.

**Field reference:**

| Field | Required | Type | Example |
|---|---|---|---|
| `strategy_name` | yes | string | Must be a valid C# class name and filename stem |
| `family` | yes | string | `sma_cross`, `sma_cross_smoke` (or register a new one) |
| `intent` | no | string | Human-readable goal for repair prompts |
| `parameters` | no | object | Strategy-specific knobs; varies by family |
| `risk_note` | no | string | Carries into decision record |

### Step 3: Run the Full Loop

```powershell
python -m ta_foundation.nt_strategy_loop.cli full-loop `
  --spec my_strategy.json `
  --instrument "NQ 06-26" `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --overwrite
```

This command:
1. Generates NinjaScript from the spec
2. Installs into NinjaTrader
3. Observes auto-compile, repairs if needed (up to 5 attempts)
4. Generates optimizer template
5. Runs Strategy Analyzer
6. Ingests results and scores against guardrails
7. Writes a decision (candidate/archive) and session folder

**Expected output:**
```json
{
  "session_id": "session_20260524_173920",
  "decision": "candidate",
  "stop_reason": {
    "code": "optimizer_complete",
    "message": "Optimizer finished successfully"
  },
  "compile_result": {
    "state": "succeeded"
  },
  "optimizer_result": {
    "decision": "candidate",
    "passing_rows": 12,
    "best_pf": 2.15,
    "best_drawdown": 1850.0
  }
}
```

**Exit codes:**
- `0` = Success (decision = "candidate")
- `2` = Partial success (decision = "archive" or optimizer incomplete)
- `3` = Failure (repair never reached compile-clean)

### Step 4: Review the Session Folder

All artifacts are written to `.ta_artifacts/nt_strategy_lab/sessions/<session_id>/`:

```
<session_id>/
  session.json              # Metadata and decisions
  strategy_spec.json        # Your original spec
  source_request.md         # Human-readable intent
  attempts/
    attempt_001/
      MyCrossBot.cs         # Generated source
      compile_status.json   # Compile result
      repair_prompt.md      # What repair was asked to fix (if repaired)
      repair_summary.md     # What repair changed
    attempt_002/            # (if repairs occurred)
      ...
  compile_clean/
    MyCrossBot.cs           # First successful compile
    compile_status.json     # Compile observation
  optimizer/
    optimizer_analysis.json # Guardrail verdict
    nt_output/              # Ingested *_Optimization.csv files
  decisions/
    STRATEGY_LOOP_SUMMARY.md   # Human-readable summary
    NEXT_ACTION.md             # Recommended next step
  manifest.json             # Index of all artifacts
```

Start your review at `decisions/STRATEGY_LOOP_SUMMARY.md`.

---

## Understanding the Workflow

### Phase 1: Author & Repair Loop

**Entry:** `repair-loop` command (or first half of `full-loop`)

**What happens:**

1. **Author:** Generate `.cs` from your strategy spec
2. **Install:** Copy `.cs` into NinjaTrader strategy folder
3. **Observe:** Let NinjaTrader auto-compile and collect any errors
4. **Repair:** If errors occur, fix them (deterministically or with LLM)
5. **Loop:** Repeat until compile-clean or max attempts reached

**Repair logic:**

```
Attempt 1: Generate initial source
  ↓ Install & Compile
  ↓ Error? (e.g., "CrossAbove not defined")
  ↓
Attempt 2: Deterministic heuristic fix
  ├─ Fix class-name mismatches
  ├─ Add missing `using` directives
  └─ If fix declines → try LLM
  ↓ Install & Compile
  ↓ Error? (different error)
  ↓
Attempt 3: LLM repair (if --repair-llm enabled)
  ├─ Send spec + code + errors to Ollama
  └─ Extract corrected .cs from response
  ↓ Install & Compile
  ↓ Compile success? → Phase 2
  ↓ No → check for repeated errors
         If same error: STOP (repeated_signature)
         Else: continue loop
```

**Stop reasons (repair phase):**

| Code | Meaning | Recovery |
|---|---|---|
| `compile_clean` | ✅ Success | Proceed to Phase 2 |
| `max_attempts` | Exhausted --max-repair-attempts | Increase limit or fix spec |
| `repeated_signature` | Same error came back twice | Error is hard; needs manual fix |
| `repair_declined` | No heuristic + no LLM fix | Enable --repair-llm or fix manually |
| `peer_compile_block` | Another .cs in bin\Custom has errors | Fix or quarantine the blocking file |
| `stale_assembly` | NinjaTrader.Custom.dll wasn't rewritten | Restart AddOn; re-run ensure-nt-ready |

### Phase 2: Optimizer & Analysis

**Entry:** `optimizer-bridge` command (or second half of `full-loop`)

**What happens:**

1. **Seed Template:** Generate Strategy Analyzer seed XML from compile-clean `.cs`
2. **Chunking:** Break parameter sweep into chunks (e.g., 5000 combinations per chunk)
3. **RunBatch:** Send chunks to NinjaTrader Strategy Analyzer via AddOn
4. **Poll:** Check status every 5 seconds (configurable)
5. **Ingest:** Read `*_Optimization.csv` results
6. **Score:** Check guardrails (max drawdown, min trades, min profit factor)
7. **Decide:** candidate (≥1 row passes) or archive (no rows pass)

**Guardrails defaults:**

```
--max-drawdown 2500         # Max drawdown in dollars
--min-trades 10             # Minimum number of trades in backtest
--min-profit-factor 1.5     # Min (gross profit / gross loss) ratio
```

**Decision logic:**

| Condition | Decision | Exit code |
|---|---|---|
| ≥1 optimizer row passes guardrails | `candidate` | 0 |
| All rows fail guardrails | `archive` | 2 |
| Optimizer didn't complete | `incomplete` | 2 |

**Optimizer result structure:**

```json
{
  "decision": "candidate",
  "best_row": {
    "param_FastPeriod": 9,
    "param_SlowPeriod": 21,
    "param_ProfitTargetTicks": 24,
    "param_StopLossTicks": 16,
    "total_net_profit": 4250.0,
    "max_drawdown": 1850.0,
    "profit_factor": 2.15,
    "trade_count": 42
  },
  "passing_rows": 12,
  "best_pf": 2.15,
  "best_drawdown": 1850.0
}
```

---

## CLI Commands Reference

### `ensure-nt-ready`

**Purpose:** Start/login NinjaTrader and authorize the AddOn.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready [options]
```

**Common options:**

| Flag | Default | Description |
|---|---|---|
| `--restart` | false | Close and restart NinjaTrader before waiting |
| `--startup-wait-seconds` | 150 | Seconds to wait for AddOn startup after authorization prompt |
| `--nt-exe` | `C:\Program Files\...\NinjaTrader.exe` | Path to NinjaTrader executable |
| `--username` | `eirwin` | NinjaTrader login username |
| `--password-file` | `C:\Users\Owner\Downloads\P.txt` | Local password file |

**Example:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready --restart
```

---

### `observe-compile`

**Purpose:** Install a `.cs` file, let NinjaTrader auto-compile it, and report the result.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli observe-compile --source <path> [options]
```

**Required options:**

| Flag | Description |
|---|---|
| `--source` | Path to the NinjaScript `.cs` file |

**Optional options:**

| Flag | Default | Description |
|---|---|---|
| `--strategy-name` | Derived from filename | C# class name |
| `--overwrite` | false | Overwrite existing strategy file |
| `--timeout-seconds` | 120 | Max time to wait for compile |
| `--wait-for-quiet-seconds` | 3 | Time to wait before checking compile result |
| `--out` | (stdout only) | Write JSON result to file |

**Example:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli observe-compile `
  --source "C:\generated\MyCrossBot.cs" `
  --strategy-name MyCrossBot `
  --overwrite `
  --out result.json
```

**Output:**
```json
{
  "ok": true,
  "state": "succeeded",
  "compiled": true,
  "strategy_name": "MyCrossBot",
  "error_count": 0
}
```

---

### `repair-loop`

**Purpose:** Author a strategy, install it, and repair until compile-clean.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop --spec <path> [options]
```

**Required options:**

| Flag | Description |
|---|---|
| `--spec` | Path to strategy spec JSON file |

**Optional options:**

| Flag | Default | Description |
|---|---|---|
| `--max-repair-attempts` | 5 | Max loop iterations |
| `--compile-mode` | `live` | `live` (NinjaTrader) or `fixture` (testing) |
| `--lab-root` | `.ta_artifacts/nt_strategy_lab/sessions` | Session root folder |
| `--overwrite` | false | Overwrite existing strategy file |
| `--repair-llm` | false | Use Ollama for LLM-assisted repair |
| `--repair-llm-model` | `qwen3-coder:30b` | Ollama model name |
| `--repair-llm-url` | `http://localhost:11434` | Ollama server URL |

**Example with deterministic repair only:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json `
  --max-repair-attempts 5 `
  --overwrite
```

**Example with LLM-assisted repair:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json `
  --max-repair-attempts 5 `
  --repair-llm `
  --repair-llm-model qwen3-coder:30b `
  --repair-llm-url http://localhost:11434 `
  --overwrite
```

---

### `optimizer-bridge`

**Purpose:** Run optimizer on a compile-clean strategy and ingest results.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli optimizer-bridge [options]
```

**Required options:**

| Flag | Description |
|---|---|
| `--session-dir` | Existing strategy loop session directory |
| `--compile-clean-source` | Path to compile-clean `.cs` file |

**Optional options:**

| Flag | Default | Description |
|---|---|---|
| `--instrument` | `NQ 06-26` | Instrument for optimizer (e.g., `ES 06-26`) |
| `--market-suffix` | `NQ` | Market suffix for data lookup |
| `--max-drawdown` | 2500 | Guardrail: max drawdown in dollars |
| `--min-trades` | 10 | Guardrail: min trade count |
| `--min-profit-factor` | 1.5 | Guardrail: min profit factor |
| `--keep-best-results` | 500 | Keep top N results in memory |
| `--max-combinations-per-chunk` | 5000 | Chunk size for RunBatch |
| `--poll-seconds` | 5 | Status check interval |
| `--timeout-seconds` | 3600 | Max time to wait for optimizer |

**Example:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli optimizer-bridge `
  --session-dir ".ta_artifacts\nt_strategy_lab\sessions\session_20260524_173920" `
  --compile-clean-source ".ta_artifacts\nt_strategy_lab\sessions\session_20260524_173920\compile_clean\MyCrossBot.cs" `
  --instrument "NQ 06-26" `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

---

### `full-loop`

**Purpose:** End-to-end: author → repair → compile-clean → optimize → analyze.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli full-loop --spec <path> [options]
```

**Required options:**

| Flag | Description |
|---|---|
| `--spec` | Path to strategy spec JSON file |

**Optional options:** (combines repair-loop + optimizer-bridge)

| Flag | Default | Description |
|---|---|---|
| `--max-repair-attempts` | 5 | Max repair loop iterations |
| `--instrument` | `NQ 06-26` | Instrument for optimizer |
| `--market-suffix` | `NQ` | Market suffix |
| `--max-drawdown` | 2500 | Guardrail: max drawdown |
| `--min-trades` | 10 | Guardrail: min trades |
| `--min-profit-factor` | 1.5 | Guardrail: min PF |
| `--compile-mode` | `live` | `live` or `fixture` |
| `--overwrite` | false | Overwrite strategy file |
| `--repair-llm` | false | Use LLM repair |
| `--repair-llm-model` | `qwen3-coder:30b` | LLM model |
| `--repair-llm-url` | `http://localhost:11434` | LLM URL |
| `--keep-best-results` | 500 | Top results to keep |
| `--max-combinations-per-chunk` | 5000 | Optimizer chunk size |
| `--optimizer-poll-seconds` | 5 | Optimizer status check interval |
| `--optimizer-timeout-seconds` | 3600 | Optimizer max wait time |

**Recommended example:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli full-loop `
  --spec my_strategy.json `
  --instrument "NQ 06-26" `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --max-repair-attempts 5 `
  --overwrite
```

---

### `generate-seed-template`

**Purpose:** Generate Strategy Analyzer seed XML from a compile-clean `.cs` file.

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli generate-seed-template `
  --source <path> `
  --output <path> `
  [options]
```

**Required options:**

| Flag | Description |
|---|---|
| `--source` | Compile-clean NinjaScript `.cs` file |
| `--output` | Output XML file path |

**Optional options:**

| Flag | Default | Description |
|---|---|---|
| `--instrument` | `NQ 06-26` | Instrument for the template |
| `--optimizer-type` | `NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer` | Optimizer class |
| `--optimization-fitness` | `NinjaTrader.NinjaScript.OptimizationFitnesses.MaxNetProfit` | Fitness function |
| `--keep-best-results` | 500 | Results to keep |

**Example:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli generate-seed-template `
  --source "compile_clean\MyCrossBot.cs" `
  --output "seed_template.xml" `
  --instrument "NQ 06-26"
```

---

### `smoke-loop`

**Purpose:** Run a tiny test loop end-to-end (useful for verifying setup).

**Usage:**
```powershell
python -m ta_foundation.nt_strategy_loop.cli smoke-loop [options]
```

**Optional options:**

| Flag | Default | Description |
|---|---|---|
| `--compile-mode` | `fixture` | `fixture` (offline) or `live` (with NinjaTrader) |
| `--strategy-name` | `AutonomousLoopSmoke` | Name for test strategy |

**Example (offline):**
```powershell
python -m ta_foundation.nt_strategy_loop.cli smoke-loop --compile-mode fixture
```

**Example (with live NinjaTrader):**
```powershell
python -m ta_foundation.nt_strategy_loop.cli smoke-loop --compile-mode live
```

---

## Decisions & Exit Codes

### Full-Loop Decisions

The `full-loop` command ends with one of these decisions:

| Decision | Meaning | Exit Code |
|---|---|---|
| `candidate` | Compile-clean + ≥1 optimizer row passed guardrails | 0 |
| `archive` | Compile-clean but no optimizer row passed guardrails | 2 |
| `incomplete` | Optimizer didn't reach a terminal state | 2 |
| `halted` | Repair never reached compile-clean | 3 |

**Decision record:** Always check `decisions/STRATEGY_LOOP_SUMMARY.md` in the session folder.

---

## Troubleshooting

### "Commands hang or time out right after launch"

**Cause:** NinjaTrader is still cold-starting.

**Fix:**
1. Wait 1–2 minutes after NinjaTrader starts
2. Or run `ensure-nt-ready` again before retrying

---

### "peer_compile_block" stop reason

**Cause:** Another `.cs` file in `bin\Custom\Strategies` has compile errors and blocks the whole assembly rebuild.

**Fix:**
1. Open NinjaTrader Control Center
2. Find the file listed in the error message
3. Either fix it or move it to a quarantine folder
4. Re-run the command

---

### "stale_assembly" stop reason

**Cause:** NinjaTrader didn't rebuild `NinjaTrader.Custom.dll` even though the strategy file changed.

**Fix:**
1. Confirm the BatchStrategyOptimizerAddOn is authorized
2. Run `ensure-nt-ready` to re-authorize
3. Retry the repair loop

---

### "--repair-llm always declines (no LLM repair attempted)"

**Cause:** Ollama is not running or the model name is wrong.

**Fix:**
1. Start Ollama: `ollama serve`
2. Verify the model exists: `ollama list`
3. Verify the model name matches `--repair-llm-model` (default `qwen3-coder:30b`)
4. Retry the command

---

### "optimizer-bridge reports incomplete"

**Cause:** NinjaTrader/AddOn not running or not authorized.

**Fix:**
1. Run `ensure-nt-ready`
2. Confirm the AddOn is loaded in Control Center → Output
3. Verify `C:\temp\` exists and is writable
4. Retry against the same session: `optimizer-bridge --session-dir <path>`

---

### "no in-tree renderer for family ..."

**Cause:** The strategy spec's `family` field doesn't match any registered renderer.

**Fix:**
1. Check the strategy spec's `family` field (e.g., `sma_cross`)
2. List built-in families (see [Built-in Strategy Families](#built-in-strategy-families) below)
3. Use a built-in family, or register a custom one in `authoring.py`

---

## Built-in Strategy Families

The system includes these pre-registered strategy families:

| Family | Description | Parameters |
|---|---|---|
| `sma_cross` | Simple SMA crossover (MA1 > MA2 = long) | `FastPeriod`, `SlowPeriod`, `ProfitTargetTicks`, `StopLossTicks`, `Reverse` |
| `sma_cross_smoke` | Tiny SMA crossover for smoke testing | `FastPeriod`, `SlowPeriod` |

To register a custom family:

1. Create a renderer in `src/ta_foundation/nt_strategy_loop/authoring.py`
2. Register it with `authoring.register_family(name, renderer_func)`
3. Use the new family name in your strategy spec

---

## Session Folder Structure

Each run creates an append-only session folder:

```
.ta_artifacts/nt_strategy_lab/sessions/<session_id>/
  session.json                       # Metadata (ID, timestamp, decision)
  strategy_spec.json                 # Your original spec
  source_request.md                  # Human-readable intent
  
  attempts/
    attempt_001/
      <StrategyName>.cs              # Generated source
      compile_status.json            # Compile result
      repair_prompt.md               # (if repaired) What repair was asked
      repair_summary.md              # (if repaired) What repair changed
    attempt_002/
      ...
  
  compile_clean/
    <StrategyName>.cs                # First successful compile
    compile_status.json              # Compile observation
  
  optimizer/
    optimizer_analysis.json          # Guardrail verdict + best result
    nt_output/                       # Ingested *_Optimization.csv files
      RunBatch_001_Optimization.csv
      ...
  
  decisions/
    STRATEGY_LOOP_SUMMARY.md         # Human-readable summary
    NEXT_ACTION.md                   # Recommended next step
  
  manifest.json                      # Index of all artifacts
```

**Key files to review:**

- **`decisions/STRATEGY_LOOP_SUMMARY.md`** — Start here. Summary of what happened.
- **`optimizer_analysis.json`** — Best optimizer row and guardrail verdict.
- **`compile_clean/<Name>.cs`** — The successful compiled strategy (ready to deploy).

---

## Integration with Live Trading

Once you have a `candidate` decision:

1. **Review** `decisions/STRATEGY_LOOP_SUMMARY.md` carefully
2. **Copy** `compile_clean/<Name>.cs` into your deployment folder
3. **Enable** in NinjaTrader (not before human review!)
4. **Monitor** paper-trading first
5. **Track** daily P&L and risk metrics

**Key safety gates:**

- ✅ Strategy is compile-clean
- ✅ Optimizer results passed guardrails
- ⚠️ Walk-forward validation recommended before live
- ⚠️ Robustness checks recommended (neighborhood testing, stress tests)
- ⚠️ Human approval required for live trading

Do NOT rely on optimizer results alone. Backtesting is not live trading.

---

## Advanced Usage

### Running Only the Repair Loop

If you already have a `.cs` file and just want to repair it:

```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json `
  --max-repair-attempts 5 `
  --overwrite
```

Session will end with `decision: compile_clean` (exit 0) or `halted` (exit 3).

### Running Only the Optimizer

If you already have a compile-clean `.cs` and just want to optimize:

```powershell
python -m ta_foundation.nt_strategy_loop.cli optimizer-bridge `
  --session-dir ".ta_artifacts\nt_strategy_lab\sessions\<id>" `
  --compile-clean-source "<id>\compile_clean\MyStrategy.cs" `
  --instrument "NQ 06-26" `
  --max-drawdown 2500
```

### Dry Run (Fixture Mode)

Test the whole workflow without NinjaTrader:

```powershell
python -m ta_foundation.nt_strategy_loop.cli full-loop `
  --spec my_strategy.json `
  --compile-mode fixture `
  --instrument "NQ 06-26"
```

Useful for validating specs and session folder structure before live run.

### Custom Repair Policy

Modify retry limits and stop conditions:

```powershell
python -m ta_foundation.nt_strategy_loop.cli repair-loop `
  --spec my_strategy.json `
  --max-repair-attempts 10  # More attempts
```

Or edit `RepairPolicy` in `policy.py` for complex policies.

---

## Integration with Execution Bridge

After you have a `candidate` strategy, send signals to NinjaTrader via the Execution Bridge:

```python
from ta_foundation.cli.bridge_sender import send_message

send_message(
    signal_type="enter_long",
    entry_price=5450.00,
    stop_price=5440.00,
    target_price=5470.00
)
```

See **EXECUTION_BRIDGE_GUIDE.md** for full details.

---

## See Also

- **nt_strategy_loop/README.md** — Module overview
- **docs/designs/autonomous_ninjatrader_strategy_loop.md** — Design document
- **docs/handoffs/observe_compile_strategy_analyzer_parity_*.md** — Implementation status
- **EXECUTION_BRIDGE_GUIDE.md** — Live trading signal integration

---

## Support & Debugging

### Getting Help

1. **Check the session folder:** `decisions/STRATEGY_LOOP_SUMMARY.md` often has the answer
2. **Review troubleshooting:** See the [Troubleshooting](#troubleshooting) section above
3. **Check logs:** Output from commands prints JSON with detailed state
4. **Enable debug mode:** (Future) Set `DEBUG=1` environment variable for verbose logging

### Common Mistakes

| Mistake | Fix |
|---|---|
| Forgot `--overwrite` flag | Add it: `--overwrite` |
| Strategy spec JSON is invalid | Validate with `python -m json.tool spec.json` |
| NinjaTrader not running | Run `ensure-nt-ready` first |
| AddOn not authorized | Run `ensure-nt-ready --restart` to re-authorize |
| Ollama not running (but `--repair-llm` used) | Start Ollama: `ollama serve` |
| Wrong strategy family name | Check built-in families or register custom one |

---

## Success Checklist

Use this checklist after your first successful full-loop:

- [ ] Strategy spec was valid JSON
- [ ] NinjaScript generated without errors
- [ ] Compile loop succeeded (compile-clean .cs in `compile_clean/`)
- [ ] Optimizer ran and completed (results in `nt_output/`)
- [ ] At least one optimizer row passed guardrails
- [ ] `decisions/STRATEGY_LOOP_SUMMARY.md` shows `decision: candidate`
- [ ] Exit code was 0
- [ ] Ready to review and deploy

Next: Copy `compile_clean/<Name>.cs` to deployment folder and test in paper trading.

