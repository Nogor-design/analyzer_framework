from __future__ import annotations

"""Tests for the god/monster portrait image lookup fallback chain."""

from pathlib import Path
from unittest.mock import patch

import pytest

from ta_foundation.web.optimizer_image_lookup import (
    lookup_image_for_template,
    plain_english_summary,
)


class _FakeDecision:
    def __init__(self, *, phase, ma_name, descriptor, direction,
                 compact=None, spaced=None):
        self.phase = phase
        self.ma_name = ma_name
        self.descriptor = descriptor
        self.direction = direction
        self.compact_name = compact or f"{phase}{ma_name}{descriptor}{direction}"
        self.spaced_name = spaced or f"{phase} {ma_name} {descriptor} {direction}"
        self.output_file_name = f"{self.compact_name}.xml"
        self.ma_value = 175.0
        self.rr_value = 1.8
        self.per_trade_max_loss = 1500.0
        self.true_max_loss = 3000.0


@pytest.fixture
def fake_decision(monkeypatch):
    """Patch template_naming.analyze_template so tests don't need a real XML."""
    import ta_foundation.web.optimizer_image_lookup as mod
    decision = _FakeDecision(phase="Coiling", ma_name="Poseidon",
                             descriptor="Fire", direction="B")

    def _fake_import_and_analyze(path):
        return decision

    # The module imports lazily inside the function; patch sys.modules.
    import sys
    fake_pkg = type(sys)("template_naming")
    fake_pkg.analyze_template = lambda path: decision
    monkeypatch.setitem(sys.modules, "template_naming", fake_pkg)
    return decision


def _touch(path: Path, content: bytes = b"x"):
    path.write_bytes(content)


def test_full_compact_match_wins(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"
    template.write_text("<x/>", encoding="utf-8")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "CoilingPoseidonFire.png")
    _touch(imgs / "PoseidonFire.png")
    _touch(imgs / "Poseidon.png")
    _touch(imgs / "Default.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert result.image_path is not None
    assert Path(result.image_path).name == "CoilingPoseidonFire.png"
    assert result.matched_step == "phase+ma+descriptor"


def test_ma_descriptor_fallback(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "PoseidonFire.jpeg")  # only this matches
    _touch(imgs / "OtherBot.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "PoseidonFire.jpeg"
    assert result.matched_step == "ma+descriptor"


def test_ma_only_fallback(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "Poseidon.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "Poseidon.png"
    assert result.matched_step == "ma"


def test_default_fallback(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "Default.jpeg")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "Default.jpeg"
    assert result.matched_step == "default"


def test_substring_search_finds_prefixed_files(tmp_path: Path, fake_decision):
    """Mirrors the operator's "BrassZeusFire.png matches a ZeusFire card" case
    — even when none of the deterministic stems match, a stem that *contains*
    MA+Descriptor (or MA alone) wins."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    # No exact-stem matches; only a prefixed one that contains PoseidonFire.
    _touch(imgs / "BrassPoseidonFire.png")
    _touch(imgs / "UnrelatedBot.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "BrassPoseidonFire.png"
    assert result.matched_step == "substring"
    assert any("substring search" in n for n in result.notes)


def test_returns_none_when_no_match_anywhere(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "TotallyUnrelated.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert result.image_path is None
    assert result.decoded["compact_name"] == "CoilingPoseidonFireB"
    assert any("no image matched" in n for n in result.notes)


def test_no_images_dir_returns_decoded_only(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    result = lookup_image_for_template(template, images_dir=None)
    assert result.image_path is None
    assert result.decoded["compact_name"] == "CoilingPoseidonFireB"
    assert any("no images_dir" in n for n in result.notes)


def test_missing_images_dir_returns_decoded_only(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    result = lookup_image_for_template(template, images_dir=tmp_path / "ghost")
    assert result.image_path is None
    assert any("does not exist" in n for n in result.notes)


def test_missing_template_returns_empty(tmp_path: Path):
    result = lookup_image_for_template(tmp_path / "missing.xml",
                                       images_dir=tmp_path)
    assert result.image_path is None
    assert result.decoded == {}
    assert any("not found" in n for n in result.notes)


def test_plain_english_summary_complete():
    decoded = {
        "phase": "Coiling", "ma_name": "Poseidon",
        "descriptor": "Fire", "direction": "B",
        "compact_name": "CoilingPoseidonFireB",
    }
    text = plain_english_summary(decoded)
    assert "Coiling" in text and "Poseidon" in text and "Fire" in text
    assert "both directions" in text


def test_plain_english_summary_falls_back_to_compact():
    decoded = {"compact_name": "FallbackOnly"}
    assert plain_english_summary(decoded) == "FallbackOnly"


def test_extension_case_insensitive(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "CoilingPoseidonFire.PNG")
    result = lookup_image_for_template(template, images_dir=imgs)
    assert result.image_path is not None
    assert Path(result.image_path).name == "CoilingPoseidonFire.PNG"
