"""``risk_parameter_proposals`` collection schema (P0-7 §1.4 + X-012).

LLM-writable proposal ledger for the four LLM positive-list fields
(P0-10 §1.1 — ``proposal_text`` only). After
``P0-7-amendment-2026-05-11-risk-proposals-shadow-validation`` the
record flows through the P2-2 self-evolution chain (BrokerScheduler
5th cron → shadow validate → amendment_drafter → Feishu) rather than
the original weekly-review path. The amendment locks four new fields
on top of the P0-7 §1.4 baseline; default values keep the schema
backwards-compatible with historical records (Mongo "field missing"
documents deserialise into the defaults).

Module isolation: lives under ``backend.models`` instead of
``backend.risk`` so the Phase X evolution dispatcher
(``backend/services/evolution_dispatcher.py``) can import the model
without breaching the P2-2 §2 red line 17 import gate (Phase X
modules may not import ``backend.{api, broker, risk, llm, agents,
mirofish, data}``).

Locked LLM negative-list invariants (CLAUDE.md §2.2 / P0-10 §1.1):

* ``target_field`` / ``target_artifact_type`` are NEVER LLM-written —
  code chooses them when building the proposal.
* ``shadow_validation_status`` is written exclusively by the
  ``evolution_shadow_run`` cron (X-008 dispatcher).
* ``pending_amendment_id`` is written exclusively by the
  ``amendment_drafter`` (X-013).
* ``feishu_notified_at`` is written exclusively by the
  ``evolution_feishu_notifier`` (X-014).
* ``accepted`` / ``accepted_at`` / ``accepted_by`` are written only by
  the human-review handler — the LLM cannot self-promote.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

PROPOSAL_ID_RE = re.compile(r"^RPP-\d{8}-\d{6}-\d{6}-\d{3}$")
"""``RPP-{YYYYMMDD}-{HHMMSS}-{microseconds}-{seq}`` — monotonically
increasing. Mirrors the P0-3 ``instruction_id`` shape so audit + UI
filters share the same parser."""

TARGET_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "risk_config",
        "position_limits",
        "circuit_breaker",
        "watchlist_policy",
        "broker_config",
    }
)
"""Five discriminator values locked by
``P0-7-amendment-2026-05-11-risk-proposals-shadow-validation``. The
field is set by code (not the LLM) so a proposal cannot self-upgrade
across categories."""

SHADOW_VALIDATION_STATES: frozenset[str] = frozenset(
    {"pending", "running", "passed", "failed", "promoted", "rejected"}
)
"""Six-state machine the P2-2 cron drives. ``pending`` is the default
for new proposals; ``promoted``/``rejected`` are terminal."""

PENDING_AMENDMENT_ID_RE = re.compile(r"^pending/[A-Za-z0-9._-]+\.md$")
"""Repo-relative path under ``docs/decisions/pending/`` produced by the
``amendment_drafter`` (X-013). Locked here so a stray LLM-written
string cannot smuggle a different folder past the schema."""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class RiskParameterProposal(BaseModel):
    """One proposal row in ``risk_parameter_proposals``.

    Frozen + strict + ``extra='forbid'`` (P0-3 §2 red line 12). The 4
    P2-2 amendment fields default to non-LLM-controlled values so the
    schema accepts pre-amendment records on read without rewrite.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # === P0-7 §1.4 baseline (unchanged) ============================
    proposal_id: str = Field(pattern=PROPOSAL_ID_RE.pattern)
    proposed_by: Literal["fund_manager", "risk_officer"]
    proposal_text: str = Field(min_length=1, max_length=5_000)
    """LLM positive-list field (P0-10 §1.1). The Builder writes it from
    the agent's reasoning output; the strict ``min_length=1`` keeps an
    LLM that "decided not to write anything" from polluting the ledger."""

    target_field: str = Field(min_length=1, max_length=160)
    proposed_value: float | int | str
    current_value: float | int | str
    evidence_collection_ids: tuple[str, ...] = Field(
        default_factory=tuple, max_length=64
    )
    proposed_at: datetime
    accepted: bool = False
    accepted_at: datetime | None = None
    accepted_by: str | None = Field(default=None, max_length=128)

    # === P2-2 amendment (4 new fields, non-destructive) ============
    target_artifact_type: Literal[
        "risk_config",
        "position_limits",
        "circuit_breaker",
        "watchlist_policy",
        "broker_config",
    ] = "risk_config"
    """Discriminator the X-008 dispatcher routes on. Code-written;
    LLM red line (CLAUDE.md §2.2)."""

    shadow_validation_status: Literal[
        "pending", "running", "passed", "failed", "promoted", "rejected"
    ] = "pending"
    """P2-2 cron-controlled state. LLM may never write this."""

    pending_amendment_id: str | None = Field(default=None, max_length=256)
    """``pending/<proposal_id>.md`` written by ``amendment_drafter``
    after shadow validate passes. ``None`` until X-013 fires."""

    feishu_notified_at: datetime | None = None
    """Stamp filled by ``evolution_feishu_notifier`` when the operator
    is paged. ``None`` until X-014 fires."""

    @model_validator(mode="after")
    def _check_invariants(self) -> RiskParameterProposal:
        if not PROPOSAL_ID_RE.fullmatch(self.proposal_id):
            raise ValueError(
                f"proposal_id must match {PROPOSAL_ID_RE.pattern!r}, "
                f"got {self.proposal_id!r}"
            )
        if (
            self.pending_amendment_id is not None
            and not PENDING_AMENDMENT_ID_RE.fullmatch(self.pending_amendment_id)
        ):
            raise ValueError(
                f"pending_amendment_id must match "
                f"{PENDING_AMENDMENT_ID_RE.pattern!r}, got "
                f"{self.pending_amendment_id!r}"
            )
        if self.accepted and self.accepted_at is None:
            raise ValueError(
                "accepted=True requires accepted_at to be set (human-review "
                "handler must stamp both atomically)"
            )
        # accepted_at acts as the review stamp. It is valid when either:
        #   * accepted=True (promotion), or
        #   * shadow_validation_status='rejected' (rejection review).
        # Setting accepted_at without one of those signals is inconsistent
        # — surface the bug instead of silently saving (codex review P2-4).
        if (
            self.accepted_at is not None
            and not self.accepted
            and self.shadow_validation_status != "rejected"
        ):
            raise ValueError(
                "accepted_at present but accepted=False and status != "
                "'rejected' — accepted_at is the human-review stamp; set "
                "accepted=True for promotion or status='rejected' for "
                "rejection"
            )
        # promoted / rejected terminal states require a review stamp.
        if self.shadow_validation_status in {"promoted", "rejected"}:
            if not self.accepted_at:
                raise ValueError(
                    f"shadow_validation_status={self.shadow_validation_status} "
                    "is a terminal state and requires accepted_at"
                )
        # ``promoted`` additionally implies accepted=True (the human
        # signed off to promote the artifact); a rejected terminal
        # state stays accepted=False (the human reviewed and refused).
        if self.shadow_validation_status == "promoted" and not self.accepted:
            raise ValueError(
                "shadow_validation_status='promoted' implies accepted=True"
            )
        if self.shadow_validation_status == "rejected" and self.accepted:
            raise ValueError(
                "shadow_validation_status='rejected' is incompatible with "
                "accepted=True"
            )
        return self


__all__ = [
    "PENDING_AMENDMENT_ID_RE",
    "PROPOSAL_ID_RE",
    "RiskParameterProposal",
    "SHADOW_VALIDATION_STATES",
    "TARGET_ARTIFACT_TYPES",
]
