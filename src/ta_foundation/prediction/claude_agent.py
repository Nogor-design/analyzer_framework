from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import anthropic

from ._prompt import CLAUDE_TOOL_SYSTEM_PROMPT, build_user_message

_DEFAULT_MODEL = "claude-opus-4-7"

_SUBMIT_TOOL: Dict[str, Any] = {
    "name": "submit_market_prediction",
    "description": "Submit the complete next-day market prediction. Call this exactly once.",
    "input_schema": {
        "type": "object",
        "required": [
            "trend_direction", "trend_confidence", "is_trending", "chop_confidence",
            "predicted_high", "predicted_low", "breakout_probability", "breakout_direction",
            "event_risk_score", "key_levels", "reasoning",
        ],
        "properties": {
            "trend_direction":      {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "trend_confidence":     {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "is_trending":          {"type": "boolean"},
            "chop_confidence":      {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "predicted_high":       {"type": "number"},
            "predicted_low":        {"type": "number"},
            "breakout_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "breakout_direction":   {"type": "string", "enum": ["up", "down", "either", "none"]},
            "event_risk_score":     {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "key_levels": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object",
                    "required": ["price", "label", "level_type", "touch_probability"],
                    "properties": {
                        "price":             {"type": "number"},
                        "label":             {"type": "string"},
                        "level_type":        {"type": "string", "enum": ["support", "resistance", "pivot", "magnet"]},
                        "touch_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                },
            },
            "reasoning": {"type": "string"},
        },
    },
}


class ClaudeMarketAgent:
    """
    LLM-powered prediction agent using claude-opus-4-7 with adaptive thinking
    and tool-use structured output.

    Usage:
        agent = ClaudeMarketAgent()
        prediction = predict_next_day(..., agent_fn=agent, agent_id=agent.agent_id)
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: Optional[str] = None,
        agent_id: str = "claude-opus-4-7",
    ) -> None:
        self.model    = model
        self.agent_id = agent_id
        self._client  = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def __call__(self, context: Dict[str, Any]) -> Dict[str, Any]:
        user_message = build_user_message(context)

        with self._client.messages.stream(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=CLAUDE_TOOL_SYSTEM_PROMPT,
            tools=[_SUBMIT_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            message = stream.get_final_message()

        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_market_prediction":
                return dict(block.input)

        # Fallback: try raw text if tool call is missing
        for block in message.content:
            if hasattr(block, "text") and block.text:
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    pass

        raise RuntimeError(
            f"ClaudeMarketAgent: no tool call in response. "
            f"stop_reason={message.stop_reason}, "
            f"blocks={[b.type for b in message.content]}"
        )
