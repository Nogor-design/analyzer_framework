"""Tests for the tool registry exports."""

from __future__ import annotations

from ta_foundation.agent.tools import (
    ALL_TOOLS,
    READ_TOOLS,
    TOOLS_BY_NAME,
    TOOLS_BY_ROLE,
    WRITE_TOOLS,
)


def test_read_and_write_tools_disjoint() -> None:
    read_names = {t._tool_name for t in READ_TOOLS}
    write_names = {t._tool_name for t in WRITE_TOOLS}
    assert read_names.isdisjoint(write_names)


def test_all_tools_registered_by_name() -> None:
    for t in ALL_TOOLS:
        assert TOOLS_BY_NAME[t._tool_name] is t


def test_no_duplicate_tool_names() -> None:
    names = [t._tool_name for t in ALL_TOOLS]
    assert len(names) == len(set(names))


def test_role_index_covers_every_tool() -> None:
    flat = {t for tools in TOOLS_BY_ROLE.values() for t in tools}
    assert flat == set(ALL_TOOLS)


def test_each_tool_has_metadata() -> None:
    for t in ALL_TOOLS:
        assert t._tool_name
        assert t._tool_role
        assert isinstance(t._tool_schema, dict)
        assert t._tool_description


def test_minimum_tool_counts() -> None:
    assert len(READ_TOOLS) >= 9
    assert len(WRITE_TOOLS) >= 6
