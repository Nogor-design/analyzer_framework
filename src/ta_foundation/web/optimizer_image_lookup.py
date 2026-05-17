from __future__ import annotations

"""Resolve a god/monster portrait image for an optimizer candidate.

Uses the ``template_naming`` package (installed editable from
``D:\\templateNaming``) to decode the candidate's strategy template
into its canonical ``Phase + MA + Descriptor`` form, then walks a
deterministic filename fallback chain inside a configured images
directory.

Fallback chain (illustrated for ``CoilingPoseidonFireB-NQ.xml``):

1. ``CoilingPoseidonFire`` — full compact name without direction / market
2. ``PoseidonFire``        — MA + Descriptor only
3. ``Poseidon``            — MA only
4. ``Default``             — last-resort placeholder
5. *substring search*      — any file in the dir whose stem contains
   ``PoseidonFire`` (or just ``Poseidon`` if the longer match is empty).
   This catches portraits whose filename starts with an unrelated
   prefix like ``BrassZeusFire.png``.

Each step tries both ``.jpeg`` and ``.png`` (case-insensitive on
Windows). The first hit wins. If nothing matches, returns ``None``
and the caller renders the decoded name without a portrait.

The lookup never raises for missing directories or unparseable
templates — it returns ``None`` and surfaces the reason in the
``notes`` field of the result. Section renderers can show those notes
as a debug aid.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_EXTS: tuple[str, ...] = (".jpeg", ".jpg", ".png")


@dataclass(frozen=True)
class ImageLookupResult:
    template_path: str | None
    image_path: str | None
    decoded: dict[str, Any]      # NamingDecision fields, JSON-safe
    candidates_tried: list[str]  # filename stems we checked, in order
    matched_step: str | None     # which step in the chain hit
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_path": self.template_path,
            "image_path": self.image_path,
            "decoded": dict(self.decoded),
            "candidates_tried": list(self.candidates_tried),
            "matched_step": self.matched_step,
            "notes": list(self.notes),
        }


def lookup_image_for_template(
    template_path: Path | str,
    *,
    images_dir: Path | str | None,
) -> ImageLookupResult:
    """Resolve the best portrait for the candidate XML at ``template_path``.

    Parameters
    ----------
    template_path : Path | str
        XML template — e.g. a final_backtest_handoff/named_backtest_templates
        XML. The decoder reads it to extract Phase/MA/Descriptor.
    images_dir : Path | str | None
        Directory of portrait images. ``None`` (or non-existent) skips
        the lookup and just returns the decoded name with a note.
    """
    template = Path(template_path) if template_path else None
    notes: list[str] = []

    decoded: dict[str, Any] = {}
    if template is None or not template.exists():
        notes.append(f"template not found: {template}")
        return ImageLookupResult(
            template_path=str(template) if template else None,
            image_path=None, decoded=decoded,
            candidates_tried=[], matched_step=None, notes=notes,
        )

    try:
        # template_naming is editable-installed at D:\templateNaming
        from template_naming import analyze_template
        decision = analyze_template(template)
    except Exception as exc:  # pragma: no cover - import / parse failure surface
        notes.append(f"template_naming.analyze_template failed: {exc}")
        return ImageLookupResult(
            template_path=str(template), image_path=None, decoded={},
            candidates_tried=[], matched_step=None, notes=notes,
        )

    decoded = _decision_to_dict(decision)

    if not images_dir:
        notes.append("no images_dir configured")
        return ImageLookupResult(
            template_path=str(template), image_path=None, decoded=decoded,
            candidates_tried=[], matched_step=None, notes=notes,
        )

    img_dir = Path(images_dir)
    if not img_dir.exists() or not img_dir.is_dir():
        notes.append(f"images_dir does not exist: {img_dir}")
        return ImageLookupResult(
            template_path=str(template), image_path=None, decoded=decoded,
            candidates_tried=[], matched_step=None, notes=notes,
        )

    phase = decoded.get("phase") or ""
    ma_name = decoded.get("ma_name") or ""
    descriptor = decoded.get("descriptor") or ""

    # Deterministic fallback chain.
    chain: list[tuple[str, str]] = [
        ("phase+ma+descriptor", f"{phase}{ma_name}{descriptor}"),
        ("ma+descriptor", f"{ma_name}{descriptor}"),
        ("ma", ma_name),
        ("default", "Default"),
    ]
    chain = [(step, stem) for step, stem in chain if stem]

    candidates_tried: list[str] = []
    for step, stem in chain:
        candidates_tried.append(stem)
        match = _find_in_dir(img_dir, stem)
        if match is not None:
            return ImageLookupResult(
                template_path=str(template), image_path=str(match),
                decoded=decoded, candidates_tried=candidates_tried,
                matched_step=step, notes=notes,
            )

    # Substring search — first file whose stem contains the MA+Descriptor
    # (or just MA) substring. Useful when portraits live with a prefix
    # like ``BrassZeusFire.png`` and the canonical lookup misses.
    for needle in (f"{ma_name}{descriptor}", ma_name):
        if not needle:
            continue
        match = _substring_search(img_dir, needle)
        if match is not None:
            notes.append(f"matched by substring search: {needle}")
            return ImageLookupResult(
                template_path=str(template), image_path=str(match),
                decoded=decoded, candidates_tried=candidates_tried + [f"~{needle}"],
                matched_step="substring", notes=notes,
            )

    notes.append("no image matched the fallback chain or substring search")
    return ImageLookupResult(
        template_path=str(template), image_path=None,
        decoded=decoded, candidates_tried=candidates_tried,
        matched_step=None, notes=notes,
    )


def plain_english_summary(decoded: dict[str, Any]) -> str:
    """Render a short human-readable summary of a decoded name.

    Mirrors the ``NamingUserGuide.md`` decoder examples — Phase tells
    when, MA tells personality, Descriptor tells risk shape, Direction
    tells long/short/both. Falls back to ``compact_name`` if any field
    is missing.
    """
    phase = decoded.get("phase") or ""
    ma_name = decoded.get("ma_name") or ""
    descriptor = decoded.get("descriptor") or ""
    direction = decoded.get("direction") or ""
    if not (phase and ma_name and descriptor and direction):
        return decoded.get("compact_name") or ""
    dir_text = {"L": "long only", "S": "short only", "B": "both directions"}.get(direction, direction)
    return f"{phase} session · {ma_name} MA personality · {descriptor} risk shape · {dir_text}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _decision_to_dict(decision: Any) -> dict[str, Any]:
    return {
        "phase": getattr(decision, "phase", None),
        "ma_name": getattr(decision, "ma_name", None),
        "descriptor": getattr(decision, "descriptor", None),
        "direction": getattr(decision, "direction", None),
        "compact_name": getattr(decision, "compact_name", None),
        "spaced_name": getattr(decision, "spaced_name", None),
        "output_file_name": getattr(decision, "output_file_name", None),
        "ma_value": _safe_float(getattr(decision, "ma_value", None)),
        "rr_value": _safe_float(getattr(decision, "rr_value", None)),
        "per_trade_max_loss": _safe_float(getattr(decision, "per_trade_max_loss", None)),
        "true_max_loss": _safe_float(getattr(decision, "true_max_loss", None)),
    }


def _find_in_dir(img_dir: Path, stem: str) -> Path | None:
    """Case-insensitive exact-stem lookup across supported extensions."""
    if not stem:
        return None
    stem_lower = stem.lower()
    for entry in img_dir.iterdir():
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix not in SUPPORTED_EXTS:
            continue
        if entry.stem.lower() == stem_lower:
            return entry
    return None


def _substring_search(img_dir: Path, needle: str) -> Path | None:
    if not needle:
        return None
    needle_lower = needle.lower()
    # Prefer the shortest filename containing the substring — usually
    # cleaner than a noisy long prefix.
    best: Path | None = None
    for entry in img_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if needle_lower in entry.stem.lower():
            if best is None or len(entry.stem) < len(best.stem):
                best = entry
    return best


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f
