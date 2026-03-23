"""Base helpers for agent LLM calls and response parsing."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="agent_base")

_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


async def call_agent(
    router: LLMRouter,
    agent_name: str,
    system_prompt: str,
    user_content: str,
) -> str:
    """Call the LLM via router for a given agent.

    Args:
        router: The LLM router instance.
        agent_name: Key in agent_models.yaml (e.g. "news_crawler").
        system_prompt: Chinese system prompt for the agent.
        user_content: User message with context data.

    Returns:
        LLM response content string. On failure, returns an error
        description string (graceful degradation, never raises).
    """
    try:
        response = await router.complete(
            agent_name,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        if not response.choices:
            log.warning("agent_empty_response", agent_name=agent_name)
            return f"[{agent_name} error: empty response]"
        content = response.choices[0].message.content or ""
        log.info(
            "agent_call_complete",
            agent_name=agent_name,
            content_length=len(content),
        )
        return content
    except Exception as exc:
        log.warning(
            "agent_call_failed",
            agent_name=agent_name,
            error=str(exc),
        )
        return f"[{agent_name} error: {exc}]"


def extract_json_from_response(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from LLM response text.

    Args:
        text: Raw LLM response that may contain JSON embedded in text.

    Returns:
        Parsed dict if valid JSON found, None otherwise.
    """
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        return None
