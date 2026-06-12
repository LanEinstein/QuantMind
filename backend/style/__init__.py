"""backend.style — deterministic position-style classification (Phase AC).

Pure, import-isolated (0 LLM) style labelling: ``SHORT_TERM`` vs ``VALUE``.
The label is display-only + soft-layer-only; hard risk numbers are style-
invariant (AC-006). See ``backend/style/CLAUDE.md``.
"""

from __future__ import annotations

from backend.style.classifier import STYLE_FEATURE_CODE_VERSION, classify_style
from backend.style.models import (
    StyleClassification,
    StyleClassifierConfig,
    StyleInputs,
    StyleTag,
)

__all__ = [
    "STYLE_FEATURE_CODE_VERSION",
    "StyleClassification",
    "StyleClassifierConfig",
    "StyleInputs",
    "StyleTag",
    "classify_style",
]
