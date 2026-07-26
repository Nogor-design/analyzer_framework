from __future__ import annotations

"""
Loader for the Discovery glossary.

The glossary is a YAML file authored separately so it can be edited without
touching code (see discovery_glossary.yaml). The web layer surfaces it via
GET /api/discovery/glossary, and the UI uses it to:
- power the right-side glossary slide-in panel
- render the "?" hover modal next to every parameter label
- cross-link related concepts via see_also

This module loads, validates, and caches the glossary at import time.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_GLOSSARY_PATH = Path(__file__).parent / "discovery_glossary.yaml"

_REQUIRED_TERM_FIELDS = ("short_name", "category", "one_line", "details", "why_it_matters")
_REQUIRED_CATEGORY_FIELDS = ("id", "label", "summary")


class GlossaryError(ValueError):
    """Raised when the glossary YAML fails validation."""


@dataclass(frozen=True)
class GlossaryPayload:
    schema_version: int
    categories: list[dict[str, Any]]
    terms: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "categories": list(self.categories),
            "terms": dict(self.terms),
        }


def load_glossary(path: Path | str | None = None) -> GlossaryPayload:
    """Load and validate the glossary YAML.

    Pass an explicit path for tests. By default reads
    src/ta_foundation/web/discovery_glossary.yaml.
    """
    target = Path(path) if path else _GLOSSARY_PATH
    if not target.exists():
        raise GlossaryError(f"Glossary file not found: {target}")

    with open(target, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise GlossaryError("Glossary YAML must be a mapping at the top level.")

    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, int):
        raise GlossaryError("Glossary must declare an integer schema_version.")

    categories_raw = raw.get("categories") or []
    if not isinstance(categories_raw, list) or not categories_raw:
        raise GlossaryError("Glossary must declare a non-empty 'categories' list.")

    categories: list[dict[str, Any]] = []
    category_ids: set[str] = set()
    for idx, item in enumerate(categories_raw, start=1):
        if not isinstance(item, dict):
            raise GlossaryError(f"Category #{idx} must be a mapping.")
        for field in _REQUIRED_CATEGORY_FIELDS:
            if not str(item.get(field) or "").strip():
                raise GlossaryError(f"Category #{idx} is missing required field '{field}'.")
        if item["id"] in category_ids:
            raise GlossaryError(f"Duplicate category id: {item['id']}")
        category_ids.add(item["id"])
        categories.append(
            {
                "id": str(item["id"]),
                "label": str(item["label"]),
                "summary": str(item["summary"]),
                "term_ids": [],
            }
        )

    terms_raw = raw.get("terms") or {}
    if not isinstance(terms_raw, dict) or not terms_raw:
        raise GlossaryError("Glossary must declare a non-empty 'terms' mapping.")

    terms: dict[str, dict[str, Any]] = {}
    for term_id, body in terms_raw.items():
        if not isinstance(body, dict):
            raise GlossaryError(f"Term '{term_id}' must be a mapping.")
        for field in _REQUIRED_TERM_FIELDS:
            value = body.get(field)
            if not isinstance(value, str) or not value.strip():
                raise GlossaryError(
                    f"Term '{term_id}' is missing required field '{field}' (must be a non-empty string)."
                )
        category = body["category"]
        if category not in category_ids:
            raise GlossaryError(
                f"Term '{term_id}' references unknown category '{category}'."
            )
        see_also = body.get("see_also") or []
        if not isinstance(see_also, list):
            raise GlossaryError(f"Term '{term_id}' has non-list see_also.")
        for ref in see_also:
            if not isinstance(ref, str):
                raise GlossaryError(f"Term '{term_id}' see_also contains non-string entry.")

        terms[str(term_id)] = {
            "id": str(term_id),
            "short_name": str(body["short_name"]).strip(),
            "category": str(body["category"]).strip(),
            "one_line": str(body["one_line"]).strip(),
            "details": str(body["details"]).strip(),
            "why_it_matters": str(body["why_it_matters"]).strip(),
            "see_also": [str(ref) for ref in see_also],
            "formula": str(body["formula"]).strip() if isinstance(body.get("formula"), str) else None,
        }

    # Resolve see_also references and assign each term to its category bucket.
    by_category: dict[str, list[str]] = {cid: [] for cid in category_ids}
    for term_id, term in terms.items():
        for ref in term["see_also"]:
            if ref not in terms:
                raise GlossaryError(
                    f"Term '{term_id}' see_also references unknown term '{ref}'."
                )
        by_category[term["category"]].append(term_id)

    for cat in categories:
        cat["term_ids"] = sorted(by_category[cat["id"]])

    return GlossaryPayload(
        schema_version=schema_version,
        categories=categories,
        terms=terms,
    )


# Cached at import — the YAML doesn't change at runtime.
_CACHE: GlossaryPayload | None = None


def get_glossary() -> GlossaryPayload:
    """Return the cached glossary, loading on first call."""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_glossary()
    return _CACHE


def reload_glossary() -> GlossaryPayload:
    """Force a reload from disk. Mostly for tests."""
    global _CACHE
    _CACHE = load_glossary()
    return _CACHE
