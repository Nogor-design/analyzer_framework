"""
Generate NinjaTrader 8 strategy template XML files from a blueprints.json
file emitted by ta_foundation.analysis.large_candle_excursion.strategy_blueprint_exporter.

One blueprint → one NT8 .xml template the user can drop into
%UserProfile%\\Documents\\NinjaTrader 8\\templates\\Strategy\\LargeCandleReversal\\

Seed-based design
-----------------
NT8 strategy-template XML carries machine-specific metadata — in particular the
dynamic-assembly hash baked into every enum's `ParameterTypeSerializable`
attribute.  That hash comes from the user's local NT8 NinjaScript compile.

To sidestep this, the generator works from a **seed template** you save once
from NinjaTrader (right-click the LargeCandleReversal strategy in a Strategy
Analyzer → Save As… Template → call it `Test.xml` and place it in the output
directory).  The generator loads that seed, rewrites only the parameter
values for the blueprint, and writes a new template.

Usage
-----
python -m ta_foundation.strategies.LargeCandleReversal.generate_nt8_template \\
    --input  path/to/<report>_blueprints.json \\
    --output src/ta_foundation/strategies/LargeCandleReversal/templates \\
    --seed   src/ta_foundation/strategies/LargeCandleReversal/templates/Test.xml \\
    [--strict]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enum mappings (blueprint string → NT C# enum value)
# ---------------------------------------------------------------------------

ONSET_ENUM: Dict[str, str] = {
    "first_large_after_failed_continuation":                 "FirstLargeAfterFailedContinuation",
    "first_large_after_directional_run":                     "FirstLargeAfterDirectionalRun",
    "first_large_at_key_level_with_extended_vwap_stretch":   "FirstLargeAtKeyLevelVwapStretched",
    "first_large_after_session_range_break":                 "FirstLargeAfterSessionRangeBreak",
    "first_large_after_compression":                         "FirstLargeAfterCompression",
}

# Task 2: only this onset is implemented in LargeCandleReversal.cs.  Templates
# for any other onset take zero trades — the C# strategy warns loudly at startup.
IMPLEMENTED_ONSETS = frozenset({"first_large_after_failed_continuation"})


DIRECTION_POLICY_ENUM: Dict[str, str] = {
    "counter_to_failed_continuation":         "CounterToFailedContinuation",
    "counter_to_directional_run":             "CounterToDirectionalRun",
    "counter_to_level_rejection":             "CounterToLevelRejection",
    "continuation_of_range_break":            "ContinuationOfRangeBreak",
    "continuation_of_compression_breakout":   "ContinuationOfCompression",
    "signal_direction":                       "SignalDirection",
}

BASIS_ENUM: Dict[str, str] = {
    "range": "Range",
    "body":  "Body",
}

TRIGGER_EVENT_ENUM: Dict[str, str] = {
    "on_bar_close": "OnBarClose",
}

FILL_TYPE_ENUM: Dict[str, str] = {
    "market_on_next_open": "MarketOnNextOpen",
}

STOP_STYLE_ENUM: Dict[str, str] = {
    "signal_candle_extreme_with_cap": "SignalCandleExtremeWithCap",
    "atr_multiple":                   "AtrMultiple",
}

HOLD_RULE_MODE_ENUM: Dict[str, str] = {
    "any_of":       "AnyOf",
    "primary_only": "PrimaryOnly",
}

# Task 3: research label → (C# LcrPrimaryHoldRule enum, template-input overrides).
# Keys are the research `early_path_condition` values emitted by regime_discovery /
# strategy_blueprint_exporter.  Values specify the C# enum and the threshold
# inputs that must be written into the template for that rule to fire.
#
# Compound labels like "adv2bar_20_40pct" are intentionally absent: the current
# C# strategy only supports a ≤ cap, not a [min, max] band.  A future
# LcrPrimaryHoldRule.Adv2BarBand (with both OrderlyAdv2BarPctMin and
# OrderlyAdv2BarPctMax) would let us include those; until then the generator
# rejects the label.
HOLD_RULE_MAP: Dict[str, Dict[str, Any]] = {
    "midpoint_reclaim_yes": {
        "enum": "MidpointReclaimYes",
        "inputs": {},
    },
    "rebreak_no": {
        "enum": "RebreakNo",
        "inputs": {},
    },
    "explosive_start": {
        "enum": "ExplosiveStart",
        "inputs": {
            "ExplosiveFav2BarPctMin": 45.0,
            "ExplosiveAdv2BarPctMax": 20.0,
        },
    },
    "orderly_start": {
        "enum": "OrderlyStart",
        "inputs": {
            "OrderlyFav2BarPctMin": 25.0,
            "OrderlyAdv2BarPctMax": 35.0,
        },
    },
    "fav2bar_ge_35pct": {
        "enum": "Fav2BarOnly",
        "inputs": {"Fav2BarOnlyPctMin": 35.0},
    },
    "fav2bar_ge_45pct": {
        "enum": "Fav2BarOnly",
        "inputs": {"Fav2BarOnlyPctMin": 45.0},
    },
    "adv2bar_lt_20pct": {
        "enum": "Adv2BarOnly",
        "inputs": {"Adv2BarOnlyPctMax": 20.0},
    },
    "adv2bar_lt_35pct": {
        "enum": "Adv2BarOnly",
        "inputs": {"Adv2BarOnlyPctMax": 35.0},
    },
}

ON_FAIL_ACTION_ENUM: Dict[str, str] = {
    "exit_at_market":      "ExitAtMarket",
    "exit_at_scalp_target": "ExitAtScalpTarget",
}

ON_PASS_ACTION_ENUM: Dict[str, str] = {
    "trail_for_runner":    "TrailForRunner",
    "exit_at_scalp_target": "ExitAtScalpTarget",
}

TRAIL_STYLE_ENUM: Dict[str, str] = {
    "atr":        "Atr",
    "chandelier": "Chandelier",
    "none":       "None",
}

SESSION_MODE_ENUM: Dict[str, str] = {
    "all_session": "AllSession",
    "allowlist":   "Allowlist",
}


# ---------------------------------------------------------------------------
# Task 1: session label canonicalisation
# ---------------------------------------------------------------------------
#
# The C# strategy replaced its free-text <AllowedSessionsCsv> input with ten
# individual boolean properties (AllowSessionAsia, AllowSessionNyOpen, ...).
# Misspelling is now structurally impossible: a typo in the research label
# produces a <AllowSessionFooBar> element that doesn't exist in the .cs file,
# and NinjaTrader fails to load the template rather than silently taking zero
# trades.
#
# SESSION_LABELS_CANONICAL is the single source of truth mapping the canonical
# Python research label → the C# property name.  Any label the research can
# emit must resolve to one of these keys (after alias normalisation below).

SESSION_LABELS_CANONICAL: Dict[str, str] = {
    "asia":              "AllowSessionAsia",
    "early_london":      "AllowSessionEarlyLondon",
    "mid_london":        "AllowSessionMidLondon",
    "london_ny_overlap": "AllowSessionLondonNyOverlap",
    "ny_pre_open":       "AllowSessionNyPreOpen",
    "ny_open":           "AllowSessionNyOpen",
    "mid_day":           "AllowSessionMidDay",
    "power_hour":        "AllowSessionPowerHour",
    "after_hours":       "AllowSessionAfterHours",
    "overnight":         "AllowSessionOvernight",
}

# Some research pipelines still emit legacy / truncated labels (for example,
# session_classifier.py assigns "ny_pre", "mid_ny", "london").  Normalise these
# to canonical labels here so the template emits the right <AllowSession*>
# element.  Identity entries are listed explicitly for clarity.
#
# NOTE: the underlying time-window definitions in session_classifier.py do not
# line up 1:1 with the C# ClassifySession — e.g. Python "mid_ny" (10:30-14:00 ET)
# overlaps C# "ny_open" and "mid_day".  The alias below resolves the LABEL
# collision so the template isn't structurally broken; aligning the TIME
# windows is a separate, larger piece of work tracked in the direction-policy
# design note.
_RESEARCH_SESSION_ALIASES: Dict[str, str] = {
    "asia":               "asia",
    "early_london":       "early_london",
    "mid_london":         "mid_london",
    "london":             "asia",              # Python 00:00-03:00 ≈ C# asia tail
    "london_ny_overlap":  "london_ny_overlap",
    "ny_pre":             "ny_pre_open",       # legacy truncation
    "ny_pre_open":        "ny_pre_open",
    "ny_open":            "ny_open",
    "mid_ny":             "mid_day",           # legacy label
    "mid_day":            "mid_day",
    "power_hour":         "power_hour",
    "after_hours":        "after_hours",
    "overnight":          "overnight",
}


def canonical_session_label(research_label: str) -> str:
    """Translate a research session label (possibly legacy / truncated) to the
    canonical label used by SESSION_LABELS_CANONICAL.  Raise ValueError if the
    label is unknown — the alternative is silently dropping it, which leaves
    the template filtering out every bar."""
    key = str(research_label or "").strip().lower()
    if not key:
        raise ValueError("[lcr-export] empty session label (cannot resolve to an AllowSession* property)")
    canon = _RESEARCH_SESSION_ALIASES.get(key)
    if canon is None:
        raise ValueError(
            f"[lcr-export] unknown session label {research_label!r}; "
            f"not in _RESEARCH_SESSION_ALIASES. Known canonical labels: "
            f"{sorted(SESSION_LABELS_CANONICAL.keys())}"
        )
    if canon not in SESSION_LABELS_CANONICAL:
        raise ValueError(
            f"[lcr-export] alias {research_label!r} → {canon!r}, but {canon!r} "
            f"is not in SESSION_LABELS_CANONICAL (bug in the alias map)."
        )
    return canon


# Parameters that appear in the OptimizationParameters section.  NT8 skips
# string-typed fields there, so BlueprintId stays in the Strategy section only.
OPTIMIZER_PARAMETER_NAMES = {
    "OnsetCondition", "DirectionPolicy",
    "CandleLookbackBars", "CandleBasis", "ThresholdMultiplier",
    "MinBodyTicks", "MinRangeTicks",
    "MaxBodyTicks", "MaxRangeTicks",
    "FailedContinuationLookbackSignals", "FailedSignalRebreakWindowBars",
    "AtrPeriod",
    "VwapStretchAtrMin", "VolumeMultipleMin", "DirectionStreakMin", "RangeExpansionMultipleMin",
    "RequireContextGates",
    "QuietLookbackMinutes", "QuietMaxPriorSignals",
    "SessionMode",
    "AllowSessionAsia", "AllowSessionEarlyLondon", "AllowSessionMidLondon",
    "AllowSessionLondonNyOverlap", "AllowSessionNyPreOpen", "AllowSessionNyOpen",
    "AllowSessionMidDay", "AllowSessionPowerHour", "AllowSessionAfterHours",
    "AllowSessionOvernight",
    "TriggerEvent", "FillType", "MaxEntriesPerSession", "Contracts",
    "StopStyle", "StopBaseOffsetTicks", "MaxStopTicks", "AtrFallbackMultiple", "RecommendedStopTicks",
    "EvaluationBar", "HoldRuleMode", "PrimaryHoldRule",
    "MidpointReclaimBars", "RebreakCheckBars",
    "ExplosiveFav2BarPctMin", "ExplosiveAdv2BarPctMax",
    "OrderlyFav2BarPctMin", "OrderlyAdv2BarPctMax",
    "Fav2BarOnlyPctMin", "Adv2BarOnlyPctMax",
    "OnFailAction", "OnPassAction",
    "ScalpTargetPct", "ExpansionTargetPct", "RunnerTargetPct", "TimeStopMinutes",
    "TrailStyle", "AtrTrailMultiple",
    "BaseUnit", "MaxAddOns", "MaxTotalUnits",
    "EnableDebugPrint", "DrawMarkers",
}


# ---------------------------------------------------------------------------
# Value formatters
# ---------------------------------------------------------------------------

def _enum(value: Optional[str], mapping: Dict[str, str], default: str) -> str:
    if not value:
        return default
    return mapping.get(str(value).lower(), default)


def _int_str(v: Any, default: int = 0) -> str:
    if v is None:
        return str(default)
    try:
        return str(int(round(float(v))))
    except Exception:
        return str(default)


def _float_str(v: Any, default: float = 0.0, digits: int = 3) -> str:
    if v is None:
        v = default
    try:
        f = float(v)
    except Exception:
        f = default
    s = f"{f:.{digits}f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _bool_lower(v: Any, default: bool = False) -> str:
    return ("true" if (bool(v) if v is not None else default) else "false")


def _safe_filename(name: str) -> str:
    out = ""
    for ch in name:
        if ch.isalnum() or ch in ("_", "-"):
            out += ch
        else:
            out += "_"
    return out.strip("_") or "blueprint"


# ---------------------------------------------------------------------------
# Task 4: candle bucket parsing
# ---------------------------------------------------------------------------

def parse_candle_bucket(bucket: Any) -> Tuple[int, Optional[int]]:
    """Parse a candle bucket string like "50-75" (ticks) into (min, max).
    Open-ended "75+" returns (75, None).  Returns (0, None) for a missing /
    unparseable bucket."""
    if bucket is None:
        return (0, None)
    s = str(bucket).strip()
    if not s:
        return (0, None)
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            lo, hi = hi, lo
        return (lo, hi)
    m = re.match(r"^\s*(\d+)\s*\+\s*$", s)
    if m:
        return (int(m.group(1)), None)
    m = re.match(r"^\s*(\d+)\s*$", s)
    if m:
        return (int(m.group(1)), None)
    return (0, None)


def _candle_bucket_fields(bucket: Any, basis: str, existing_min_body: int, existing_min_range: int) -> Dict[str, int]:
    """Return the Min/Max ticks fields for this candle bucket + basis combo.
    Body-basis templates pin Body ticks; Range-basis templates pin Range ticks."""
    lo, hi = parse_candle_bucket(bucket)
    basis_lc = str(basis or "range").lower()
    # Defaults if no bucket: keep existing seed Min* values; Max* disabled.
    if lo == 0 and hi is None:
        return {
            "MinBodyTicks":  int(existing_min_body),
            "MaxBodyTicks":  0,
            "MinRangeTicks": int(existing_min_range),
            "MaxRangeTicks": 0,
        }
    if basis_lc == "body":
        return {
            "MinBodyTicks":  lo or int(existing_min_body),
            "MaxBodyTicks":  int(hi) if hi is not None else 0,
            "MinRangeTicks": int(existing_min_range),
            "MaxRangeTicks": 0,
        }
    # Default: Range basis.
    return {
        "MinBodyTicks":  int(existing_min_body),
        "MaxBodyTicks":  0,
        "MinRangeTicks": lo or int(existing_min_range),
        "MaxRangeTicks": int(hi) if hi is not None else 0,
    }


# ---------------------------------------------------------------------------
# Task 1: session boolean field computation
# ---------------------------------------------------------------------------

def _session_boolean_fields(allowed_sessions: List[str], strict: bool = False) -> Dict[str, str]:
    """Return a dict whose keys are the ten AllowSession* property names and
    whose values are "true"/"false" strings.  Raises ValueError on any unknown
    label — every session must resolve via canonical_session_label."""
    # Start with all false so every property is present.
    fields: Dict[str, str] = {prop: "false" for prop in SESSION_LABELS_CANONICAL.values()}
    for raw in allowed_sessions or []:
        canon = canonical_session_label(raw)
        prop = SESSION_LABELS_CANONICAL[canon]
        fields[prop] = "true"
    return fields


# ---------------------------------------------------------------------------
# Blueprint → field values
# ---------------------------------------------------------------------------

def blueprint_to_fields(bp: Dict[str, Any]) -> Dict[str, str]:
    """Map a blueprint dict to the NT8 input name → string-value dict.

    Boolean fields are returned lowercase (matching the Strategy-section
    convention).  For the OptimizationParameters `ValueSerializable` field
    the caller converts to PascalCase.
    """
    prov   = bp.get("provenance") or {}
    od     = bp.get("onset_detection") or {}
    cs     = od.get("candle_size") or {}
    cg     = od.get("context_gates") or {}
    qf     = od.get("quiet_filter") or {}
    fc     = od.get("failed_continuation") or {}
    entry  = bp.get("entry_rule") or {}
    stop   = bp.get("stop_rule") or {}
    pem    = bp.get("post_entry_management") or {}
    primary_hold = pem.get("primary_hold_rule") or {}
    union_rules_obj = pem.get("union_hold_rules") or {}
    union_rules = {r.get("name"): r for r in (union_rules_obj.get("rules") or [])}
    exit_r = bp.get("exit_rule") or {}
    sf     = bp.get("session_filter") or {}
    risk   = bp.get("risk_and_friction") or {}

    # Task 3 — hold rule lookup.  Falls back to MidpointReclaimYes so the
    # template remains loadable even if the blueprint carries a legacy label;
    # the caller should have already filtered unknown labels during
    # write_templates_from_file.
    primary_name = str(primary_hold.get("name") or "").lower()
    hold_entry = HOLD_RULE_MAP.get(primary_name)
    if hold_entry is None:
        primary_enum = "MidpointReclaimYes"
        hold_inputs: Dict[str, float] = {}
    else:
        primary_enum = hold_entry["enum"]
        hold_inputs = dict(hold_entry.get("inputs") or {})

    # Inline overrides from the primary_hold_rule block (e.g. explicit
    # fav_2bar_pct_min on an explosive_start rule).
    if "fav_2bar_pct_min" in primary_hold:
        # Route to the right input name based on enum.
        if primary_enum == "ExplosiveStart":
            hold_inputs["ExplosiveFav2BarPctMin"] = float(primary_hold["fav_2bar_pct_min"])
        elif primary_enum == "OrderlyStart":
            hold_inputs["OrderlyFav2BarPctMin"] = float(primary_hold["fav_2bar_pct_min"])
        elif primary_enum == "Fav2BarOnly":
            hold_inputs["Fav2BarOnlyPctMin"] = float(primary_hold["fav_2bar_pct_min"])
    if "adv_2bar_pct_max" in primary_hold:
        if primary_enum == "ExplosiveStart":
            hold_inputs["ExplosiveAdv2BarPctMax"] = float(primary_hold["adv_2bar_pct_max"])
        elif primary_enum == "OrderlyStart":
            hold_inputs["OrderlyAdv2BarPctMax"] = float(primary_hold["adv_2bar_pct_max"])
        elif primary_enum == "Adv2BarOnly":
            hold_inputs["Adv2BarOnlyPctMax"] = float(primary_hold["adv_2bar_pct_max"])

    # If the primary isn't explosive_start but the union carries one, use its
    # thresholds for the explosive inputs (back-compat with the existing
    # blueprint shape where all three rules travel together).
    if primary_enum != "ExplosiveStart" and "explosive_start" in union_rules:
        e = union_rules["explosive_start"]
        hold_inputs.setdefault("ExplosiveFav2BarPctMin", float(e.get("fav_2bar_pct_min", 45.0)))
        hold_inputs.setdefault("ExplosiveAdv2BarPctMax", float(e.get("adv_2bar_pct_max", 20.0)))

    # Default backstops so every input is always present in the template.
    hold_inputs.setdefault("ExplosiveFav2BarPctMin", 45.0)
    hold_inputs.setdefault("ExplosiveAdv2BarPctMax", 20.0)
    hold_inputs.setdefault("OrderlyFav2BarPctMin",   25.0)
    hold_inputs.setdefault("OrderlyAdv2BarPctMax",   35.0)
    hold_inputs.setdefault("Fav2BarOnlyPctMin",      35.0)
    hold_inputs.setdefault("Adv2BarOnlyPctMax",      20.0)

    mp_bars = 2
    if primary_name == "midpoint_reclaim_yes":
        mp_bars = int(primary_hold.get("requires_midpoint_reclaim_within_bars", 2)) or 2
    elif "midpoint_reclaim_yes" in union_rules:
        mp_bars = int(union_rules["midpoint_reclaim_yes"].get("requires_midpoint_reclaim_within_bars", 2)) or 2

    rb_bars = 2
    if primary_name == "rebreak_no":
        rb_bars = int(primary_hold.get("check_bars", 2)) or 2
    elif "rebreak_no" in union_rules:
        rb_bars = int(union_rules["rebreak_no"].get("check_bars", 2)) or 2

    # Task 4 — candle bucket (e.g. "50-75") populates Min/Max Range or Body
    # ticks based on the basis.  Fall back to cs.min_* if bucket is absent.
    candle_bucket = (
        cs.get("candle_bucket")
        or cs.get("bucket")
        or prov.get("candle_bucket")
        or (bp.get("conditions") or {}).get("candle_bucket")
    )
    bucket_fields = _candle_bucket_fields(
        candle_bucket,
        cs.get("basis") or "range",
        int(cs.get("min_body_ticks") or 2),
        int(cs.get("min_range_ticks") or 4),
    )

    # Task 1 — ten AllowSession* booleans replace the old AllowedSessionsCsv.
    session_bools = _session_boolean_fields(sf.get("allowed_sessions") or [])

    # Task 6 — default FailedContinuationLookbackSignals to 1.  The research
    # definition is singular ("the previous signal had weak early behavior");
    # higher counts should only be emitted when the research explicitly
    # validates them.
    failed_lookback = int(fc.get("lookback_signals") or 1)
    if failed_lookback < 1:
        failed_lookback = 1

    fields: Dict[str, str] = {
        # String — only in the Strategy section.
        "BlueprintId":                        str(bp.get("blueprint_id") or "lcr"),

        # Enums + numerics — duplicated in both OptimizationParameters and Strategy.
        "OnsetCondition":                     _enum(prov.get("onset_condition"), ONSET_ENUM, "FirstLargeAfterFailedContinuation"),
        "DirectionPolicy":                    _enum(bp.get("direction_policy"), DIRECTION_POLICY_ENUM, "CounterToFailedContinuation"),

        "CandleLookbackBars":                 _int_str(cs.get("lookback_bars"), 20),
        "CandleBasis":                        _enum(cs.get("basis"), BASIS_ENUM, "Range"),
        "ThresholdMultiplier":                _float_str(cs.get("threshold_value"), 1.5),
        "MinBodyTicks":                       str(bucket_fields["MinBodyTicks"]),
        "MinRangeTicks":                      str(bucket_fields["MinRangeTicks"]),
        "MaxBodyTicks":                       str(bucket_fields["MaxBodyTicks"]),
        "MaxRangeTicks":                      str(bucket_fields["MaxRangeTicks"]),
        "FailedContinuationLookbackSignals":  str(failed_lookback),
        "FailedSignalRebreakWindowBars":      _int_str(fc.get("rebreak_window_bars"), 10),
        "AtrPeriod":                          _int_str(od.get("atr_period"), 14),

        "VwapStretchAtrMin":                  _float_str(cg.get("vwap_stretch_atr_min"), 0.75),
        "VolumeMultipleMin":                  _float_str(cg.get("volume_multiple_min"), 2.5),
        "DirectionStreakMin":                 _int_str(cg.get("direction_streak_min"), 3),
        "RangeExpansionMultipleMin":          _float_str(cg.get("range_expansion_multiple_min"), 1.5),
        "RequireContextGates":                _bool_lower(False),

        "QuietLookbackMinutes":               _int_str(qf.get("lookback_minutes"), 60),
        "QuietMaxPriorSignals":               _int_str(qf.get("max_prior_signals"), 1),

        "SessionMode":                        _enum(sf.get("mode"), SESSION_MODE_ENUM, "AllSession"),

        "TriggerEvent":                       _enum(entry.get("trigger_event"), TRIGGER_EVENT_ENUM, "OnBarClose"),
        "FillType":                           _enum(entry.get("fill_type"), FILL_TYPE_ENUM, "MarketOnNextOpen"),
        "MaxEntriesPerSession":               _int_str(entry.get("max_entries_per_session"), 1),
        "Contracts":                          _int_str((risk or {}).get("base_unit"), 1),

        "StopStyle":                          _enum(stop.get("style"), STOP_STYLE_ENUM, "SignalCandleExtremeWithCap"),
        "StopBaseOffsetTicks":                _int_str(stop.get("base_offset_ticks"), 4),
        "MaxStopTicks":                       _int_str(stop.get("max_stop_ticks"), 100),
        "AtrFallbackMultiple":                _float_str(stop.get("atr_fallback_multiple"), 1.2),
        "RecommendedStopTicks":               _int_str(stop.get("recommended_stop_ticks"), 40),

        "EvaluationBar":                      _int_str(pem.get("evaluation_bar"), 2),
        "HoldRuleMode":                       _enum(union_rules_obj.get("type"), HOLD_RULE_MODE_ENUM, "AnyOf"),
        "PrimaryHoldRule":                    primary_enum,
        "MidpointReclaimBars":                str(mp_bars),
        "RebreakCheckBars":                   str(rb_bars),
        "ExplosiveFav2BarPctMin":             _float_str(hold_inputs["ExplosiveFav2BarPctMin"], 45.0, digits=2),
        "ExplosiveAdv2BarPctMax":             _float_str(hold_inputs["ExplosiveAdv2BarPctMax"], 20.0, digits=2),
        "OrderlyFav2BarPctMin":               _float_str(hold_inputs["OrderlyFav2BarPctMin"], 25.0, digits=2),
        "OrderlyAdv2BarPctMax":               _float_str(hold_inputs["OrderlyAdv2BarPctMax"], 35.0, digits=2),
        "Fav2BarOnlyPctMin":                  _float_str(hold_inputs["Fav2BarOnlyPctMin"], 35.0, digits=2),
        "Adv2BarOnlyPctMax":                  _float_str(hold_inputs["Adv2BarOnlyPctMax"], 20.0, digits=2),
        "OnFailAction":                       _enum(pem.get("on_fail_action"), ON_FAIL_ACTION_ENUM, "ExitAtMarket"),
        "OnPassAction":                       _enum(pem.get("on_pass_action"), ON_PASS_ACTION_ENUM, "TrailForRunner"),

        "ScalpTargetPct":                     _int_str(exit_r.get("scalp_target_pct_of_signal_candle"), 30),
        "ExpansionTargetPct":                 _int_str(exit_r.get("expansion_target_pct_of_signal_candle"), 62),
        "RunnerTargetPct":                    _int_str(exit_r.get("runner_target_pct_of_signal_candle"), 125),
        "TimeStopMinutes":                    _int_str(exit_r.get("time_stop_minutes"), 30),
        "TrailStyle":                         _enum(exit_r.get("trail_style"), TRAIL_STYLE_ENUM, "Atr"),
        "AtrTrailMultiple":                   _float_str(exit_r.get("atr_trail_multiple"), 1.5),

        "BaseUnit":                           _int_str(risk.get("base_unit"), 1),
        "MaxAddOns":                          _int_str(risk.get("max_add_ons"), 1),
        "MaxTotalUnits":                      _int_str(risk.get("max_total_units"), 2),

        "EnableDebugPrint":                   _bool_lower(False),
        "DrawMarkers":                        _bool_lower(True),
    }
    # Session booleans added after the rest so they appear as ten distinct fields.
    fields.update(session_bools)
    return fields


# ---------------------------------------------------------------------------
# Seed-based XML patching — regex on raw text.
# ---------------------------------------------------------------------------
#
# We cannot use ElementTree here: NT's XmlSerializer declares xmlns:xsd and
# xmlns:xsi on specific inner elements (<ArrayOfParameter>, <LargeCandleReversal>),
# and ElementTree collapses / relocates namespace declarations during write.
# That produces XML the serialiser cannot round-trip.  To stay byte-faithful
# to the seed we work directly on the raw text.

_BOOL_FIELDS = (
    {"RequireContextGates", "EnableDebugPrint", "DrawMarkers"}
    | set(SESSION_LABELS_CANONICAL.values())  # ten AllowSession* are booleans
)

# Enum fields — Max/Min in their <Parameter> blocks are integer indices
# (NT emits "0" for both when the min-max range is unset) and must NOT be
# overwritten with the enum string.  Only <ValueSerializable> takes the enum.
_ENUM_FIELDS = {
    "OnsetCondition", "DirectionPolicy", "CandleBasis",
    "SessionMode", "TriggerEvent", "FillType", "StopStyle",
    "HoldRuleMode", "PrimaryHoldRule", "OnFailAction", "OnPassAction", "TrailStyle",
}


def _value_for_serialisable(name: str, lower_value: str) -> str:
    """Return the string to write to <ValueSerializable>.
    Booleans use PascalCase (True/False); everything else passes through."""
    if name in _BOOL_FIELDS:
        return "True" if lower_value == "true" else "False"
    return lower_value


def _bool_parameter_block(name: str, value_lower: str) -> str:
    """Construct a complete <Parameter>…</Parameter> block for a new boolean
    input (e.g., AllowSessionAsia) when the seed template doesn't already
    contain one.  Booleans don't carry a machine-specific assembly hash, so
    the stock mscorlib token is safe."""
    value_pascal = "True" if value_lower == "true" else "False"
    return (
        "      <Parameter>\n"
        "        <EnumValuesSerializable />\n"
        "        <Increment>1</Increment>\n"
        f'        <Max xsi:type="xsd:boolean">{value_lower}</Max>\n'
        f'        <Min xsi:type="xsd:boolean">{value_lower}</Min>\n'
        f"        <Name>{name}</Name>\n"
        "        <ParameterTypeSerializable>System.Boolean, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</ParameterTypeSerializable>\n"
        f"        <ValueSerializable>{value_pascal}</ValueSerializable>\n"
        "      </Parameter>\n"
    )


def _patch_parameter_block(block: str, name: str, value_lower: str) -> str:
    """Within a <Parameter>…</Parameter> block whose <Name> is `name`,
    rewrite <Max>, <Min>, and <ValueSerializable>.  For enum fields only
    <ValueSerializable> is rewritten; Max/Min stay at their seed values
    (NT stores enum range as integer indices there)."""
    serial_val = _value_for_serialisable(name, value_lower)
    if name not in _ENUM_FIELDS:
        # Non-enum: booleans in Max/Min are already lowercase; numeric values pass through.
        block = re.sub(
            r'(<Max\s+xsi:type="[^"]+">)[^<]*(</Max>)',
            lambda m: m.group(1) + value_lower + m.group(2),
            block,
            count=1,
        )
        block = re.sub(
            r'(<Min\s+xsi:type="[^"]+">)[^<]*(</Min>)',
            lambda m: m.group(1) + value_lower + m.group(2),
            block,
            count=1,
        )
    block = re.sub(
        r'(<ValueSerializable>)[^<]*(</ValueSerializable>)',
        lambda m: m.group(1) + serial_val + m.group(2),
        block,
        count=1,
    )
    return block


def _remove_allowed_sessions_csv(text: str) -> str:
    """Strip any <AllowedSessionsCsv …> element from the text — it was removed
    from the C# strategy and NinjaTrader errors on load when it is present."""
    text = re.sub(r"\s*<AllowedSessionsCsv\s*/>\s*", "\n      ", text, count=1)
    text = re.sub(r"\s*<AllowedSessionsCsv>[^<]*</AllowedSessionsCsv>\s*", "\n      ", text, count=1)
    return text


