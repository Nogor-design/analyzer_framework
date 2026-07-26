from __future__ import annotations

"""
Stage and family definitions for the Discovery web UI.

Two registries:

- STAGE_DEFINITIONS: the six funnel stages (01_quick_scan through 06_validate)
  plus the Large Candle Excursion event study sibling. Each stage carries
  beginner-facing metadata (label, blurb, runtime, sticky help) and loads its
  standard-preset YAML directly from the existing discovery/*.yaml file at
  import time. The hand-written YAMLs remain the single source of truth for
  the standard preset values; this module just wraps them with display
  metadata and family wiring.

- FAMILY_DEFINITIONS: the eight signal families (candle, ma, orb, bb, lcr,
  breakout, pullback, level) plus the LCE event study. Family definitions are
  static — the same family schema is reused across stages.

Loading the YAML at import time means future edits to discovery/*.yaml
automatically flow into the web UI as the new standard preset, and the
YAML builder (Step 4) can snapshot-test against them for parity.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

StageKind = Literal["funnel", "event_study"]


# ---------------------------------------------------------------------------
# Discovery folder lookup
# ---------------------------------------------------------------------------

def _default_discovery_dir() -> Path:
    """Locate the repo's discovery/ folder relative to this module."""
    # src/ta_foundation/web/discovery_stages.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3] / "discovery"


# ---------------------------------------------------------------------------
# Family definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyDefinition:
    id: str
    yaml_block: str            # the top-level key in the YAML, e.g. "candle_discovery"
    label: str
    glossary_term: str         # term id in discovery_glossary.yaml
    one_liner: str
    sub_signals: tuple[dict[str, str], ...] = ()
    # sub_signals: list of {"id": "large_body", "label": "Large Body Bar",
    #                       "glossary_term": "large_body"}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sub_signals"] = list(self.sub_signals)
        return d


_CANDLE_PATTERNS: tuple[dict[str, str], ...] = (
    {"id": "large_body",         "label": "Large Body Bar",       "glossary_term": "large_body"},
    {"id": "clean_breakout_bar", "label": "Clean Breakout Bar",    "glossary_term": "clean_breakout_bar"},
    {"id": "pin_bar_bullish",    "label": "Bullish Pin Bar",       "glossary_term": "pin_bar"},
    {"id": "pin_bar_bearish",    "label": "Bearish Pin Bar",       "glossary_term": "pin_bar"},
    {"id": "engulfing_bullish",  "label": "Bullish Engulfing",     "glossary_term": "engulfing"},
    {"id": "engulfing_bearish",  "label": "Bearish Engulfing",     "glossary_term": "engulfing"},
    {"id": "inside_bar",         "label": "Inside Bar",            "glossary_term": "inside_bar"},
    {"id": "outside_bar",        "label": "Outside Bar",           "glossary_term": "outside_bar"},
    {"id": "doji",               "label": "Doji",                  "glossary_term": "doji"},
)

_BB_SIGNALS: tuple[dict[str, str], ...] = (
    {"id": "bb_mean_reversion",   "label": "Bollinger Mean Reversion", "glossary_term": "bb_family"},
    {"id": "bb_continuation",     "label": "Bollinger Continuation",   "glossary_term": "bb_family"},
    {"id": "bb_squeeze_breakout", "label": "Bollinger Squeeze Breakout", "glossary_term": "bb_family"},
    {"id": "bb_walk",             "label": "Bollinger Band Walk",      "glossary_term": "bb_family"},
)

_MA_SIGNALS: tuple[dict[str, str], ...] = (
    {"id": "ma_cross",    "label": "MA Crossover", "glossary_term": "ma_family"},
    {"id": "ma_pullback", "label": "MA Pullback",  "glossary_term": "ma_family"},
)

_BREAKOUT_SIGNALS: tuple[dict[str, str], ...] = (
    {"id": "n_bar_breakout",   "label": "N-Bar Breakout",   "glossary_term": "breakout_family"},
    {"id": "volatility_break", "label": "Volatility Break", "glossary_term": "breakout_family"},
)

