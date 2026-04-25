from __future__ import annotations

"""
Ollama LLM Integration
======================
Converts a natural language strategy idea into a filled strategy template
JSON by calling a locally-running Ollama model.

The LLM is given:
  - A tight system prompt with the template schema, valid values, and examples
  - The user's natural language idea as the user message
  - JSON output mode (forces valid JSON where supported)

The LLM never writes Python code — it only fills in JSON field values.
The template is shown to the user for review before any code runs.

Usage
-----
    from ta_foundation.analysis.strategy_composer.llm import OllamaComposer

    oc = OllamaComposer(model="qwen3-coder:30b")
    template_dict = oc.generate("Buy NQ on the 5m ORB break at 8:30 Denver time")
    print(template_dict)

    # Or stream tokens to a callback
    oc.generate_stream("...", on_token=lambda t: print(t, end="", flush=True))
"""

import json
import re
from typing import Any, Callable, Dict, Iterator, Optional

import urllib.request
import urllib.error

OLLAMA_BASE = "http://localhost:11434"

# ---------------------------------------------------------------------------
# System prompt — injected before every LLM call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a quantitative trading strategy assistant.
Your ONLY job is to convert a natural language strategy description into a JSON strategy template.
You MUST output ONLY valid JSON — no explanation, no markdown, no code blocks.

=== TEMPLATE STRUCTURE ===

{
  "template_name": "short_snake_case_name",
  "hypothesis": "One clear sentence describing the edge and why it should exist",
  "instrument": "NQ",
  "contract": "H25",
  "timeframe": "5m",
  "direction": "long",

  "entry_signal": {
    "type": "ma_cross",
    "timing": "next_open"
  },

  "entry_filters": [],

  "stop_mode": "signal_extreme_capped",
  "hard_stop_ticks_cap": 44,
  "initial_target_ticks": 30,

  "partial_rules": {
    "enabled": false,
    "partial_size_pct": 50,
    "partial_target_ticks": 18,
    "move_stop_to_break_even_after_partial": true
  },

  "runner_rules": {
    "enabled": false
  },

  "session_rules": {
    "enabled": true,
    "session_start": "08:30:00",
    "session_end": "15:00:00",
    "timezone": "America/Denver"
  },

  "max_hold_bars": 20,
  "flatten_on_session_end": true
}

=== VALID VALUES ===

direction: "long" | "short" | "both"
timeframe: "1m" | "3m" | "5m" | "10m" | "15m" | "30m" | "1h"
timing: "next_open" | "break_extreme" | "body_midpoint"
stop_mode: "signal_extreme_capped" | "atr_based" | "fixed_ticks"

entry_signal.type options and their extra fields:

1. "candle_pattern" — add: "pattern": one of:
   large_body | pin_bar_bullish | pin_bar_bearish | inside_bar | outside_bar |
   engulfing_bullish | engulfing_bearish | doji | clean_breakout_bar

2. "ma_cross" — add: "fast_type": "ema"|"sma", "fast_period": int,
   "slow_type": "ema"|"sma", "slow_period": int

3. "ma_touch" — add: "ma_type": "ema"|"sma", "period": int,
   "touch_tolerance_pct": float (default 0.1)

4. "breakout" — add: "lookback_bars": int, "confirmation_bars": int (default 1)

5. "orb" — add: "orb_minutes": int (e.g. 5, 15, 30),
   "session_start": "HH:MM", "retest_bars": int (default 0)

6. "rsi_threshold" — add: "period": int, "threshold": float,
   "cross_direction": "cross_above" | "cross_below"

7. "price_level" — add: "level_type": one of:
   vwap | or_high | or_low | pd_high | pd_low | overnight_high | overnight_low
   "touch_direction": "bounce_up" | "bounce_down"
   "touch_tolerance_ticks": int (default 4)
   "orb_minutes": int (only if level_type is or_high or or_low)

entry_filters is a list of filter objects. Each filter has a "type" field:

  {"type": "trend", "indicator": "sma"|"ema", "period": int,
   "condition": "price_above"|"price_below", "description": "..."}

  {"type": "volatility", "period": int, "condition": "above_median"|"below_median",
   "lookback": int, "description": "..."}

  {"type": "rsi", "period": int, "condition": "above"|"below",
   "threshold": float, "description": "..."}

=== EXAMPLES ===

Example 1 — "Buy NQ 5m when 9 EMA crosses above 21 SMA, only when above 50 SMA":
{
  "template_name": "ema9_sma21_cross_long",
  "hypothesis": "When the 9 EMA crosses above the 21 SMA on NQ 5m bars, short-term momentum has shifted bullish and the next open provides a positive-expectancy entry",
  "instrument": "NQ", "contract": "H25", "timeframe": "5m", "direction": "long",
  "entry_signal": {"type": "ma_cross", "fast_type": "ema", "fast_period": 9, "slow_type": "sma", "slow_period": 21, "timing": "next_open"},
  "entry_filters": [{"type": "trend", "indicator": "sma", "period": 50, "condition": "price_above", "description": "Only long when above 50 SMA"}],
  "stop_mode": "signal_extreme_capped", "hard_stop_ticks_cap": 40, "initial_target_ticks": 30,
  "partial_rules": {"enabled": true, "partial_size_pct": 50, "partial_target_ticks": 16, "move_stop_to_break_even_after_partial": true},
  "runner_rules": {"enabled": false},
  "session_rules": {"enabled": true, "session_start": "08:30:00", "session_end": "15:00:00", "timezone": "America/Denver"},
  "max_hold_bars": 20, "flatten_on_session_end": true
}

