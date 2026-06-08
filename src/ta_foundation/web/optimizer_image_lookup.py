from __future__ import annotations

"""Resolve a god/monster portrait image for an optimizer candidate.

Uses the ``template_naming`` package (installed editable from
``D:\\templateNaming``) to decode the candidate's strategy template
into its canonical ``Phase + MA + Descriptor`` form, then walks a
deterministic filename fallback chain inside one or more configured
images directories.

Search strategy (per dir, in this order):

1. **Exact-stem match** against the following chain (first hit wins;
   ``.png``/``.jpeg``/``.jpg`` accepted, case-insensitive):

   - ``<Phase><MA><Descriptor><Direction>-<Market>`` e.g. ``CoilingAresFireB-NQ``
   - ``<Phase><MA><Descriptor><Direction>``         e.g. ``CoilingAresFireB``
   - ``<Phase><MA><Descriptor>``                    e.g. ``CoilingAresFire``
   - ``<MA><Descriptor>``                           e.g. ``AresFire``
   - ``<MA>``                                       e.g. ``Ares``
   - ``Default``

2. **Per-god subdir pool** — if the dir contains ``<MA>_images/`` (e.g.
   ``Zeus_images``) the lookup picks a deterministic image from inside
   it (alphabetical first). This is how raw god-image albums are
   organized in ``NewGodImages/``.

3. **Substring search** in the dir — first file whose stem contains
   ``<MA><Descriptor>`` (or just ``<MA>``). Catches prefixed names like
   ``BrassZeusFire.png`` that don't match the deterministic chain.

If multiple dirs are passed, each dir is exhausted (steps 1→3) before
moving to the next dir. The first hit wins.

``_Background.*`` variants are skipped automatically — those are
companion files for compositing in the legacy ``God images`` dir and
are never the portrait.

The lookup never raises for missing directories or unparseable
templates — it returns ``None`` and surfaces the reason in ``notes``.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_EXTS: tuple[str, ...] = (".jpeg", ".jpg", ".png")
_PORTRAIT_CACHE: dict[str, tuple[Path, ...]] = {}
_CHILD_DIR_CACHE: dict[str, tuple[Path, ...]] = {}


@dataclass(frozen=True)
class ImageLookupResult:
    template_path: str | None
    image_path: str | None
    decoded: dict[str, Any]      # NamingDecision fields, JSON-safe
    candidates_tried: list[str]  # filename stems we checked, in order
    matched_step: str | None     # which step in the chain hit
    matched_dir: str | None = None  # which images dir contained the match
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_path": self.template_path,
            "image_path": self.image_path,
            "decoded": dict(self.decoded),
            "candidates_tried": list(self.candidates_tried),
            "matched_step": self.matched_step,
            "matched_dir": self.matched_dir,
            "notes": list(self.notes),
        }


def lookup_image_for_template(
    template_path: Path | str,
    *,
    images_dir: Path | str | Iterable[Path | str] | None,
    market_suffix: str | None = None,
) -> ImageLookupResult:
    """Resolve the best portrait for the candidate XML at ``template_path``.

    Parameters
    ----------
    template_path : Path | str
        XML template — e.g. a final_backtest_handoff/named_backtest_templates
        XML. The decoder reads it to extract Phase/MA/Descriptor.
    images_dir : Path | str | Iterable | None
        Directory or list of directories of portrait images, searched in
        priority order. A single string with embedded newlines is also
        treated as a multi-dir list (one path per line).
    market_suffix : str | None
        Market root (e.g. ``"NQ"``) used to try the
        ``<compact>-<market>`` exact-match stem first. Derived by the
        caller from the session contract (``"NQ 06-26"`` -> ``"NQ"``).
        If absent, that step is simply skipped.
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

    dirs = _normalize_dirs(images_dir, notes=notes)
    if not dirs:
        return ImageLookupResult(
            template_path=str(template), image_path=None, decoded=decoded,
            candidates_tried=[], matched_step=None, notes=notes,
        )

    phase = decoded.get("phase") or ""
    ma_name = decoded.get("ma_name") or ""
    descriptor = decoded.get("descriptor") or ""
    direction = decoded.get("direction") or ""
    market = (market_suffix or "").strip()

    # Deterministic fallback chain — extended with direction+market.
    chain: list[tuple[str, str]] = [
        ("compact+direction+market", f"{phase}{ma_name}{descriptor}{direction}-{market}" if market else ""),
        ("compact+direction", f"{phase}{ma_name}{descriptor}{direction}"),
        ("phase+ma+descriptor", f"{phase}{ma_name}{descriptor}"),
        ("ma+descriptor", f"{ma_name}{descriptor}"),
        ("ma", ma_name),
    ]
    chain = [(step, stem) for step, stem in chain if stem]

    candidates_tried: list[str] = []

    # Per-dir: exact chain → god-pool subdir → substring search.
    for img_dir in dirs:
        for step, stem in chain:
            candidates_tried.append(stem)
            match = _find_in_dir(img_dir, stem)
            if match is not None:
                return ImageLookupResult(
                    template_path=str(template), image_path=str(match),
                    decoded=decoded, candidates_tried=candidates_tried,
                    matched_step=step, matched_dir=str(img_dir), notes=notes,
                )

        semantic_needle = f"{ma_name}{descriptor}"
        if semantic_needle:
            match = _substring_search(img_dir, semantic_needle)
            if match is not None:
                notes.append(f"matched by semantic substring search: {semantic_needle}")
                return ImageLookupResult(
                    template_path=str(template), image_path=str(match),
                    decoded=decoded, candidates_tried=candidates_tried + [f"~{semantic_needle}"],
                    matched_step="substring", matched_dir=str(img_dir), notes=notes,
                )

        if ma_name:
            pool_match = _pick_from_god_pool(img_dir, ma_name)
            if pool_match is not None:
                notes.append(f"matched from per-god pool: {img_dir.name}/{ma_name}_images/")
                return ImageLookupResult(
                    template_path=str(template), image_path=str(pool_match),
                    decoded=decoded, candidates_tried=candidates_tried + [f"{ma_name}_images/*"],
                    matched_step="god_pool", matched_dir=str(img_dir), notes=notes,
                )

        for needle in (ma_name,):
            if not needle:
                continue
            match = _substring_search(img_dir, needle)
            if match is not None:
                notes.append(f"matched by substring search: {needle}")
                return ImageLookupResult(
                    template_path=str(template), image_path=str(match),
                    decoded=decoded, candidates_tried=candidates_tried + [f"~{needle}"],
                    matched_step="substring", matched_dir=str(img_dir), notes=notes,
                )

        candidates_tried.append("Default")
        default_match = _find_in_dir(img_dir, "Default")
        if default_match is not None:
            return ImageLookupResult(
                template_path=str(template), image_path=str(default_match),
                decoded=decoded, candidates_tried=candidates_tried,
                matched_step="default", matched_dir=str(img_dir), notes=notes,
            )

    notes.append("no image matched the fallback chain, per-god pool, or substring search")
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