_PULLBACK_SIGNALS: tuple[dict[str, str], ...] = (
    {"id": "trend_pullback",    "label": "Trend Pullback",    "glossary_term": "pullback_family"},
    {"id": "retracement_entry", "label": "Retracement Entry", "glossary_term": "pullback_family"},
)

_LEVEL_SIGNALS: tuple[dict[str, str], ...] = (
    {"id": "swing_level_test",        "label": "Swing Level",            "glossary_term": "level_family"},
    {"id": "consolidation_breakout",  "label": "Consolidation Breakout", "glossary_term": "level_family"},
    {"id": "round_number_bounce",     "label": "Round Number",           "glossary_term": "level_family"},
    {"id": "vwap_reclaim_reject",     "label": "VWAP Reclaim / Reject",  "glossary_term": "level_family"},
    {"id": "prior_session_reaction",  "label": "Prior / Overnight Level", "glossary_term": "level_family"},
    {"id": "liquidity_sweep_failure", "label": "Liquidity Sweep Failure", "glossary_term": "level_family"},
)

_LCR_SIGNAL_TYPES: tuple[dict[str, str], ...] = (
    {"id": "fresh",   "label": "Fresh",   "glossary_term": "signal_type_fresh"},
    {"id": "touch",   "label": "Touch",   "glossary_term": "signal_type_touch"},
    {"id": "break",   "label": "Break",   "glossary_term": "signal_type_break"},
    {"id": "retrace", "label": "Retrace", "glossary_term": "signal_type_retrace"},
)


_FAMILIES: tuple[FamilyDefinition, ...] = (
    FamilyDefinition(
        id="candle",
        yaml_block="candle_discovery",
        label="Candle Patterns",
        glossary_term="candle_family",
        one_liner="Strategies built around single-bar or multi-bar candle shapes.",
        sub_signals=_CANDLE_PATTERNS,
    ),
    FamilyDefinition(
        id="ma",
        yaml_block="ma_discovery",
        label="Moving Average",
        glossary_term="ma_family",
        one_liner="Trend signals built on moving averages — crossovers and pullbacks.",
        sub_signals=_MA_SIGNALS,
    ),
    FamilyDefinition(
        id="orb",
        yaml_block="orb_discovery",
        label="Opening Range Breakout",
        glossary_term="orb_family",
        one_liner="Trade the break of the first N minutes' high/low after the bell.",
    ),
    FamilyDefinition(
        id="bb",
        yaml_block="bb_discovery",
        label="Bollinger Bands",
        glossary_term="bb_family",
        one_liner="Squeeze, mean-reversion, and breakout setups around the band.",
        sub_signals=_BB_SIGNALS,
    ),
    FamilyDefinition(
        id="lcr",
        yaml_block="lcr_discovery",
        label="Large Candle Region (LCR)",
        glossary_term="lcr_family",
        one_liner="Treat the range of an unusually large bar as a magnetic zone.",
        sub_signals=_LCR_SIGNAL_TYPES,
    ),
    FamilyDefinition(
        id="breakout",
        yaml_block="breakout_discovery",
        label="Breakout",
        glossary_term="breakout_family",
        one_liner="Buy at new N-bar highs (or sell at new N-bar lows).",
        sub_signals=_BREAKOUT_SIGNALS,
    ),
    FamilyDefinition(
        id="pullback",
        yaml_block="pullback_discovery",
        label="Pullback",
        glossary_term="pullback_family",
        one_liner="Wait for a counter-trend pause inside a trend, then re-enter.",
        sub_signals=_PULLBACK_SIGNALS,
    ),
    FamilyDefinition(
        id="level",
        yaml_block="level_discovery",
        label="Levels",
        glossary_term="level_family",
        one_liner="Trade reactions at swing highs/lows, ranges, and round numbers.",
        sub_signals=_LEVEL_SIGNALS,
    ),
    FamilyDefinition(
        id="large_candle_excursion",
        yaml_block="large_candle_excursion",
        label="Large Candle Excursion",
        glossary_term="lcr_family",
        one_liner="Event study: how far does price travel after an oversized bar?",
    ),
)


