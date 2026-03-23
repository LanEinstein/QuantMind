"""Parse LLM responses into structured MiroFish simulation models."""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from backend.mirofish.schemas import (
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    SentimentSnapshot,
)

log = structlog.get_logger(component="mirofish.parser")

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_deep_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text.

    Tries multiple strategies to handle LLM output variability:
    1. Parse entire text as JSON
    2. Extract from markdown ```json fences
    3. Find outermost { ... } braces and parse

    Args:
        text: Raw LLM response.

    Returns:
        Parsed dict or None if no valid JSON found.
    """
    if not text or not text.strip():
        return None

    stripped = text.strip()

    # Strategy 1: entire text is JSON
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: extract from markdown fences
    match = _FENCE_RE.search(text)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: find matching braces via bracket counting
    first = stripped.find("{")
    if first != -1:
        depth = 0
        for idx in range(first, len(stripped)):
            if stripped[idx] == "{":
                depth += 1
            elif stripped[idx] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        result = json.loads(stripped[first : idx + 1])
                        if isinstance(result, dict):
                            return result
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break

    log.warning("json_extraction_failed", text_preview=text[:200])
    return None


def parse_persona_response(raw: str) -> tuple[str, dict[str, float]] | None:
    """Parse persona generation LLM response.

    Returns:
        Tuple of (event_summary, initial_sentiment_dict) or None.
    """
    data = extract_deep_json(raw)
    if data is None:
        return None

    summary = data.get("event_summary")
    sentiment = data.get("initial_sentiment")
    if not summary or not isinstance(sentiment, dict):
        log.warning("persona_missing_fields", keys=list(data.keys()))
        return None

    return str(summary), {
        "bullish": float(sentiment.get("bullish", 0.33)),
        "bearish": float(sentiment.get("bearish", 0.33)),
        "neutral": float(sentiment.get("neutral", 0.34)),
    }


def parse_evolution_response(
    raw: str,
) -> tuple[SentimentSnapshot, ...] | None:
    """Parse evolution simulation LLM response.

    Returns:
        Tuple of SentimentSnapshot objects or None.
    """
    data = extract_deep_json(raw)
    if data is None:
        return None

    evolution = data.get("sentiment_evolution")
    if not isinstance(evolution, list) or not evolution:
        log.warning("evolution_missing_or_empty")
        return None

    snapshots: list[SentimentSnapshot] = []
    for item in evolution:
        if not isinstance(item, dict):
            continue
        try:
            snapshots.append(
                SentimentSnapshot(
                    round=int(item.get("round", len(snapshots) + 1)),
                    bullish=float(item.get("bullish", 0.33)),
                    bearish=float(item.get("bearish", 0.33)),
                    neutral=float(item.get("neutral", 0.34)),
                )
            )
        except Exception as exc:
            log.warning("snapshot_parse_failed", error=str(exc))
            continue

    return tuple(snapshots) if snapshots else None


def parse_extraction_response(
    raw: str,
) -> dict[str, Any] | None:
    """Parse extraction LLM response.

    Returns:
        Dict with hidden_variables, key_inflection_points,
        extreme_scenarios, recommended_action — or None.
    """
    data = extract_deep_json(raw)
    if data is None:
        return None

    hidden_vars: list[HiddenVariable] = []
    for item in data.get("hidden_variables", []):
        if isinstance(item, dict):
            try:
                hidden_vars.append(
                    HiddenVariable(
                        variable=str(item.get("variable", "")),
                        probability=float(item.get("probability", 0.5)),
                        reasoning=str(item.get("reasoning", "")),
                    )
                )
            except Exception as exc:
                log.warning("hidden_var_parse_failed", error=str(exc))
                continue

    inflection_pts: list[InflectionPoint] = []
    for item in data.get("key_inflection_points", []):
        if isinstance(item, dict):
            try:
                inflection_pts.append(
                    InflectionPoint(
                        day=int(item.get("day", 1)),
                        event=str(item.get("event", "")),
                    )
                )
            except Exception as exc:
                log.warning("inflection_pt_parse_failed", error=str(exc))
                continue

    extreme_scenarios: list[ExtremeScenario] = []
    for item in data.get("extreme_scenarios", []):
        if isinstance(item, dict):
            try:
                extreme_scenarios.append(
                    ExtremeScenario(
                        scenario=str(item.get("scenario", "")),
                        probability=float(item.get("probability", 0.1)),
                        impact=str(item.get("impact", "")),
                    )
                )
            except Exception as exc:
                log.warning("extreme_scenario_parse_failed", error=str(exc))
                continue

    return {
        "hidden_variables": tuple(hidden_vars),
        "key_inflection_points": tuple(inflection_pts),
        "extreme_scenarios": tuple(extreme_scenarios),
        "recommended_action": str(
            data.get("recommended_action", "仿真数据不足，建议观望")
        ),
    }