def _normalize_dirs(
    images_dir: Path | str | Iterable[Path | str] | None,
    *,
    notes: list[str],
) -> list[Path]:
    """Coerce ``images_dir`` to an ordered list of existing directories.

    Accepts:
    - ``None``                                  → empty (annotated note)
    - a single ``Path`` / ``str``                → one-element list
    - a ``str`` with newlines                    → split per line
    - any ``Iterable`` of paths/strings          → preserved order

    Non-existent or non-directory entries are dropped with a note so
    typos surface in the result without breaking the call.
    """
    if images_dir is None:
        notes.append("no images_dir configured")
        return []

    raw: list[Any]
    if isinstance(images_dir, (str, Path)):
        text = str(images_dir)
        if "\n" in text:
            raw = [line.strip() for line in text.splitlines() if line.strip()]
        else:
            raw = [text] if text.strip() else []
    else:
        try:
            raw = list(images_dir)
        except TypeError:
            notes.append(f"images_dir not iterable: {type(images_dir).__name__}")
            return []

    out: list[Path] = []
    seen: set[str] = set()
    for entry in raw:
        if not entry:
            continue
        p = Path(entry)
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if not p.exists() or not p.is_dir():
            notes.append(f"images_dir does not exist: {p}")
            continue
        out.append(p)
    if not out and not notes:
        notes.append("no images_dir configured")
    return out


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


def _is_portrait(entry: Path) -> bool:
    """Skip non-image files and the legacy ``*_Background.*`` companions."""
    if not entry.is_file():
        return False
    if entry.suffix.lower() not in SUPPORTED_EXTS:
        return False
    if entry.stem.lower().endswith("_background"):
        return False
    return True


def _find_in_dir(img_dir: Path, stem: str) -> Path | None:
    """Case-insensitive exact-stem lookup across supported extensions."""
    if not stem:
        return None
    stem_lower = stem.lower()
    for entry in _iter_portraits(img_dir):
        if not _is_portrait(entry):
            continue
        if entry.stem.lower() == stem_lower:
            return entry
    return None


def _pick_from_god_pool(img_dir: Path, ma_name: str) -> Path | None:
    """Pick a deterministic portrait from ``<img_dir>/<MA>_images/``.

    The sub-directory naming convention is ``<MaName>_images`` (e.g.
    ``Zeus_images``, ``Hera_images``). Match is case-insensitive on the
    subdir name. Inside it, sort by stem and return the first portrait —
    deterministic across reruns and easy to override by renaming a file.
    """
    needle = f"{ma_name}_images".lower()
    subdir: Path | None = None
    for entry in _child_dirs(img_dir):
        if entry.name.lower() == needle:
            subdir = entry
            break
    if subdir is None:
        return None

    candidates = _iter_portraits(subdir)
    return candidates[0] if candidates else None


def _substring_search(img_dir: Path, needle: str) -> Path | None:
    if not needle:
        return None
    needle_lower = needle.lower()
    best: Path | None = None
    for entry in _iter_portraits(img_dir):
        if needle_lower in entry.stem.lower():
            if best is None or len(entry.stem) < len(best.stem):
                best = entry
    return best


def _iter_portraits(img_dir: Path) -> list[Path]:
    key = str(img_dir.resolve())
    cached = _PORTRAIT_CACHE.get(key)
    if cached is not None:
        return list(cached)
    try:
        portraits = tuple(
            sorted((entry for entry in img_dir.rglob("*") if _is_portrait(entry)), key=lambda p: str(p).lower())
        )
    except OSError:
        return []
    _PORTRAIT_CACHE[key] = portraits
    return list(portraits)


def _child_dirs(img_dir: Path) -> list[Path]:
    key = str(img_dir.resolve())
    cached = _CHILD_DIR_CACHE.get(key)
    if cached is not None:
        return list(cached)
    try:
        children = tuple(sorted((entry for entry in img_dir.iterdir() if entry.is_dir()), key=lambda p: p.name.lower()))
    except OSError:
        return []
    _CHILD_DIR_CACHE[key] = children
    return list(children)


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