def _insert_session_bool_elements_after_session_mode(body: str, fields: Dict[str, str]) -> str:
    """Ensure the Strategy body contains all ten <AllowSession*> elements,
    immediately after <SessionMode>.  Any existing element is left in place
    (will be patched by the main Pass 2 loop); missing elements are inserted."""
    session_mode_match = re.search(r"(<SessionMode>[^<]*</SessionMode>)", body)
    if not session_mode_match:
        return body
    to_insert: List[str] = []
    for canon, prop in SESSION_LABELS_CANONICAL.items():
        if re.search(r"<" + re.escape(prop) + r"(\s*/>|>)", body):
            continue
        value = fields.get(prop, "false")
        to_insert.append(f"<{prop}>{value}</{prop}>")
    if not to_insert:
        return body
    insertion = "\n      " + "\n      ".join(to_insert)
    head_end = session_mode_match.end()
    return body[:head_end] + insertion + body[head_end:]


def _insert_session_bool_parameters(
    text: str, fields: Dict[str, str]
) -> str:
    """Insert <Parameter> blocks for missing AllowSession* fields into
    OptimizationParameters, immediately after the SessionMode parameter."""
    # Locate the ArrayOfParameter block first.
    aof = re.search(r"(<ArrayOfParameter\b[^>]*>)(.*?)(</ArrayOfParameter>)", text, flags=re.DOTALL)
    if not aof:
        return text
    inner = aof.group(2)

    # Find the SessionMode <Parameter> block as our anchor.
    anchor_match = re.search(
        r"<Parameter>(?:(?!<Parameter>).)*?<Name>SessionMode</Name>(?:(?!</Parameter>).)*?</Parameter>",
        inner,
        flags=re.DOTALL,
    )
    if not anchor_match:
        return text  # nothing to anchor against; leave alone

    existing_names: set = set(re.findall(r"<Name>([^<]+)</Name>", inner))
    to_add: List[str] = []
    for canon, prop in SESSION_LABELS_CANONICAL.items():
        if prop in existing_names:
            continue
        value = fields.get(prop, "false")
        to_add.append(_bool_parameter_block(prop, value))

    if not to_add:
        return text

    insertion = "\n" + "".join(to_add)
    anchor_end = anchor_match.end()
    new_inner = inner[:anchor_end] + insertion + inner[anchor_end:]
    return text[: aof.start(2)] + new_inner + text[aof.end(2):]


