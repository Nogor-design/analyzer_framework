from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ._prompt import SYSTEM_PROMPT, build_user_message

_MAX_RETRIES = 2

# Fields that must be present and within bounds for a valid prediction
_FLOAT_RANGE_FIELDS = (
    "trend_confidence", "chop_confidence", "breakout_probability", "event_risk_score"
)
_VALID_TREND_DIRS    = {"bullish", "bearish", "neutral"}
_VALID_BREAKOUT_DIRS = {"up", "down", "either", "none"}


def _validate_and_coerce(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight coercion pass so minor type drift (int vs float, string bools)
    doesn't fail _validate_agent_response in the orchestrator.
    Raises ValueError with a human-readable message on unrecoverable issues.
    """
    errors: List[str] = []

    for field in _FLOAT_RANGE_FIELDS:
        if field not in raw:
            errors.append(f"missing field: {field!r}")
            continue
        try:
            v = float(raw[field])
        except (TypeError, ValueError):
            errors.append(f"{field!r}: cannot convert {raw[field]!r} to float")
            continue
        if not (0.0 <= v <= 1.0):
            # Clamp silently — common when a model outputs 0.75 as "75%" etc.
            raw[field] = max(0.0, min(1.0, v / 100.0 if v > 1.0 else v))
        else:
            raw[field] = v

    for field in ("predicted_high", "predicted_low"):
        if field not in raw:
            errors.append(f"missing field: {field!r}")
            continue
        try:
            raw[field] = float(raw[field])
        except (TypeError, ValueError):
            errors.append(f"{field!r}: cannot convert to float")

    # Trend direction
    td = str(raw.get("trend_direction", "")).lower().strip()
    if td not in _VALID_TREND_DIRS:
        # Attempt common synonyms
        if td in ("up", "long", "bull"):
            td = "bullish"
        elif td in ("down", "short", "bear"):
            td = "bearish"
        elif td in ("flat", "sideways", "range"):
            td = "neutral"
        else:
            errors.append(f"trend_direction: {raw.get('trend_direction')!r} not in {_VALID_TREND_DIRS}")
            td = "neutral"
    raw["trend_direction"] = td

    # Breakout direction
    bd = str(raw.get("breakout_direction", "none")).lower().strip()
    if bd not in _VALID_BREAKOUT_DIRS:
        bd = "none"
    raw["breakout_direction"] = bd

    # is_trending coercion (some models emit "true"/"false" strings)
    it = raw.get("is_trending")
    if isinstance(it, str):
        raw["is_trending"] = it.lower() in ("true", "yes", "1")
    elif isinstance(it, int):
        raw["is_trending"] = bool(it)
    elif not isinstance(it, bool):
        errors.append(f"is_trending: cannot coerce {it!r} to bool")
        raw["is_trending"] = False

    # key_levels
    if "key_levels" not in raw:
        raw["key_levels"] = []
    elif not isinstance(raw["key_levels"], list):
        raw["key_levels"] = []

    cleaned_levels = []
    for lvl in raw["key_levels"][:8]:
        if not isinstance(lvl, dict):
            continue
        try:
            price = float(lvl.get("price", 0))
            label = str(lvl.get("label") or "level")
            ltype = str(lvl.get("level_type") or "support").lower()
            if ltype not in ("support", "resistance", "pivot", "magnet"):
                ltype = "support"
            tp = float(lvl.get("touch_probability", 0.5))
            tp = max(0.0, min(1.0, tp))
            cleaned_levels.append({
                "price": price,
                "label": label,
                "level_type": ltype,
                "touch_probability": tp,
            })
        except (TypeError, ValueError):
            continue
    raw["key_levels"] = cleaned_levels

    # reasoning
    raw.setdefault("reasoning", "No reasoning provided.")
    raw["reasoning"] = str(raw["reasoning"])

    if errors:
        raise ValueError(f"OllamaAgent response validation failed: {'; '.join(errors)}")

    return raw


def _correction_prompt(original_response: str, errors: str) -> str:
    return (
        f"Your previous response had these issues:\n{errors}\n\n"
        f"Your response was:\n{original_response}\n\n"
        "Fix ONLY the issues listed above and return a corrected JSON object. "
        "Do not add explanations — only the raw JSON."
    )


class OllamaMarketAgent:
    """
    Market prediction agent backed by a locally-running Ollama model.

    The agent uses Ollama's native JSON mode (`format: "json"`) for structured
    output. If the model returns invalid JSON or a schema mismatch, it retries
    up to _MAX_RETRIES times with a correction prompt.

    Usage:
        agent = OllamaMarketAgent(model="llama3.1:70b")
        prediction = predict_next_day(..., agent_fn=agent, agent_id=agent.agent_id)

    Requirements:
        pip install ollama        (official Python client)
        # OR the runner falls back to requests if ollama is not installed
    """

    def __init__(
        self,
        model: str = "llama3.1:70b",
        base_url: str = "http://localhost:11434",
        agent_id: Optional[str] = None,
        temperature: float = 0.2,
        num_ctx: int = 8192,
    ) -> None:
        self.model       = model
        self.base_url    = base_url.rstrip("/")
        self.agent_id    = agent_id or f"ollama-{model.replace(':', '-')}"
        self.temperature = temperature
        self.num_ctx     = num_ctx

        # Lazy-import: prefer official ollama client, fall back to requests
        self._use_ollama_client = False
        try:
            import ollama  # noqa: F401
            self._use_ollama_client = True
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Agent interface
    # ------------------------------------------------------------------

    def __call__(self, context: Dict[str, Any]) -> Dict[str, Any]:
        user_message = build_user_message(context)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ]

        last_error: Optional[str] = None
        last_raw:   Optional[str] = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0 and last_error and last_raw:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append({"role": "user",      "content": _correction_prompt(last_raw, last_error)})

            raw_text = self._call_ollama(messages)
            last_raw = raw_text

            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                last_error = f"Response is not valid JSON: {exc}"
                continue

            try:
                result = _validate_and_coerce(parsed)
                return result
            except ValueError as exc:
                last_error = str(exc)
                continue

        raise RuntimeError(
            f"OllamaMarketAgent ({self.model}): failed after {_MAX_RETRIES + 1} attempts. "
            f"Last error: {last_error}\nLast response: {last_raw}"
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _call_ollama(self, messages: List[Dict[str, Any]]) -> str:
        if self._use_ollama_client:
            return self._call_via_client(messages)
        return self._call_via_requests(messages)

    def _call_via_client(self, messages: List[Dict[str, Any]]) -> str:
        import ollama
        response = ollama.chat(
            model=self.model,
            messages=messages,
            format="json",
            options={
                "temperature": self.temperature,
                "num_ctx":     self.num_ctx,
            },
        )
        return response["message"]["content"]

    def _call_via_requests(self, messages: List[Dict[str, Any]]) -> str:
        import urllib.request

        payload = json.dumps({
            "model":    self.model,
            "messages": messages,
            "format":   "json",
            "stream":   False,
            "options": {
                "temperature": self.temperature,
                "num_ctx":     self.num_ctx,
            },
        }).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())

        return body["message"]["content"]
