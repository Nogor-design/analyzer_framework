"""
Dynamic Parameter Discovery & Auto-Expansion

Analyzes results from one discovery stage and recommends parameter expansions
for the next stage. For example, if candles win with PF > 1.5, automatically
expands TP/SL ranges to find the optimal levels.

Key functions:
- rank_results(results, metric='profit_factor') — sort by quality
- identify_varying_params(top_results) — which params differ most?
- expand_param_range(params, expansion_factor=1.5) — widen ranges
- generate_expanded_config(stage1_cfg, stage1_results) — produce stage 2 YAML
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


def rank_results(
    results: List[Dict[str, Any]],
    metric: str = "profit_factor",
    min_trades: int = 20,
) -> List[Dict[str, Any]]:
    """
    Rank sweep results by a specified metric (descending).

    Parameters
    ----------
    results      : list of result dicts (from sweep_results)
    metric       : "profit_factor", "sharpe", "expectancy", etc.
    min_trades   : filter out results with fewer trades

    Returns
    -------
    Ranked list of results
    """
    filtered = [r for r in results if r.get("n_trades", 0) >= min_trades]
    if not filtered:
        return []

    def _get_metric(r: Dict) -> float:
        m = r.get("metrics", {})
        val = m.get(metric)
        if val is None or pd.isna(val):
            return -float("inf")
        return float(val)

    return sorted(filtered, key=_get_metric, reverse=True)


def identify_top_by_family(
    results: List[Dict[str, Any]],
    top_n: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group results by signal_id (pattern name) and return top N from each.

    Returns
    -------
    {signal_id: [top_result_1, top_result_2, ...]}
    """
    ranked = rank_results(results)
    by_family: Dict[str, List[Dict[str, Any]]] = {}

    for r in ranked:
        sig_id = r.get("signal_id")
        if not sig_id:
            continue
        by_family.setdefault(sig_id, [])
        if len(by_family[sig_id]) < top_n:
            by_family[sig_id].append(r)

    return by_family


def get_param_ranges_in_results(results: List[Dict[str, Any]]) -> Dict[str, set]:
    """
    Extract all unique values for each parameter across results.

    Returns
    -------
    {param_name: {val1, val2, ...}}
    Example: {"tp_ticks": {20, 40, 60}, "sl_ticks": {10, 20}}
    """
    param_ranges: Dict[str, set] = {}

    for r in results:
        params = r.get("params", {})
        for k, v in params.items():
            param_ranges.setdefault(k, set()).add(v)

    return param_ranges


def identify_varying_params(
    top_results: List[Dict[str, Any]],
) -> Dict[str, Tuple[float, float]]:
    """
    For top performers, identify which params vary most (have largest ranges).

    Returns
    -------
    {param_name: (min_val, max_val)}

    This shows which parameters should be expanded in the next stage.
    """
    ranges = get_param_ranges_in_results(top_results)

    # Filter to numeric params and compute ranges
    varying: Dict[str, Tuple[float, float]] = {}
    for k, vals in ranges.items():
        try:
            numeric_vals = sorted([float(v) for v in vals])
            if len(numeric_vals) > 1:
                varying[k] = (numeric_vals[0], numeric_vals[-1])
        except (ValueError, TypeError):
            pass

    return varying


def recommend_param_expansion(
    stage1_results: List[Dict[str, Any]],
    expansion_factor: float = 2.0,
    top_n_per_family: int = 5,
    min_pf_threshold: float = 1.2,
) -> Dict[str, Any]:
    """
    Analyze stage 1 results and recommend parameter expansions for stage 2.

    Parameters
    ----------
    stage1_results      : list of dicts from stage 1 sweep
    expansion_factor    : how much wider to make param ranges (e.g., 2.0 = 2× wider)
    top_n_per_family    : consider top N results per signal family
    min_pf_threshold    : only expand families with PF >= this

    Returns
    -------
    {
        "recommendations": {
            "candle": {
                "expand": ["tp_ticks", "sl_ticks"],
                "param_ranges": {"tp_ticks": (20, 100), ...},
                "pf": 1.45,
                "n_trades": 250,
            },
            ...
        },
        "summary": "3 families exceed PF threshold; expand candle TP/SL ranges"
    }
    """
    by_family = identify_top_by_family(stage1_results, top_n=top_n_per_family)
    recommendations: Dict[str, Any] = {}

    for family, top_results in by_family.items():
        if not top_results:
            continue

        best = top_results[0]
        pf = best.get("metrics", {}).get("profit_factor")

        if pf is None or pf < min_pf_threshold:
            continue

        varying = identify_varying_params(top_results)

        recommendations[family] = {
            "expand": list(varying.keys()),
            "param_ranges": {k: v for k, v in varying.items()},
            "pf": float(pf),
            "n_trades": best.get("n_trades", 0),
            "top_result_params": best.get("params", {}),
        }

    summary = f"{len(recommendations)} families exceed PF >= {min_pf_threshold}; recommend expanding: {', '.join(recommendations.keys())}"

    return {
        "recommendations": recommendations,
        "summary": summary,
    }


