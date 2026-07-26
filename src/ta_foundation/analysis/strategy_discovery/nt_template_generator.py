from __future__ import annotations

"""
NinjaTrader Template Generator
================================
Reads Strategy Discovery analysis results for a single run and produces a
NinjaTrader 8 XML parameter template for the StrategyDiscoveryFilter strategy.

Entry point: generate_nt_template(sd_dict, options) -> GeneratedTemplate

The function reads data from the strategy_discovery dict attached to
pkg.metadata["derived"]["strategy_discovery"] and returns:
  - xml_str      : full XML string ready to save as .xml
  - parameters   : {param_name: value} dict
  - reasoning    : {param_name: explanation_str} dict
  - warnings     : list of warning strings (data gaps, low confidence)

Usage from section renderer:
  from ta_foundation.analysis.strategy_discovery.nt_template_generator import generate_nt_template
  result = generate_nt_template(sd, options={"tick_value": 5.0, "tick_size": 0.25})
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class GeneratedTemplate:
    xml_str: str
    parameters: Dict[str, Any]
    reasoning: Dict[str, str]
    warnings: List[str]


# ---------------------------------------------------------------------------
# Policy family parsing helpers
# ---------------------------------------------------------------------------

def _family_of(policy_name: str) -> str:
    name = str(policy_name).lower()
    if name.startswith("fixed_rr"):
        return "fixed_rr"
    if name.startswith("be_trail"):
        return "be_atr_trail"
    if name.startswith("chandelier"):
        return "chandelier"
    if name.startswith("atr_trail"):
        return "atr_trail"
    if name.startswith("giveback"):
        return "giveback"
    return "other"


def _parse_fixed_rr(policy_name: str):
    """Parse fixed_rr_s{stop}_t{target} -> (stop_ticks, target_ticks)"""
    m = re.search(r"_s(\d+)_t(\d+)", policy_name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _parse_atr_trail(policy_name: str):
    """Parse atr_trail_s{stop_mult}_t{trail_mult} -> (stop_mult, trail_mult)"""
    m = re.search(r"_s([\d.]+)_t([\d.]+)", policy_name)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _parse_giveback(policy_name: str):
    """Parse giveback_arm{arm}_gb{giveback} -> giveback_pct"""
    m = re.search(r"arm(\d+)_gb(\d+)", policy_name)
    if m:
        arm, gb = int(m.group(1)), int(m.group(2))
        if arm > 0:
            return round(gb / arm, 2)
    return 0.40


# ---------------------------------------------------------------------------
# Parameter extraction helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        import math
        return None if not math.isfinite(f) else f
    except Exception:
        return None


def _dollars_to_ticks(usd: Optional[float], tick_value: float) -> Optional[int]:
    """Convert a dollar MAE/MFE value to whole ticks."""
    if usd is None or tick_value <= 0:
        return None
    ticks = usd / tick_value
    return max(1, round(ticks))


def _extract_mae_mfe(sd: Dict[str, Any], tick_value: float, warnings: List[str]):
    """
    Extract StopTicks and TargetTicks from (in priority order):
      1. Trade MAE/MFE profile (p75 MAE winners → stop, p50 MFE all → target)
      2. Signal exit sweep overall_best (corpus-optimised stop/target grid winner)
      3. Signal corpus MAE/MFE p50 from top signal rule (×1.2 / ×0.75 scaling)
      4. Hard-coded defaults (60 / 90) with warning

    Returns (stop_ticks, target_ticks, stop_reasoning, target_reasoning).
    """
    mf = (sd.get("mae_mfe_profile") or {})
    dist = mf.get("distributions") or {}

    # --- Priority 1: trade MAE/MFE profile ---
    mae_usd = _safe_float(dist.get("mae_winners_p75"))
    mfe_usd = _safe_float(dist.get("mfe_all_p50"))
    stop_ticks   = _dollars_to_ticks(mae_usd, tick_value)
    target_ticks = _dollars_to_ticks(mfe_usd, tick_value)

    stop_reasoning   = "Default (no MAE data)"
    target_reasoning = "Default (no MFE data)"

    if stop_ticks is not None:
        stop_reasoning = (
            f"p75 MAE of winners = ${mae_usd:.0f} -> {stop_ticks} ticks "
            f"(protects 75% of trades from stopping out on noise)"
        )
    if target_ticks is not None:
        target_reasoning = (
            f"p50 MFE all trades = ${mfe_usd:.0f} -> {target_ticks} ticks "
            f"(captures the median natural move)"
        )

    if stop_ticks is not None and target_ticks is not None:
        return stop_ticks, target_ticks, stop_reasoning, target_reasoning

    # --- Priority 2: signal exit sweep overall_best ---
    ses = sd.get("signal_exit_sweep") or {}
    overall_best = ses.get("overall_best") or {}
    sweep_stop   = overall_best.get("stop")
    sweep_target = overall_best.get("target")

    if sweep_stop is not None and sweep_target is not None:
        sweep_stop   = int(sweep_stop)
        sweep_target = int(sweep_target)
        n_rules  = overall_best.get("n_rules_voting", 0)
        avg_wr   = overall_best.get("avg_win_rate")
        avg_pf   = overall_best.get("avg_profit_factor")
        rr       = round(sweep_target / max(sweep_stop, 1), 2)
        detail = f"n_rules_voting={n_rules}"
        if avg_wr is not None:
            detail += f", avg_WR={avg_wr:.1%}"
        if avg_pf is not None:
            detail += f", avg_PF={avg_pf:.2f}"
        if stop_ticks is None:
            stop_ticks = sweep_stop
            stop_reasoning = (
                f"Signal exit sweep best stop={sweep_stop} ticks "
                f"(RR={rr}, {detail}; no trade MAE profile)"
            )
        if target_ticks is None:
            target_ticks = sweep_target
            target_reasoning = (
                f"Signal exit sweep best target={sweep_target} ticks "
                f"(RR={rr}, {detail}; no trade MFE profile)"
            )
        if stop_ticks is not None and target_ticks is not None:
            return stop_ticks, target_ticks, stop_reasoning, target_reasoning

    # --- Priority 3: corpus rule MAE/MFE p50 ---
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    corpus_mae: Optional[float] = None
    corpus_mfe: Optional[float] = None
    for rule in top_rules[:3]:
        if corpus_mae is None:
            corpus_mae = _safe_float(rule.get("mae_p50"))
        if corpus_mfe is None:
            corpus_mfe = _safe_float(rule.get("mfe_p50"))
        if corpus_mae is not None and corpus_mfe is not None:
            break

    if stop_ticks is None:
        if corpus_mae is not None and corpus_mae > 0:
            stop_ticks = max(4, round(corpus_mae * 1.2))
            stop_reasoning = (
                f"Signal corpus MAE p50={corpus_mae:.1f} ticks ×1.2 -> {stop_ticks} ticks "
                f"(no trade MAE profile; using corpus rule fallback)"
            )
        else:
            stop_ticks = 60
            warnings.append("No MAE data — StopTicks defaulted to 60")

    if target_ticks is None:
        if corpus_mfe is not None and corpus_mfe > 0:
            target_ticks = max(4, round(corpus_mfe * 0.75))
            target_reasoning = (
                f"Signal corpus MFE p50={corpus_mfe:.1f} ticks ×0.75 -> {target_ticks} ticks "
                f"(no trade MFE profile; using corpus rule fallback)"
            )
        else:
            target_ticks = 90
            warnings.append("No MFE data — TargetTicks defaulted to 90")

    return stop_ticks, target_ticks, stop_reasoning, target_reasoning


def _extract_exit_policy(sd: Dict[str, Any], warnings: List[str]):
    """
    Extract ExitPolicy enum value and related parameters from exit_discovery.
    Returns dict with: exit_policy_int, atr_trail_multiple, chandelier_lookback,
                       giveback_pct, reasoning.
    """
    ed = sd.get("exit_discovery") or {}
    ranking = ed.get("policy_ranking") or []

    defaults = {
        "exit_policy_int": 0,
        "atr_trail_multiple": 2.0,
        "chandelier_lookback": 20,
        "giveback_pct": 0.40,
        "reasoning": "Default FixedRR (no exit discovery data)",
    }

    if not ranking:
        warnings.append("No exit policy ranking found — using FixedRR default")
        return defaults

    best = ranking[0]
    policy_name = str(best.get("policy_name") or "")
    pf = _safe_float(best.get("profit_factor"))
    net = _safe_float(best.get("net_ticks"))
    family = _family_of(policy_name)

    result = dict(defaults)

    pf_str  = f"{pf:.2f}"  if pf  is not None else "n/a"
    net_str = f"{net:.0f}" if net is not None else "n/a"

    if family == "fixed_rr":
        result["exit_policy_int"] = 0
        result["reasoning"] = (
            f"Best policy: {policy_name} (FixedRR) — "
            f"PF={pf_str}, net={net_str} ticks"
        )

    elif family == "atr_trail":
        stop_m, trail_m = _parse_atr_trail(policy_name)
        result["exit_policy_int"] = 1
        result["atr_trail_multiple"] = trail_m or 2.0
        result["reasoning"] = (
            f"Best policy: {policy_name} (ATR Trail) — "
            f"stop_mult={stop_m}, trail_mult={trail_m}, "
            f"PF={pf_str}, net={net_str} ticks"
        )

    elif family == "be_atr_trail":
        stop_m, trail_m = _parse_atr_trail(policy_name)
        result["exit_policy_int"] = 5  # BreakEvenOnly maps closest
        result["atr_trail_multiple"] = trail_m or 2.0
        result["reasoning"] = (
            f"Best policy: {policy_name} (BE+ATR Trail) — "
            f"trail_mult={trail_m}, PF={pf_str}, net={net_str} ticks"
        )

    elif family == "chandelier":
        result["exit_policy_int"] = 2
        result["reasoning"] = (
            f"Best policy: {policy_name} (Chandelier) — "
            f"PF={pf_str}, net={net_str} ticks"
        )

    elif family == "giveback":
        gb_pct = _parse_giveback(policy_name)
        result["exit_policy_int"] = 3
        result["giveback_pct"] = gb_pct
        gb_str = f"{gb_pct:.0%}" if gb_pct is not None else "n/a"
        result["reasoning"] = (
            f"Best policy: {policy_name} (Giveback {gb_str}) — "
            f"PF={pf_str}, net={net_str} ticks"
        )

    return result


def _extract_adx_threshold(sd: Dict[str, Any], warnings: List[str]):
    """
    Extract AdxThreshold from top entry rules AND signal corpus rules.
    Checks signal_entry_discovery rules first (larger corpus, less execution bias).
    Falls back to trade-anchored entry_discovery rules.
    Returns (threshold_int, reasoning).
    """
    from collections import Counter

    def _scan_rules_for_adx(rules: list) -> list:
        vals = []
        for rule in rules[:10]:
            for cond in (rule.get("conditions") or []):
                col = str(cond.get("column") or "")
                op = str(cond.get("op") or "")
                val = cond.get("value")
                if col == "adx" and op == "gte" and val is not None:
                    try:
                        vals.append(float(val))
                    except Exception:
                        pass
        return vals

    # Signal corpus rules (preferred — larger unbiased sample)
    sed = sd.get("signal_entry_discovery") or {}
    signal_rules = sed.get("top_signal_rules") or []
    signal_adx = _scan_rules_for_adx(signal_rules)

    if signal_adx:
        most_common = Counter(signal_adx).most_common(1)[0][0]
        threshold = int(round(most_common))
        return threshold, (
            f"ADX >= {threshold} from signal corpus rules "
            f"({signal_adx.count(most_common)} of top signal rules, n_corpus={sed.get('diagnostics', {}).get('n_signals', '?')})"
        )

    # Fallback: trade-anchored entry_discovery
    ed = sd.get("entry_discovery") or {}
    trade_adx = _scan_rules_for_adx(ed.get("top_rules") or [])

    if trade_adx:
        most_common = Counter(trade_adx).most_common(1)[0][0]
        threshold = int(round(most_common))
        top_rules = ed.get("top_rules") or []
        return threshold, (
            f"ADX >= {threshold} found in {trade_adx.count(most_common)} of top trade rules "
            f"(out of {len(top_rules)} rules checked)"
        )

    warnings.append("No ADX threshold found in entry rules — defaulting to 25")
    return 25, "Default (no ADX conditions found in entry or signal rules)"


_REGIME_LABEL_TO_MODE = {
    "trending_up":           3,
    "trending_down":         4,
    "trending_tight":        1,
    "ranging_tight":         2,
    "ranging_normal":        2,
    "ranging_wide":          2,
    "high_vol_expansion":    7,
    "low_vol_compression":   6,
}


def _extract_regime_mode(sd: Dict[str, Any], warnings: List[str]):
    """
    Extract RegimeMode int from regime/feature importance data.
    Falls back to signal_entry_discovery top-rule regime conditions.

    RegimeMode mapping:
      0=Any, 1=TrendingOnly, 2=RangingOnly, 3=TrendingUp, 4=TrendingDown,
      5=NoHighVol, 6=LowVolOnly, 7=HighVolOnly
    """
    from collections import Counter

    # -- Primary: trade evaluation breakdown --
    ev = sd.get("evaluation") or {}
    breakdown = ev.get("by_regime") or {}
    fi = sd.get("importance") or {}
    top_features = [str(f) for f in (fi.get("top_features") or [])[:5]]

    high_vol = breakdown.get("high_vol_expansion") or {}
    high_vol_pf = _safe_float(high_vol.get("profit_factor"))
    if high_vol_pf is not None and high_vol_pf < 1.0:
        return 5, (
            f"high_vol_expansion regime has PF={high_vol_pf:.2f} < 1.0 — "
            f"recommend NoHighVol (exclude high volatility days)"
        )

    if "adx" in top_features or "regime" in top_features:
        up_data = breakdown.get("trending_up") or {}
        down_data = breakdown.get("trending_down") or {}
        up_pf = _safe_float(up_data.get("profit_factor")) or 0
        down_pf = _safe_float(down_data.get("profit_factor")) or 0
        up_n = int(up_data.get("n_trades") or 0)
        down_n = int(down_data.get("n_trades") or 0)
        if up_pf >= 1.2 and down_pf < 1.0 and up_n >= 10:
            return 3, f"trending_up PF={up_pf:.2f}, trending_down PF={down_pf:.2f} — TrendingUp"
        if down_pf >= 1.2 and up_pf < 1.0 and down_n >= 10:
            return 4, f"trending_down PF={down_pf:.2f}, trending_up PF={up_pf:.2f} — TrendingDown"
        return 1, f"ADX/regime in top features — TrendingOnly (up={up_pf:.2f}, down={down_pf:.2f})"

    # -- Fallback: scan signal corpus rules for regime conditions --
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    regime_votes: list = []
    adx_gte_present = False
    for rule in top_rules[:10]:
        for cond in (rule.get("conditions") or []):
            col = str(cond.get("column") or "")
            if col == "regime":
                val = str(cond.get("value") or "")
                if val:
                    regime_votes.append(val)
            elif col == "adx" and str(cond.get("op") or "") == "gte":
                adx_gte_present = True

    if regime_votes:
        most_common = Counter(regime_votes).most_common(1)[0][0]
        mode = _REGIME_LABEL_TO_MODE.get(most_common, 0)
        return mode, (
            f"Signal corpus: dominant regime condition = '{most_common}' "
            f"(appears in {regime_votes.count(most_common)} of top rules) — "
            f"{_REGIME_MODE_STRINGS.get(mode, 'Any')}"
        )

    if adx_gte_present:
        return 1, "Signal corpus: ADX threshold conditions present — TrendingOnly"

    warnings.append("Regime mode unclear from data — defaulting to Any (0)")
    return 0, "No clear regime preference found — using Any (baseline)"


# PantheonMaster session → StrategyDiscoveryFilter RTH/ETH/ONH buckets
_RTH_SESSION_LABELS = {"NyOpen", "NyMid", "PowerHr"}
_ETH_SESSION_LABELS = {"NyPre"}
_ONH_SESSION_LABELS = {"London", "Asia"}


def _extract_session_filters(sd: Dict[str, Any], warnings: List[str]):
    """
    Extract AllowRTH/ONH/ETH from evaluation.by_session.
    Falls back to session_label conditions in signal corpus rules.
    Returns (allow_rth, allow_onh, allow_eth, reasoning).
    """
    from collections import Counter

    ev = sd.get("evaluation") or {}
    by_session = ev.get("by_session") or {}

    rth_pf_vals, eth_pf_vals, onh_pf_vals = [], [], []
    for label, data in by_session.items():
        pf = _safe_float((data or {}).get("profit_factor"))
        if pf is None:
            continue
        if label in _RTH_SESSION_LABELS:
            rth_pf_vals.append(pf)
        elif label in _ETH_SESSION_LABELS:
            eth_pf_vals.append(pf)
        else:
            onh_pf_vals.append(pf)

    avg_rth = sum(rth_pf_vals) / len(rth_pf_vals) if rth_pf_vals else None
    avg_eth = sum(eth_pf_vals) / len(eth_pf_vals) if eth_pf_vals else None
    avg_onh = sum(onh_pf_vals) / len(onh_pf_vals) if onh_pf_vals else None

    if avg_rth is not None or avg_eth is not None or avg_onh is not None:
        allow_rth = (avg_rth is None) or (avg_rth >= 1.0)
        allow_eth = (avg_eth is not None) and (avg_eth >= 1.1)
        allow_onh = (avg_onh is not None) and (avg_onh >= 1.1)
        parts = []
        if avg_rth is not None:
            parts.append(f"RTH avg PF={avg_rth:.2f}")
        if avg_eth is not None:
            parts.append(f"ETH avg PF={avg_eth:.2f}")
        if avg_onh is not None:
            parts.append(f"ONH avg PF={avg_onh:.2f}")
        return allow_rth, allow_onh, allow_eth, "; ".join(parts)

    # -- Fallback: scan signal corpus rules for session_label conditions --
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    session_votes: list = []
    for rule in top_rules[:10]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "session_label":
                val = str(cond.get("value") or "")
                if val and val not in ("nan", "None"):
                    session_votes.append(val)

    if session_votes:
        counts = Counter(session_votes)
        rth_count = sum(counts[l] for l in _RTH_SESSION_LABELS)
        eth_count = sum(counts[l] for l in _ETH_SESSION_LABELS)
        onh_count = sum(counts[l] for l in _ONH_SESSION_LABELS)
        total = max(1, len(session_votes))
        allow_rth = rth_count > 0 or (rth_count == 0 and eth_count == 0 and onh_count == 0)
        allow_eth = eth_count / total >= 0.2
        allow_onh = onh_count / total >= 0.2
        top_sessions = [f"{s}(×{n})" for s, n in counts.most_common(3)]
        return allow_rth, allow_onh, allow_eth, (
            f"Signal corpus session conditions: {', '.join(top_sessions)}"
        )

    warnings.append("No session breakdown data — defaulting to RTH=true, ONH=false, ETH=false")
    return True, False, False, "No session data"


def _extract_direction_filters(sd: Dict[str, Any], warnings: List[str]):
    """
    Extract AllowLong/AllowShort from evaluation.by_direction.
    Falls back to direction conditions in signal corpus rules.
    Returns (allow_long, allow_short, reasoning).
    """
    from collections import Counter

    ev = sd.get("evaluation") or {}
    by_dir = ev.get("by_direction") or {}
    long_data = by_dir.get("Long") or by_dir.get("long") or {}
    short_data = by_dir.get("Short") or by_dir.get("short") or {}
    long_pf = _safe_float(long_data.get("profit_factor"))
    short_pf = _safe_float(short_data.get("profit_factor"))
    long_n = int(long_data.get("n_trades") or 0)
    short_n = int(short_data.get("n_trades") or 0)

    if long_pf is not None or short_pf is not None:
        allow_long = True
        allow_short = True
        parts = []
        if long_pf is not None:
            parts.append(f"Long PF={long_pf:.2f} (n={long_n})")
            if long_pf < 1.0 and long_n >= 10:
                allow_long = False
        if short_pf is not None:
            parts.append(f"Short PF={short_pf:.2f} (n={short_n})")
            if short_pf < 1.0 and short_n >= 10:
                allow_short = False
        if not allow_long and not allow_short:
            if (long_pf or 0) >= (short_pf or 0):
                allow_long = True
            else:
                allow_short = True
            warnings.append("Both Long and Short PF < 1.0 — re-enabling the better direction")
        return allow_long, allow_short, "; ".join(parts)

    # -- Fallback: scan signal corpus rules for direction conditions --
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    dir_votes: list = []
    for rule in top_rules[:10]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "direction":
                val = str(cond.get("value") or "")
                try:
                    dir_votes.append(int(float(val)))
                except Exception:
                    pass

    if dir_votes:
        counts = Counter(dir_votes)
        long_count = counts.get(1, 0)
        short_count = counts.get(-1, 0)
        # Allow a direction if it appears in at least 1 top rule
        allow_long = long_count > 0 or short_count == 0
        allow_short = short_count > 0 or long_count == 0
        return allow_long, allow_short, (
            f"Signal corpus direction conditions: Long×{long_count}, Short×{short_count}"
        )

    warnings.append("No direction breakdown data — allowing both directions")
    return True, True, "No direction data"


def _extract_daily_risk(sd: Dict[str, Any], warnings: List[str], tick_value: float = 5.0):
    """
    Extract MaxDailyLossUsd and MaxDailyTrades from drawdown + evaluation.
    Falls back to signal corpus baseline avg_ticks when no trade data exists.
    Returns (max_daily_loss, max_daily_trades, max_daily_profit, reasoning).
    """
    dd = sd.get("drawdown_analysis") or {}
    streaks = dd.get("streaks") or {}
    max_consec_losses = int(streaks.get("max_consecutive_losses") or 3)

    ev = sd.get("evaluation") or {}
    avg_trade = _safe_float(ev.get("avg_trade"))

    max_daily_trades = max_consec_losses + 2

    if avg_trade is not None and avg_trade > 0:
        max_daily_loss = round(avg_trade * 1.0, 0)
        max_daily_profit = max_daily_loss * 3.0
        reasoning = (
            f"max_consecutive_losses={max_consec_losses} -> MaxDailyTrades={max_daily_trades}; "
            f"avg_trade=${avg_trade:.0f} -> MaxDailyLossUsd=${max_daily_loss:.0f}"
        )
        return max_daily_loss, max_daily_trades, max_daily_profit, reasoning

    # -- Fallback: use corpus baseline avg_ticks × tick_value --
    sed = sd.get("signal_entry_discovery") or {}
    baseline = sed.get("baseline") or {}
    corpus_avg_ticks = _safe_float(baseline.get("avg_ticks"))
    if corpus_avg_ticks is not None and corpus_avg_ticks > 0:
        estimated_avg = corpus_avg_ticks * tick_value
        max_daily_loss = max(100.0, round(estimated_avg * 2.0, -1))  # 2× avg signal to nearest $10
        max_daily_profit = max_daily_loss * 3.0
        return max_daily_loss, max_daily_trades, max_daily_profit, (
            f"Corpus baseline avg_ticks={corpus_avg_ticks:.1f} × ${tick_value:.0f} = "
            f"${estimated_avg:.0f}/signal → MaxDailyLossUsd=${max_daily_loss:.0f} "
            f"(2× corpus avg, no trade history)"
        )

    max_daily_loss = 500.0
    max_daily_profit = 1500.0
    warnings.append("No avg_trade data — MaxDailyLossUsd defaulted to $500")
    reasoning = f"max_consecutive_losses={max_consec_losses} -> MaxDailyTrades={max_daily_trades}"
    return max_daily_loss, max_daily_trades, max_daily_profit, reasoning


# ---------------------------------------------------------------------------
# Signal corpus insights (Phase 4 — from signal_entry_discovery)
# ---------------------------------------------------------------------------

def _extract_signal_insights(sd: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """
    Read signal_entry_discovery top rules and extract:
      - best_regime    : dominant regime in top rule conditions
      - best_adx       : best ADX threshold from signal rules
      - corpus_mfe_p50 : MFE p50 ticks from the top signal rule
      - corpus_mae_p50 : MAE p50 ticks from the top signal rule
      - top_families   : list of pattern families in top rules
      - top_structures : list of pattern structures in top rules
      - has_signal_data: bool

    These are used to:
      - Refine StopTicks / TargetTicks if corpus p50 values are available
      - Add pattern family/structure info to reasoning notes
    """
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    baseline = sed.get("baseline") or {}

    result: Dict[str, Any] = {
        "has_signal_data": len(top_rules) > 0,
        "best_regime": None,
        "corpus_mfe_p50": None,
        "corpus_mae_p50": None,
        "top_families": [],
        "top_structures": [],
        "n_corpus_signals": baseline.get("n_signals"),
        "corpus_baseline_wr": baseline.get("win_rate"),
    }

    if not top_rules:
        return result

    # Top rule (highest scored)
    top = top_rules[0]
    mfe = _safe_float(top.get("mfe_p50"))
    mae = _safe_float(top.get("mae_p50"))
    if mfe is not None and mfe > 0:
        result["corpus_mfe_p50"] = mfe
    if mae is not None and mae > 0:
        result["corpus_mae_p50"] = mae

    # Scan top 5 rules for regime and pattern info
    regime_counts: dict = {}
    families: list = []
    structures: list = []
    for rule in top_rules[:5]:
        for cond in (rule.get("conditions") or []):
            col = str(cond.get("column") or "")
            val = str(cond.get("value") or "")
            if col == "regime" and val:
                regime_counts[val] = regime_counts.get(val, 0) + 1
            elif col == "family" and val and val not in families:
                families.append(val)
            elif col == "structure" and val and val not in structures:
                structures.append(val)

    if regime_counts:
        result["best_regime"] = max(regime_counts, key=regime_counts.get)
    result["top_families"] = families[:3]
    result["top_structures"] = structures[:3]

    return result


# ---------------------------------------------------------------------------
# Enum string mappings (must match C# enum names exactly)
# ---------------------------------------------------------------------------

_REGIME_MODE_STRINGS = {
    0: "Any",
    1: "TrendingOnly",
    2: "RangingOnly",
    3: "TrendingUp",
    4: "TrendingDown",
    5: "NoHighVol",
    6: "LowVolOnly",
    7: "HighVolOnly",
}

_EXIT_POLICY_STRINGS = {
    0: "FixedRR",
    1: "AtrTrail",
    2: "Chandelier",
    3: "Giveback",
    4: "FixedStop",
    5: "BreakEvenOnly",
}


# ---------------------------------------------------------------------------
# Entry signal mapping (Phase 1 — parameterized entry, Path A)
# ---------------------------------------------------------------------------
#
# Maps a discovered candle/breakout *structure* to the SdfEntrySignal enum name
# in StrategyDiscoveryFilter.cs. Keys are the PATTERN_REGISTRY ids from
# analysis/entry_strategies/candle/patterns.py plus the breakout family.
# The enum names MUST match the C# enum exactly.

_STRUCTURE_TO_ENTRY_SIGNAL = {
    "large_body":          "LargeBody",
    "pin_bar_bullish":     "PinBarBullish",
    "pin_bar_bearish":     "PinBarBearish",
    "inside_bar":          "InsideBar",
    "outside_bar":         "OutsideBar",
    "engulfing_bullish":   "EngulfingBullish",
    "engulfing_bearish":   "EngulfingBearish",
    "doji":                "Doji",
    "clean_breakout_bar":  "CleanBreakoutBar",
    # breakout family
    "n_bar_breakout":      "NbarBreakout",
    "nbar_breakout":       "NbarBreakout",
    # MA family — default to SmaCross (the on-disk PantheonMasterBotV01TesterV2 results
    # are SMA crosses). 'ma_cross' is refined to Ema/SmaCross by ma_type in
    # _extract_entry_signal; the explicit aliases pin directly.
    "ma_cross":            "SmaCross",
    "sma_cross":           "SmaCross",
    "ema_cross":           "EmaCross",
}

# Structures whose Ema-vs-Sma resolution depends on the discovered ma_type param.
_MA_FAMILY_STRUCTURES = {"ma_cross"}


def _refine_ma_cross_signal(struct: str, options: Dict[str, Any]) -> Optional[str]:
    """
    For the ambiguous 'ma_cross' structure, choose the C# entry enum from the
    discovered ma_type: 'ema' -> EmaCross, anything else (incl. unset) -> SmaCross.

    NOTE: discovery's detect_ma_cross is a PRICE×MA crossover, whereas the C#
    Ema/SmaCross is a FAST×SLOW MA crossover — structurally similar (a MA-cross
    entry) but not identical. The operator opted to pin SmaCross for the SMA-cross
    workflow; verify entry parity with scripts/parity_signal_export.py if it matters.
    """
    if struct not in _MA_FAMILY_STRUCTURES:
        return None
    ma_type = str(((options or {}).get("entry_params") or {}).get("ma_type", "")).lower()
    return "EmaCross" if ma_type == "ema" else "SmaCross"

# Discovery timing-mode string → C# SdfTimingMode enum name.
_TIMING_TO_ENUM = {
    "next_open":     "NextOpen",
    "break_extreme": "BreakExtreme",
    "body_midpoint": "BodyMidpoint",
}


def _extract_entry_signal(sd: Dict[str, Any], options: Dict[str, Any], warnings: List[str]):
    """
    Resolve the SdfEntrySignal enum name + reasoning, in priority order:
      1. options['entry_signal'] explicit override (already an enum name)
      2. a `structure` condition in one of the top signal rules
      3. top_structures from the signal corpus insights
      4. 'EmaCross' legacy fallback (with a warning)

    Returns (entry_signal_name, reasoning_str).
    """
    opt_sig = (options or {}).get("entry_signal")
    if opt_sig:
        return str(opt_sig), "explicit option override"

    sed = sd.get("signal_entry_discovery") or {}
    for rule in (sed.get("top_signal_rules") or [])[:5]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column")) == "structure":
                struct = str(cond.get("value"))
                sig = _STRUCTURE_TO_ENTRY_SIGNAL.get(struct)
                if sig:
                    sig = _refine_ma_cross_signal(struct, options) or sig
                    return sig, f"structure condition '{struct}' in top signal rule"

    sig_insights = _extract_signal_insights(sd, warnings)
    for struct in (sig_insights.get("top_structures") or []):
        struct = str(struct)
        sig = _STRUCTURE_TO_ENTRY_SIGNAL.get(struct)
        if sig:
            sig = _refine_ma_cross_signal(struct, options) or sig
            return sig, f"top structure '{struct}' from signal corpus"

    warnings.append(
        "No entry structure found in discovery — EntrySignal defaulted to "
        "EmaCross (legacy). The NT backtest will NOT match the discovered entry."
    )
    return "EmaCross", "no structure found — legacy EMA cross (does not match discovery)"


def _extract_entry_pattern_params(options: Dict[str, Any]):
    """
    Build the entry-pattern parameter block. Values come from
    options['entry_params'] (e.g. the swept candle params behind a discovered
    rule); anything absent defaults to the patterns.py / features.py default,
    which is exactly what StrategyDiscoveryFilter.cs SetDefaults uses. So a
    discovery that used default thresholds matches with no params supplied.

    Returns (params_dict, timeframe_minutes).
    """
    ep = (options or {}).get("entry_params") or {}

    def g(key: str, default: Any) -> Any:
        v = ep.get(key)
        return default if v is None else v

    timing_raw = str(g("timing_mode", "next_open")).lower()
    timing_enum = _TIMING_TO_ENUM.get(timing_raw, "NextOpen")

    timeframe_minutes = int(
        (options or {}).get("timeframe_minutes")
        or ep.get("tf")
        or ep.get("timeframe")
        or 1
    )

    params = {
        "EntrySignal": None,  # filled by caller
        "TimingMode": timing_enum,
        "BufferTicks": float(g("buffer_ticks", 1.0)),
        "FillTimeoutBars": int(g("fill_timeout_bars", 3)),
        "BodyRollLookback": int(g("lookback", 20)),
        "ExtremeLookback": int(g("extreme_lookback", 20)),
        "BodyMultiplier": float(g("body_multiplier", 1.5)),
        "WickToBodyMax": float(g("wick_to_body_max", 0.5)),
        "PinWickToBodyMin": float(g("wick_to_body_min", 1.5)),
        "PinOppWickToBodyMax": float(g("opp_wick_to_body_max", 0.5)),
        "PinBodyToRangeMax": float(g("pin_body_to_range_max", 0.35)),
        "EngulfRatio": float(g("engulf_ratio", 1.0)),
        "DojiBodyToRangeMax": float(g("doji_body_to_range_max", 0.15)),
        "CleanAtrMult": float(g("atr_mult", 1.5)),
        "CleanBodyToRangeMin": float(g("body_to_range_min", 0.60)),
        "EntryMinSizeTicks": int(g("min_size_ticks", 4)),
        "EntryMaxSizeTicks": int(g("max_size_ticks", 200)),
    }
    return params, timeframe_minutes


# ---------------------------------------------------------------------------
# XML builder — matches the NinjaTrader StrategyTemplate format exactly
# ---------------------------------------------------------------------------

def _fmt_val(v: Any) -> str:
    """Serialize a parameter value to its XML string representation."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        # Trim trailing zeros but keep at least one decimal for floats
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s
    return str(v)


