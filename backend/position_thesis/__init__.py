"""backend.position_thesis — deterministic PositionThesis derivation + store.

Pure, import-isolated quant module (Phase W). It derives the machine-checkable
invalidation thresholds from the buy-time snapshot (no LLM), persists the thesis
append-only, and evaluates thesis health over PIT data for the Line-2
``THESIS_QUANT_BREAK`` trigger. The LLM pillar text it carries is opaque to the
derivation maths — see :mod:`backend.position_thesis.derivation`.
"""

from __future__ import annotations

from backend.position_thesis.config import (
    FEATURE_CODE_VERSION,
    ThesisDerivationConfig,
)
from backend.position_thesis.derivation import (
    ThesisDerivationError,
    ThesisEntrySnapshot,
    build_position_thesis,
    derive_invalidation_conditions,
)
from backend.position_thesis.evaluation import (
    ThesisHealthResult,
    ThesisObservation,
    evaluate_condition,
    evaluate_thesis_health,
)
from backend.position_thesis.store import (
    PositionThesisError,
    PositionThesisStore,
    ThesisEventType,
)

__all__ = [
    "FEATURE_CODE_VERSION",
    "PositionThesisError",
    "PositionThesisStore",
    "ThesisDerivationConfig",
    "ThesisDerivationError",
    "ThesisEntrySnapshot",
    "ThesisEventType",
    "ThesisHealthResult",
    "ThesisObservation",
    "build_position_thesis",
    "derive_invalidation_conditions",
    "evaluate_condition",
    "evaluate_thesis_health",
]
