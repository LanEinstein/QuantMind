# Round-2 benchmark-relative diagnostics (R2-3, train_val only)

> Panel: 326854 rows / 3003 codes / 498 rebalance dates. Benchmark-weighted dates (publish<d, ≥2016): 449/498 (pre-2016 have no CSI300 weights → skipped).
> Composite = EQUAL-weight over the carry set's industry/size-neutralized columns (round-1 seven + roe/gpm/np_yoy/rev_yoy). This is DEVELOPMENT evidence over an ILLUSTRATIVE tilt grid — NOT a selection; the deflated CPCV search is R2-4, the verdict is R2-6 forward.

## 1. Primary arm — benchmark-relative long-only (deployable)

| k | a_max | periods | total excess | annual excess | TE | IR | turnover | gross active | forced UW | net active | size active | max ind active |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.05 | 0.01 | 449 | +16.81% | +1.76% | 5.90% | +0.33 | 0.18 | 42.88% | 16.20% | -2.72e-18 | -0.602 | 4.25% |
| 0.05 | 0.02 | 449 | +16.16% | +1.70% | 6.29% | +0.30 | 0.20 | 45.34% | 16.20% | +2.15e-18 | -0.672 | 4.63% |
| 0.05 | 0.04 | 449 | +16.14% | +1.69% | 6.55% | +0.29 | 0.21 | 47.05% | 16.20% | +9.17e-19 | -0.712 | 4.97% |
| 0.10 | 0.01 | 449 | +15.57% | +1.64% | 5.92% | +0.30 | 0.18 | 43.00% | 16.20% | +2.33e-18 | -0.607 | 4.21% |
| 0.10 | 0.02 | 449 | +15.32% | +1.61% | 6.32% | +0.28 | 0.19 | 45.23% | 16.20% | -1.99e-18 | -0.673 | 4.54% |
| 0.10 | 0.04 | 449 | +15.55% | +1.64% | 6.51% | +0.28 | 0.20 | 46.60% | 16.20% | -4.95e-19 | -0.708 | 4.78% |
| 0.20 | 0.01 | 449 | +15.11% | +1.59% | 5.93% | +0.30 | 0.18 | 43.06% | 16.20% | +5.53e-20 | -0.609 | 4.19% |
| 0.20 | 0.02 | 449 | +14.26% | +1.51% | 6.32% | +0.27 | 0.19 | 45.14% | 16.20% | +3.49e-18 | -0.674 | 4.48% |
| 0.20 | 0.04 | 449 | +14.81% | +1.56% | 6.51% | +0.27 | 0.19 | 46.24% | 16.20% | -4.29e-19 | -0.705 | 4.66% |

## 2. Reference arm — market-neutral (RESEARCH ONLY, not deployable)

> long top-20% composite − short CSI300. **Never a PASS claim, never deployed, never enters the verdict** (A-share retail cannot short; 永禁真实下单). Bounds the factors' alpha upside only.

- periods: 449
- total alpha: +31.67% / annual +3.14%
- alpha Sharpe: +0.30
- max drawdown: 25.11%
- avg turnover: 0.42

## 3. Honest read

- **Beta neutral, size NOT neutral.** Net active ≈ 0 (max |net| 3e-18; beta ≈ 1 by the renormalize-to-Σw=1 design), but `size active` runs -0.71…-0.60 std and `gross active` 43%…47%. The tilt spans the full investable universe but starts from 300 CSI300 weights, so high-composite NON-constituents (mostly small/mid caps, w_bench=0) get positive active → a systematic size drift even though the FACTORS are size-neutralized. The disclosed excess / IR (+0.27…+0.33) is therefore CONTAMINATED by a size bet, not a clean factor tilt — exactly the hidden bet this disclosure exists to catch.
- **R2-4 must constrain off-benchmark exposure**: restrict the active tilt to CSI300 constituents (true enhanced-index), and/or add a portfolio-level size-neutrality constraint, and/or cap non-constituent active + target a TE band. Size-neutralized factors alone do NOT prevent a universe-mismatch size drift.
- **TE/IR scaling**: IR is supplementary disclosure (NOT a replacement for the four owner-locked gates); higher a_max raises TE/turnover — R2-4 searches (k, a_max, weights, exposure constraints) under DSR/PBO/SPA deflation.
- **Forced underweight (16.2%)**: CSI300 constituents excluded by the investable universe (round-1 exclusions — STAR 科创板 / 北交 BSE boards, ST, liquidity/price, bottom-30% size; 创业板 ChiNext is whitelisted, NOT excluded) are forced to 0 — a passive active the index leg penalises. Its size/industry attributes are NOT in the panel, so the `size active` / `max ind active` columns cover the investable sleeve ONLY and understate this residual (see the R2-2 66% industry-coverage gap).
- **Reference arm caveat**: the market-neutral +alpha is similarly inflated by the small-cap-vs-large-cap-index mismatch; it bounds upside only and is RESEARCH-ONLY (never deployed, never a verdict).
- **This is development evidence, not PASS/FAIL.** The verdict is the one-shot R2-6 forward test on data postdating the freeze.

