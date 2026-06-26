# Batch-A crowding / blow-off EXIT diagnostics (train_val only)

> Panel: 326854 rows / 3003 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Panel params: rebalance=5td / batch-A crowding EXIT (bias/ideal_amplitude/blowoff).
> Family: `bias_20d` (乖离 over-extension) / `ideal_amplitude_20d` (理想振幅, Kaiyuan, size-neutral re-test) / `blowoff_20d` (run-up × turnover surge). All attractive-LOW (high = EXIT/trim). Neutralization: industry SW-L1 + log(circ_mv) per-date OLS, winsor=0.01, min_obs=20; bottom-30% size already cut in the panel.
> **The load-bearing test is the crash-probability conditional (§4), not the mean IC** — the prior (§2.1) is that crowding predicts the LEFT TAIL, not mean return; a weak mean IC CONFIRMS that, it does not fail it. EXIT-useful gate = paired tail-Δ ``|t| ≥ 3`` AND positive; orthogonality gate = |corr| ≤ 0.7 vs carry+QGR.

## 1. IC honest verdict — crash-aligned sign from zero (raw)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| bias_20d | fwd_ret_20d | -0.0573 | -7.67 | yes | yes |
| ideal_amplitude_20d | fwd_ret_20d | -0.0785 | -14.23 | yes | yes |
| blowoff_20d | fwd_ret_20d | -0.0522 | -9.19 | yes | yes |

## 2. IC honest verdict (industry+size neutralized)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| bias_20d_neut | fwd_ret_20d | -0.0448 | -9.69 | yes | yes |
| ideal_amplitude_20d_neut | fwd_ret_20d | -0.0479 | -15.02 | yes | yes |
| blowoff_20d_neut | fwd_ret_1d | -0.0118 | -2.43 | yes | no |

## 3. ⭐ Crash-probability conditional (LOAD-BEARING) — top-decile vs rest
Forward-5d left tail of the top-crowding decile (size-neutral) vs the rest; paired Δ = per-date P(<−5%|crowded) − P(<−5%|rest) (regime-robust); sub-periods = 3 contiguous thirds (R5 regime guard).

| factor (neut) | n_dates | P(<−5%) crowded / rest | P(<−10%) c/r | CVaR5 c/r | paired Δ (t) | sub-periods |
|---|---|---|---|---|---|---|
| `bias_20d` | 498 | 0.309 / 0.192 | 0.139 / 0.061 | -0.2185 / -0.1626 | +0.1097 (+21.87) | +0.105 / +0.095 / +0.129 |
| `ideal_amplitude_20d` | 498 | 0.291 / 0.194 | 0.120 / 0.063 | -0.2034 / -0.1658 | +0.0924 (+21.56) | +0.085 / +0.076 / +0.116 |
| `blowoff_20d` | 498 | 0.274 / 0.195 | 0.114 / 0.064 | -0.2048 / -0.1657 | +0.0707 (+13.99) | +0.070 / +0.066 / +0.076 |

## 4. Significance + non-zeroing ledger (DSR / CPCV / collinearity)
> Trial ledger `data/factor_research/mfi_trial_ledger.jsonl`: legacy floor 2382 effective + batch-A appends; ONC effective N over the 3 EXIT spreads (common dates) = 3; deflation N = 2382 (changing the criterion does NOT reset the debt). Cadence: rebalance=5td, horizon=5td → DSR HAC lag=0, Sharpe annualization √50.

| factor | spread Sharpe (raw) | spread DSR (N=2382) | CPCV combo EXIT-aligned frac | max |corr| carry (name, support) |
|---|---|---|---|---|
| `bias_20d` | +1.71 | 0.966 | 1.00 | 0.81 (`ret_20d`, 498d) |
| `ideal_amplitude_20d` | +2.48 | 1.000 | 1.00 | 0.34 (`max_20d`, 498d) |
| `blowoff_20d` | +0.93 | 0.282 | 1.00 | 0.58 (`turn_spike`, 497d) |

