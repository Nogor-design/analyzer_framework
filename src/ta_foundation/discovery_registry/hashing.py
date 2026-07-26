"""Deterministic probe-identity extraction and hashing.

A `ProbeIdentity` is the structural fingerprint of a discovery probe: the
discovery families that are enabled, their signals + parameter ranges, the
outcome geometry, the entry-timing modes, the session filter, and the
instrument. The hash is stable across YAML formatting differences because
all collections are sorted and serialised canonically.

This is NOT a hash of the YAML file. Two YAMLs with the same probe
identity will hash identically even if they have different titles,
research notes, or whitespace.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Discovery YAML blocks the hash recognises. Order is irrelevant — keys are
# sorted at hash time.
KNOWN_DISCOVERY_BLOCKS: Tuple[str, ...] = (
    "candle_discovery",
    "ma_discovery",
    "orb_discovery",
    "bb_discovery",
    "breakout_discovery",
    "pullback_discovery",
    "level_discovery",
    "lcr_discovery",
)

# Parameter keys inside a signal block that are *not* part of the search
# grid (they configure the detector, not the hypothesis variation).
_NON_GRID_PARAM_KEYS = frozenset({
    "enabled",
    "tick_size",
    "atr_period",
    "min_atr_ticks",
    # ORB session-window scalars — fixed by session config, not hypothesis.
    "session_open_hour",
    "session_open_minute",
    "session_close_hour",
    "session_close_minute",
    "one_signal_per_side",
})

# Per-block configuration for signal extraction. Some blocks (level_discovery,
# bb_discovery, lcr_discovery) wrap signals under `signals.<sig>: {...}`.
# Other blocks (orb_discovery) embed a single signal config under a fixed key
# like `orb`, with the signal identity given by a `signal_type` field.
_BLOCK_SIGNAL_LAYOUT: Dict[str, Dict[str, Any]] = {
    "orb_discovery":     {"inner_key": "orb",     "signal_type_key": "signal_type"},
    "candle_discovery":  {"inner_key": "candles", "signal_type_key": None},
}


@dataclass(frozen=True)
class SignalSpec:
    """One enabled signal inside a discovery block."""
    block: str
    signal: str
    param_ranges: Dict[str, Tuple[Any, ...]]  # param_name → sorted tuple of values

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block": self.block,
            "signal": self.signal,
            "param_ranges": {k: list(v) for k, v in sorted(self.param_ranges.items())},
        }


@dataclass(frozen=True)
class ProbeIdentity:
    """Structural fingerprint of a probe YAML."""
    instrument: str
    signals: Tuple[SignalSpec, ...]          # sorted by (block, signal)
    timeframes: Tuple[Any, ...]              # sorted
    entry_timing: Tuple[str, ...]            # sorted, only enabled modes
    outcome_take_profit: Tuple[Any, ...]     # sorted
    outcome_stop: Tuple[Any, ...]            # sorted
    outcome_mode: str                        # "ticks" or "atr" or "mixed"
    session_filter: Optional[Dict[str, Any]] = None
    stage: Optional[str] = None              # discovery.stage (e.g. "03_levels_regions")

    def to_canonical(self) -> Dict[str, Any]:
        """Canonical dict representation used for hashing."""
        return {
            "instrument": self.instrument,
            "signals": [s.to_dict() for s in self.signals],
            "timeframes": list(self.timeframes),
            "entry_timing": list(self.entry_timing),
            "outcome_take_profit": list(self.outcome_take_profit),
            "outcome_stop": list(self.outcome_stop),
            "outcome_mode": self.outcome_mode,
            "session_filter": self.session_filter,
            "stage": self.stage,
        }


def _normalize_value(v: Any) -> Any:
    """Coerce values to a stable hashable form.

    Lists become tuples (sorted if all elements are comparable). Dicts and
    None pass through.
    """
    if isinstance(v, list):
        try:
            return tuple(sorted(v))
        except TypeError:
            # Heterogeneous list — preserve order but freeze.
            return tuple(v)
    return v


def _extract_param_ranges(signal_block: Dict[str, Any]) -> Dict[str, Tuple[Any, ...]]:
    """Pick out only parameter-grid keys from a signal block."""
    out: Dict[str, Tuple[Any, ...]] = {}
    for k, v in signal_block.items():
        if k in _NON_GRID_PARAM_KEYS:
            continue
        if isinstance(v, list):
            out[k] = _normalize_value(v)
        elif isinstance(v, (int, float, str, bool)):
            # Scalar grid value — wrap in a 1-tuple so all grid params share a shape.
            out[k] = (v,)
    return out


_SKIP_BLOCK_LEVEL_KEYS = frozenset({
    "signals", "entry_timing", "outcome", "filter_discovery",
    "entry_discovery", "session_filter", "timeframes", "min_trades",
    "atr_period", "hardening", "min_atr_ticks", "enabled", "orb",
    "candles",
})


def _signal_names_from_type_field(sig_cfg: Dict[str, Any], type_key: str) -> List[str]:
    """Extract signal-name identifiers from a `signal_type: [...]` style field.

    If the field is a list, every value contributes a distinct SignalSpec
    (since each signal_type is structurally a different hypothesis). If it's
    a single scalar, return a 1-element list. If missing, return ["_block"].
    """
    val = sig_cfg.get(type_key)
    if isinstance(val, list) and val:
        return [str(v) for v in val]
    if isinstance(val, str):
        return [val]
    return ["_block"]


def _extract_signals(raw: Dict[str, Any]) -> List[SignalSpec]:
    """Walk known discovery blocks and collect every enabled signal."""
    specs: List[SignalSpec] = []
    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = raw.get(block_name)
        if not isinstance(block, dict):
            continue
        if not block.get("enabled", False):
            continue

        signals = block.get("signals")
        if isinstance(signals, dict):
            # Pattern A: <block>.signals.<sig>.{enabled, params...}
            for sig_name, sig_cfg in signals.items():
                if not isinstance(sig_cfg, dict):
                    continue
                if not sig_cfg.get("enabled", False):
                    continue
                specs.append(SignalSpec(
                    block=block_name,
                    signal=sig_name,
                    param_ranges=_extract_param_ranges(sig_cfg),
                ))
            continue

        # Pattern B: per-block embedded config (orb_discovery.orb, etc.)
        layout = _BLOCK_SIGNAL_LAYOUT.get(block_name)
        if layout is not None:
            inner = block.get(layout["inner_key"])
            if isinstance(inner, dict):
                type_key = layout.get("signal_type_key")
                names = (
                    _signal_names_from_type_field(inner, type_key)
                    if type_key else ["_block"]
                )
                ranges = _extract_param_ranges(inner)
                for n in names:
                    specs.append(SignalSpec(
                        block=block_name,
                        signal=n,
                        param_ranges=ranges,
                    ))
                continue

        # Pattern C: grid params live directly on the block.
        param_ranges = _extract_param_ranges({
            k: v for k, v in block.items() if k not in _SKIP_BLOCK_LEVEL_KEYS
        })
        if param_ranges:
            specs.append(SignalSpec(
                block=block_name,
                signal="_block",
                param_ranges=param_ranges,
            ))

    specs.sort(key=lambda s: (s.block, s.signal))
    return specs


def _extract_outcome(raw: Dict[str, Any]) -> Tuple[Tuple[Any, ...], Tuple[Any, ...], str]:
    """Find the first enabled discovery block's outcome block.

    Outcome may live at `<block>.outcome` (most blocks) or under the inner
    key like `<block>.orb.outcome` — try both.
    """
    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = raw.get(block_name)
        if not isinstance(block, dict) or not block.get("enabled", False):
            continue
        outcome = block.get("outcome")
        if not isinstance(outcome, dict):
            layout = _BLOCK_SIGNAL_LAYOUT.get(block_name)
            if layout is not None:
                inner = block.get(layout["inner_key"])
                if isinstance(inner, dict):
                    outcome = inner.get("outcome")
        if not isinstance(outcome, dict):
            continue
        ticks = outcome.get("ticks")
        atr = outcome.get("atr")
        if isinstance(ticks, dict) and ticks.get("enabled", False):
            tp = _normalize_value(ticks.get("take_profit") or [])
            sl = _normalize_value(ticks.get("stop") or [])
            return tp, sl, "ticks"
        if isinstance(atr, dict) and atr.get("enabled", False):
            tp_val = atr.get("take_profit_mult") or atr.get("target_mult") or []
            sl_val = atr.get("stop_mult") or []
            if not isinstance(tp_val, (list, tuple, set)):
                tp_val = [tp_val]
            if not isinstance(sl_val, (list, tuple, set)):
                sl_val = [sl_val]
            tp = _normalize_value(list(tp_val))
            sl = _normalize_value(list(sl_val))
            return tp, sl, "atr"
    return (), (), "none"


def _extract_entry_timing(raw: Dict[str, Any]) -> Tuple[str, ...]:
    """Collect enabled entry-timing modes across discovery blocks."""
    modes: set = set()
    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = raw.get(block_name)
        if not isinstance(block, dict) or not block.get("enabled", False):
            continue
        et = block.get("entry_timing")
        if not isinstance(et, dict):
            layout = _BLOCK_SIGNAL_LAYOUT.get(block_name)
            if layout is not None:
                inner = block.get(layout["inner_key"])
                if isinstance(inner, dict):
                    et = inner.get("entry_timing")
        if isinstance(et, dict):
            for mode, mode_cfg in et.items():
                if isinstance(mode_cfg, dict) and mode_cfg.get("enabled", False):
                    modes.add(mode)
    return tuple(sorted(modes))


def _extract_timeframes(raw: Dict[str, Any]) -> Tuple[Any, ...]:
    """Union of timeframes across enabled discovery blocks."""
    tfs: set = set()
    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = raw.get(block_name)
        if not isinstance(block, dict) or not block.get("enabled", False):
            continue
        tf_list = block.get("timeframes")
        if isinstance(tf_list, list):
            tfs.update(tf_list)
    try:
        return tuple(sorted(tfs))
    except TypeError:
        return tuple(tfs)


def _extract_session_filter(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = raw.get(block_name)
        if isinstance(block, dict):
            sf = block.get("session_filter")
            if isinstance(sf, dict) and sf:
                return {k: sf[k] for k in sorted(sf.keys())}
    sf = raw.get("session_filter")
    if isinstance(sf, dict) and sf:
        return {k: sf[k] for k in sorted(sf.keys())}
    return None


def compute_probe_identity(raw: Dict[str, Any]) -> ProbeIdentity:
    """Build a `ProbeIdentity` from a parsed YAML dict.

    Accepts the same dict shape that `load_report_configs` produces (i.e. the
    parsed YAML root). Returns an identity even for non-probe YAMLs; in that
    case `signals` will be empty.
    """
    discovery = raw.get("discovery") or {}
    instrument = str(discovery.get("instrument") or raw.get("instrument") or "UNKNOWN")
    stage = discovery.get("stage")
    signals = tuple(_extract_signals(raw))
    timeframes = _extract_timeframes(raw)
    entry_timing = _extract_entry_timing(raw)
    tp, sl, mode = _extract_outcome(raw)
    session_filter = _extract_session_filter(raw)
    return ProbeIdentity(
        instrument=instrument,
        signals=signals,
        timeframes=timeframes,
        entry_timing=entry_timing,
        outcome_take_profit=tp,
        outcome_stop=sl,
        outcome_mode=mode,
        session_filter=session_filter,
        stage=stage,
    )


def probe_hash(identity: ProbeIdentity) -> str:
    """SHA-256 of the canonical representation. Returns the full 64-char hex."""
    payload = json.dumps(identity.to_canonical(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def jaccard_overlap(a: Tuple[Any, ...], b: Tuple[Any, ...]) -> float:
    """Set-based Jaccard similarity. Returns 1.0 if both are empty."""
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 1.0