def patch_seed_text(seed_text: str, fields: Dict[str, str]) -> str:
    """Rewrite parameter values in the seed XML, preserving all other text.

    Three passes:
      1. Inject missing AllowSession* <Parameter> blocks into OptimizationParameters.
      2. OptimizationParameters — per-parameter block with Max/Min/ValueSerializable.
      3. Strategy/LargeCandleReversal — drop AllowedSessionsCsv, inject missing
         AllowSession* elements, then rewrite every per-field text element.
    """
    # Pass 1a: inject missing boolean parameter blocks.
    seed_text = _insert_session_bool_parameters(seed_text, fields)

    # Pass 1b: for each existing <Parameter>…</Parameter> block whose <Name>X</Name>
    # matches an optimizer field, rewrite the three value tags.
    def _rewrite_param(match: "re.Match[str]") -> str:
        block = match.group(0)
        name_match = re.search(r"<Name>\s*([^<\s][^<]*?)\s*</Name>", block)
        if not name_match:
            return block
        name = name_match.group(1).strip()
        if name not in fields or name not in OPTIMIZER_PARAMETER_NAMES:
            return block
        return _patch_parameter_block(block, name, fields[name])

    new_text = re.sub(
        r"<Parameter>.*?</Parameter>",
        _rewrite_param,
        seed_text,
        flags=re.DOTALL,
    )

    # Pass 2: rewrite Strategy-section fields.  Only do this inside the
    # <LargeCandleReversal …> section to avoid accidentally matching names
    # elsewhere.
    lcr_match = re.search(
        r"(<LargeCandleReversal\b[^>]*>)(.*?)(</LargeCandleReversal>)",
        new_text,
        flags=re.DOTALL,
    )
    if not lcr_match:
        raise ValueError("seed template missing <Strategy>/<LargeCandleReversal> section")
    head, body, tail = lcr_match.group(1), lcr_match.group(2), lcr_match.group(3)

    # Drop the obsolete AllowedSessionsCsv element entirely.
    body = _remove_allowed_sessions_csv(body)

    # Ensure all ten AllowSession* elements are present.
    body = _insert_session_bool_elements_after_session_mode(body, fields)

    for tag, value in fields.items():
        if tag == "BlueprintId":
            body, n = re.subn(
                r"<BlueprintId\s*/>",
                f"<BlueprintId>{value}</BlueprintId>",
                body,
                count=1,
            )
            if n == 0:
                body = re.sub(
                    r"(<BlueprintId>)[^<]*(</BlueprintId>)",
                    lambda m, v=value: m.group(1) + v + m.group(2),
                    body,
                    count=1,
                )
            continue
        # Self-closing seed → reopen with value.
        body, n = re.subn(
            r"<" + re.escape(tag) + r"\s*/>",
            f"<{tag}>{value}</{tag}>",
            body,
            count=1,
        )
        if n == 0:
            body = re.sub(
                r"(<" + re.escape(tag) + r">)[^<]*(</" + re.escape(tag) + r">)",
                lambda m, v=value: m.group(1) + v + m.group(2),
                body,
                count=1,
            )

    return new_text[: lcr_match.start()] + head + body + tail + new_text[lcr_match.end():]


