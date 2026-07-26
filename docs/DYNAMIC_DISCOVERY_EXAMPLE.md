# Dynamic Parameter Discovery — Hands-On Example

## Overview

Dynamic Parameter Discovery automatically expands parameter ranges based on stage 1 results, eliminating manual guesswork about which parameters to test in deeper stages.

**Problem it solves:**
- Stage 1 tests 250 combos with minimal params
- If candles win (PF=1.5), you manually guess: "maybe TP should go from 20 to 100?"
- With dynamic discovery, it analyzes stage 1 results and tells you exactly which params vary in winners
- Auto-generates stage 2 with expanded ranges

## Workflow

```
Stage 1: Run quick_scan.yaml
  └─→ Candle PF=1.5, uses TP=[20], SL=[10]
      ├─→ Analyze: TP showed 1 value, might be constraining edge
      └─→ Recommend: Expand TP to [10, 20, 30, 40, 50, 60, 70, 80]

Stage 2: Run auto-generated 02_quick_scan_expanded.yaml
  └─→ Candle PF=1.6 with TP=45, SL=15
      ├─→ Analyze: TP and SL both show varying params
      └─→ Recommend: Further expand

Stage 3: Run 03_quick_scan_expanded.yaml
  └─→ Candle PF=1.65 with TP=50, SL=12
      └─→ Ready to deploy
```

## Implementation Example

### 1. Basic Usage

```python
from ta_foundation.analysis.entry_strategies.dynamic_params import (
    recommend_param_expansion,
    generate_expanded_config,
    print_recommendation_summary,
)
from ta_foundation.analysis.entry_strategies.stage_coordinator import (
    DiscoveryStageCoordinator,
)
import yaml

# Load stage 1 results (from CLI output or saved JSON)
stage1_results = sweep_result.get("sweep_results", [])

# Analyze and recommend
recommendation = recommend_param_expansion(
    stage1_results,
    expansion_factor=1.5,  # 50% wider
    min_pf_threshold=1.2,
)

print(print_recommendation_summary(recommendation))

# Generate stage 2 config with expanded params
with open("discovery/01_quick_scan.yaml") as f:
    stage1_cfg = yaml.safe_load(f)

stage2_cfg = generate_expanded_config(
    stage1_cfg,
    stage1_results,
    expansion_factor=1.5,
)

# Save for next run
with open("discovery/02_quick_scan_expanded.yaml", "w") as f:
    yaml.dump(stage2_cfg, f)
```

### 2. Using the Stage Coordinator

```python
from ta_foundation.analysis.entry_strategies.stage_coordinator import (
    DiscoveryStageCoordinator,
)

coordinator = DiscoveryStageCoordinator(
    output_dir="./discovery_results",
    expansion_factor=1.5,
)

# After running stage 1
stage1_results = run_candle_discovery(bars_1m, stage1_config)

# Auto-generate stage 2
stage2_config = coordinator.expand_for_next_stage(
    current_stage=1,
    current_config=stage1_config,
    current_results=stage1_results,
    min_pf_threshold=1.2,
)

# Save for next run
coordinator.save_stage_config(stage2_config, stage=2)

# Save analysis results
coordinator.save_analysis(1, stage1_results.get("sweep_results", []))
```

### 3. Multi-Stage Interactive Flow

```python
from ta_foundation.analysis.entry_strategies.stage_coordinator import (
    interactive_discovery_flow,
)

results = interactive_discovery_flow(
    bars_1m=bars_1m,
    discovery_modules={
        "candle": run_candle_discovery,
        "ma": run_ma_discovery,
    },
    stage_configs_dir=Path("./discovery"),
    output_dir=Path("./discovery_results"),
    num_stages=3,
    min_pf_threshold=1.2,
)
```

## Output Example

Running `recommend_param_expansion()` produces output like:

```
======================================================================
DYNAMIC PARAMETER EXPANSION RECOMMENDATIONS
======================================================================

3 families exceed PF >= 1.2; recommend expanding: candle, ma, orb

CANDLE
  Profit Factor: 1.45
  Trades: 250
  Expand params: tp_ticks, sl_ticks, body_multiplier
    tp_ticks: [20, 60]
    sl_ticks: [10, 30]
    body_multiplier: [1.5, 2.0]

MA
  Profit Factor: 1.32
  Trades: 180
  Expand params: fast_period, slow_period
    fast_period: [9, 21]
    slow_period: [20, 50]

ORB
  Profit Factor: 1.22
  Trades: 120
  Expand params: orb_window_min
    orb_window_min: [5, 30]

======================================================================
```

## Key Functions

### `recommend_param_expansion()`
- **Input:** List of result dicts from a sweep
- **Output:** Recommendations with param ranges to expand
- **Parameters:**
  - `expansion_factor` (1.5): How much wider to make ranges (1.5 = 50% wider)
  - `top_n_per_family` (5): Consider top N results per signal family
  - `min_pf_threshold` (1.2): Only recommend families with PF >= this

### `generate_expanded_config()`
- **Input:** Current stage YAML config + results
- **Output:** New config with expanded param lists
- **How it works:**
  1. Identifies which params vary in top performers
  2. Expands ranges by 50% (or specified factor)
  3. Returns new config ready for next stage

### `expand_numeric_list()`
- **Input:** List like `[20, 40, 60]`
- **Output:** Extended list like `[5, 20, 35, 50, 65, 80]`
- **Parameters:**
  - `factor` (1.5): Expansion multiplier
  - `min_val`, `max_val`: Optional bounds

## Workflow Tips

### Choosing Expansion Factor
- **1.2-1.3:** Conservative, safe
- **1.5:** Balanced (recommended)
- **2.0:** Aggressive, wider exploration

### Choosing Min PF Threshold
- **1.0:** Include everything (no filtering)
- **1.2:** Standard threshold (recommended)
- **1.5:** Only best performers

### Manual Tweaks
You can still edit the auto-generated YAML:

```yaml
candle_discovery:
  patterns:
    large_body:
      body_multiplier: [1.2, 1.5, 2.0]  # Manually adjust
      tp_ticks: [20, 40, 60, 80]        # Keep auto-generated
```

## Next Steps

1. **Run Stage 1:** Use `01_quick_scan.yaml` as-is
2. **Analyze:** Use `recommend_param_expansion()` on results
3. **Generate Stage 2:** Use `generate_expanded_config()`
4. **Run Stage 2:** Execute with auto-generated config
5. **Repeat:** Until results plateau or you're satisfied

## Troubleshooting

**Q: No recommendations generated?**
A: Check that `min_pf_threshold` isn't too high. Lower it to 1.1 or 1.0 to see all families.

**Q: Parameters not expanding?**
A: Ensure the parameter names in recommendations match those in your YAML. Common issue: YAML has `tp_ticks` but results have `tp`.

**Q: Expansion making things worse?**
A: Try lower `expansion_factor` (e.g., 1.2 instead of 1.5). You can also manually adjust ranges in YAML.

**Q: Want to skip a family?**
A: Manually set `enabled: false` in the auto-generated YAML for families you want to skip.

## Integration with CLI

Future enhancement: Add `--auto-expand` flag to CLI:

```bash
python -m ta_foundation.cli.main \
  --input "C:/exports" \
  --output ./results \
  --report-config ./discovery/01_quick_scan.yaml \
  --market-data "D:/MarketData" \
  --auto-expand  # ← New flag
  --no-tick-data
```

This would:
1. Run stage 1
2. Auto-analyze results
3. Generate stage 2 config
4. Run stage 2
5. Repeat for num_stages
