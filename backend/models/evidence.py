"""Evidence ID model and 5-prefix validation (P0-8 §1.6.2).

`InstructionPlan.evidence_ids` is a by-reference tuple linking back to
MongoDB evidence collections. P0-8 §2 red line 14 locks the prefix set to
exactly five values: `NEWS` / `MIROFISH` / `MARKET` / `RISK` / `DEBATE`.
Adding a sixth prefix requires a `P0-8-amendment-*.md` before any code
change.

The regex is exported so the frontend JS mirror (B-003 / P1-5 §2 red
line 5) and the redline-check.sh static guard can reuse a single source
of truth — backend lint, scripts, and frontend cannot drift.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidencePrefix(StrEnum):
    """Allowed evidence_id prefixes (P0-8 §1.6.2).

    The string values are the literal text that must appear before the
    first dash in any `evidence_id`. Order is documented for stability —
    do not rename or reorder; the redline-check.sh scanner relies on the
    pattern string below to be exhaustive.
    """

    NEWS = "NEWS"
    MIROFISH = "MIROFISH"
    MARKET = "MARKET"
    RISK = "RISK"
    DEBATE = "DEBATE"


EVIDENCE_PREFIXES: tuple[str, ...] = tuple(p.value for p in EvidencePrefix)
"""Tuple form of the allowed prefixes, used by lint/redline checks."""

EVIDENCE_ID_PATTERN: str = (
    r"^(NEWS|MIROFISH|MARKET|RISK|DEBATE)-[A-Za-z0-9_:.\-]{1,128}$"
)
"""Single source of truth regex for `evidence_id` strings.

- Prefix: one of the five locked values (P0-8 §1.6.2).
- Separator: single literal `-`.
- Suffix: 1-128 chars of ASCII alphanumerics + `_` + `:` + `.` + `-`
  to accommodate ISO timestamps (`MARKET-600519-2026-05-12T09:30:00`)
  and round labels (`DEBATE-run123-r3`).
- Total length capped to keep MongoDB index size predictable.
"""

_EVIDENCE_ID_RE = re.compile(EVIDENCE_ID_PATTERN)


def validate_evidence_id(evidence_id: str) -> None:
    """Raise ``ValueError`` if ``evidence_id`` violates the locked format.

    Pure function — Pydantic models call this from their validators, and
    the redline-check.sh / lint hooks call it to enforce the rule at
    write-time across the codebase.
    """
    if not _EVIDENCE_ID_RE.fullmatch(evidence_id):
        raise ValueError(
            f"evidence_id {evidence_id!r} violates P0-8 §1.6.2 — "
            f"must match {EVIDENCE_ID_PATTERN}"
        )


def parse_evidence_prefix(evidence_id: str) -> EvidencePrefix:
    """Return the :class:`EvidencePrefix` for a valid ``evidence_id``.

    Validates the full id before parsing; an invalid id raises
    ``ValueError``. Pure function — callers (frontend filters / audit
    queries) get the prefix without re-implementing the regex.
    """
    validate_evidence_id(evidence_id)
    head = evidence_id.split("-", 1)[0]
    return EvidencePrefix(head)


class EvidenceId(BaseModel):
    """Frozen wrapper around a validated evidence_id string.

    Carries the parsed :class:`EvidencePrefix` alongside the raw text so
    callers can switch on prefix without redoing the regex. Use this
    type whenever a single id needs to flow through the system; for
    collections embedded in an :class:`InstructionPlan`, prefer the raw
    ``tuple[str, ...]`` form so MongoDB serialization stays trivial.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    value: str = Field(min_length=1, max_length=160)
    prefix: EvidencePrefix | None = None

    @model_validator(mode="after")
    def _check_and_assign_prefix(self) -> EvidenceId:
        validate_evidence_id(self.value)
        head = self.value.split("-", 1)[0]
        expected = EvidencePrefix(head)
        if self.prefix is not None and self.prefix is not expected:
            raise ValueError(
                f"prefix {self.prefix!r} does not match value prefix {expected!r}"
            )
        # Frozen models cannot reassign attributes via normal setattr — use
        # `object.__setattr__` to write the derived field during validation.
        if self.prefix is None:
            object.__setattr__(self, "prefix", expected)
        return self