Example 2 — "Short NQ 15m Opening Range break below":
{
  "template_name": "orb15_short",
  "hypothesis": "When NQ breaks below the 15-minute opening range on the 15m chart, intraday momentum is bearish and short entries at the break provide positive expectancy",
  "instrument": "NQ", "contract": "H25", "timeframe": "15m", "direction": "short",
  "entry_signal": {"type": "orb", "orb_minutes": 15, "session_start": "07:30", "retest_bars": 2, "timing": "next_open"},
  "entry_filters": [],
  "stop_mode": "signal_extreme_capped", "hard_stop_ticks_cap": 44, "initial_target_ticks": 40,
  "partial_rules": {"enabled": false, "partial_size_pct": 50, "partial_target_ticks": 20, "move_stop_to_break_even_after_partial": true},
  "runner_rules": {"enabled": false},
  "session_rules": {"enabled": true, "session_start": "08:30:00", "session_end": "15:00:00", "timezone": "America/Denver"},
  "max_hold_bars": 15, "flatten_on_session_end": true
}

=== RULES ===
- Output ONLY the JSON object. No markdown. No explanation. No code fences.
- Use only the field names and values defined above.
- Choose realistic stop/target sizes for NQ futures (1 tick = $5, typical stops 30-50 ticks).
- If the user does not specify instrument, assume NQ H25.
- If the user does not specify timeframe, choose the most appropriate one.
- The hypothesis must be a single specific, falsifiable sentence.
"""


# ---------------------------------------------------------------------------
# Ollama HTTP helpers
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_stream(url: str, payload: dict, timeout: int = 300) -> Iterator[str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "") or chunk.get("response", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue


def list_models(base: str = OLLAMA_BASE) -> list[str]:
    """Return list of model names available in Ollama."""
    try:
        result = _post_json(f"{base}/api/tags", {}, timeout=5)
        return [m["name"] for m in result.get("models", [])]
    except Exception:
        return []


def _extract_json(text: str) -> dict:
    """Extract a JSON object from LLM output, handling markdown fences."""
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find the first { ... } block
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract valid JSON from LLM output:\n{text[:500]}")


# ---------------------------------------------------------------------------
# OllamaComposer
# ---------------------------------------------------------------------------

class OllamaComposer:
    """
    Converts a natural language strategy idea into a strategy template dict
    using a locally-running Ollama model.

    Parameters
    ----------
    model
        Ollama model name (e.g., "qwen3-coder:30b").
    base_url
        Ollama server base URL (default: http://localhost:11434).
    temperature
        Sampling temperature. Lower = more deterministic JSON output.
    """

    def __init__(
        self,
        model: str = "qwen3-coder:30b",
        base_url: str = OLLAMA_BASE,
        temperature: float = 0.2,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    def generate(self, idea: str) -> Dict[str, Any]:
        """
        Generate a strategy template dict from a natural language idea.

        Retries once if the first response cannot be parsed as JSON.

        Parameters
        ----------
        idea
            Natural language description of the strategy.

        Returns
        -------
        dict — ready to pass to StrategyTemplate.from_dict()
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": idea},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": self.temperature},
        }

        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = _post_json(f"{self.base_url}/api/chat", payload, timeout=180)
                raw = resp.get("message", {}).get("content", "") or resp.get("response", "")
                return _extract_json(raw)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError(
                    f"Cannot reach Ollama at {self.base_url}. "
                    f"Is it running? (ollama serve)"
                ) from exc
            except (ValueError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt == 0:
                    # Retry without JSON format mode (some models ignore it)
                    payload = dict(payload)
                    payload.pop("format", None)

        raise ValueError(f"LLM did not return valid JSON after 2 attempts: {last_exc}")

    def generate_stream(
        self,
        idea: str,
        on_token: Callable[[str], None],
    ) -> Dict[str, Any]:
        """
        Stream tokens to ``on_token`` callback and return the parsed template
        when generation is complete.

        Parameters
        ----------
        idea
            Natural language strategy description.
        on_token
            Called with each text token as it arrives.

        Returns
        -------
        dict — parsed template
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": idea},
            ],
            "stream": True,
            "options": {"temperature": self.temperature},
        }

        buffer = []
        try:
            for token in _post_stream(f"{self.base_url}/api/chat", payload):
                on_token(token)
                buffer.append(token)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Is it running? (ollama serve)"
            ) from exc

        full_text = "".join(buffer)
        return _extract_json(full_text)
