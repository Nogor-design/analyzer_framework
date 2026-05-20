from __future__ import annotations

import urllib.error

import pytest

from ta_foundation.nt_strategy_loop import repair_llm
from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.compile_observer import CompileError
from ta_foundation.nt_strategy_loop.repair import RepairContext


_CLEAN_CS = (
    "namespace NinjaTrader.NinjaScript.Strategies\n"
    "{\n"
    "    public class LoopUnit : Strategy\n"
    "    {\n"
    '        protected override void OnStateChange() { Name = "LoopUnit"; }\n'
    "    }\n"
    "}\n"
)


def _context() -> RepairContext:
    return RepairContext(
        spec=StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent=""),
        current_source="public class LoopUnit : Strategy {}\n",
        target_class_name="LoopUnit",
        target_file_name="LoopUnit.cs",
        errors=(
            CompileError(
                file="LoopUnit.cs",
                line=1,
                column=1,
                code="CS1234",
                message="novel error",
                raw="",
                source="LoopUnit.cs",
            ),
        ),
        attempt=1,
    )


def test_extract_cs_source_from_fenced_block() -> None:
    text = f"Here is the fix:\n\n```csharp\n{_CLEAN_CS}```\n\nDone."
    extracted = repair_llm._extract_cs_source(text)
    assert extracted is not None
    assert "public class LoopUnit : Strategy" in extracted
    assert "```" not in extracted


def test_extract_cs_source_from_raw_text() -> None:
    extracted = repair_llm._extract_cs_source(_CLEAN_CS)
    assert extracted is not None
    assert extracted.endswith("\n")


def test_extract_cs_source_rejects_non_ninjascript() -> None:
    assert repair_llm._extract_cs_source("Sorry, I cannot help with that.") is None
    assert repair_llm._extract_cs_source("") is None


def test_extract_cs_source_picks_longest_block() -> None:
    text = f"```text\nshort\n```\nand\n```csharp\n{_CLEAN_CS}```"
    extracted = repair_llm._extract_cs_source(text)
    assert extracted is not None
    assert "public class LoopUnit" in extracted


def test_callback_returns_repaired_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repair_llm,
        "_post_chat",
        lambda base_url, payload, timeout: f"```csharp\n{_CLEAN_CS}```",
    )
    callback = repair_llm.make_ollama_repair_callback()
    result = callback(_context())
    assert result is not None
    assert "public class LoopUnit : Strategy" in result


def test_callback_declines_when_ollama_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(base_url, payload, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(repair_llm, "_post_chat", _boom)
    callback = repair_llm.make_ollama_repair_callback()
    assert callback(_context()) is None


def test_callback_declines_on_junk_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repair_llm,
        "_post_chat",
        lambda base_url, payload, timeout: "I think you should try harder.",
    )
    callback = repair_llm.make_ollama_repair_callback()
    assert callback(_context()) is None
