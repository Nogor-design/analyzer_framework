from __future__ import annotations

"""Tests for the god/monster portrait image lookup fallback chain."""

from pathlib import Path

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


def _patch_decision(monkeypatch, decision):
    """Stand in for the editable ``template_naming`` install."""
    import sys
    fake_pkg = type(sys)("template_naming")
    fake_pkg.analyze_template = lambda path: decision
    monkeypatch.setitem(sys.modules, "template_naming", fake_pkg)


@pytest.fixture
def fake_decision(monkeypatch):
    decision = _FakeDecision(phase="Coiling", ma_name="Poseidon",
                             descriptor="Fire", direction="B")
    _patch_decision(monkeypatch, decision)
    return decision


def _touch(path: Path, content: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_compact_direction_market_match_beats_compact_only(tmp_path: Path, fake_decision):
    """The user's NewGodImages convention: ``CoilingPoseidonFireB-NQ.png``.

    With a market suffix passed in, the longest stem wins.
    """
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "CoilingPoseidonFireB-NQ.png")
    _touch(imgs / "CoilingPoseidonFire.png")  # would win without market

    result = lookup_image_for_template(template, images_dir=imgs, market_suffix="NQ")
    assert Path(result.image_path).name == "CoilingPoseidonFireB-NQ.png"
    assert result.matched_step == "compact+direction+market"


def test_compact_direction_match_when_no_market_passed(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "CoilingPoseidonFireB.png")
    _touch(imgs / "CoilingPoseidonFire.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "CoilingPoseidonFireB.png"
    assert result.matched_step == "compact+direction"


def test_ma_descriptor_fallback(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "PoseidonFire.jpeg")
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


def test_god_pool_subdir_picks_alphabetically_first(tmp_path: Path, fake_decision):
    """When no named file matches, fall into ``<MaName>_images/`` and pick
    the alphabetical first portrait. Mirrors the NewGodImages layout
    where each god has a raw album of page scans."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "Poseidon_images" / "page_0003_img_02.jpeg")
    _touch(imgs / "Poseidon_images" / "page_0001_img_01.jpeg")
    _touch(imgs / "Poseidon_images" / "page_0002_img_01.jpeg")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "page_0001_img_01.jpeg"
    assert result.matched_step == "god_pool"
    assert any("per-god pool" in n for n in result.notes)


def test_god_pool_subdir_is_case_insensitive(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "POSEIDON_IMAGES" / "z.png")
    _touch(imgs / "POSEIDON_IMAGES" / "a.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "a.png"
    assert result.matched_step == "god_pool"


def test_substring_search_finds_prefixed_files(tmp_path: Path, fake_decision):
    """Even when none of the deterministic stems match and no god-pool
    subdir exists, a stem that *contains* MA+Descriptor wins."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "BrassPoseidonFire.png")
    _touch(imgs / "UnrelatedBot.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "BrassPoseidonFire.png"
    assert result.matched_step == "substring"
    assert any("substring search" in n for n in result.notes)


def test_background_companions_are_skipped(tmp_path: Path, fake_decision):
    """The legacy ``God images`` dir ships ``_Background`` siblings for
    every portrait. Treat them as non-portraits."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    imgs = tmp_path / "imgs"; imgs.mkdir()
    _touch(imgs / "CoilingPoseidonFire_Background.png")  # do NOT pick
    _touch(imgs / "PoseidonFire.png")

    result = lookup_image_for_template(template, images_dir=imgs)
    assert Path(result.image_path).name == "PoseidonFire.png"
    assert result.matched_step == "ma+descriptor"


def test_multiple_dirs_priority_order(tmp_path: Path, fake_decision):
    """First dir to produce a match wins, even if a later dir would have
    yielded a longer-stem hit."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    primary = tmp_path / "primary"; primary.mkdir()
    legacy = tmp_path / "legacy"; legacy.mkdir()
    _touch(primary / "PoseidonFire.png")
    _touch(legacy / "CoilingPoseidonFire.png")

    result = lookup_image_for_template(template, images_dir=[primary, legacy])
    assert Path(result.image_path).name == "PoseidonFire.png"
    assert result.matched_dir == str(primary)
    assert result.matched_step == "ma+descriptor"


def test_multiple_dirs_falls_through_when_first_empty(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    primary = tmp_path / "primary"; primary.mkdir()
    legacy = tmp_path / "legacy"; legacy.mkdir()
    _touch(legacy / "BronzePoseidonFire.png")  # substring match in legacy only

    result = lookup_image_for_template(template, images_dir=[primary, legacy])
    assert Path(result.image_path).name == "BronzePoseidonFire.png"
    assert result.matched_dir == str(legacy)
    assert result.matched_step == "substring"


def test_newline_separated_dirs_are_parsed(tmp_path: Path, fake_decision):
    """The UI textarea hands us a multiline string; parse it into a list."""
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    primary = tmp_path / "primary"; primary.mkdir()
    legacy = tmp_path / "legacy"; legacy.mkdir()
    _touch(legacy / "Poseidon.png")

    multiline = f"{primary}\n{legacy}\n"
    result = lookup_image_for_template(template, images_dir=multiline)
    assert Path(result.image_path).name == "Poseidon.png"
    assert result.matched_dir == str(legacy)


def test_missing_dirs_in_list_are_noted_not_fatal(tmp_path: Path, fake_decision):
    template = tmp_path / "candidate.xml"; template.write_text("<x/>")
    real = tmp_path / "real"; real.mkdir()
    _touch(real / "Poseidon.png")
    ghost = tmp_path / "ghost"  # never created

    result = lookup_image_for_template(template, images_dir=[ghost, real])
    assert result.image_path is not None
    assert any("does not exist" in n and "ghost" in n for n in result.notes)


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