_FAMILIES_BY_ID: dict[str, FamilyDefinition] = {fam.id: fam for fam in _FAMILIES}


def list_families() -> list[dict[str, Any]]:
    return [fam.to_dict() for fam in _FAMILIES]


def get_family(family_id: str) -> FamilyDefinition | None:
    if not family_id:
        return None
    return _FAMILIES_BY_ID.get(str(family_id).strip())


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageDefinition:
    id: str
    ordinal: int                      # 1..6 for funnel; 0 for event studies
    kind: StageKind
    label: str
    short_label: str
    one_liner: str
    long_blurb: str
    what_to_look_at: str
    runtime_estimate: str             # human-readable, e.g. "~3-5 min"
    runtime_seconds_estimate: int     # midpoint, used by combo estimator math
    source_yaml_filename: str         # relative to the discovery folder
    enabled_families: tuple[str, ...]
    sticky_help: tuple[str, ...]
    next_stage_recommendations: tuple[str, ...]
    depends_on_promotions: bool       # Stage 6 only
    default_yaml: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_default_yaml: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "label": self.label,
            "short_label": self.short_label,
            "one_liner": self.one_liner,
            "long_blurb": self.long_blurb,
            "what_to_look_at": self.what_to_look_at,
            "runtime_estimate": self.runtime_estimate,
            "runtime_seconds_estimate": self.runtime_seconds_estimate,
            "source_yaml_filename": self.source_yaml_filename,
            "enabled_families": list(self.enabled_families),
            "sticky_help": list(self.sticky_help),
            "next_stage_recommendations": list(self.next_stage_recommendations),
            "depends_on_promotions": self.depends_on_promotions,
            "sections": _extract_sections(self.default_yaml),
        }
        if include_default_yaml:
            d["default_yaml"] = self.default_yaml
        return d


def _extract_sections(yaml_dict: dict[str, Any]) -> list[dict[str, Any]]:
    sections = yaml_dict.get("sections") if isinstance(yaml_dict, dict) else None
    if not isinstance(sections, list):
        return []
    return [dict(s) if isinstance(s, dict) else {"id": str(s)} for s in sections]


