from __future__ import annotations

from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.compile_observer import CompileError
from ta_foundation.nt_strategy_loop.repair import (
    RepairContext,
    build_repair_prompt,
    repair,
)


def _spec() -> StrategySpec:
    return StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent="")


def _context(source: str, errors: tuple[CompileError, ...]) -> RepairContext:
    return RepairContext(
        spec=_spec(),
        current_source=source,
        target_class_name="LoopUnit",
        target_file_name="LoopUnit.cs",
        errors=errors,
        attempt=1,
    )


def test_repair_fixes_class_name_mismatch() -> None:
    source = (
        "namespace NinjaTrader.NinjaScript.Strategies\n"
        "{\n"
        "    public class WrongName : Strategy\n"
        "    {\n"
        '        protected override void OnStateChange() { Name = "WrongName"; }\n'
        "    }\n"
        "}\n"
    )
    result = repair(_context(source, errors=()))

    assert result is not None
    assert "public class LoopUnit : Strategy" in result.source
    assert "Name = \"LoopUnit\"" in result.source
    assert any("renamed class" in note for note in result.applied)


def test_repair_inserts_missing_using_for_unresolved_indicator() -> None:
    source = (
        "using System;\n"
        "namespace NinjaTrader.NinjaScript.Strategies\n"
        "{\n"
        "    public class LoopUnit : Strategy {}\n"
        "}\n"
    )
    error = CompileError(
        file="LoopUnit.cs",
        line=10,
        column=5,
        code="CS0103",
        message="The name 'SMA' does not exist in the current context",
        raw="",
        source="LoopUnit.cs",
    )
    result = repair(_context(source, errors=(error,)))

    assert result is not None
    assert "using NinjaTrader.NinjaScript.Indicators;" in result.source


def test_repair_returns_none_when_no_heuristic_and_no_callback() -> None:
    source = "public class LoopUnit : Strategy {}\n"
    error = CompileError(
        file="LoopUnit.cs",
        line=1,
        column=1,
        code="CS1234",
        message="totally novel error nobody has a heuristic for",
        raw="",
        source="LoopUnit.cs",
    )
    result = repair(_context(source, errors=(error,)))
    assert result is None


def test_repair_uses_callback_when_heuristic_declines() -> None:
    source = "public class LoopUnit : Strategy {}\n"
    error = CompileError(
        file="LoopUnit.cs",
        line=1,
        column=1,
        code="CS1234",
        message="novel error",
        raw="",
        source="LoopUnit.cs",
    )

    def callback(_ctx: RepairContext) -> str:
        return source + "// repaired by callback\n"

    result = repair(_context(source, errors=(error,)), callback=callback)
    assert result is not None
    assert result.channel == "callback"
    assert "repaired by callback" in result.source


def test_build_repair_prompt_includes_errors_and_class_constraint() -> None:
    error = CompileError(
        file="LoopUnit.cs",
        line=10,
        column=5,
        code="CS0103",
        message="The name 'SMA' does not exist in the current context",
        raw="",
        source="LoopUnit.cs",
    )
    prompt = build_repair_prompt(_context("// src\n", (error,)))
    assert "LoopUnit" in prompt
    assert "CS0103" in prompt
    assert "must be named" in prompt
