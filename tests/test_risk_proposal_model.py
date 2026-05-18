"""X-012 — RiskParameterProposal schema unit tests.

Covers the P0-7 baseline + the 4 P2-2 amendment fields, the
default-value backwards compatibility, and the LLM-red-line
discriminator/terminal-state guards.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.models.risk_proposal import (
    PENDING_AMENDMENT_ID_RE,
    PROPOSAL_ID_RE,
    SHADOW_VALIDATION_STATES,
    TARGET_ARTIFACT_TYPES,
    RiskParameterProposal,
)

_PROPOSED_AT = datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
_PROPOSAL_ID = "RPP-20260518-140000-000001-001"


def _baseline_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "proposal_id": _PROPOSAL_ID,
        "proposed_by": "fund_manager",
        "proposal_text": "Suggest tightening single-stock cap to 12%",
        "target_field": "PositionLimitsConfig.max_single_stock_pct",
        "proposed_value": 0.12,
        "current_value": 0.15,
        "evidence_collection_ids": ("RISK-001", "MARKET-002"),
        "proposed_at": _PROPOSED_AT,
    }
    base.update(overrides)
    return base


class TestBaseline:
    def test_proposal_id_pattern_locked(self) -> None:
        assert PROPOSAL_ID_RE.fullmatch(_PROPOSAL_ID)

    def test_minimal_record_accepts_defaults(self) -> None:
        rp = RiskParameterProposal(**_baseline_kwargs())
        assert rp.target_artifact_type == "risk_config"
        assert rp.shadow_validation_status == "pending"
        assert rp.pending_amendment_id is None
        assert rp.feishu_notified_at is None
        assert rp.accepted is False

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(unknown_field="x")
            )

    def test_frozen_setattr_blocked(self) -> None:
        rp = RiskParameterProposal(**_baseline_kwargs())
        with pytest.raises(ValidationError):
            rp.proposal_text = "hacked"  # type: ignore[misc]

    def test_proposal_id_bad_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(proposal_id="RPP-bad-shape")
            )

    def test_proposal_text_minimum_length(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(**_baseline_kwargs(proposal_text=""))

    def test_proposed_by_constrained(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(proposed_by="technical_analyst")
            )


class TestP22AmendmentFields:
    def test_target_artifact_type_lock(self) -> None:
        for value in TARGET_ARTIFACT_TYPES:
            rp = RiskParameterProposal(
                **_baseline_kwargs(target_artifact_type=value)
            )
            assert rp.target_artifact_type == value

    def test_unknown_target_artifact_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(target_artifact_type="custom_thing")
            )

    def test_shadow_validation_state_lock(self) -> None:
        # passed / running / failed do not require accepted_at
        rp = RiskParameterProposal(
            **_baseline_kwargs(shadow_validation_status="passed")
        )
        assert rp.shadow_validation_status == "passed"

    def test_unknown_shadow_state_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(shadow_validation_status="weird")
            )

    def test_shadow_validation_states_constant_matches_literal(self) -> None:
        # Defensive: schema literal and the exported constant must agree.
        assert SHADOW_VALIDATION_STATES == {
            "pending",
            "running",
            "passed",
            "failed",
            "promoted",
            "rejected",
        }

    def test_pending_amendment_id_pattern(self) -> None:
        rp = RiskParameterProposal(
            **_baseline_kwargs(
                pending_amendment_id=f"pending/{_PROPOSAL_ID}.md"
            )
        )
        assert rp.pending_amendment_id == f"pending/{_PROPOSAL_ID}.md"
        assert PENDING_AMENDMENT_ID_RE.fullmatch(rp.pending_amendment_id)

    def test_pending_amendment_id_wrong_folder_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(
                    pending_amendment_id="staging/foo.md",
                )
            )

    def test_feishu_notified_at_optional(self) -> None:
        rp = RiskParameterProposal(
            **_baseline_kwargs(feishu_notified_at=_PROPOSED_AT)
        )
        assert rp.feishu_notified_at == _PROPOSED_AT


class TestTerminalStates:
    def test_promoted_requires_accepted_at(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(shadow_validation_status="promoted")
            )

    def test_rejected_requires_accepted_at(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(shadow_validation_status="rejected")
            )

    def test_promoted_with_accepted_at_ok(self) -> None:
        rp = RiskParameterProposal(
            **_baseline_kwargs(
                shadow_validation_status="promoted",
                accepted=True,
                accepted_at=_PROPOSED_AT,
                accepted_by="owner",
            )
        )
        assert rp.shadow_validation_status == "promoted"

    def test_accepted_true_requires_accepted_at(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(accepted=True)
            )

    def test_accepted_at_without_accepted_flag_rejected(self) -> None:
        # accepted_at without accepted=True and status != 'rejected'
        # is inconsistent.
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(accepted_at=_PROPOSED_AT)
            )

    def test_rejected_terminal_keeps_accepted_false(self) -> None:
        # Codex review P2-4 regression: rejected state requires
        # accepted_at but must NOT force accepted=True.
        rp = RiskParameterProposal(
            **_baseline_kwargs(
                shadow_validation_status="rejected",
                accepted=False,
                accepted_at=_PROPOSED_AT,
                accepted_by="owner",
            )
        )
        assert rp.shadow_validation_status == "rejected"
        assert rp.accepted is False
        assert rp.accepted_at == _PROPOSED_AT

    def test_rejected_with_accepted_true_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(
                    shadow_validation_status="rejected",
                    accepted=True,
                    accepted_at=_PROPOSED_AT,
                    accepted_by="owner",
                )
            )

    def test_promoted_with_accepted_false_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParameterProposal(
                **_baseline_kwargs(
                    shadow_validation_status="promoted",
                    accepted=False,
                    accepted_at=_PROPOSED_AT,
                )
            )