def _build_xml(
    params: Dict[str, Any],
    run_id: str,
    reasoning: Dict[str, str],
    timeframe_minutes: int = 1,
) -> str:
    """
    Build a NinjaTrader StrategyTemplate XML string.
    Matches the format that NinjaTrader 8 uses for saved strategy templates.

    timeframe_minutes drives the primary bar period so the NT backtest runs on
    the same timeframe the discovery used (previously hard-coded to 1 minute).
    """
    tf = max(1, int(timeframe_minutes or 1))

    def e(tag: str, value: Any) -> str:
        return f"      <{tag}>{_fmt_val(value)}</{tag}>"

    # Boilerplate notes as an XML comment (truncate long reasons for readability)
    notes_lines = ["      <!-- Auto-generated by ta_foundation Strategy Discovery"]
    notes_lines.append(f"           Source run: {run_id}")
    for key, reason in reasoning.items():
        notes_lines.append(f"           {key}: {reason[:120]}")
    notes_lines.append("      -->")
    notes_block = "\n".join(notes_lines)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.StrategyDiscoveryFilter</StrategyType>
  <Strategy>
    <StrategyDiscoveryFilter xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <IsVisible>true</IsVisible>
      <calculate2>OnBarClose</calculate2>
      <AreLinesConfigurable>true</AreLinesConfigurable>
      <ArePlotsConfigurable>true</ArePlotsConfigurable>
      <BarsPeriodSerializable>
        <BarsPeriodTypeSerialize>4</BarsPeriodTypeSerialize>
        <BaseBarsPeriodType>Minute</BaseBarsPeriodType>
        <BaseBarsPeriodValue>{tf}</BaseBarsPeriodValue>
        <VolumetricDeltaType>BidAsk</VolumetricDeltaType>
        <MarketDataType>Last</MarketDataType>
        <PointAndFigurePriceType>Close</PointAndFigurePriceType>
        <ReversalType>Tick</ReversalType>
        <Value>{tf}</Value>
        <Value2>1</Value2>
      </BarsPeriodSerializable>
      <BarsToLoad>0</BarsToLoad>
      <Calculate>OnBarClose</Calculate>
      <Displacement>0</Displacement>
      <DisplayInDataBox>true</DisplayInDataBox>
      <From>2026-01-05T00:00:00</From>
      <IsAutoScale>true</IsAutoScale>
      <Lines />
      <MaximumBarsLookBack>TwoHundredFiftySix</MaximumBarsLookBack>
      <Name>StrategyDiscoveryFilter</Name>
      <Panel>-1</Panel>
      <Plots />
      <ScaleJustification>Right</ScaleJustification>
      <ShowTransparentPlotsInDataBox>false</ShowTransparentPlotsInDataBox>
      <To>2026-03-26T00:00:00</To>
      <IsDataSeriesRequired>true</IsDataSeriesRequired>
      <IsOverlay>true</IsOverlay>
      <SelectedValueSeries>0</SelectedValueSeries>
      <Gtd>1800-01-01T00:00:00</Gtd>
      <Template />
      <TimeInForce>Gtc</TimeInForce>
      <BacktestCommissionTemplate />
      <BarsPeriodParameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">0</Max>
        <Min xsi:type="xsd:int">0</Min>
        <Name />
        <ParameterTypeSerializable>System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</ParameterTypeSerializable>
        <ValueSerializable>0</ValueSerializable>
      </BarsPeriodParameter>
      <BarsRequiredToTrade>50</BarsRequiredToTrade>
      <Category>Backtest</Category>
      <ConnectionLossHandling>Recalculate</ConnectionLossHandling>
      <DaysToLoad>5</DaysToLoad>
      <DefaultQuantity>{params["Contracts"]}</DefaultQuantity>
      <DisconnectDelaySeconds>10</DisconnectDelaySeconds>
      <EntriesPerDirection>1</EntriesPerDirection>
      <EntryHandling>AllEntries</EntryHandling>
      <ExitOnSessionCloseSeconds>30</ExitOnSessionCloseSeconds>
      <IncludeCommission>false</IncludeCommission>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
      <IsAggregated>false</IsAggregated>
      <IsExitOnSessionCloseStrategy>true</IsExitOnSessionCloseStrategy>
      <IsFillLimitOnTouch>false</IsFillLimitOnTouch>
      <IsOptimizeDataSeries>false</IsOptimizeDataSeries>
      <IsStableSession>true</IsStableSession>
      <IsTickReplay>false</IsTickReplay>
      <IsTradingHoursBreakLineVisible>true</IsTradingHoursBreakLineVisible>
      <IsWaitUntilFlat>false</IsWaitUntilFlat>
      <NumberRestartAttempts>4</NumberRestartAttempts>
      <OptimizationPeriod>10</OptimizationPeriod>
      <OrderFillResolution>Standard</OrderFillResolution>
      <OrderFillResolutionType>Minute</OrderFillResolutionType>
      <OrderFillResolutionValue>1</OrderFillResolutionValue>
      <RestartsWithinMinutes>5</RestartsWithinMinutes>
      <SetOrderQuantity>Strategy</SetOrderQuantity>
      <Slippage>0</Slippage>
      <StartBehavior>WaitUntilFlat</StartBehavior>
      <StopTargetHandling>PerEntryExecution</StopTargetHandling>
      <SupportsOptimizationGraph>true</SupportsOptimizationGraph>
      <TestPeriod>28</TestPeriod>
      <TradingHoursSerializable />
      <DrawOnPricePanel>false</DrawOnPricePanel>
      <ZOrder>-2147483648</ZOrder>
{notes_block}
{e("RegimeMode", params["RegimeMode"])}
{e("AdxPeriod", params["AdxPeriod"])}
{e("AdxThreshold", params["AdxThreshold"])}
{e("RegimeEmaPeriod", params["RegimeEmaPeriod"])}
{e("AtrPeriod", params["AtrPeriod"])}
{e("AtrLookbackBars", params["AtrLookbackBars"])}
{e("VolLowPercentile", params["VolLowPercentile"])}
{e("VolHighPercentile", params["VolHighPercentile"])}
{e("AllowRTH", params["AllowRTH"])}
{e("AllowONH", params["AllowONH"])}
{e("AllowETH", params["AllowETH"])}
{e("RthStartH", params["RthStartH"])}
{e("RthStartM", params["RthStartM"])}
{e("RthEndH", params["RthEndH"])}
{e("RthEndM", params["RthEndM"])}
{e("EthStartH", params["EthStartH"])}
{e("EthStartM", params["EthStartM"])}
{e("EthEndH", params["EthEndH"])}
{e("EthEndM", params["EthEndM"])}
{e("AllowLong", params["AllowLong"])}
{e("AllowShort", params["AllowShort"])}
{e("UseTrendAlignment", params["UseTrendAlignment"])}
{e("EntryEmaPeriod", params["EntryEmaPeriod"])}
{e("SlowEmaPeriod", params["SlowEmaPeriod"])}
{e("RequireEmaConfirmation", params["RequireEmaConfirmation"])}
{e("RequireMinAtrMultiple", params["RequireMinAtrMultiple"])}
{e("EntrySignal", params["EntrySignal"])}
{e("TimingMode", params["TimingMode"])}
{e("BufferTicks", params["BufferTicks"])}
{e("FillTimeoutBars", params["FillTimeoutBars"])}
{e("BodyRollLookback", params["BodyRollLookback"])}
{e("ExtremeLookback", params["ExtremeLookback"])}
{e("BodyMultiplier", params["BodyMultiplier"])}
{e("WickToBodyMax", params["WickToBodyMax"])}
{e("PinWickToBodyMin", params["PinWickToBodyMin"])}
{e("PinOppWickToBodyMax", params["PinOppWickToBodyMax"])}
{e("PinBodyToRangeMax", params["PinBodyToRangeMax"])}
{e("EngulfRatio", params["EngulfRatio"])}
{e("DojiBodyToRangeMax", params["DojiBodyToRangeMax"])}
{e("CleanAtrMult", params["CleanAtrMult"])}
{e("CleanBodyToRangeMin", params["CleanBodyToRangeMin"])}
{e("EntryMinSizeTicks", params["EntryMinSizeTicks"])}
{e("EntryMaxSizeTicks", params["EntryMaxSizeTicks"])}
{e("ExitPolicy", params["ExitPolicy"])}
{e("StopTicks", params["StopTicks"])}
{e("TargetTicks", params["TargetTicks"])}
{e("AtrTrailMultiple", params["AtrTrailMultiple"])}
{e("ChandelierLookback", params["ChandelierLookback"])}
{e("GivebackPct", params["GivebackPct"])}
{e("BreakEvenTriggerTicks", params["BreakEvenTriggerTicks"])}
{e("BreakEvenPlusTicks", params["BreakEvenPlusTicks"])}
{e("MaxDailyLossUsd", params["MaxDailyLossUsd"])}
{e("MaxDailyProfitUsd", params["MaxDailyProfitUsd"])}
{e("MaxDailyTrades", params["MaxDailyTrades"])}
{e("EnableDebugPrint", params["EnableDebugPrint"])}
{e("Contracts", params["Contracts"])}
    </StrategyDiscoveryFilter>
  </Strategy>
