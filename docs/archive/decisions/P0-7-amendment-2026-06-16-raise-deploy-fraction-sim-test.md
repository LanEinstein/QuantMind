# P0-7-amendment-2026-06-16 — Raise allocation `deploy_fraction` 0.33 → 0.6 for the simulation profitability test

**Status:** accepted (owner, 2026-06-16)
**Adjusts:** `P0-7-amendment-2026-05-30-portfolio-allocation.md` (which set `deploy_fraction=0.33`).
**Scope:** `config/allocation_policy.yaml` `allocation.deploy_fraction` only (0.33 → 0.6). No red line touched — single-stock ≤15% / total ≤70% / single-instr ≤¥5万 / 100-share lot / inverse-vol method all unchanged; the RiskEngine 14-check stays independently authoritative (the allocation clamp only *tightens* orders).

## Context — why

In the `simulation_auto` ¥30k profitability test (see [[project-sim-auto-profitability-test-2026-06-16]]), `deploy_fraction=0.33` deploys only ~⅓ of available cash per day. The ¥30k preview (dry-run) deployed just **1 lot of 1 name (¥1,815 ≈ 6% of the account)** on day 1 — the rest cash. With the account ~94% cash, the equity curve is **cash-dragged toward flat and the HS300 excess metric becomes meaningless**, so stock-picking profitability cannot be measured cleanly in the early window.

## Decision

`config/allocation_policy.yaml` `deploy_fraction`: **0.33 → 0.6**.

`deployable_cash = min(cash × deploy_fraction, cash − cash_buffer_pct × total_assets)`. At ¥30k: ¥9,900 → **¥18,000** daily tranche.

**Honest effect (not over-sold):** `deploy_fraction` governs the per-DAY deployment *speed*, not the ceiling. The steady-state remains bounded by `per_name_target_pct=0.10` (¥3,000/name at ¥30k) + the 100-share lot + the per-stock 15% / total 70% RiskEngine caps. So raising it to 0.6 mainly:
- unlocks names previously **skipped at 0 lots** (their per-name target now clears one 100-share lot → they get debated + potentially bought), and
- **reaches the ~5-name × ~10% ≈ ~50% invested steady-state in ~2–3 days instead of ~5–6**, cutting early cash drag.

It does **not** jump to 60% on day 1 (per-name 10% cap + lot rounding still bind each name to ~1–2 lots). Capped at **0.6 (≤0.7)** deliberately: a day-1 tranche must stay under the RiskEngine 70% total-position cap (check #08) or orders past 70% get REJECTED.

If the owner later wants a higher invested ceiling, the **secondary lever is `per_name_target_pct` (0.10 → up to <0.15)** — a separate amendment.

## Apply

`allocation_policy.yaml` is load-once at boot (no hot-reload). **amendment + restart** — takes effect at the next 09:35 Line-1 cron. **No account re-baseline needed** (this changes allocation behavior, not account capital; the ¥30k account stands).

## Test-phase intent / reversibility

Raised **for the simulation profitability test** to avoid cash-drag distortion. **Revert to 0.33 (conservative tranching) before the eventual feishu real-money phase** — gradual deployment is the right risk posture for live capital. Reversible by a one-line amendment + restart.
