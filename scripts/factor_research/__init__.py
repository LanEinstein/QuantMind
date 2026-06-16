"""Offline factor-strategy research package (2026-06-16 special project).

This package is **offline research only**. It derives and validates A-share
equity-selection factor strategies on real PIT history; it never touches the
live ``simulation_auto`` trading path and never wires anything into the live
``FACTOR_WEIGHTS`` (that is owner-gated — amendment + LiveArtifactRegistry +
45-day forward shadow + manual pin + restart).

Governing docs:
* Task book   — ``docs/research/factor-strategy-research-brief-2026-06-16.md``
* Phase 1 survey — ``docs/research/factor-theory-survey-2026-06-16.md``

Import isolation (mirrors the ``backend.screening`` Line-1 closure): these
modules MUST NOT import ``backend.{llm,agents,mirofish}``. They MAY import the
pure quant infra they reuse — ``backend.marketdata_snapshot`` (PIT snapshots),
``backend.backtest`` (the deterministic engine), ``backend.strategy_evolution``
(DSR / PBO / SPA / Sobol / sentinel) — and ``backend.data.historical_ingest``
(snapshot parsing + qfq view) via a per-line ``# noqa: TID251`` as the screener
does. ``factor_lib`` itself imports nothing from ``backend`` (pure stdlib maths).
"""
