"""Prompt artifacts as POLICY artifacts (AB-006 / P2-2-amendment §1.5;
codex P0-3).

Prompt evolution is the indirect LLM-autonomy leak surface: a "wording
improvement" that smuggles imperative/decision language into an agent
prompt would steer live behaviour without ever touching a decision
field. Three cages, all deterministic:

1. **Forbidden-class lint** — order verbs with quantities, instruction
   id/plan vocabulary, risk-bypass phrasing, prompt-injection markers.
   A linted-out candidate never becomes an experiment.
2. **Byte capture** — every shadow-phase LLM request/response is
   captured (sha256 + lengths, mirroring the theme provenance
   discipline); a candidate with ANY uncaptured call is non-promotable.
3. **Frozen skeleton** — the SOP/persona section skeleton must survive
   the variant verbatim and in order (wording inside sections is the
   evolvable part).

All three feed the SAME objective promotion gate (AB-002) — prompts
get no private path.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="strategy_evolution.prompt_policy")

FORBIDDEN_PROMPT_PATTERNS: tuple[tuple[str, str], ...] = (
    # Direct order language with quantities — a prompt may DISCUSS buy
    # and sell concepts, but never issue sized orders.
    ("order_verb_with_size", r"(买入|卖出|加仓|清仓|建仓)\s*\d"),
    ("order_verb_with_size_en", r"\b(BUY|SELL)\b\s+\d"),
    # Instruction-plan vocabulary (single-construction-point red line).
    ("instruction_id_literal", r"QM-\d{8}-\d{6}"),
    ("instruction_plan_call", r"InstructionPlan\s*\("),
    ("order_field_assignment", r"(limit_price|volume|side)\s*[:=]\s*[\"']?\d"),
    # Risk-bypass phrasing.
    ("risk_bypass_zh", r"(跳过|绕过|忽略)\s*(风控|风险检查|RiskEngine|14)"),
    ("risk_bypass_en", r"\b(bypass|skip|ignore)\b.{0,24}\b(risk|riskengine)\b"),
    # Prompt-injection markers.
    ("injection_en", r"ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    ("injection_zh", r"忽略(之前|上述|以上)(的)?(全部|所有)?(指令|提示|要求)"),
    # Direct-execution imperatives.
    ("direct_execution", r"(立即执行|直接下单|自动下单|execute\s+the\s+order)"),
)
"""v1 deny-list (deterministic regex, case-insensitive). Extending the
list is a code change — never an evolvable artifact itself."""


class PromptLintViolation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    rule: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(max_length=120)


class PromptLintResult(BaseModel):
    """Deterministic lint verdict for one prompt artifact."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    passed: bool
    violations: tuple[PromptLintViolation, ...] = Field(
        default_factory=tuple
    )


def lint_prompt_artifact(text: str) -> PromptLintResult:
    """Reject forbidden instruction/decision classes (pure)."""
    violations: list[PromptLintViolation] = []
    for rule, pattern in FORBIDDEN_PROMPT_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - 20)
            violations.append(
                PromptLintViolation(
                    rule=rule,
                    excerpt=text[start : match.end() + 20][:120],
                )
            )
    return PromptLintResult(
        passed=not violations, violations=tuple(violations)
    )


class PromptByteCapture(BaseModel):
    """One captured shadow-phase LLM exchange (theme-provenance style)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    call_index: int = Field(ge=0)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_bytes: int = Field(ge=1)
    response_bytes: int = Field(ge=1)
    captured_at: datetime


def capture_exchange(
    *,
    call_index: int,
    request_payload: bytes,
    response_payload: bytes,
    captured_at: datetime,
) -> PromptByteCapture:
    """Build the capture record from RAW bytes (never re-serialised)."""
    return PromptByteCapture(
        call_index=call_index,
        request_sha256=hashlib.sha256(request_payload).hexdigest(),
        response_sha256=hashlib.sha256(response_payload).hexdigest(),
        request_bytes=len(request_payload),
        response_bytes=len(response_payload),
        captured_at=captured_at,
    )


def is_capture_complete(
    *,
    expected_calls: int,
    captures: Sequence[PromptByteCapture],
) -> bool:
    """Promotability precondition: EVERY shadow call captured.

    Fail-closed: zero expected calls is itself non-promotable (a prompt
    variant whose shadow made no LLM call was never exercised), and the
    capture set must cover call indices 0..expected_calls-1 exactly.
    """
    if expected_calls <= 0:
        return False
    indices = sorted(c.call_index for c in captures)
    return indices == list(range(expected_calls))


def verify_skeleton_sections(
    frozen_sections: Sequence[str], candidate_text: str
) -> bool:
    """Frozen skeleton check: all section markers present, in order."""
    cursor = 0
    for section in frozen_sections:
        found = candidate_text.find(section, cursor)
        if found < 0:
            return False
        cursor = found + len(section)
    return True


__all__ = [
    "FORBIDDEN_PROMPT_PATTERNS",
    "PromptByteCapture",
    "PromptLintResult",
    "PromptLintViolation",
    "capture_exchange",
    "is_capture_complete",
    "lint_prompt_artifact",
    "verify_skeleton_sections",
]