def write_text(new_text: str, output_path: Path, source_bytes: bytes) -> None:
    """Write the patched XML, preserving the seed's BOM (if any) byte-for-byte."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bom = b"\xef\xbb\xbf" if source_bytes.startswith(b"\xef\xbb\xbf") else b""
    output_path.write_bytes(bom + new_text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Task 2: onset + hold-rule whitelisting
# ---------------------------------------------------------------------------

def _blueprint_onset(bp: Dict[str, Any]) -> str:
    return str((bp.get("provenance") or {}).get("onset_condition") or "").lower()


def _blueprint_hold_rule_name(bp: Dict[str, Any]) -> str:
    pem = bp.get("post_entry_management") or {}
    return str(((pem.get("primary_hold_rule") or {}).get("name") or "")).lower()


def _classify_blueprint(bp: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (is_tradeable, reason).  reason is empty when tradeable."""
    onset = _blueprint_onset(bp)
    if onset not in IMPLEMENTED_ONSETS:
        return (False, f"onset '{onset}' not implemented (IMPLEMENTED_ONSETS={sorted(IMPLEMENTED_ONSETS)})")
    hold = _blueprint_hold_rule_name(bp)
    if hold and hold not in HOLD_RULE_MAP:
        return (False, f"hold rule '{hold}' not in HOLD_RULE_MAP")
    # Also require at least one canonical session if SessionMode=Allowlist.
    sf = bp.get("session_filter") or {}
    if str(sf.get("mode") or "").lower() == "allowlist":
        sessions = sf.get("allowed_sessions") or []
        try:
            canonicalised = [canonical_session_label(s) for s in sessions]
        except ValueError as exc:
            return (False, str(exc))
        if not canonicalised:
            return (False, "SessionMode=allowlist but allowed_sessions is empty")
    return (True, "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_templates_from_file(
    blueprints_json_path: Path,
    output_dir: Path,
    seed_xml_path: Path,
    *,
    strict: bool = False,
    skipped_report_path: Optional[Path] = None,
) -> List[Path]:
    if not seed_xml_path.is_file():
        raise FileNotFoundError(
            f"seed template not found: {seed_xml_path}. "
            "Save a template from NinjaTrader (Strategy Analyzer → Save As… Template) "
            "and place it at this path before running the generator."
        )
    seed_bytes = seed_xml_path.read_bytes()
    seed_text = seed_bytes.decode("utf-8-sig")

    data = json.loads(blueprints_json_path.read_text(encoding="utf-8"))
    blueprints = data.get("blueprints") or []
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    skipped: List[Dict[str, str]] = []
    seed_abs = seed_xml_path.resolve()
    for bp in blueprints:
        ok, reason = _classify_blueprint(bp)
        if not ok:
            msg = (
                f"[lcr-export] skipping blueprint "
                f"{bp.get('blueprint_id')!r}: {reason}"
            )
            print(msg)
            skipped.append({
                "blueprint_id": str(bp.get("blueprint_id") or ""),
                "onset_condition": _blueprint_onset(bp),
                "primary_hold_rule": _blueprint_hold_rule_name(bp),
                "reason": reason,
            })
            if strict:
                raise ValueError(msg)
            continue

        fields = blueprint_to_fields(bp)
        new_text = patch_seed_text(seed_text, fields)
        name = _safe_filename(str(bp.get("blueprint_id") or "blueprint"))
        path = (output_dir / f"{name}.xml").resolve()
        if path == seed_abs:
            path = (output_dir / f"{name}_generated.xml").resolve()
        write_text(new_text, path, seed_bytes)
        written.append(path)

    # Always record skipped blueprints; default to <output_dir>/skipped_blueprints.csv.
    if skipped:
        target = skipped_report_path or (output_dir / "skipped_blueprints.csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["blueprint_id", "onset_condition", "primary_hold_rule", "reason"],
            )
            w.writeheader()
            for row in skipped:
                w.writerow(row)
        print(f"[lcr-export] wrote skipped blueprints report: {target} (n={len(skipped)})")
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Generate NT8 strategy templates from a blueprints.json file, seeded from a user-saved Test.xml.")
    p.add_argument("--input",  required=True, type=Path, help="Path to *_blueprints.json")
    p.add_argument("--output", required=True, type=Path, help="Output directory for .xml template files")
    p.add_argument("--seed",   required=False, type=Path, default=None,
                   help="Path to a user-saved NT8 strategy template (default: <output>/Test.xml)")
    p.add_argument("--strict", action="store_true",
                   help="Raise if any blueprint is skipped (unimplemented onset or hold rule).")
    args = p.parse_args()

    if not args.input.is_file():
        print(f"[lcr-export] ERROR: input file not found: {args.input}")
        return 2

    seed = args.seed if args.seed is not None else (args.output / "Test.xml")
    try:
        written = write_templates_from_file(args.input, args.output, seed, strict=args.strict)
    except FileNotFoundError as exc:
        print(f"[lcr-export] ERROR: {exc}")
        return 2
    except ValueError as exc:
        print(f"[lcr-export] ERROR: {exc}")
        return 3
    if not written:
        print("[lcr-export] WARNING: no blueprints produced templates; nothing written.")
        return 1
    for p_ in written:
        print(f"Wrote: {p_}")
    print(f"[lcr-export] {len(written)} template(s) written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