## 5. IC tables — crowding factors (raw + neutralized)

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| bias_20d | fwd_ret_1d | -0.0328 | -0.167 | -3.72 | 0.56 | 498 | -1 | yes |
| bias_20d | fwd_ret_5d | -0.0472 | -0.266 | -5.93 | 0.59 | 498 | -1 | yes |
| bias_20d | fwd_ret_10d | -0.0477 | -0.272 | -6.08 | 0.59 | 498 | -1 | yes |
| bias_20d | fwd_ret_20d | -0.0573 | -0.344 | -7.67 | 0.64 | 498 | -1 | yes |
| ideal_amplitude_20d | fwd_ret_1d | -0.0385 | -0.321 | -7.17 | 0.66 | 498 | -1 | yes |
| ideal_amplitude_20d | fwd_ret_5d | -0.0630 | -0.527 | -11.77 | 0.70 | 498 | -1 | yes |
| ideal_amplitude_20d | fwd_ret_10d | -0.0729 | -0.606 | -13.51 | 0.74 | 498 | -1 | yes |
| ideal_amplitude_20d | fwd_ret_20d | -0.0785 | -0.637 | -14.23 | 0.76 | 498 | -1 | yes |
| blowoff_20d | fwd_ret_1d | -0.0280 | -0.192 | -4.29 | 0.56 | 498 | -1 | yes |
| blowoff_20d | fwd_ret_5d | -0.0413 | -0.316 | -7.06 | 0.64 | 498 | -1 | yes |
| blowoff_20d | fwd_ret_10d | -0.0448 | -0.340 | -7.58 | 0.65 | 498 | -1 | yes |
| blowoff_20d | fwd_ret_20d | -0.0522 | -0.412 | -9.19 | 0.70 | 498 | -1 | yes |
| bias_20d_neut | fwd_ret_1d | -0.0340 | -0.280 | -6.25 | 0.59 | 498 | -1 | yes |
| bias_20d_neut | fwd_ret_5d | -0.0412 | -0.358 | -7.98 | 0.63 | 498 | -1 | yes |
| bias_20d_neut | fwd_ret_10d | -0.0406 | -0.375 | -8.36 | 0.64 | 498 | -1 | yes |
| bias_20d_neut | fwd_ret_20d | -0.0448 | -0.434 | -9.69 | 0.66 | 498 | -1 | yes |
| ideal_amplitude_20d_neut | fwd_ret_1d | -0.0254 | -0.323 | -7.20 | 0.62 | 498 | -1 | yes |
| ideal_amplitude_20d_neut | fwd_ret_5d | -0.0387 | -0.505 | -11.26 | 0.71 | 498 | -1 | yes |
| ideal_amplitude_20d_neut | fwd_ret_10d | -0.0442 | -0.612 | -13.66 | 0.73 | 498 | -1 | yes |
| ideal_amplitude_20d_neut | fwd_ret_20d | -0.0479 | -0.673 | -15.02 | 0.76 | 498 | -1 | yes |
| blowoff_20d_neut | fwd_ret_1d | -0.0118 | -0.109 | -2.43 | 0.55 | 498 | -1 | yes |
| blowoff_20d_neut | fwd_ret_5d | -0.0029 | -0.025 | -0.56 | 0.49 | 498 | -1 | yes |
| blowoff_20d_neut | fwd_ret_10d | -0.0004 | -0.004 | -0.09 | 0.52 | 498 | -1 | yes |
| blowoff_20d_neut | fwd_ret_20d | +0.0022 | +0.020 | +0.45 | 0.51 | 498 | -1 | **NO** |

## 6. Honest read (FAIL is reported, not laundered)
- **`bias_20d`**: EXIT-USEFUL (tail-significant); REDUNDANT (~`ret_20d`, |corr| 0.81); spread DSR 0.966. Crowding fattens the left tail as the asymmetry predicts.
- **`ideal_amplitude_20d`**: EXIT-USEFUL (tail-significant); orthogonal (|corr| 0.34); spread DSR 1.000. Crowding fattens the left tail as the asymmetry predicts.
- **`blowoff_20d`**: EXIT-USEFUL (tail-significant); orthogonal (|corr| 0.58); spread DSR 0.282. Crowding fattens the left tail as the asymmetry predicts.

- **Asymmetry check (§2.1)**: the deployable use is a long-only VETO (drop top-crowding from the buy set); the decile spread here is a GROSS research series (no A-share shorting) — its net P&L as a veto in the event loop is the deferred QGR-4-scale test, NOT claimed here.
- **A2 (理想振幅 §8)**: the neutralized `ideal_amplitude_20d` verdict above is our own size-neutral re-test of the broker's unreplicated claim; sign/significance read from zero.
- **A3 (§2.10②)**: a factor REDUNDANT with the carry/QGR cluster is disclosed as reversal/size in disguise, not sold as new alpha.


## 7. Falsification ledger resolution (A1 / A2 / A3) + conclusion
- **A1 (crowding → fat left tail / crash probability)**: **PASS** — all 3 EXIT factors' top-decile vs rest paired tail-Δ are positive with |t| ≥ 3 (min t = +14.0), stable across the 3 sub-periods. The asymmetry (§2.1) holds on real A-share data: crowding/over-extension is a RISK/EXIT signal.
- **A2 (ideal_amplitude survives independent size-neutralization, §8)**: **PASS** — the broker's unreplicated size-neutral claim REPLICATES under our own industry+size neutralization: `ideal_amplitude_20d_neut` stays a significant NEGATIVE (exit) axis AND is orthogonal to the carry/QGR cluster.
- **A3 (not just reversal/size in disguise, §2.10②)**: **MIXED** — orthogonal new axes: `ideal_amplitude_20d`, `blowoff_20d`; disguise (disclosed, not sold as new alpha): `bias_20d`~`ret_20d` (|corr| 0.81).
- **DSR gate (≥ 0.95, non-zeroing N)**: passes = `bias_20d`, `ideal_amplitude_20d` — a tail-significant factor whose deflated spread Sharpe still fails is an honest partial result (the tail edge is real; the long-short mean edge does not survive deflation).
- **Conclusion**: the batch-A asymmetry is confirmed (A1) and `ideal_amplitude_20d` is a genuine orthogonal size-neutral EXIT factor (A2) — the strongest first-cut RISK/EXIT result; `bias_20d` is a tail-valid but reversal-redundant overlay, and `blowoff_20d`'s mean edge largely dissolves under neutralization (size/turnover in disguise). Deployable use = a long-only VETO; net P&L is the deferred event-loop test.

> Scope caveats: **train_val only** (the sealed test window is never read; a real OOS / forward confirmation is the B-layer gate, not done here); the decile spread is a GROSS long-short research series (no A-share shorting) used only as a significance probe; DSR deflation uses the non-zeroing legacy floor (N≈2382 — the round-1..4 mining debt is NOT reset by re-framing the criterion).

