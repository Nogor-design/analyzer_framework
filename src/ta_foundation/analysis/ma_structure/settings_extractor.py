from __future__ import annotations

"""
settings_extractor.py
---------------------
Reads pkg.settings (a DataFrame with columns: section, item, value) and
returns a list[AnchorSpec] for use by the MA Anchor interaction engine.

Each strategy family has its own registered extractor function.  The
orchestrator calls extract_anchors_from_settings() with the optional
strategy_family name from report.yaml.

Resolution order:
  1. Exact match on strategy_family (case-insensitive, strips spaces/underscores).
  2. Partial match on strategy_family (if specified).
  3. Default: Pantheon extractor — reads averageFast/averageSlow/averageTrend from
     any Pantheon-family _Settings.csv. Falls through to keyword scan if those
     section/item names are not found.

Adding a new strategy
---------------------
1. Write a function that accepts a settings DataFrame and returns list[AnchorSpec].
2. Decorate it with @register_extractor("YourStrategyName").
   The key is compared case-insensitively and with spaces/underscores stripped.
"""

from typing import Callable, Optional

import pandas as pd

from .models import AnchorSpec

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_EXTRACTORS: dict[str, Callable[[pd.DataFrame], list[AnchorSpec]]] = {}


def register_extractor(family: str) -> Callable:
    """Decorator that registers an extractor function for a strategy family."""
    def decorator(fn: Callable) -> Callable:
        _EXTRACTORS[_norm_key(family)] = fn
        return fn
    return decorator


def _norm_key(s: str) -> str:
    return str(s or "").strip().lower().replace(" ", "").replace("_", "")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_anchors_from_settings(
    settings_df: Optional[pd.DataFrame],
    strategy_family: str = "",
) -> list[AnchorSpec]:
    """
    Read pkg.settings and return a list of AnchorSpec objects.

    Returns an empty list on any failure — the orchestrator handles the
    fallback to YAML-configured anchors.
    """
    if settings_df is None or not isinstance(settings_df, pd.DataFrame) or settings_df.empty:
        return []

    key = _norm_key(strategy_family)
    fn = _EXTRACTORS.get(key)

    if fn is None and key:
        # try partial match only when a family was actually specified
        for registered_key, registered_fn in _EXTRACTORS.items():
            if key in registered_key or registered_key in key:
                fn = registered_fn
                break

    if fn is None:
        # Default: use the Pantheon extractor which reads averageFast/averageSlow/averageTrend.
        # This works for all Pantheon templates regardless of template name.
        fn = _extract_pantheon_v01_tester_v2

    try:
        result = fn(settings_df)
        return result if result else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm_col(s: object) -> str:
    return str(s or "").strip().lower().replace(" ", "").replace("_", "")


def _lookup_int(
    settings_df: pd.DataFrame,
    section_patterns: list[str],
    item_patterns: list[str],
) -> Optional[int]:
    """
    Find an integer value by matching section and item with tolerant name
    normalization (case-insensitive, whitespace/underscore-stripped).
    """
    df = settings_df.copy()
    df["_snorm"] = df["section"].apply(_norm_col)
    df["_inorm"] = df["item"].apply(_norm_col)

    s_norm = [_norm_col(s) for s in section_patterns]
    i_norm = [_norm_col(i) for i in item_patterns]

    for sn in s_norm:
        for in_ in i_norm:
            match = df[(df["_snorm"] == sn) & (df["_inorm"] == in_)]
            if not match.empty:
                try:
                    return int(float(str(match.iloc[0]["value"]).strip()))
                except (ValueError, TypeError):
                    continue
    return None


def _lookup_bool(
    settings_df: pd.DataFrame,
    section_patterns: list[str],
    item_patterns: list[str],
    default: bool = True,
) -> bool:
    df = settings_df.copy()
    df["_snorm"] = df["section"].apply(_norm_col)
    df["_inorm"] = df["item"].apply(_norm_col)

    s_norm = [_norm_col(s) for s in section_patterns]
    i_norm = [_norm_col(i) for i in item_patterns]

    for sn in s_norm:
        for in_ in i_norm:
            match = df[(df["_snorm"] == sn) & (df["_inorm"] == in_)]
            if not match.empty:
                val = str(match.iloc[0]["value"]).strip().lower()
                if val in ("true", "1", "yes"):
                    return True
                if val in ("false", "0", "no"):
                    return False
    return default