</StrategyTemplate>
"""
    return xml


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_nt_template(
    sd: Dict[str, Any],
    run_id: str = "unknown",
    options: Optional[Dict[str, Any]] = None,
) -> GeneratedTemplate:
    """
    Generate a NinjaTrader XML template from Strategy Discovery results.

    Parameters
    ----------
    sd      : pkg.metadata["derived"]["strategy_discovery"] dict
    run_id  : identifier string for labeling
    options : optional dict with:
                tick_value (float, default 5.0)
                tick_size  (float, default 0.25)

    Returns
    -------
    GeneratedTemplate dataclass with xml_str, parameters, reasoning, warnings
    """
    options = options or {}
    tick_value = float(options.get("tick_value") or 5.0)
    warnings: List[str] = []
    reasoning: Dict[str, str] = {}

    # -- Signal corpus insights (Phase 4) --
    sig = _extract_signal_insights(sd, warnings)
    if sig["has_signal_data"]:
        n_sig = sig.get("n_corpus_signals") or "?"
        base_wr = sig.get("corpus_baseline_wr")
        wr_str = f"{base_wr:.1%}" if base_wr is not None else "?"
        fam_str = ", ".join(sig["top_families"]) if sig["top_families"] else "none"
        struct_str = ", ".join(sig["top_structures"]) if sig["top_structures"] else "none"
        reasoning["SignalCorpus"] = (
            f"{n_sig} corpus signals, baseline WR={wr_str}; "
            f"top families: {fam_str}; structures: {struct_str}"
        )

    # -- A: Regime --
    regime_mode, regime_reason = _extract_regime_mode(sd, warnings)
    adx_threshold, adx_reason = _extract_adx_threshold(sd, warnings)
    reasoning["RegimeMode"] = regime_reason
    reasoning["AdxThreshold"] = adx_reason

    # -- B: Session --
    allow_rth, allow_onh, allow_eth, session_reason = _extract_session_filters(sd, warnings)
    reasoning["AllowRTH/ONH/ETH"] = session_reason

    # -- C: Direction --
    allow_long, allow_short, dir_reason = _extract_direction_filters(sd, warnings)
    reasoning["AllowLong/Short"] = dir_reason

    # -- E: Exit — _extract_mae_mfe handles corpus fallback internally --
    stop_ticks, target_ticks, stop_reason, target_reason = _extract_mae_mfe(sd, tick_value, warnings)

    exit_info = _extract_exit_policy(sd, warnings)
    reasoning["StopTicks"] = stop_reason
    reasoning["TargetTicks"] = target_reason
    reasoning["ExitPolicy"] = exit_info["reasoning"]

    # -- F: Daily risk — pass tick_value so corpus avg_ticks can be converted --
    max_daily_loss, max_daily_trades, max_daily_profit, risk_reason = _extract_daily_risk(
        sd, warnings, tick_value=tick_value
    )
    reasoning["MaxDailyLossUsd/MaxDailyTrades"] = risk_reason

    # -- D2: Entry pattern (Path A — the entry trigger itself) --
    entry_signal, entry_signal_reason = _extract_entry_signal(sd, options, warnings)
    entry_pattern_params, timeframe_minutes = _extract_entry_pattern_params(options)
    entry_pattern_params["EntrySignal"] = entry_signal
    reasoning["EntrySignal"] = entry_signal_reason
    if entry_signal != "EmaCross":
        reasoning["Timeframe"] = f"{timeframe_minutes}m bars (from discovery)"

    # Assemble parameters dict
    # RegimeMode and ExitPolicy use C# enum string names (must match exactly)
    params: Dict[str, Any] = {
        # A: Regime — RegimeMode uses string enum name
        "RegimeMode": _REGIME_MODE_STRINGS.get(regime_mode, "Any"),
        "AdxPeriod": 14,
        "AdxThreshold": adx_threshold,
        "RegimeEmaPeriod": 50,
        "AtrPeriod": 14,
        "AtrLookbackBars": 100,
        "VolLowPercentile": 0.3,
        "VolHighPercentile": 0.7,
        # B: Session
        "AllowRTH": allow_rth,
        "AllowONH": allow_onh,
        "AllowETH": allow_eth,
        "RthStartH": 8,
        "RthStartM": 30,
        "RthEndH": 15,
        "RthEndM": 0,
        "EthStartH": 7,
        "EthStartM": 0,
        "EthEndH": 8,
        "EthEndM": 29,
        # C: Direction
        "AllowLong": allow_long,
        "AllowShort": allow_short,
        "UseTrendAlignment": True,
        # D: Entry
        "EntryEmaPeriod": 5,
        "SlowEmaPeriod": 200,
        "RequireEmaConfirmation": True,
        "RequireMinAtrMultiple": 0,
        # E: Exit — ExitPolicy uses string enum name
        "ExitPolicy": _EXIT_POLICY_STRINGS.get(exit_info["exit_policy_int"], "FixedRR"),
        "StopTicks": stop_ticks,
        "TargetTicks": target_ticks,
        "AtrTrailMultiple": exit_info["atr_trail_multiple"],
        "ChandelierLookback": exit_info["chandelier_lookback"],
        "GivebackPct": exit_info["giveback_pct"],
        "BreakEvenTriggerTicks": max(4, round(stop_ticks * 0.5)),
        "BreakEvenPlusTicks": 4,
        # F: Daily risk
        "MaxDailyLossUsd": int(max_daily_loss),
        "MaxDailyProfitUsd": int(max_daily_profit),
        "MaxDailyTrades": max_daily_trades,
        # G: Debug
        "EnableDebugPrint": False,
        "Contracts": 1,
    }

    # D2: Entry pattern — merge the entry trigger + pattern thresholds.
    params.update(entry_pattern_params)

    xml_str = _build_xml(params, run_id, reasoning, timeframe_minutes=timeframe_minutes)

    return GeneratedTemplate(
        xml_str=xml_str,
        parameters=params,
        reasoning=reasoning,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Per-rule templates — one template per discovered signal rule
# ---------------------------------------------------------------------------

@dataclass
class PerRuleTemplate:
    rule_str: str
    rule_rank: int
    n_signals: int
    best_stop: int
    best_target: int
    best_rr: float
    best_win_rate: Optional[float]
    best_pf: Optional[float]
    template: GeneratedTemplate


def generate_per_rule_templates(
    sd: Dict[str, Any],
    run_id: str = "unknown",
    options: Optional[Dict[str, Any]] = None,
    max_rules: int = 8,
) -> List[PerRuleTemplate]:
    """
    Generate one NinjaTrader template per discovered signal rule.

    For each rule in signal_exit_sweep.per_rule_sweep (sorted by best_score):
      - Override signal_exit_sweep.overall_best with the rule's best stop/target
      - Build a synthetic single-rule signal_entry_discovery so regime/session/
        direction helpers can extract conditions specific to this rule
      - Call generate_nt_template with the modified sd

    Parameters
    ----------
    sd          : pkg.metadata["derived"]["strategy_discovery"]
    run_id      : identifier for labeling
    options     : same options as generate_nt_template (tick_value, tick_size)
    max_rules   : max number of per-rule templates to generate

    Returns
    -------
    List of PerRuleTemplate, sorted by best_score descending.
    """
    options = options or {}
    per_rule_sweep = (sd.get("signal_exit_sweep") or {}).get("per_rule_sweep") or []
    if not per_rule_sweep:
        return []

    # Also get the original top_signal_rules for condition lookup
    sed = sd.get("signal_entry_discovery") or {}
    orig_rules = {
        r.get("rule_str", ""): r
        for r in (sed.get("top_signal_rules") or [])
    }

    results: List[PerRuleTemplate] = []

    for rank_idx, sweep_rule in enumerate(per_rule_sweep[:max_rules], start=1):
        rule_str  = str(sweep_rule.get("rule_str") or f"Rule {rank_idx}")
        n_signals = int(sweep_rule.get("n_signals") or 0)
        best_stop = int(sweep_rule.get("best_stop") or 60)
        best_tgt  = int(sweep_rule.get("best_target") or 90)
        best_rr   = float(sweep_rule.get("best_rr") or round(best_tgt / max(best_stop, 1), 2))
        best_wr   = sweep_rule.get("best_win_rate")
        best_pf   = sweep_rule.get("best_pf")

        # Build a rule-scoped sd dict:
        #  - override overall_best with this rule's stop/target
        #  - set signal_entry_discovery.top_signal_rules to just this rule
        orig_rule_dict = orig_rules.get(rule_str) or {}
        rule_conditions = orig_rule_dict.get("conditions") or sweep_rule.get("conditions") or []

        synthetic_rule = {
            "rule_str": rule_str,
            "conditions": rule_conditions,
            "n_signals": n_signals,
            "win_rate": best_wr,
            "mae_p50": orig_rule_dict.get("mae_p50"),
            "mfe_p50": orig_rule_dict.get("mfe_p50"),
        }

        scoped_sd = dict(sd)
        # Override exit sweep to use this rule's stop/target
        scoped_sd["signal_exit_sweep"] = {
            "overall_best": {
                "stop": best_stop,
                "target": best_tgt,
                "rr": best_rr,
                "n_rules_voting": 1,
                "avg_win_rate": best_wr,
                "avg_profit_factor": best_pf,
            },
            "per_rule_sweep": [sweep_rule],
        }
        # Override signal_entry_discovery to use just this rule (for regime/session extraction)
        scoped_sd["signal_entry_discovery"] = {
            "top_signal_rules": [synthetic_rule],
            "baseline": sed.get("baseline") or {},
        }

        try:
            tmpl = generate_nt_template(
                scoped_sd,
                run_id=f"{run_id}::rule{rank_idx}",
                options=options,
            )
        except Exception as exc:
            tmpl = GeneratedTemplate(
                xml_str=f"<!-- generation error: {exc} -->",
                parameters={},
                reasoning={},
                warnings=[f"Template generation failed: {exc}"],
            )

        results.append(PerRuleTemplate(
            rule_str=rule_str,
            rule_rank=rank_idx,
            n_signals=n_signals,
            best_stop=best_stop,
            best_target=best_tgt,
            best_rr=best_rr,
            best_win_rate=best_wr,
            best_pf=best_pf,
            template=tmpl,
        ))

    return results
