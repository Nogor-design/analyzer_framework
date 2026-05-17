from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web.discovery_glossary import (
    GlossaryError,
    get_glossary,
    load_glossary,
    reload_glossary,
)


def test_real_glossary_loads_without_errors():
    payload = reload_glossary()
    assert payload.schema_version >= 1
    assert payload.categories
    assert payload.terms


def test_required_terms_are_present():
    """A handful of terms are referenced from many UI surfaces — they must always be defined."""
    payload = reload_glossary()
    must_have = {
        "profit_factor", "win_rate", "expectancy", "is_oos_degradation",
        "tier", "combo", "sweep", "walk_forward", "min_trades",
        "promote", "tick", "slippage",
        "large_body", "pin_bar", "engulfing", "inside_bar", "outside_bar",
        "doji", "clean_breakout_bar",
        "candle_family", "lcr_family", "orb_family", "bb_family", "ma_family",
        "breakout_family", "pullback_family", "level_family",
        "next_open", "break_extreme", "body_midpoint",
        "take_profit", "stop_loss", "atr_outcome", "max_bars_timeout",
        "atr", "session_filter", "rth", "timeframe",
    }
    missing = must_have - set(payload.terms.keys())
    assert not missing, f"Glossary missing required terms: {sorted(missing)}"


def test_each_term_has_required_fields():
    payload = reload_glossary()
    for term_id, term in payload.terms.items():
        for field in ("short_name", "category", "one_line", "details", "why_it_matters"):
            assert term.get(field), f"{term_id}: missing {field}"
        assert isinstance(term["see_also"], list)


def test_every_term_category_is_declared():
    payload = reload_glossary()
    declared = {cat["id"] for cat in payload.categories}
    for term_id, term in payload.terms.items():
        assert term["category"] in declared, (
            f"Term '{term_id}' uses undeclared category '{term['category']}'"
        )


def test_see_also_references_resolve():
    payload = reload_glossary()
    known_ids = set(payload.terms.keys())
    for term_id, term in payload.terms.items():
        for ref in term["see_also"]:
            assert ref in known_ids, f"{term_id} see_also '{ref}' is unknown"


def test_categories_carry_their_term_ids():
    payload = reload_glossary()
    for cat in payload.categories:
        assert isinstance(cat["term_ids"], list)
        for tid in cat["term_ids"]:
            assert payload.terms[tid]["category"] == cat["id"]


def test_get_glossary_caches():
    a = get_glossary()
    b = get_glossary()
    assert a is b


def test_loader_rejects_missing_required_field(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "categories:\n"
        "  - id: x\n"
        "    label: X\n"
        "    summary: stuff\n"
        "terms:\n"
        "  busted:\n"
        "    short_name: B\n"
        "    category: x\n"
        "    one_line: short\n"
        "    why_it_matters: why\n",
        encoding="utf-8",
    )
    with pytest.raises(GlossaryError):
        load_glossary(bad)


def test_loader_rejects_unknown_category(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "categories:\n"
        "  - id: real\n"
        "    label: Real\n"
        "    summary: stuff\n"
        "terms:\n"
        "  thing:\n"
        "    short_name: T\n"
        "    category: ghost\n"
        "    one_line: x\n"
        "    details: y\n"
        "    why_it_matters: z\n",
        encoding="utf-8",
    )
    with pytest.raises(GlossaryError, match="unknown category"):
        load_glossary(bad)


def test_loader_rejects_dangling_see_also(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "categories:\n"
        "  - id: c\n"
        "    label: C\n"
        "    summary: s\n"
        "terms:\n"
        "  a:\n"
        "    short_name: A\n"
        "    category: c\n"
        "    one_line: o\n"
        "    details: d\n"
        "    why_it_matters: w\n"
        "    see_also: [does_not_exist]\n",
        encoding="utf-8",
    )
    with pytest.raises(GlossaryError, match="references unknown term"):
        load_glossary(bad)


def test_payload_to_dict_is_json_safe():
    payload = reload_glossary()
    d = payload.to_dict()
    import json

    json.dumps(d)
