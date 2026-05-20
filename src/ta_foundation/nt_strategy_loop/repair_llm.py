from __future__ import annotations

"""LLM-backed repair callback for the autonomous strategy loop.

`repair.repair()` always runs the deterministic heuristics first (class-name
mismatch, missing usings). When those decline, it calls an optional
`RepairCallback`. This module builds one backed by a locally-running Ollama
model — the same server `ta_foundation.analysis.strategy_composer.llm` already
uses — so the loop can fix compile errors beyond the heuristic set without any
cloud API key.

Usage
-----
    from ta_foundation.nt_strategy_loop.repair_llm import make_ollama_repair_callback
    from ta_foundation.nt_strategy_loop.repair_loop import run_repair_loop

    callback = make_ollama_repair_callback(model="qwen3-coder:30b")
    run_repair_loop(spec, repair_callback=callback)

The callback is intentionally fail-soft: if Ollama is unreachable or the model
returns something that does not look like NinjaScript, it returns ``None``
(declines the repair) rather than raising. `run_repair_loop` then halts the
attempt with a clear ``repair_declined`` stop reason instead of crashing.
"""

import json
import re
import sys
import urllib.error
import urllib.request

from ta_foundation.nt_strategy_loop.repair import RepairCallback, RepairContext, build_repair_prompt


OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder:30b"


_SYSTEM_PROMPT = """You are an expert NinjaTrader 8 NinjaScript (C#) engineer.
Your ONLY job is to repair a NinjaScript strategy source file so that it
compiles cleanly inside NinjaTrader.

RULES:
- Output ONLY the complete, corrected C# source file.
- Do NOT include explanations, commentary, or markdown code fences.
- Preserve the strategy's intent and its public [NinjaScriptProperty] surface.
- The class name and the `Name = "..."` string must both match the requested
  strategy class name exactly.
- Make the smallest change that resolves every listed compiler error.
- Do not invent NinjaScript APIs. Use only standard NinjaTrader 8 namespaces
  (NinjaTrader.Cbi, NinjaTrader.Data, NinjaTrader.NinjaScript,
  NinjaTrader.NinjaScript.Indicators, NinjaTrader.NinjaScript.Strategies).
- The file must remain a single self-contained .cs file.
"""


def _post_chat(base_url: str, payload: dict, timeout: int) -> str:
    """POST to Ollama /api/chat and return the assistant message content."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "") or body.get("response", "")


def _extract_cs_source(text: str) -> str | None:
    """Pull a NinjaScript .cs body out of a model response.

    Handles models that wrap the file in a ```csharp fenced block (taking the
    longest fenced block) as well as models that return raw source. Returns
    ``None`` when the result does not look like a NinjaScript strategy file.
    """
    if not text or not text.strip():
        return None

    candidates = re.findall(r"```(?:[a-zA-Z#+]*)\n(.*?)```", text, flags=re.DOTALL)
    body = max(candidates, key=len).strip() if candidates else text.strip()

    looks_like_cs = ("namespace" in body or "class" in body) and "Strategy" in body
    if not looks_like_cs:
        return None
    return body + ("\n" if not body.endswith("\n") else "")


def _build_user_message(context: RepairContext) -> str:
    return (
        f"{build_repair_prompt(context)}\n"
        f"## Current source ({context.target_file_name})\n\n"
        f"```csharp\n{context.current_source}\n```\n\n"
        "Return the full corrected file."
    )


def make_ollama_repair_callback(
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = OLLAMA_BASE,
    temperature: float = 0.1,
    timeout: int = 300,
) -> RepairCallback:
    """Build a `RepairCallback` that repairs NinjaScript via a local Ollama model.

    Parameters
    ----------
    model
        Ollama model name. A code-tuned model is strongly recommended.
    base_url
        Ollama server base URL (default: the local server on port 11434).
    temperature
        Sampling temperature. Low values keep the repair deterministic.
    timeout
        Per-request timeout in seconds. NinjaScript files are small but large
        code models can be slow on the first token.
    """

    def _callback(context: RepairContext) -> str | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(context)},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            raw = _post_chat(base_url, payload, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(
                f"[repair-llm] Ollama unreachable at {base_url} ({exc}); "
                "declining repair. Is `ollama serve` running?",
                file=sys.stderr,
            )
            return None
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[repair-llm] bad response from Ollama: {exc}", file=sys.stderr)
            return None

        source = _extract_cs_source(raw)
        if source is None:
            print(
                "[repair-llm] model response did not contain a NinjaScript file; "
                "declining repair.",
                file=sys.stderr,
            )
        return source

    return _callback