_STAGE_METADATA: tuple[dict[str, Any], ...] = (
    {
        "id": "01_quick_scan",
        "ordinal": 1,
        "kind": "funnel",
        "label": "Quick Scan",
        "short_label": "Scan",
        "one_liner": "Find out which signal families have any edge on this market.",
        "long_blurb": (
            "The widest, cheapest sweep in the funnel. We test one parameter "
            "set per family across 250-ish combinations. The output is a "
            "single cross-family ranking table — your map of where edge lives."
            "\n\n"
            "If nothing scores above PF 1.2, stop here. The instrument or "
            "date range doesn't have edge worth chasing. Try a different "
            "instrument or a longer date range before moving on."
        ),
        "what_to_look_at": (
            "The unified ranking table. Note which family ranked #1 and what "
            "timeframe it preferred. That family becomes your Stage 2 target."
        ),
        "runtime_estimate": "~3-5 min",
        "runtime_seconds_estimate": 240,
        "source_yaml_filename": "01_quick_scan.yaml",
        "enabled_families": ("candle", "ma", "orb", "bb", "lcr", "breakout", "pullback", "level"),
        "sticky_help": (
            "Run this first. Every other stage depends on knowing which family has edge.",
            "Keep --no-tick-data on. Discovery never needs tick data.",
            "If no family beats PF 1.2, the right move is to pick a different instrument or a longer history.",
        ),
        "next_stage_recommendations": (
            "02_candle_patterns",
            "03_levels_regions",
            "04_ny_open",
            "05_orb_momentum",
        ),
        "depends_on_promotions": False,
    },
    {
        "id": "02_candle_patterns",
        "ordinal": 2,
        "kind": "funnel",
        "label": "Candle Pattern Deep Dive",
        "short_label": "Candle",
        "one_liner": "Find the exact candle pattern + TP/SL that wins on this market.",
        "long_blurb": (
            "Sweeps all nine candle patterns with the full parameter grid, "
            "two entry types, and a wider TP/SL grid across 1m and 5m. "
            "Output is a 5-tier ranking — Most Robust, High Quality, Solid, "
            "Marginal, and rest. Anything in the top two tiers is a real candidate."
        ),
        "what_to_look_at": (
            "The 5-tier ranking. Focus on Most Robust first — those are the "
            "setups whose IS/OOS degradation is below 10%. Note the exact "
            "pattern, body multiplier, TP, SL, and timeframe — those become "
            "your trade rules."
        ),
        "runtime_estimate": "~8-12 min",
        "runtime_seconds_estimate": 600,
        "source_yaml_filename": "02_candle_patterns.yaml",
        "enabled_families": ("candle",),
        "sticky_help": (
            "Run this only if Stage 1 ranked candle in the top 3.",
            "If nothing reaches the High Quality tier, the candle family isn't your edge — try Stage 3 or 5 instead.",
            "The pattern + TP + SL combination matters as a unit. Don't mix and match across rows.",
        ),
        "next_stage_recommendations": ("06_validate",),
        "depends_on_promotions": False,
    },
    {
        "id": "03_levels_regions",
        "ordinal": 3,
        "kind": "funnel",
        "label": "Zones & Levels",
        "short_label": "Zones",
        "one_liner": "Test whether large-candle regions and structural levels produce edge.",
        "long_blurb": (
            "Three families: LCR (large candle regions), breakout, and level. "
            "LCR signals are evaluated four ways — fresh, touch, break, "
            "retrace — and retrace is usually the highest-PF subtype. "
            "Levels and breakouts test 1m and 5m simultaneously."
        ),
        "what_to_look_at": (
            "LCR overview first. A retrace rate above 80% means the zone is "
            "a strong magnet — trade the retrace. Below 60% means zones break "
            "cleanly — trade the continuation."
        ),
        "runtime_estimate": "~5-8 min",
        "runtime_seconds_estimate": 420,
        "source_yaml_filename": "03_levels_regions.yaml",
        "enabled_families": ("lcr", "breakout", "level"),
        "sticky_help": (
            "Run this only if Stage 1 ranked LCR, breakout, or level in the top 3.",
            "LCR retrace usually wins. If your top result is anything else, look twice before trusting it.",
            "Round-number levels work better on lower-tick instruments (CL, GC) than equity-index futures.",
        ),
        "next_stage_recommendations": ("06_validate",),
        "depends_on_promotions": False,
    },
    {
        "id": "04_ny_open",
        "ordinal": 4,
        "kind": "funnel",
        "label": "NY Open Scalp",
        "short_label": "NY Open",
        "one_liner": "Does the last premarket bar predict the first NY open move?",
        "long_blurb": (
            "Filtered to 06:00–10:00 Denver — premarket plus the first hour "
            "of the NY session. Tests five candle patterns specifically "
            "relevant to the open: large body, clean breakout bar, pin bars, "
            "inside bar, and engulfing. Plus an ORB sweep for comparison."
            "\n\n"
            "TP/SL ranges are scalp-sized — 10-20 ticks — and the timeout "
            "is 10 bars instead of 15."
        ),
        "what_to_look_at": (
            "Which pattern wins on next-open entry? Check whether the win "
            "comes mostly from one direction (long-only or short-only) — "
            "if so, that's your daily-bias rule."
        ),
        "runtime_estimate": "~3-5 min",
        "runtime_seconds_estimate": 240,
        "source_yaml_filename": "04_ny_open.yaml",
        "enabled_families": ("candle", "orb"),
        "sticky_help": (
            "Run this independently — it tests a different thesis than the broad scan.",
            "Premarket bars are thin; min_trades is set to 15 to keep enough samples.",
            "In winter (MST) the NY bell is at 08:30 Denver — adjust session_open_minute.",
        ),
        "next_stage_recommendations": ("06_validate",),
        "depends_on_promotions": False,
    },
    {
        "id": "05_orb_momentum",
        "ordinal": 5,
        "kind": "funnel",
        "label": "ORB & Momentum",
        "short_label": "Momentum",
        "one_liner": "Compare opening range, Bollinger, MA, and pullback signals head-to-head.",
        "long_blurb": (
            "Four families compared on equal footing: ORB (4 window sizes), "
            "BB (3 signals × 2 periods × 2 std-devs), MA (cross + pullback "
            "across multiple periods), and pullback (trend continuation, "
            "retracement entry). Both 1m and 5m timeframes."
        ),
        "what_to_look_at": (
            "The unified ranking, then the family-specific overviews. ORB "
            "windows tell you the right opening-range size; MA fast/slow "
            "ratios tell you the right trend-strength threshold."
        ),
        "runtime_estimate": "~4-6 min",
        "runtime_seconds_estimate": 300,
        "source_yaml_filename": "05_orb_momentum.yaml",
        "enabled_families": ("orb", "bb", "ma", "pullback"),
        "sticky_help": (
            "Run this only if Stage 1 ranked ORB, BB, MA, or pullback in the top 3.",
            "Pullback usually has the highest win rate of these four; ORB usually has the highest expectancy.",
            "If two families both look strong, run them separately in Stage 6 — don't combine.",
        ),
        "next_stage_recommendations": ("06_validate",),
        "depends_on_promotions": False,
    },
    {
        "id": "06_validate",
        "ordinal": 6,
        "kind": "funnel",
        "label": "Validate",
        "short_label": "Validate",
        "one_liner": "Walk-forward validation on your top combos from earlier stages.",
        "long_blurb": (
            "The final gate. We re-run only the combos you promoted from "
            "stages 2-5, with min_trades raised to 30 for a stricter sample. "
            "The walk-forward report shows IS/OOS degradation and month-by-"
            "month consistency."
            "\n\n"
            "Trade nothing that hasn't passed Stage 6. A degradation under "
            "0.15 means the edge is real; over 0.40 means it was overfit."
        ),
        "what_to_look_at": (
            "The Walk-Forward Validation table — degradation column. The "
            "Cohort Analysis below shows whether PF stays consistent month "
            "to month or decays."
        ),
        "runtime_estimate": "~2-3 min",
        "runtime_seconds_estimate": 150,
        "source_yaml_filename": "06_validate.yaml",
        "enabled_families": ("candle", "ma", "orb", "bb", "lcr", "breakout", "pullback", "level"),
        "sticky_help": (
            "Promote at least one combo from earlier stages first. Stage 6 won't run on empty inputs.",
            "Aim for IS/OOS degradation below 0.15. Above 0.40 the setup is overfit.",
            "Even a strong Stage 6 verdict is not a green light — paper trade for a week before going live.",
        ),
        "next_stage_recommendations": (),
        "depends_on_promotions": True,
    },
    {
        "id": "large_candle_excursion",
        "ordinal": 0,
        "kind": "event_study",
        "label": "Large Candle Excursion",
        "short_label": "LCE",
        "one_liner": "How far does price travel after an unusually large candle?",
        "long_blurb": (
            "An event study, not a sweep. We tag every bar that's at least "
            "N× the rolling-average size, then measure favorable and "
            "adverse excursion over the next 5 / 10 / 15 / 30 minutes. "
            "Output is percentile tables (P10/P25/P50/P75/P90) and threshold "
            "hit rates that you use to calibrate stops and targets."
            "\n\n"
            "Best for: stop/target sizing, building intuition about how a "
            "given instrument behaves after impulse moves."
        ),
        "what_to_look_at": (
            "Setups where mean_fav >> mean_adv and Fav Dominant% ≥ 55%. "
            "Use percentile tables to set initial TP. The threshold-hit "
            "table tells you what % of events reached 5t / 10t / 20t etc."
        ),
        "runtime_estimate": "~2-5 min",
        "runtime_seconds_estimate": 210,
        "source_yaml_filename": "large_candle_excursion.yaml",
        "enabled_families": ("large_candle_excursion",),
        "sticky_help": (
            "This is independent of the funnel. Run it any time you want stop/target intuition.",
            "Look at multiple thresholds (1.5×, 2.0×, 2.5×) — each corresponds to a different rarity of setup.",
            "Tick analysis is off by default. Turn on only if you have tick data files in your input folder.",
        ),
        "next_stage_recommendations": (),
        "depends_on_promotions": False,
    },
)


