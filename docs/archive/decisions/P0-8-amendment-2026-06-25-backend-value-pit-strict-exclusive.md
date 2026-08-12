# P0-8 Amendment (2026-06-25) — Backend value/quality PIT cutoff → strict-exclusive (M2)

> **Status**: proposed → implementing (production-hardening audit pass, Batch 6).
> **Amends**: §2.0 new red line ① (PIT 数据可复现 + 无前视 / no look-ahead). Safety-base
> redlines unchanged (official Tushare SDK only, no LLM in the data path, PIT
> byte-archive, value sleeve remains env-OFF / dormant).
> **Driver**: the 2026-06-25 production-hardening audit (M2) found the backend
> point-in-time fundamentals cutoff is **inclusive** (`ann_date <= as_of`) while
> the research PIT is **strictly exclusive** (`ann_date < decision`,
> `scripts/factor_research/fundamentals_pit.py:206`). The two disagree on a
> report announced **on the decision date itself** — the backend would consume
> it (a same-day look-ahead the research arena rejects).

## 1. Decision

### 1.1 Align the backend value/quality PIT cutoff to strict-exclusive (boundary change)
`backend/screening/value_factors.py::pit_fundamentals_value` changes its keep
predicate from "announced **on or before** `as_of_date`" to "announced
**strictly before** `as_of_date`":

```
# before:  if announce_date >  as_of_date: continue   # keeps ann_date == as_of
# after:   if announce_date >= as_of_date: continue   # drops ann_date == as_of
```

`backend/quality_fundamentals/quality.py::quality_pit_values` (the only
consumer, AF-003) inherits the stricter cutoff; its docstring is corrected to
"strictly before".

### 1.2 Rationale
- **Enforces the §2.0 no-look-ahead red line.** A financial report whose
  `ann_date` equals the decision date may have been published *after* the
  09:35 Line-1 decision (or not yet priced by the market). Consuming it is a
  same-day information leak — exactly the leakage the strict-before research
  convention exists to prevent. Inclusive was the weaker (leak-permitting)
  choice; strict-exclusive is the correct PIT discipline.
- **Single PIT standard across research and runtime.** The §2.0 red line
  requires backtest / shadow / live to be same-source and reproducible. A
  research factor validated under `ann_date < d` that silently became
  `ann_date <= d` once wired into the backend value sleeve would not replay
  bit-identically against the research arena — a provenance break.
- **Conservative direction.** Strict-exclusive can only *drop* a same-day
  vintage (fall back to the prior announced value or `None`); it can never
  fabricate a value. Fail-safe.

### 1.3 Scope / blast radius
- The **value sleeve is dormant** (`enabled: false`, env-OFF; see
  `value-sleeve-amendment-2026-06-22`). There is **no live behavioural change**
  today — `value_score` providers return `None`/unchanged until owner-gated
  activation. This amendment aligns the standard for when it activates.
- No change to the research code (already strict-exclusive). No change to the
  exclusion four-piece / screening / RiskEngine paths. No governance enum, no
  new write endpoint, no LLM path touched.

## 2. Verification
- A boundary regression test asserts a record announced **on** `as_of_date` is
  excluded (falls back to the prior vintage), pinning the strict-before contract.
- Existing `pit_fundamentals_value` / AF-003 tests stay green (their fixtures
  have no `ann_date == as_of` row, so the cross-section is unchanged).
- Full `pytest` + `ruff` + `scripts/redline-check.sh` green before commit.
