"""Build a runnable discovery config for a pre-registered hypothesis.

Phase 0 defect #9: `author_probe` used to emit a YAML carrying only a
`pre_registration:` block - no `orb_discovery:` / `candle_discovery:` / etc.
sweep config - so the CLI ran with nothing to sweep and produced zero
candidates. This module closes that seam.

Approach (per the Phase 0 decision): a per-family template + substitution.
Each supported family has a skeleton at `discovery/templates/<family>.yaml`
holding every fixed engine/economics constant; the builder loads it and
substitutes the family-whitelist params (single locked values, not grids -
a pre-registered hypothesis is one rule) plus the session window and
direction. The result honours pre-registration: the run executes exactly the
locked combination.

Scope: only `orb_failure_reclaim` is wired today (the family with a proven
survivor). Any other family raises `ConfigBuildError` - a loud, honest gap
rather than a silently-broken YAML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import yaml as _yaml

# discovery/templates lives at the repo root; this file is at
# src/ta_foundation/agent/tools/probe_config.py.
TEMPLATE_DIR = Path(__file__).resolve().parents[4] / "discovery" / "templates"

# orb engine direction encoding (see analysis/entry_strategies/orb_sweep.py).
_DIRECTION_CODES = {"both": 0, "long": 1, "short": -1}


class ConfigBuildError(Exception):
    """Raised when no faithful runnable config can be built for a hypothesis."""


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def has_config_builder(family: str) -> bool:
    """True when `build_probe_config` can emit a runnable config for `family`."""
    return family in _FAMILY_BUILDERS


def build_probe_config(
    *,
    hypothesis_id: str,
    family: str,
    instrument: str,
    timeframe: str,
    session_window: Optional[str],
    direction: Optional[str],
    params: dict,
) -> dict:
    """Return a full, runnable discovery config dict for a hypothesis.

    Raises ConfigBuildError when the family has no template or when a param
    cannot be faithfully expressed in the engine's vocabulary.
    """
    builder = _FAMILY_BUILDERS.get(family)
    if builder is None:
        raise ConfigBuildError(
            f"no discovery-config template for family {family!r} yet "
            f"(supported: {sorted(_FAMILY_BUILDERS)})"
        )
    cfg = builder(
        _load_template(family),
        instrument=instrument,
        session_window=session_window,
        direction=direction,
        params=params,
    )
    cfg["report"] = {
        "title": f"Pre-registered probe - {hypothesis_id}",
        "output_filename": f"{hypothesis_id}.html",
        "timezone": "America/Denver",
    }
    return cfg


# --------------------------------------------------------------------------
# orb_failure_reclaim
# --------------------------------------------------------------------------


def _substitute_orb_failure_reclaim(
    cfg: dict,
    *,
    instrument: str,
    session_window: Optional[str],
    direction: Optional[str],
    params: dict,
) -> dict:
    """Substitute the orb_failure_reclaim locked params into the template.

    Documented mapping decisions (family whitelist -> orb engine vocabulary):
      - orb_minutes / sweep_min_ticks / reclaim_within_bars / stop_ticks /
        target_ticks map directly onto single-element grids.
      - fill_mode: 'body_midpoint' -> the engine's body_midpoint entry timing;
        'reclaim_close' -> the engine's next-bar-open timing; 'range_midpoint'
        has no engine entry timing and is rejected.
      - the template is NQ tick-economics scoped; a non-NQ instrument is
        rejected rather than run with wrong tick_size/tick_value.
    """
    if instrument != "NQ":
        raise ConfigBuildError(
            f"orb_failure_reclaim template is NQ tick-economics scoped; "
            f"got instrument {instrument!r}"
        )
    direction_code = _DIRECTION_CODES.get(direction or "both")
    if direction_code is None:
        raise ConfigBuildError(
            f"unsupported direction {direction!r} (expected long/short/both)")

    orb = cfg["orb_discovery"]["orb"]
    orb["orb_minutes"] = [int(params["orb_minutes"])]
    orb["min_sweep_ticks"] = [float(params["sweep_min_ticks"])]
    orb["max_reclaim_bars"] = [int(params["reclaim_within_bars"])]
    orb["direction"] = [direction_code]
    open_h, open_m, close_h = _resolve_ny_session(session_window)
    orb["session_open_hour"] = open_h
    orb["session_open_minute"] = open_m
    orb["session_close_hour"] = close_h

    ticks = cfg["orb_discovery"]["outcome"]["ticks"]
    ticks["take_profit"] = [int(params["target_ticks"])]
    ticks["stop"] = [int(params["stop_ticks"])]

    cfg["orb_discovery"]["entry_timing"] = _orb_entry_timing(params["fill_mode"])
    cfg["discovery"]["instrument"] = instrument
    return cfg


def _orb_entry_timing(fill_mode: str) -> dict:
    base = {
        "next_open": {"enabled": False},
        "break_extreme": {"enabled": False, "buffer_ticks": 1,
                          "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": False, "fill_timeout_bars": 5},
    }
    if fill_mode == "body_midpoint":
        base["body_midpoint"]["enabled"] = True
    elif fill_mode == "reclaim_close":
        base["next_open"]["enabled"] = True
    else:
        raise ConfigBuildError(
            f"fill_mode {fill_mode!r} has no orb-engine entry_timing mapping "
            "(supported: body_midpoint, reclaim_close)"
        )
    return base


def _resolve_ny_session(session_window: Optional[str]) -> tuple[int, int, int]:
    """Parse a `*_HHMM_HHMM_*` session window into (open_h, open_m, close_h).

    `ny_open_0730_1000_denver` -> (7, 30, 10). Falls back to the NY-open
    default when the window is absent or not in the recognised format.
    """
    default = (7, 30, 10)
    if not session_window:
        return default
    quads = [t for t in str(session_window).split("_")
             if len(t) == 4 and t.isdigit()]
    if len(quads) < 2:
        return default
    open_hhmm, close_hhmm = quads[0], quads[1]
    return (int(open_hhmm[:2]), int(open_hhmm[2:]), int(close_hhmm[:2]))


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


_Builder = Callable[..., dict]

_FAMILY_BUILDERS: dict[str, _Builder] = {
    "orb_failure_reclaim": _substitute_orb_failure_reclaim,
}


def _load_template(family: str) -> dict:
    path = TEMPLATE_DIR / f"{family}.yaml"
    if not path.is_file():
        raise ConfigBuildError(f"template file not found: {path}")
    return _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