def _build_stage_definitions(discovery_dir: Path) -> dict[str, StageDefinition]:
    out: dict[str, StageDefinition] = {}
    for entry in _STAGE_METADATA:
        path = discovery_dir / entry["source_yaml_filename"]
        default_yaml: dict[str, Any] = {}
        if path.exists():
            with open(path, encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
            if isinstance(loaded, dict):
                default_yaml = loaded
        out[entry["id"]] = StageDefinition(
            id=entry["id"],
            ordinal=entry["ordinal"],
            kind=entry["kind"],
            label=entry["label"],
            short_label=entry["short_label"],
            one_liner=entry["one_liner"],
            long_blurb=entry["long_blurb"],
            what_to_look_at=entry["what_to_look_at"],
            runtime_estimate=entry["runtime_estimate"],
            runtime_seconds_estimate=entry["runtime_seconds_estimate"],
            source_yaml_filename=entry["source_yaml_filename"],
            enabled_families=tuple(entry["enabled_families"]),
            sticky_help=tuple(entry["sticky_help"]),
            next_stage_recommendations=tuple(entry["next_stage_recommendations"]),
            depends_on_promotions=entry["depends_on_promotions"],
            default_yaml=default_yaml,
        )
    return out


# Cache at import — the YAMLs and metadata don't change at runtime.
_CACHE: dict[str, StageDefinition] | None = None


def _ensure_cache() -> dict[str, StageDefinition]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _build_stage_definitions(_default_discovery_dir())
    return _CACHE


def reload_stages(discovery_dir: Path | str | None = None) -> dict[str, StageDefinition]:
    """Force a reload from disk. Mostly for tests with a custom discovery dir."""
    global _CACHE
    target = Path(discovery_dir) if discovery_dir else _default_discovery_dir()
    _CACHE = _build_stage_definitions(target)
    return _CACHE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_stages() -> list[dict[str, Any]]:
    """All stages: funnel stages ordered 1..6, then event studies. No default_yaml."""
    cache = _ensure_cache()
    funnel = sorted(
        (s for s in cache.values() if s.kind == "funnel"),
        key=lambda s: s.ordinal,
    )
    events = sorted(
        (s for s in cache.values() if s.kind == "event_study"),
        key=lambda s: s.id,
    )
    return [s.to_dict() for s in funnel] + [s.to_dict() for s in events]


def list_funnel_stages() -> list[dict[str, Any]]:
    cache = _ensure_cache()
    funnel = sorted(
        (s for s in cache.values() if s.kind == "funnel"),
        key=lambda s: s.ordinal,
    )
    return [s.to_dict() for s in funnel]


def list_event_studies() -> list[dict[str, Any]]:
    cache = _ensure_cache()
    events = sorted(
        (s for s in cache.values() if s.kind == "event_study"),
        key=lambda s: s.id,
    )
    return [s.to_dict() for s in events]


def get_stage(stage_id: str, *, include_default_yaml: bool = False) -> dict[str, Any] | None:
    cache = _ensure_cache()
    stage = cache.get(str(stage_id or "").strip())
    if stage is None:
        return None
    return stage.to_dict(include_default_yaml=include_default_yaml)


def get_stage_definition(stage_id: str) -> StageDefinition | None:
    """Internal accessor returning the dataclass (with default_yaml). Used by the YAML builder."""
    cache = _ensure_cache()
    return cache.get(str(stage_id or "").strip())
