"""Tests for the @journaled_tool framework: schema validation, preconditions,
journaling, and output truncation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.agent.tools._decorators import (
    MAX_INLINE_OUTPUT_BYTES,
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


# ---------- Schema validation ----------------------------------------------


def test_validates_required_param_missing(repo: Repository) -> None:
    @journaled_tool(name="t_required", role="agent:test", description="x",
                    schema={"name": {"type": "str", "min_length": 1}})
    def t(repo, *, name: str): return {"got": name}

    out = t(repo)
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"
    codes = {v["code"] for v in out["violations"]}
    assert "required" in codes


def test_validates_type_mismatch(repo: Repository) -> None:
    @journaled_tool(name="t_int", role="agent:test", description="x",
                    schema={"n": {"type": "int", "min": 0, "max": 10}})
    def t(repo, *, n: int): return {"n": n}

    out = t(repo, n="seven")
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"


def test_validates_below_min_above_max(repo: Repository) -> None:
    @journaled_tool(name="t_range", role="agent:test", description="x",
                    schema={"n": {"type": "int", "min": 1, "max": 5}})
    def t(repo, *, n: int): return n

    low = t(repo, n=0)
    assert low["ok"] is False
    high = t(repo, n=99)
    assert high["ok"] is False


def test_validates_enum(repo: Repository) -> None:
    @journaled_tool(name="t_enum", role="agent:test", description="x",
                    schema={"side": {"type": "enum", "values": ["a", "b"]}})
    def t(repo, *, side: str): return side

    out = t(repo, side="c")
    assert out["ok"] is False


def test_default_value_is_filled(repo: Repository) -> None:
    @journaled_tool(name="t_default", role="agent:test", description="x",
                    schema={"limit": {"type": "int", "required": False, "default": 25,
                                       "min": 1, "max": 100}})
    def t(repo, *, limit: int = 25): return limit

    out = t(repo)
    assert out["ok"] is True
    assert out["result"] == 25


def test_unknown_param_rejected(repo: Repository) -> None:
    @journaled_tool(name="t_unknown", role="agent:test", description="x", schema={})
    def t(repo): return "ok"

    out = t(repo, surprise="yes")
    assert out["ok"] is False
    codes = {v["code"] for v in out["violations"]}
    assert "unknown_param" in codes


# ---------- Preconditions ---------------------------------------------------


def test_precondition_blocks_call(repo: Repository) -> None:
    def gate(repo, inputs):
        return ToolFailure(code="blocked", message="not allowed in this state")

    @journaled_tool(
        name="t_pc", role="agent:test", description="x",
        schema={"x": {"type": "int", "min": 0, "max": 9}},
        preconditions=(gate,),
    )
    def t(repo, *, x: int): return x

    out = t(repo, x=1)
    assert out["ok"] is False
    assert out["code"] == "blocked"


def test_precondition_violations_pass_through(repo: Repository) -> None:
    def gate(repo, inputs):
        return ToolFailure(
            code="composite",
            message="multiple problems",
            violations=({"param": "x", "code": "wrong"},),
        )

    @journaled_tool(name="t_pc2", role="agent:test", description="x",
                    schema={"x": {"type": "int", "min": 0, "max": 9}},
                    preconditions=(gate,))
    def t(repo, *, x: int): return x

    out = t(repo, x=1)
    assert out["ok"] is False
    assert out["violations"] == [{"param": "x", "code": "wrong"}]


def test_precondition_pass_runs_function(repo: Repository) -> None:
    def gate(repo, inputs):
        return None

    @journaled_tool(name="t_pc3", role="agent:test", description="x",
                    schema={"x": {"type": "int", "min": 0, "max": 9}},
                    preconditions=(gate,))
    def t(repo, *, x: int): return x * 2

    out = t(repo, x=4)
    assert out["ok"] is True
    assert out["result"] == 8


# ---------- Journaling ------------------------------------------------------


def test_call_is_journaled(repo: Repository) -> None:
    @journaled_tool(name="t_jrnl", role="agent:test", description="x",
                    schema={"name": {"type": "str", "min_length": 1}})
    def t(repo, *, name: str): return {"hello": name}

    t(repo, name="world")
    rows = repo.list_journal()
    assert len(rows) == 1
    assert rows[0].tool_name == "t_jrnl"
    assert rows[0].error is None
    assert rows[0].duration_ms >= 0


def test_failed_call_is_journaled_with_error_code(repo: Repository) -> None:
    @journaled_tool(name="t_fail", role="agent:test", description="x",
                    schema={"x": {"type": "int", "min": 0, "max": 9}})
    def t(repo, *, x: int): return x

    t(repo, x=99)
    rows = repo.list_journal()
    assert len(rows) == 1
    assert rows[0].error == "schema_validation_failed"


def test_exception_inside_tool_is_journaled(repo: Repository) -> None:
    @journaled_tool(name="t_boom", role="agent:test", description="x", schema={})
    def t(repo): raise RuntimeError("kaboom")

    out = t(repo)
    assert out["ok"] is False
    assert out["code"] == "tool_exception"
    assert "kaboom" in out["error"]
    rows = repo.list_journal()
    assert rows[0].error == "tool_exception"


# ---------- Output truncation ----------------------------------------------


def test_large_output_is_spilled_to_disk(repo: Repository, tmp_path: Path,
                                          monkeypatch) -> None:
    # Re-point the truncated-output dir into the test tmp.
    from ta_foundation.agent.tools import _decorators
    monkeypatch.setattr(_decorators, "TRUNCATED_OUTPUT_DIR", tmp_path / "spill")

    @journaled_tool(name="t_big", role="agent:test", description="x", schema={})
    def t(repo):
        return {"blob": "x" * (MAX_INLINE_OUTPUT_BYTES * 2)}

    out = t(repo)
    assert out["ok"] is True
    assert out.get("truncated") is True
    artifact = Path(out["artifact_path"])
    assert artifact.exists()
    body = json.loads(artifact.read_text(encoding="utf-8"))
    assert body["ok"] is True
    assert "blob" in body["result"]


def test_small_output_is_returned_inline(repo: Repository) -> None:
    @journaled_tool(name="t_small", role="agent:test", description="x", schema={})
    def t(repo): return {"x": 1, "y": 2}

    out = t(repo)
    assert "truncated" not in out
    assert out["result"] == {"x": 1, "y": 2}
