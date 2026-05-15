"""FundManagerOutput — strict LLM-only contract that feeds the Builder.

Why this exists separately from the legacy
:class:`backend.agents.records.FundManagerRecord` /
:class:`backend.agents.models.TradingSignal`:

The legacy records carry ``target_price`` and ``confidence`` /
``risk_score`` floats produced by the LLM. P0-10 §1 explicitly limits
the LLM-writable surface to four fields:

* ``InstructionPlan.reasoning``-equivalent text
* ``evidence_collection.content``
* ``agent_debate_records.{reasoning_text, conclusion}``
* ``risk_parameter_proposals.proposal_text``

``FundManagerOutput`` is the bridge model the Builder consumes — it
exposes ONLY the LLM-writable fields (``side`` + ``proposal_text``).
Numeric quantities (volume, limit_price, valid_until, risk_summary,
status) are computed by the Builder from non-LLM inputs (account
snapshot, market metadata, RiskEngine, etc.).

Hard schema gate (locked):

* ``model_config = ConfigDict(frozen=True, strict=True, extra='forbid')``
* No numeric "decision" fields exist on the schema, so a future
  refactor that tries to pass ``volume`` / ``limit_price`` here would
  fail validation at import time — that is the lint half of the
  P0-10 schema/lint dual gate (CLAUDE.md §2.2).
* ``proposal_text`` is bounded (1-4096 chars). LLM cannot smuggle
  arbitrary structured payloads via gigantic free-text blobs.

Conversion from legacy ``FundManagerRecord``:

The Phase D runtime wiring (out of D-004 scope) is responsible for
calling :func:`from_fund_manager_record` to drop the legacy advisory
floats. Tests in this task exercise the conversion directly so the
contract is enforceable today even without the full pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.instruction import InstructionSide

_ACTION_TO_SIDE: dict[str, InstructionSide] = {
    "买入": InstructionSide.BUY,
    "持有": InstructionSide.HOLD,
    "卖出": InstructionSide.SELL,
}


class FundManagerOutput(BaseModel):
    """LLM-only contract surfaced from the fund_manager agent.

    Frozen + strict + ``extra='forbid'`` per P0-3 §2 redline 12 and
    P0-10 §2 redline 1 (LLM-writable surface lock).

    Attributes:
        side: BUY / SELL / HOLD recommendation. The Builder fans this
            out: HOLD goes to the ledger as a no-trade; BUY/SELL feeds
            the 14-check + InstructionPlan assembly.
        proposal_text: the LLM's short rationale (1-4096 chars). This
            is the *only* free-text field the LLM owns on this
            contract; longer narratives belong on the debate record.
        parse_ok: False iff the LLM response failed to parse and the
            agent emitted a synthetic placeholder; the Builder treats
            ``parse_ok=False`` as a forced HOLD per P0-3 §2 redline 6.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    side: InstructionSide
    proposal_text: str = Field(min_length=1, max_length=4096)
    parse_ok: bool = True


def from_fund_manager_record(
    *,
    action: Literal["买入", "持有", "卖出"],
    reasoning: str,
    parse_ok: bool = True,
) -> FundManagerOutput:
    """Build a :class:`FundManagerOutput` from the legacy record fields.

    Why:
        The current pipeline emits :class:`FundManagerRecord` /
        :class:`TradingSignal` (Chinese ``action`` enum, advisory
        ``target_price`` + ``confidence`` floats). The runtime
        integration in a follow-up task will call this helper to drop
        the LLM-advisory numbers and surface only the four
        LLM-writable fields onto the strict schema.

    Args:
        action: legacy ``"买入" / "持有" / "卖出"`` action string. Any
            other value raises ``ValueError`` because the InstructionSide
            enum is locked to BUY / SELL / HOLD.
        reasoning: legacy free-text field. Empty → ValueError because
            ``proposal_text`` requires ``min_length=1``; the caller
            should detect synthetic placeholders upstream and pass
            ``parse_ok=False`` rather than blank text.
        parse_ok: forwarded to the output; False forces the Builder
            into the HOLD path even if ``action`` says BUY/SELL.
    """

    side = _ACTION_TO_SIDE.get(action)
    if side is None:
        raise ValueError(
            f"unknown legacy action {action!r}; "
            f"expected one of {sorted(_ACTION_TO_SIDE)}"
        )
    return FundManagerOutput(
        side=side,
        proposal_text=reasoning or "(no reasoning supplied)",
        parse_ok=parse_ok,
    )


__all__ = [
    "FundManagerOutput",
    "from_fund_manager_record",
]
