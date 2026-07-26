from __future__ import annotations

"""PantheonBotV2 discovery output → NinjaTrader Strategy Analyzer template XML.

This is the "promote-to-template" step: it takes a discovery payload (or an
explicit settings dict) and emits a `StrategyTemplate` XML the operator can
drop into::

    C:\\Users\\Owner\\Documents\\NinjaTrader 8\\templates\\Strategy\\

and load into Strategy Analyzer.

Seed-based design
-----------------
NT8 strategy-template XML carries machine-specific metadata — most importantly
the dynamic-assembly hash baked into every enum's `ParameterTypeSerializable`
attribute. That hash comes from the user's local NinjaScript compile, so the
template is **never synthesised from scratch**. Instead the generator loads a
seed template the operator saved once from NinjaTrader (Strategy Analyzer →
Save As… Template), rewrites only the parameter values, and preserves
everything else byte-for-byte.

A canonical seed lives at
``src/ta_foundation/strategies/PantheonBotV2/templates/sampleTemplate.xml``.

Translation
-----------
`param_map.PANTHEON_BOT_V2_PARAMS` is the single source of truth for how each
`[NinjaScriptProperty]` maps to a ta_foundation analysis concept. This module
walks that registry — it does not hard-code property names — so adding a new
mapped parameter only requires a registry entry.

Two analysis keys are shared by more than one NT property and are therefore
*ambiguous* for the auto-mapper:

- ``ma_discovery.signals.ma_cross.period`` → ``averageFast`` + ``averageSlow``
- ``market_regime.vol_regime`` → ``RequiredVolatilityRegimeFilter`` +
  ``BlockedVolatilityRegimeFilter`` (requiring and blocking the same regime
  would take zero trades)

`settings_from_discovery` deliberately skips ambiguous keys and reports them in
the manifest; supply those properties through ``baseline_overrides`` instead.
"""

import re
from pathlib import Path
from typing import Any

from ta_foundation.analysis.strategies.pantheon_bot_v2.param_map import (
    PANTHEON_BOT_V2_PARAMS,
    ParamMapping,
    analysis_value_to_nt_enum,
    get_param,
)


# ---------------------------------------------------------------------------
# Value coercion — python value → canonical NT string
# ---------------------------------------------------------------------------

_TRUE_TOKENS = {"true", "1", "yes", "y", "on"}
_FALSE_TOKENS = {"false", "0", "no", "n", "off"}


def _float_str(value: Any) -> str:
    """Format a float the way NT serialises it: no trailing zeros, ints bare."""
    f = float(value)
    if f == int(f):
        return str(int(f))
    return f"{f:.6f}".rstrip("0").rstrip(".") or "0"


def _coerce_value(param: ParamMapping, value: Any) -> str:
    """Normalise an incoming value to the canonical NT string for `param`.

    - bool  → "true" / "false" (lowercase)
    - int   → integer string
    - float → trimmed decimal string
    - enum  → NT enum value string (accepts either the NT value or the
      ta_foundation analysis value, e.g. "Up" or "up")
    """
    nt_type = param.nt_type
    if nt_type == "bool":
        if isinstance(value, str):
            token = value.strip().lower()
            if token in _TRUE_TOKENS:
                return "true"
            if token in _FALSE_TOKENS:
                return "false"
            raise ValueError(f"{param.nt_property}: {value!r} is not a boolean")
        return "true" if value else "false"
    if nt_type == "int":
        return str(int(round(float(value))))
    if nt_type == "float":
        return _float_str(value)

    # enum
    token = str(value).strip()
    valid_nt = {em.nt_enum_value for em in param.enum_values}
    if token in valid_nt:
        return token
    for candidate in (token, token.lower()):
        try:
            return analysis_value_to_nt_enum(param.nt_property, candidate)
        except KeyError:
            continue
    raise ValueError(
        f"{param.nt_property}: {value!r} is not a valid NT enum value "
        f"({sorted(valid_nt)}) or analysis value "
        f"({[em.analysis_value for em in param.enum_values]})"
    )


