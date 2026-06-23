# QGR-3 ⑧ bottom-confirmation gate diagnostics (§3.8B, train_val only)

> Panel: 326854 rows / 3003 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Panel params: rebalance=5td / §3.8B gate: core(缩量+无破位+无ST+质量) + cyq band.
> **The gate is an OVERLAY, not a ranking axis** — validated by its forward-return DISCRIMINATION (confirmed vs not-confirmed), not a rank-IC. Conditions (§3.8B): ① 缩量 + ④ 无破位 + ⑤ 无困境(PIT-ST) + ⑥ 质量地板 = clean-PIT **core**; ② 站稳筹码成本带 (cyq_perf, MODEL-derived §3.5) kept SEPARATE + ablated; **③ 资金流企稳 DEFERRED** (moneyflow not ingested + §3.6 trap). Horizons = slow leg 5/10/20d.
> **The t-stat is an OPTIMISTIC SCREEN, not the verdict** (overlapping windows → autocorrelated spread, effective N < n_dates) — the honest control is the QGR-2 arena DSR/SPA/Romano-Wolf + cumulative-N deflation (QGR-4). QGR-3 runs NO search, makes NO promotion.

## 1. Coverage (evaluable-rate + confirmed-rate)

| column | defined-rate (of rows) | confirmed-rate (of evaluable) |
|---|---|---|
| bc_vol_dryup | 99.84% | 57.87% |
| bc_no_breakdown | 100.00% | 89.16% |
| bc_no_distress | 100.00% | 99.72% |
| bc_quality_floor | 81.22% | 96.06% |
| bc_above_cost_band | 69.73% | 72.64% |
| bc_core_confirmed | 90.76% | 42.68% |
| bc_full_confirmed | 81.57% | 24.92% |

## 2. Core gate discrimination (4 clean-PIT conditions, full window)

| gate / condition | horizon | mean spread | t | hit | n_dates |
|---|---|---|---|---|---|
| core gate (4 clean-PIT) | fwd_ret_5d | +0.0022 | +3.82 | 0.55 | 487 |
| core gate (4 clean-PIT) | fwd_ret_10d | +0.0037 | +4.24 | 0.56 | 487 |
| core gate (4 clean-PIT) | fwd_ret_20d | +0.0061 | +4.99 | 0.61 | 487 |

## 3. Full gate discrimination (core + cyq band, 2018+ where evaluable)

| gate / condition | horizon | mean spread | t | hit | n_dates |
|---|---|---|---|---|---|
| full gate (+cyq band) | fwd_ret_5d | +0.0017 | +2.32 | 0.51 | 354 |
| full gate (+cyq band) | fwd_ret_10d | +0.0034 | +3.21 | 0.59 | 354 |
| full gate (+cyq band) | fwd_ret_20d | +0.0057 | +4.00 | 0.56 | 354 |

## 4. Conditional on the dip pool (ret_20d bottom tercile = 买跌票)
> The precise §3.8B claim: among RECENTLY-FALLEN names, does confirmation separate healthy basers from falling knives?


| gate / condition | horizon | mean spread | t | hit | n_dates |
|---|---|---|---|---|---|
| core gate | dip pool | fwd_ret_5d | +0.0011 | +1.57 | 0.51 | 480 |
| core gate | dip pool | fwd_ret_10d | +0.0010 | +1.08 | 0.52 | 480 |
| core gate | dip pool | fwd_ret_20d | +0.0021 | +1.44 | 0.50 | 480 |

## 5. Per-condition marginal discrimination (5d)

| gate / condition | horizon | mean spread | t | hit | n_dates |
|---|---|---|---|---|---|
| bc_vol_dryup | fwd_ret_5d | +0.0016 | +2.54 | 0.52 | 496 |
| bc_no_breakdown | fwd_ret_5d | +0.0014 | +1.27 | 0.52 | 421 |
| bc_no_distress | fwd_ret_5d | +0.0126 | +1.04 | 0.55 | 33 |
| bc_quality_floor | fwd_ret_5d | +0.0028 | +2.25 | 0.55 | 440 |
| bc_above_cost_band | fwd_ret_5d | +0.0010 | +1.15 | 0.49 | 349 |

## 6. cyq_perf ablation (is the model-derived cost band load-bearing?)

> Both gates measured on the SAME cyq-available subset (**227910** rows, cyq_perf 2018+). If `full` does not beat `core`, the MODEL-derived cyq_perf cost band (§3.5) is NOT load-bearing.

| gate / condition | horizon | mean spread | t | hit | n_dates |
|---|---|---|---|---|---|
| core (no cyq) | fwd_ret_5d | +0.0016 | +2.43 | 0.53 | 354 |
| core (no cyq) | fwd_ret_10d | +0.0029 | +2.91 | 0.54 | 354 |
| core (no cyq) | fwd_ret_20d | +0.0042 | +3.12 | 0.57 | 354 |
| full (+cyq band) | fwd_ret_5d | +0.0015 | +2.10 | 0.51 | 354 |
| full (+cyq band) | fwd_ret_10d | +0.0030 | +2.86 | 0.57 | 354 |
| full (+cyq band) | fwd_ret_20d | +0.0049 | +3.46 | 0.56 | 354 |

## 7. Continuous cyq reads — SECONDARY (rank-IC, not a ranking axis)

| continuous read | horizon | rank-IC | t | n_dates |
|---|---|---|---|---|
| bc_cost_premium | fwd_ret_5d | +0.0185 | +2.39 | 355 |
| bc_cost_premium | fwd_ret_10d | +0.0268 | +3.50 | 355 |
| bc_cost_premium | fwd_ret_20d | +0.0349 | +4.62 | 355 |
| bc_winner_rate | fwd_ret_5d | -0.0157 | -1.85 | 355 |
| bc_winner_rate | fwd_ret_10d | -0.0140 | -1.73 | 355 |
| bc_winner_rate | fwd_ret_20d | -0.0196 | -2.52 | 355 |

## 8. Honest read (development evidence ≠ verdict)
- **Core = the 4 clean-PIT conditions** (`vol_dryup, no_breakdown, no_distress, quality_floor`); the cyq_perf band is ablatable, NOT core (§3.5 model-derived).
- **A positive, significant spread means the gate is a useful FILTER** on the slow-leg dip candidates — it does NOT pre-judge a strategy (rounds 1-4 had strong train_val signal yet the locked test FAILed three times). The verdict is the QGR-2 arena + QGR-4 search + QGR-6 forward window.
- **③ 资金流企稳 deferred, not dropped silently**: moneyflow/moneyflow_hsgt/margin were never ingested (QGR-1) and §3.6 flags daily moneyflow as a trap; the stabilisation is carried by ①缩量 + ④无破位.
- **cyq_perf caveat (§3.5)**: model-derived, 2018+ only, degenerate-band rows fail closed; treated as an ablatable overlay, never a clean axis.

