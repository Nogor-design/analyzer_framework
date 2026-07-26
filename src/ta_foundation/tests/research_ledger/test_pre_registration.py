"""Tests for the pre-registration drift check.

The drift check is the code-side enforcement of `Real Edge In Day Trading.md`
§5: no tweak-and-retry after seeing results. A YAML probe carrying a
`pre_registration:` block must hash-match the ledger row it claims.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ta_foundation.research_ledger import (
    PreRegistrationBlock,
    Repository,
    check_drift,
    check_yaml_file,
    extract_pre_registration_block,
    get_repository,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


@pytest.fixture()
def registered_hypothesis(repo: Repository):
    return repo.register_hypothesis(
        hypothesis_id="h_test_drift",
        family="orb_failure_reclaim",
        instrument="NQ",
        timeframe="5m",
        session_window="ny_open_06_07_denver",
        direction="both",
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 20,
            "target_ticks": 150,
        },
        mechanism=(
            "An NY-open opening-range failure-reclaim where trapped breakout "
            "buyers exit into the body-midpoint fill, providing the impulse for "
            "the reverse continuation move."
        ),
        registered_by="human:test",
    )


def _build_yaml(hypothesis_id: str, params: dict, **extras) -> dict:
    body = {
        "pre_registration": {
            "hypothesis_id": hypothesis_id,
            "params": params,
            **extras,
        }
    }
    return body


# ---------- extract_pre_registration_block ---------------------------------


def test_extract_returns_none_when_block_absent() -> None:
    assert extract_pre_registration_block({"discovery": {}, "report": {}}) is None


def test_extract_raises_when_block_malformed() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        extract_pre_registration_block({"pre_registration": "not_a_dict"})


def test_extract_raises_when_hypothesis_id_missing() -> None:
    with pytest.raises(ValueError, match="hypothesis_id"):
        extract_pre_registration_block({"pre_registration": {"params": {"x": 1}}})


def test_extract_raises_when_params_missing() -> None:
    with pytest.raises(ValueError, match="params"):
        extract_pre_registration_block(
            {"pre_registration": {"hypothesis_id": "h_x"}}
        )


def test_extract_round_trip_preserves_metadata() -> None:
    block = extract_pre_registration_block(
        {
            "pre_registration": {
                "hypothesis_id": "h_x",
                "family": "orb_failure_reclaim",
                "instrument": "NQ",
                "timeframe": "5m",
                "session_window": "ny_open_06_07_denver",
                "direction": "both",
                "params": {"orb_minutes": 5},
                "pre_reg_mechanism": "frozen text",
            }
        }
    )
    assert block is not None
    assert block.hypothesis_id == "h_x"
    assert block.session_window == "ny_open_06_07_denver"
    assert block.params == {"orb_minutes": 5}


# ---------- check_drift ----------------------------------------------------


def test_check_drift_passes_on_exact_match(repo: Repository, registered_hypothesis):
    block = PreRegistrationBlock(
        hypothesis_id=registered_hypothesis.hypothesis_id,
        family=None,
        instrument=None,
        timeframe=None,
        session_window=None,
        direction=None,
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 20,
            "target_ticks": 150,
        },
        pre_reg_mechanism=None,
    )
    report = check_drift(repo, block)
    assert report.ok
    assert report.yaml_params_hash == report.registered_params_hash


def test_check_drift_fails_when_param_value_changed(
    repo: Repository, registered_hypothesis
):
    block = PreRegistrationBlock(
        hypothesis_id=registered_hypothesis.hypothesis_id,
        family=None, instrument=None, timeframe=None,
        session_window=None, direction=None,
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 20,
            "target_ticks": 100,  # CHANGED 150 -> 100
        },
        pre_reg_mechanism=None,
    )
    report = check_drift(repo, block)
    assert not report.ok
    assert "drift" in report.reason.lower()
    assert report.yaml_params_hash != report.registered_params_hash


def test_check_drift_fails_when_param_added(repo: Repository, registered_hypothesis):
    block = PreRegistrationBlock(
        hypothesis_id=registered_hypothesis.hypothesis_id,
        family=None, instrument=None, timeframe=None,
        session_window=None, direction=None,
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 20,
            "target_ticks": 150,
            "extra_param": 99,  # ADDED
        },
        pre_reg_mechanism=None,
    )
    report = check_drift(repo, block)
    assert not report.ok


def test_check_drift_fails_when_hypothesis_missing(repo: Repository) -> None:
    block = PreRegistrationBlock(
        hypothesis_id="h_does_not_exist",
        family=None, instrument=None, timeframe=None,
        session_window=None, direction=None,
        params={"x": 1},
        pre_reg_mechanism=None,
    )
    report = check_drift(repo, block)
    assert not report.ok
    assert "not in the ledger" in report.reason


def test_check_drift_fails_when_metadata_field_mismatches(
    repo: Repository, registered_hypothesis
):
    # Same params, but YAML claims the wrong instrument.
    block = PreRegistrationBlock(
        hypothesis_id=registered_hypothesis.hypothesis_id,
        family=None,
        instrument="ES",  # registered as NQ
        timeframe=None,
        session_window=None,
        direction=None,
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 20,
            "target_ticks": 150,
        },
        pre_reg_mechanism=None,
    )
    report = check_drift(repo, block)
    assert not report.ok
    assert "instrument" in report.reason


# ---------- check_yaml_file ------------------------------------------------


def test_check_yaml_file_passes_on_disk_match(
    tmp_path: Path, repo: Repository, registered_hypothesis
) -> None:
    yaml_path = tmp_path / "probe.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            _build_yaml(
                registered_hypothesis.hypothesis_id,
                {
                    "orb_minutes": 5,
                    "sweep_min_ticks": 4,
                    "reclaim_within_bars": 1,
                    "fill_mode": "body_midpoint",
                    "stop_ticks": 20,
                    "target_ticks": 150,
                },
            )
        ),
        encoding="utf-8",
    )
    report = check_yaml_file(repo, yaml_path)
    assert report is not None and report.ok


def test_check_yaml_file_returns_drift_on_modified_yaml(
    tmp_path: Path, repo: Repository, registered_hypothesis
) -> None:
    yaml_path = tmp_path / "probe.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            _build_yaml(
                registered_hypothesis.hypothesis_id,
                {
                    "orb_minutes": 5,
                    "sweep_min_ticks": 4,
                    "reclaim_within_bars": 1,
                    "fill_mode": "reclaim_close",  # CHANGED from body_midpoint
                    "stop_ticks": 20,
                    "target_ticks": 150,
                },
            )
        ),
        encoding="utf-8",
    )
    report = check_yaml_file(repo, yaml_path)
    assert report is not None and not report.ok


def test_check_yaml_file_required_returns_failure_when_block_missing(
    tmp_path: Path, repo: Repository
) -> None:
    yaml_path = tmp_path / "probe.yaml"
    yaml_path.write_text(yaml.safe_dump({"discovery": {"enabled": True}}), encoding="utf-8")
    report = check_yaml_file(repo, yaml_path, required=True)
    assert report is not None and not report.ok
    assert "no 'pre_registration' block" in report.reason


def test_check_yaml_file_optional_returns_none_when_block_missing(
    tmp_path: Path, repo: Repository
) -> None:
    yaml_path = tmp_path / "probe.yaml"
    yaml_path.write_text(yaml.safe_dump({"discovery": {}}), encoding="utf-8")
    assert check_yaml_file(repo, yaml_path, required=False) is None