def expand_numeric_list(
    values: List[float],
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    factor: float = 1.5,
) -> List[float]:
    """
    Expand a list of values by extending min/max.

    Parameters
    ----------
    values     : existing values [20, 40, 60]
    min_val    : optional floor
    max_val    : optional ceiling
    factor     : how much to expand beyond current extremes (1.5 = 50%)

    Returns
    -------
    Extended list with same distribution
    """
    if not values:
        return []

    values = sorted(list(set(float(v) for v in values)))

    # Compute new boundaries
    current_min = min(values)
    current_max = max(values)
    span = current_max - current_min
    if span == 0:
        span = current_min if current_min != 0 else 1

    new_min = current_min - (span * (factor - 1) / 2)
    new_max = current_max + (span * (factor - 1) / 2)

    # Respect user bounds
    if min_val is not None:
        new_min = max(new_min, min_val)
    if max_val is not None:
        new_max = min(new_max, max_val)

    # Generate new values matching the original distribution
    expanded = []
    if len(values) == 1:
        expanded = list(range(int(new_min), int(new_max) + 1, max(1, int(span) or 1)))
    else:
        # Preserve relative spacing
        for i in range(len(values) + 2):  # Add 2 extra points for expansion
            frac = i / (len(values) + 1) if len(values) > 0 else 0.5
            val = new_min + (new_max - new_min) * frac
            expanded.append(val)

    # Keep as integers if they were integers originally
    if all(float(v).is_integer() for v in values):
        expanded = sorted(list(set(int(v) for v in expanded if v >= (min_val or 0))))
    else:
        expanded = sorted(list(set(round(v, 2) for v in expanded if v >= (min_val or 0))))

    return expanded


def generate_expanded_config(
    stage1_config: Dict[str, Any],
    stage1_results: List[Dict[str, Any]],
    expansion_factor: float = 1.5,
) -> Dict[str, Any]:
    """
    Given stage 1 config and results, generate stage 2 config with expanded params.

    Parameters
    ----------
    stage1_config    : the YAML config dict used for stage 1
    stage1_results   : output from stage 1 sweep
    expansion_factor : how much to expand (1.5 = 50% wider)

    Returns
    -------
    New config dict suitable for stage 2 (or higher) discovery
    """
    import copy

    rec = recommend_param_expansion(stage1_results, expansion_factor=expansion_factor)

    stage2_config = copy.deepcopy(stage1_config)
    recommendations = rec.get("recommendations", {})

    # Apply expansions to each winning family
    for family, rec_info in recommendations.items():
        # Map family name to config section
        family_config_key = family  # e.g., "large_body", "ma_cross", etc.

        # Navigate to the pattern/signal config
        patterns = stage2_config.get("patterns", {})
        signals = stage2_config.get("signals", {})

        if family_config_key in patterns:
            pattern_cfg = patterns[family_config_key]
            for param_name, (min_val, max_val) in rec_info.get("param_ranges", {}).items():
                if param_name in pattern_cfg and isinstance(pattern_cfg[param_name], list):
                    expanded = expand_numeric_list(
                        pattern_cfg[param_name],
                        min_val=min_val,
                        max_val=max_val,
                        factor=expansion_factor,
                    )
                    pattern_cfg[param_name] = expanded

        if family_config_key in signals:
            signal_cfg = signals[family_config_key]
            for param_name, (min_val, max_val) in rec_info.get("param_ranges", {}).items():
                if param_name in signal_cfg and isinstance(signal_cfg[param_name], list):
                    expanded = expand_numeric_list(
                        signal_cfg[param_name],
                        min_val=min_val,
                        max_val=max_val,
                        factor=expansion_factor,
                    )
                    signal_cfg[param_name] = expanded

    # Expand TP/SL if they were tight in stage 1
    outcome = stage2_config.get("outcome", {})
    if outcome and "ticks" in outcome:
        ticks_cfg = outcome["ticks"]
        if "take_profit" in ticks_cfg and isinstance(ticks_cfg["take_profit"], list):
            ticks_cfg["take_profit"] = expand_numeric_list(
                ticks_cfg["take_profit"],
                factor=expansion_factor,
            )
        if "stop" in ticks_cfg and isinstance(ticks_cfg["stop"], list):
            ticks_cfg["stop"] = expand_numeric_list(
                ticks_cfg["stop"],
                factor=expansion_factor,
            )

    return stage2_config


def print_recommendation_summary(rec: Dict[str, Any]) -> str:
    """Format recommendation dict into human-readable text."""
    lines = [
        "=" * 70,
        "DYNAMIC PARAMETER EXPANSION RECOMMENDATIONS",
        "=" * 70,
        "",
        rec.get("summary", ""),
        "",
    ]

    for family, info in rec.get("recommendations", {}).items():
        lines.append(f"\n{family.upper()}")
        lines.append(f"  Profit Factor: {info.get('pf', '?')}")
        lines.append(f"  Trades: {info.get('n_trades', '?')}")
        lines.append(f"  Expand params: {', '.join(info.get('expand', []))}")

        for param, (min_v, max_v) in info.get("param_ranges", {}).items():
            lines.append(f"    {param}: [{min_v}, {max_v}]")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)
