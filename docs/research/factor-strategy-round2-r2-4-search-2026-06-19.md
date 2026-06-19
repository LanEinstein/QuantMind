# Round-2 R2-4 search — selected benchmark-relative strategy (DEVELOPMENT evidence)

> **This is development evidence + the single selected strategy — NOT a PASS/FAIL
> verdict.** The four-gate verdict is the one-shot R2-6 locked test (results in
> `factor-strategy-round2-result-2026-06-19.md`). All figures here are inner
> train/val (in-sample, train_val only); the sacred test window is never read.
> Methodology frozen in `config/research/round2_experiment_manifest.json`.

## 1. What the search does

- **Kernel**: the R2-3 `benchmark_relative_backtest` (enhanced-index tilt off the
  PIT CSI300 constituent weights, beta ≈ 1, conservative buy/sell-split cost),
  now with the R2-4 **off-benchmark exposure constraints** that fix the R2-3
  small-cap drift (size active −0.67): `unconstrained` (= R2-3) /
  `constituent_only` (true enhanced index — tilt only CSI300 members) /
  `size_neutral` (remove the active's size projection) /
  `capped_nonconstituent` (cap realised non-member gross active).
- **Pre-declared N = 612** (4 exposure_constraint × 3 k × 3 a_max × 17 weight
  vectors [equal-weight anchor + 16 Sobol simplex points over the 11 carry
  factors]). N is the cumulative DSR/PBO deflation count — fixed before the run.
- **Selection** (never touches test): inner train/val split inside train_val
  (cutoff 2022-12-30, purge 4 rebalances). Score every candidate on inner-train
  IR (per-period excess Sharpe), shortlist the train-robust top-16, pick the
  unique winner = best inner-val IR among them.
- **Disclosure**: DSR (main gate ≥ 0.95, deflated by N) / PBO-CSCV / Hansen SPA
  vs three baselines (passive CSI300, momentum incumbent, round-1 frozen) / a
  shuffled-composite SENTINEL control / anchored walk-forward + combinatorial
  purged CPCV / exposure disclosure (size / forced-UW active).

## 2. Selected strategy

- **exposure_constraint = `constituent_only`** (true enhanced index — tilt only
  CSI300 members), **k = 0.10**, **a_max = 0.01**, **nonconst_cap = 0.10** (n/a here).
- **weights** (carry, neutralized composite), value/quality-tilted, momentum ≈ 0:
  `ep_ttm 0.195 · amihud_20d 0.160 · roe 0.140 · turn_20d 0.104 · gpm 0.089 ·
  np_yoy 0.076 · ret_5d 0.056 · rev_yoy 0.056 · ret_20d 0.053 · max_20d 0.040 ·
  vol_20d 0.031`.
- inner-train (337 periods, 2016→2022): IR **+0.82** / excess +18.71% / TE 3.19% /
  size active **−0.033** / forced UW 17.2%.
- inner-val (108 periods, 2023→2025-04): IR **+1.09** / total excess +6.73% /
  TE **2.83%** / size active **−0.110** / forced UW 13.2% / turnover 0.116.

> The R2-3 small-cap drift is **fixed by construction**: `constituent_only` cuts
> size active from −0.67 (unconstrained) to **−0.11**, and TE to a tight 2.83% —
> a genuine enhanced-index tilt, not a hidden size bet. The deflated search chose
> it over `unconstrained`/`size_neutral`/`capped_nonconstituent` and over every
> weight blend; all 6 train-robust finalists are `constituent_only`.

## 3. Honest disclosure (the multiple-testing panel)

| metric | value | read |
|---|---|---|
| DSR (deflated by N=612) | **0.056** | main gate ≥ 0.95 → **FAILS** (≈ round-1's 0.066) |
| PBO (CSCV) | **0.504** | ≤ 0.5 hard → just over (borderline overfit) |
| MinBTL admits | False | a ~2.3y weekly val is too short for N=612 (conservative floor) |
| SPA p-value vs passive CSI300 | **0.126** | does NOT clearly beat the index after snooping (>0.05) |
| SPA p-value vs momentum incumbent | **0.006** | DOES beat the live momentum bet (significant) |
| SPA p-value vs round-1 frozen | 1.000 | does not beat round-1's (contaminated) val excess |
| sentinel (max shuffled-composite val IR) | 0.515 | selected IR **1.09** → **passes** (signal ≫ noise) |
| CPCV OOS IR (mean / min / frac positive) | 0.865 / −0.30 / **0.89** | 89% of combinatorial OOS folds positive |

## 4. Honest read

**Mixed — and that is the point of the disclosure.** Three things are genuinely
good: (1) the construction fix works — `constituent_only` removes the R2-3 size
drift (size active −0.11, TE 2.83%), so the in-sample IR 1.09 is a real tilt, not
a small-cap artefact; (2) it separates cleanly from noise (sentinel 1.09 vs 0.51)
and from the live momentum bet (SPA 0.006); (3) CPCV is mostly positive (89%).

But the deflation gates are unforgiving and **honest**: **DSR 0.056 fails the 0.95
bar** (the same verdict round-1's DSR delivered) — after deflating for 612 trials
the in-sample IR is **not** statistically distinguishable from the best of that
many tries; **PBO 0.504** is right at the overfit threshold; and **SPA vs passive
CSI300 is 0.126** — it does not clearly beat the index after data-snooping
correction. Momentum is near-zero in the selected blend, re-confirming R2-2's
"A-share momentum is weak/absent". The weight blend is secondary — the lever was
the *construction* (constituent-only enhanced index), exactly as R2-3 predicted.

**So the development evidence says: clean construction, real in-sample tilt that
beats noise and momentum, but NOT a deflation-significant edge over the index.**
Per the honesty discipline this is reported, not papered over. The verdict is R2-6.

## 5. What this does and does NOT establish

- **Does**: identifies the single strategy carried into R2-5 freeze + R2-6
  verdict; quantifies in-sample confidence after deflation.
- **Does NOT**: decide PASS/FAIL. Even a strong in-sample IR here can fail the
  four owner gates on the locked test (round-1 precedent: val +69.68% excess →
  test −16.36%). The verdict is R2-6, one shot, on data the strategy is frozen
  before reading.