# ---------------------------------------------------------------------------
# PantheonMasterBotV01TesterV2 extractor
# ---------------------------------------------------------------------------

@register_extractor("PantheonMasterBotV01TesterV2")
def _extract_pantheon_v01_tester_v2(settings_df: pd.DataFrame) -> list[AnchorSpec]:
    """
    Extracts Fast SMA, Slow SMA, and (optionally) Trend SMA from settings.

    NinjaTrader exports C# property names under their [Gui.CategoryOrder]
    section names.  Property names for this strategy:
      - averageFast  → section "Fast Averages"
      - averageSlow  → section "Slow Averages"
      - averageTrend → section "Trend Averages"  (only if UseTrend = True)
      - UseTrend     → section "Direction" or "Trend Averages"

    We try multiple item name forms to handle NT export variations.
    """
    fast_length = _lookup_int(
        settings_df,
        section_patterns=["Fast Averages", "Fast Average", "FastAverages"],
        item_patterns=["Average Fast", "averageFast", "Fast", "Fast Period", "FastPeriod"],
    )
    slow_length = _lookup_int(
        settings_df,
        section_patterns=["Slow Averages", "Slow Average", "SlowAverages"],
        item_patterns=["Average Slow", "averageSlow", "Slow", "Slow Period", "SlowPeriod"],
    )
    trend_length = _lookup_int(
        settings_df,
        section_patterns=["Trend Averages", "Trend Average", "TrendAverages"],
        item_patterns=["Average Trend", "averageTrend", "Trend", "Trend Period", "TrendPeriod"],
    )
    use_trend = _lookup_bool(
        settings_df,
        section_patterns=["Direction", "Trend Averages", "TrendAverages"],
        item_patterns=["Use Trend", "UseTrend", "use trend"],
        default=True,
    )

    specs: list[AnchorSpec] = []
    if fast_length is not None and fast_length > 0:
        specs.append(AnchorSpec(family="SMA", length=fast_length, source="close"))
    if slow_length is not None and slow_length > 0:
        specs.append(AnchorSpec(family="SMA", length=slow_length, source="close"))
    if use_trend and trend_length is not None and trend_length > 0:
        specs.append(AnchorSpec(family="SMA", length=trend_length, source="close"))

    # If section-based lookup found nothing, fall through to keyword scan.
    # This handles NT exports that use raw C# property names (averageFast, etc.)
    # rather than [Gui.Category] section names.
    if not specs:
        return _extract_generic_ma_anchors(settings_df)

    return specs


# Register under common aliases
@register_extractor("PantheonBotV2")
def _extract_pantheon_v2(settings_df: pd.DataFrame) -> list[AnchorSpec]:
    return _extract_pantheon_v01_tester_v2(settings_df)


@register_extractor("PantheonMasterBot")
def _extract_pantheon_master(settings_df: pd.DataFrame) -> list[AnchorSpec]:
    return _extract_pantheon_v01_tester_v2(settings_df)


# ---------------------------------------------------------------------------
# Generic fallback extractor
# ---------------------------------------------------------------------------

def _extract_generic_ma_anchors(settings_df: pd.DataFrame) -> list[AnchorSpec]:
    """
    Generic fallback: scan all items for names containing 'fast', 'slow',
    or 'trend' with numeric values.  Used when no specific extractor matches.
    """
    df = settings_df.copy()
    df["_inorm"] = df["item"].apply(_norm_col)

    seen_lengths: set[int] = set()
    specs: list[AnchorSpec] = []

    for keyword in ("fast", "slow", "trend"):
        matches = df[df["_inorm"].str.contains(keyword, na=False)]
        for _, row in matches.iterrows():
            try:
                val = int(float(str(row["value"]).strip()))
                if val > 0 and val not in seen_lengths:
                    seen_lengths.add(val)
                    specs.append(AnchorSpec(family="SMA", length=val, source="close"))
            except (ValueError, TypeError):
                continue

    return specs