# ---------------------------------------------------------------------------
# Discovery payload → settings
# ---------------------------------------------------------------------------

def _dig(payload: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted path inside a nested dict; return a sentinel if absent."""
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


_MISSING = object()


def _ambiguous_analysis_keys() -> set[str]:
    """Analysis keys claimed by more than one NT property."""
    seen: dict[str, int] = {}
    for param in PANTHEON_BOT_V2_PARAMS:
        if param.analysis_key:
            seen[param.analysis_key] = seen.get(param.analysis_key, 0) + 1
    return {key for key, count in seen.items() if count > 1}


def settings_from_discovery(
    payload: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Translate a discovery payload into a `{nt_property: canonical_value}` dict.

    Walks every registry entry that declares an unambiguous ``analysis_key``,
    resolves the dotted path in `payload`, and coerces the value. `overrides`
    (keyed by NT property name) are applied last and win over auto-mapped
    values — use them for the ambiguous keys.

    Returns ``(settings, ambiguous_skipped)`` where ``ambiguous_skipped`` lists
    the NT properties that were not auto-mapped because their analysis key is
    shared.
    """
    ambiguous = _ambiguous_analysis_keys()
    settings: dict[str, str] = {}
    skipped: list[str] = []

    for param in PANTHEON_BOT_V2_PARAMS:
        if not param.analysis_key:
            continue
        if param.analysis_key in ambiguous:
            skipped.append(param.nt_property)
            continue
        found = _dig(payload, param.analysis_key)
        if found is _MISSING or found is None:
            continue
        settings[param.nt_property] = _coerce_value(param, found)

    for nt_property, value in (overrides or {}).items():
        settings[nt_property] = _coerce_value(get_param(nt_property), value)

    return settings, sorted(set(skipped))


# ---------------------------------------------------------------------------
# Seed XML patching — regex on raw text (see generate_nt8_template.py for why
# ElementTree cannot round-trip NT's namespace declarations).
# ---------------------------------------------------------------------------

def _serializable_value(param: ParamMapping, canonical: str) -> str:
    """Value for <ValueSerializable>: booleans are PascalCase there."""
    if param.nt_type == "bool":
        return "True" if canonical == "true" else "False"
    return canonical


def _sub_once(pattern: str, replacement: str, text: str) -> tuple[str, int]:
    """re.subn with a literal (non-backreference) replacement, count=1."""
    return re.subn(pattern, lambda m: m.group(1) + replacement + m.group(2), text, count=1)


def _patch_parameter_block(block: str, param: ParamMapping, canonical: str) -> str:
    """Rewrite one <Parameter>…</Parameter> block in OptimizationParameters."""
    serial = _serializable_value(param, canonical)
    if param.nt_type == "enum":
        # Enum Max/Min are integer indices NT leaves at 0; only the value and
        # the single-element value list move.
        block, _ = _sub_once(
            r"(<EnumValuesSerializable>\s*<string>)[^<]*(</string>)",
            canonical,
            block,
        )
    else:
        block, _ = _sub_once(r'(<Max\s+xsi:type="[^"]+">)[^<]*(</Max>)', canonical, block)
        block, _ = _sub_once(r'(<Min\s+xsi:type="[^"]+">)[^<]*(</Min>)', canonical, block)
    block, _ = _sub_once(r"(<ValueSerializable>)[^<]*(</ValueSerializable>)", serial, block)
    return block


def patch_seed_text(seed_text: str, settings: dict[str, str]) -> tuple[str, list[str]]:
    """Rewrite parameter values in the seed XML, preserving all other text.

    Returns ``(patched_text, applied)`` where ``applied`` lists the NT
    properties that were actually found and rewritten in the seed.
    """
    applied: set[str] = set()
    text = seed_text

    # Pass 1: OptimizationParameters — per <Parameter> block.
    aof = re.search(
        r"(<ArrayOfParameter\b[^>]*>)(.*?)(</ArrayOfParameter>)", text, flags=re.DOTALL
    )
    if aof:
        inner = aof.group(2)

        def _rewrite_param(match: "re.Match[str]") -> str:
            param_block = match.group(0)
            name_match = re.search(r"<Name>\s*([^<\s][^<]*?)\s*</Name>", param_block)
            if not name_match:
                return param_block
            name = name_match.group(1).strip()
            if name not in settings:
                return param_block
            applied.add(name)
            return _patch_parameter_block(param_block, get_param(name), settings[name])

        new_inner = re.sub(
            r"<Parameter>.*?</Parameter>", _rewrite_param, inner, flags=re.DOTALL
        )
        text = text[: aof.start(2)] + new_inner + text[aof.end(2):]

    # Pass 2: <Strategy>/<PantheonBotV2> body — per property element.
    body_match = re.search(
        r"(<PantheonBotV2\b[^>]*>)(.*?)(</PantheonBotV2>)", text, flags=re.DOTALL
    )
    if not body_match:
        raise ValueError("seed template missing <Strategy>/<PantheonBotV2> section")
    head, body, tail = body_match.group(1), body_match.group(2), body_match.group(3)

    for nt_property, canonical in settings.items():
        tag = re.escape(nt_property)
        body, n = _sub_once(
            rf"(<{tag}>)[^<]*(</{tag}>)", canonical, body
        )
        if n:
            applied.add(nt_property)

    text = text[: body_match.start()] + head + body + tail + text[body_match.end():]
    return text, sorted(applied)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_pantheon_template(
    *,
    seed_template: str | Path,
    output_path: str | Path,
    discovery_payload: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    baseline_overrides: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Write a PantheonBotV2 Strategy Analyzer template from discovery output.

    Exactly one of `discovery_payload` or `settings` must be supplied:

    - `discovery_payload` — a nested dict of ta_foundation analysis output;
      mapped through `settings_from_discovery` against the param_map registry.
    - `settings` — an explicit ``{nt_property: value}`` dict, coerced directly.

    `baseline_overrides` (``{nt_property: value}``) are applied on top of either
    source and are the intended channel for the ambiguous shared-key
    properties (`averageFast`/`averageSlow`, the Required/Blocked vol filters).

    Returns a manifest dict describing what was written.
    """
    if (discovery_payload is None) == (settings is None):
        raise ValueError("supply exactly one of discovery_payload or settings")

    seed_path = Path(seed_template)
    if not seed_path.is_file():
        raise FileNotFoundError(
            f"seed template not found: {seed_path}. Save one from NinjaTrader "
            "(Strategy Analyzer → Save As… Template) before running the generator."
        )
    seed_bytes = seed_path.read_bytes()
    seed_text = seed_bytes.decode("utf-8-sig")

    ambiguous_skipped: list[str] = []
    if discovery_payload is not None:
        resolved, ambiguous_skipped = settings_from_discovery(
            discovery_payload, overrides=baseline_overrides
        )
        source = "discovery_payload"
    else:
        resolved = {
            nt_property: _coerce_value(get_param(nt_property), value)
            for nt_property, value in (settings or {}).items()
        }
        for nt_property, value in (baseline_overrides or {}).items():
            resolved[nt_property] = _coerce_value(get_param(nt_property), value)
        source = "explicit settings"

    patched, applied = patch_seed_text(seed_text, resolved)
    unapplied = sorted(set(resolved) - set(applied))
    if strict and unapplied:
        raise ValueError(
            f"strict: {len(unapplied)} setting(s) not present in the seed "
            f"template and could not be written: {unapplied}"
        )

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bom = b"\xef\xbb\xbf" if seed_bytes.startswith(b"\xef\xbb\xbf") else b""
    out_path.write_bytes(bom + patched.encode("utf-8"))

    return {
        "output_path": str(out_path.resolve()),
        "seed_template": str(seed_path.resolve()),
        "source": source,
        "settings": resolved,
        "applied": applied,
        "unapplied": unapplied,
        "ambiguous_skipped": ambiguous_skipped,
    }
