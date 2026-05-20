from __future__ import annotations

"""Compile-error → repaired NinjaScript source.

Two repair channels exist:

1. **Heuristic** — deterministic fixes for high-frequency compile errors we
   have already seen (class-name vs filename mismatch, missing usings for
   commonly-referenced NinjaScript namespaces). Cheap, runs first, no LLM
   round-trip.
2. **Callback** — an optional `RepairCallback` the orchestrator can wire to an
   LLM repair pass. Receives the full `RepairContext` and returns the next
   `.cs` body (or `None` to decline).

Heuristics first, callback second. If both decline, the loop stops with a
clear reason recorded by `policy.evaluate_stop`.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.compile_observer import CompileError


@dataclass(frozen=True)
class RepairContext:
    spec: StrategySpec
    current_source: str
    target_class_name: str
    target_file_name: str
    errors: Sequence[CompileError]
    attempt: int
    prior_attempt_summaries: tuple[str, ...] = field(default_factory=tuple)


RepairCallback = Callable[[RepairContext], str | None]


@dataclass(frozen=True)
class RepairResult:
    source: str
    applied: tuple[str, ...]
    channel: str  # "heuristic" or "callback"

    @property
    def changed(self) -> bool:
        return bool(self.applied)


_COMMON_USINGS = (
    "System",
    "System.ComponentModel",
    "System.ComponentModel.DataAnnotations",
    "NinjaTrader.Cbi",
    "NinjaTrader.Data",
    "NinjaTrader.NinjaScript",
    "NinjaTrader.NinjaScript.Indicators",
)

_SYMBOL_TO_USING = {
    "SMA": "NinjaTrader.NinjaScript.Indicators",
    "EMA": "NinjaTrader.NinjaScript.Indicators",
    "ATR": "NinjaTrader.NinjaScript.Indicators",
    "CrossAbove": "NinjaTrader.NinjaScript.Indicators",
    "CrossBelow": "NinjaTrader.NinjaScript.Indicators",
    "MarketPosition": "NinjaTrader.Cbi",
    "Range": "System.ComponentModel.DataAnnotations",
    "Display": "System.ComponentModel.DataAnnotations",
}


def repair(
    context: RepairContext,
    *,
    callback: RepairCallback | None = None,
) -> RepairResult | None:
    heuristic = _heuristic_repair(context)
    if heuristic is not None and heuristic.changed:
        return heuristic
    if callback is None:
        return None
    candidate = callback(context)
    if candidate is None or candidate.strip() == context.current_source.strip():
        return None
    return RepairResult(source=candidate, applied=("callback",), channel="callback")


def _heuristic_repair(context: RepairContext) -> RepairResult | None:
    source = context.current_source
    applied: list[str] = []

    renamed = _fix_class_name_mismatch(source, context.target_class_name)
    if renamed is not None:
        source = renamed
        applied.append(f"renamed class to {context.target_class_name}")

    needed_usings = _missing_usings_from_errors(source, context.errors)
    if needed_usings:
        source = _insert_usings(source, needed_usings)
        applied.append(f"added usings: {', '.join(needed_usings)}")

    if not applied:
        return None
    return RepairResult(source=source, applied=tuple(applied), channel="heuristic")


def _fix_class_name_mismatch(source: str, target_class_name: str) -> str | None:
    match = re.search(r"public\s+class\s+(\w+)\s*:\s*Strategy", source)
    if not match:
        return None
    current = match.group(1)
    if current == target_class_name:
        return None
    patched = re.sub(
        r"public\s+class\s+\w+\s*:\s*Strategy",
        f"public class {target_class_name} : Strategy",
        source,
        count=1,
    )
    patched = re.sub(
        rf'Name\s*=\s*"{re.escape(current)}"',
        f'Name = "{target_class_name}"',
        patched,
    )
    return patched


def _missing_usings_from_errors(source: str, errors: Sequence[CompileError]) -> tuple[str, ...]:
    referenced: set[str] = set()
    for error in errors:
        if error.code not in {"CS0103", "CS0246"}:
            continue
        for symbol in re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", error.message):
            if symbol in _SYMBOL_TO_USING:
                referenced.add(_SYMBOL_TO_USING[symbol])
    missing = tuple(ns for ns in _COMMON_USINGS if ns in referenced and f"using {ns};" not in source)
    return missing


def _insert_usings(source: str, usings: Sequence[str]) -> str:
    lines = source.splitlines(keepends=True)
    insert_at = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("using "):
            insert_at = index + 1
        elif insert_at and not line.strip():
            insert_at = index
            break
    inject = "".join(f"using {ns};\n" for ns in usings)
    return "".join(lines[:insert_at]) + inject + "".join(lines[insert_at:])


def build_repair_prompt(context: RepairContext) -> str:
    error_lines = "\n".join(
        f"- {error.formatted()}"
        for error in context.errors
    ) or "- (no errors parsed)"
    prior = "\n".join(f"- {summary}" for summary in context.prior_attempt_summaries) or "- (none)"
    return (
        f"# Repair Attempt {context.attempt} for {context.target_class_name}\n\n"
        f"## Strategy spec\n"
        f"- family: `{context.spec.family}`\n"
        f"- intent: {context.spec.intent or '(not provided)'}\n\n"
        f"## NinjaTrader compile errors\n{error_lines}\n\n"
        f"## Prior attempts\n{prior}\n\n"
        f"Produce a corrected NinjaScript .cs body. The class must be named "
        f"`{context.target_class_name}` and live in the file "
        f"`{context.target_file_name}`. Keep the public `[NinjaScriptProperty]` "
        f"surface compatible with the spec parameters where possible.\n"
    )


def context_from_attempt(
    *,
    spec: StrategySpec,
    source_path: str | Path,
    errors: Sequence[CompileError],
    attempt: int,
    prior_attempt_summaries: Sequence[str] = (),
) -> RepairContext:
    path = Path(source_path)
    return RepairContext(
        spec=spec,
        current_source=path.read_text(encoding="utf-8"),
        target_class_name=spec.strategy_name,
        target_file_name=path.name,
        errors=tuple(errors),
        attempt=attempt,
        prior_attempt_summaries=tuple(prior_attempt_summaries),
    )
