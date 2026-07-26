"""Pre-registration drift check.

A pre-registered probe is a YAML file containing a `pre_registration:` block
that names the `hypothesis_id` whose registered params it commits to running.
This module verifies that the YAML's params match what was registered. If
they drift — even by a single value — the run aborts before any analysis
runs, and the attempt is journaled.

This is the discipline from `Real Edge In Day Trading.md` §5: no
tweak-and-retry after seeing results. The drift check is the code-side gate
behind that rule.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ta_foundation.research_ledger.repository import Repository

PRE_REGISTRATION_KEY = "pre_registration"


@dataclass(frozen=True)
class PreRegistrationBlock:
    hypothesis_id: str
    family: Optional[str]
    instrument: Optional[str]
    timeframe: Optional[str]
    session_window: Optional[str]
    direction: Optional[str]
    params: dict
    pre_reg_mechanism: Optional[str]


@dataclass(frozen=True)
class DriftReport:
    ok: bool
    hypothesis_id: str
    reason: str
    yaml_params_hash: Optional[str] = None
    registered_params_hash: Optional[str] = None


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _params_hash(params: dict) -> str:
    return hashlib.sha256(_canonical_json(params).encode("utf-8")).hexdigest()


def extract_pre_registration_block(yaml_dict: dict) -> Optional[PreRegistrationBlock]:
    """Return the pre_registration block if present and well-formed; None if
    absent. Raises ValueError if the block is present but malformed.
    """
    if not isinstance(yaml_dict, dict):
        return None
    block = yaml_dict.get(PRE_REGISTRATION_KEY)
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError(
            f"'{PRE_REGISTRATION_KEY}' block must be a mapping, got {type(block).__name__}"
        )
    hypothesis_id = block.get("hypothesis_id")
    if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
        raise ValueError(
            f"'{PRE_REGISTRATION_KEY}.hypothesis_id' is required and must be a non-empty string"
        )
    params = block.get("params")
    if not isinstance(params, dict):
        raise ValueError(
            f"'{PRE_REGISTRATION_KEY}.params' is required and must be a mapping"
        )
    return PreRegistrationBlock(
        hypothesis_id=hypothesis_id.strip(),
        family=block.get("family"),
        instrument=block.get("instrument"),
        timeframe=block.get("timeframe"),
        session_window=block.get("session_window"),
        direction=block.get("direction"),
        params=params,
        pre_reg_mechanism=block.get("pre_reg_mechanism"),
    )


def check_drift(
    repo: Repository,
    yaml_block: PreRegistrationBlock,
) -> DriftReport:
    """Compare a pre-registered YAML block against the ledger row identified
    by its hypothesis_id. Returns a DriftReport (`ok=True` on match)."""
    hypothesis = repo.get_hypothesis(yaml_block.hypothesis_id)
    if hypothesis is None:
        return DriftReport(
            ok=False,
            hypothesis_id=yaml_block.hypothesis_id,
            reason=(
                f"hypothesis_id '{yaml_block.hypothesis_id}' is not in the ledger; "
                "register it first via the agent author_probe tool"
            ),
        )

    registered_params = json.loads(hypothesis.params_json)
    yaml_hash = _params_hash(yaml_block.params)
    registered_hash = _params_hash(registered_params)

    if yaml_hash != registered_hash:
        return DriftReport(
            ok=False,
            hypothesis_id=yaml_block.hypothesis_id,
            reason=(
                "YAML params drift from registered params; "
                "tweak-and-retry is forbidden (Real Edge §5). "
                "Register a new hypothesis instead of editing this one."
            ),
            yaml_params_hash=yaml_hash,
            registered_params_hash=registered_hash,
        )

    # Light cross-checks on metadata fields when supplied — these aren't
    # part of the dedupe contract but catch obvious copy-paste mistakes.
    for field, registered in (
        ("family", hypothesis.family),
        ("instrument", hypothesis.instrument),
        ("timeframe", hypothesis.timeframe),
        ("session_window", hypothesis.session_window),
        ("direction", hypothesis.direction),
    ):
        yaml_value = getattr(yaml_block, field)
        if yaml_value is None:
            continue
        if yaml_value != registered:
            return DriftReport(
                ok=False,
                hypothesis_id=yaml_block.hypothesis_id,
                reason=(
                    f"YAML.{field} = {yaml_value!r} does not match registered "
                    f"{field} = {registered!r}"
                ),
                yaml_params_hash=yaml_hash,
                registered_params_hash=registered_hash,
            )

    return DriftReport(
        ok=True,
        hypothesis_id=yaml_block.hypothesis_id,
        reason="match",
        yaml_params_hash=yaml_hash,
        registered_params_hash=registered_hash,
    )


def check_yaml_file(
    repo: Repository,
    yaml_path: Path,
    *,
    required: bool = True,
) -> Optional[DriftReport]:
    """Convenience: load YAML from disk, extract pre-registration block, run
    drift check. Returns None when the YAML has no pre_registration block and
    `required=False`. Raises ValueError when block is malformed.
    """
    import yaml as _yaml

    text = Path(yaml_path).read_text(encoding="utf-8")
    parsed = _yaml.safe_load(text) or {}
    block = extract_pre_registration_block(parsed)
    if block is None:
        if required:
            return DriftReport(
                ok=False,
                hypothesis_id="",
                reason=(
                    f"YAML {yaml_path} has no '{PRE_REGISTRATION_KEY}' block; "
                    "--hypothesis-id requires a pre-registered probe"
                ),
            )
        return None
    return check_drift(repo, block)
