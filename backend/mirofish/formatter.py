"""Format MiroFish SimulationResult as structured Chinese text."""

from __future__ import annotations

from backend.mirofish.schemas import SimulationResult


def format_simulation_context(
    results: tuple[SimulationResult, ...],
) -> str:
    """Format simulation results as readable Chinese text.

    Produces a text block suitable for insertion into the
    Intelligence Officer's LLM prompt context.

    Args:
        results: Tuple of simulation results to format.

    Returns:
        Formatted Chinese text, or empty string if no results.
    """
    if not results:
        return ""

    sections: list[str] = []
    for result in results:
        sections.append(_format_single(result))

    return "\n\n---\n\n".join(sections)


def _format_single(result: SimulationResult) -> str:
    """Format a single SimulationResult."""
    parts: list[str] = [f"### MiroFish仿真: {result.event_summary}"]

    # Sentiment evolution
    evolution = result.sentiment_evolution
    if not evolution:
        parts.append("仿真数据不完整，仅供参考")
    else:
        parts.append("情绪演变:")
        if len(evolution) <= 8:
            for s in evolution:
                parts.append(
                    f"  R{s.round}: 看多{s.bullish:.2f} "
                    f"看空{s.bearish:.2f} 中性{s.neutral:.2f}"
                )
        else:
            # Show first 3, ..., last 3
            for s in evolution[:3]:
                parts.append(
                    f"  R{s.round}: 看多{s.bullish:.2f} "
                    f"看空{s.bearish:.2f} 中性{s.neutral:.2f}"
                )
            parts.append("  ...")
            for s in evolution[-3:]:
                parts.append(
                    f"  R{s.round}: 看多{s.bullish:.2f} "
                    f"看空{s.bearish:.2f} 中性{s.neutral:.2f}"
                )

    # Hidden variables
    if result.hidden_variables:
        parts.append("隐性变量:")
        for hv in result.hidden_variables:
            parts.append(
                f"  - {hv.variable} (概率{hv.probability:.0%}): "
                f"{hv.reasoning}"
            )

    # Inflection points
    if result.key_inflection_points:
        parts.append("关键拐点:")
        for ip in result.key_inflection_points:
            parts.append(f"  Day {ip.day}: {ip.event}")

    # Extreme scenarios
    if result.extreme_scenarios:
        parts.append("极端场景:")
        for es in result.extreme_scenarios:
            parts.append(
                f"  - {es.scenario} (概率{es.probability:.0%}, "
                f"影响{es.impact})"
            )

    # Recommended action
    parts.append(f"综合建议: {result.recommended_action}")

    # Meta
    parts.append(
        f"(仿真耗时{result.duration_seconds:.1f}s, "
        f"成本{result.cost_rmb:.2f}元)"
    )

    return "\n".join(parts)
