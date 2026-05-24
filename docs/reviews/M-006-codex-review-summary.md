# M-006 Codex Review Summary — 飞书 BUY 4 模板 + 候选/辩论模块 CLAUDE.md

> Task: M-006 `飞书 BUY 4 模板 + 候选/辩论模块 CLAUDE.md`. Four visually-distinct
> BUY-signal templates (budget-tier outcomes) rendered ONLY through
> `MessageRenderer` (single source of truth; no LLM composes wire text), the
> ETF concentration-exception confirmation flow embeds a regex-validated
> instruction_id, and the candidate_selector / agents_team sub-module CLAUDE.md
> are flipped to done. Scope: `backend/integrations/feishu/renderer.py`,
> `tests/test_feishu_buy_signal.py` (new), `backend/candidate_selector/CLAUDE.md`,
> `backend/agents_team/CLAUDE.md`.

## What M-006 adds

- `renderer.BuySignalTemplate` (4 outcomes: NORMAL_COMPLIANT /
  ETF_CONCENTRATION_EXCEPTION / NO_COMPLIANT_TRADE / PAPER_ONLY).
- `render_buy_signal(plan, *, template)` for the 3 order-bearing templates —
  distinct header + banner over the shared 7-section dispatch body (extracted
  into `_dispatch_body_lines`, reused by the existing `render_instruction_plan`
  so its golden snapshot is unchanged). ETF exception appends a confirmation
  block embedding the instruction_id, re-validated against `^QM-…` (the classic
  leakage / injection point). BUY-only + VALIDATED/DISPATCHED guards.
- `render_no_compliant_trade(...)` — the no-order NO_COMPLIANT_TRADE outcome,
  with newline-injection sanitisation (`_single_line`) so operator-influenced
  text can never forge a 【QuantMind …】 header.
- candidate_selector / agents_team CLAUDE.md status → done.

## Local gate (pre-codex)

- `pytest tests/test_feishu_buy_signal.py` → 10 passed; existing
  `tests/test_feishu_renderer.py` 30 passed (refactor output-preserving).
- Full suite → 3495 passed / 11 skipped.
- `ruff` clean; `scripts/redline-check.sh` → all green. Module isolation for
  candidate_selector / agents_team is covered by their existing AST contract
  tests ([L-002] redline green).

## Cycle 1 — `codex review --uncommitted`

**1 finding, P1 (no P0).**

- **[P1] Normalize template IDs before checking ETF confirmation** —
  `renderer.py`. `BuySignalTemplate` is a `StrEnum`, so a raw-string template
  id (e.g. `"etf_concentration_exception"` arriving from JSON/config) passes
  both the `_PLAN_BACKED_BUY_TEMPLATES` membership check and the
  `_BUY_SIGNAL_HEADERS` lookup by string equality — but the later
  `if template is BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION` identity check
  is `False`. The rendered ETF message then has the “需确认” header/banner but
  **omits the mandatory confirmation reply block**, defeating the
  explicit-confirmation safeguard.

  **Fix:** coerce `template = BuySignalTemplate(template)` as the first line of
  `render_buy_signal` (raises `ValueError` on an invalid id = fail-closed), so
  a raw string normalises to the enum and every downstream check — including
  the `is` identity check — behaves correctly. Added 3 regressions (raw-string
  ETF id → confirmation block present; raw-string normal id coerced; invalid id
  → ValueError). Re-ran: 43 renderer/buy-signal tests pass; ruff clean.

## Cycle 2 — `codex exec --sandbox read-only` (verify)

**Verdict: PASS** — `render_buy_signal` coerces `template =
BuySignalTemplate(template)` before the membership check, header lookup, and
the ETF identity check, so raw `"etf_concentration_exception"` renders the
mandatory confirmation block; raw `"normal_compliant"` / `"paper_only"` still
render; invalid strings raise `ValueError`; both enum and string
`NO_COMPLIANT_TRADE` raise the intended `use render_no_compliant_trade` error.
0 regressions.
