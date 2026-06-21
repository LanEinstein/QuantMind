# Round-4 factor diagnostics (R4-4, train_val only)

> Panel: 325718 rows / 3001 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Panel params: staleness=90d / lookback=90d / level=180d (main).
> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, winsor=0.01, min_obs=20.
> Collinearity: PAIRWISE 2-way common support on the *_neut columns (robust to the analyst factors' uneven coverage).
> Inclusion gate: neutralized |t| ≥ 3 + aligned sign + low collinearity (≤ 0.7) vs the carry cluster AND vs a stronger new factor. **The IC t-stat is OPTIMISTIC** (overlapping forward windows → autocorrelated IC, effective N < n_dates; best-of-3-horizons selection) — this is a SCREEN, not the verdict; R4-5's DSR/PBO/SPA with cumulative-N deflation is the real multiple-testing control (§7).

## 1. Analyst coverage (the headline caveat — sell-side skews large-cap)

| factor | defined-rate (of cohort rows) | mean (defined) |
|---|---|---|
| np_rev | 67.20% | -0.0839 |
| eps_rev | 67.58% | -0.1275 |
| rev_diff | 33.16% | -0.0872 |
| rating_chg | 37.95% | +0.0097 |
| tp_impl | 77.95% | +0.2990 |
| disp | 69.39% | +0.2490 |
| cover_chg | 81.53% | +0.0279 |

## 2. Analyst-factor honest verdict (raw)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| np_rev | fwd_ret_20d | +0.0322 | +6.02 | yes | yes |
| eps_rev | fwd_ret_20d | +0.0328 | +6.39 | yes | yes |
| rev_diff | fwd_ret_20d | +0.0371 | +5.89 | yes | yes |
| rating_chg | fwd_ret_5d | +0.0033 | +0.97 | yes | no |
| tp_impl | fwd_ret_20d | +0.0411 | +6.18 | yes | yes |
| disp | fwd_ret_20d | -0.0330 | -5.44 | yes | yes |
| cover_chg | fwd_ret_20d | +0.0136 | +4.12 | yes | yes |

## 3. Analyst-factor honest verdict (industry+size neutralized)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| np_rev_neut | fwd_ret_20d | +0.0211 | +5.64 | yes | yes |
| eps_rev_neut | fwd_ret_20d | +0.0194 | +5.47 | yes | yes |
| rev_diff_neut | fwd_ret_20d | +0.0260 | +5.06 | yes | yes |
| rating_chg_neut | fwd_ret_20d | +0.0054 | +1.22 | yes | no |
| tp_impl_neut | fwd_ret_20d | +0.0292 | +6.77 | yes | yes |
| disp_neut | fwd_ret_20d | -0.0058 | -1.69 | yes | no |
| cover_chg_neut | fwd_ret_20d | +0.0153 | +5.11 | yes | yes |

## 4. Collinearity vs the round-3 carry cluster + mutual

| analyst factor | most-collinear carry | |corr| | support (dates) | redundant >0.7? |
|---|---|---|---|---|
| np_rev | np_yoy | 0.29 | 488 | no |
| eps_rev | np_yoy | 0.25 | 488 | no |
| rev_diff | np_yoy | 0.27 | 447 | no |
| rating_chg | ep_ttm | 0.05 | 498 | no |
| tp_impl | ret_20d | 0.38 | 498 | no |
| disp | roe | 0.26 | 488 | no |
| cover_chg | vol_20d | 0.09 | 498 | no |

**Mutually collinear analyst pairs (|corr| > 0.7):**
- `eps_rev` ↔ `np_rev` = **0.90**

## 5. IC tables — analyst factors (raw + neutralized)

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| np_rev | fwd_ret_5d | +0.0209 | +0.188 | +4.19 | 0.59 | 498 | +1 | yes |
| np_rev | fwd_ret_10d | +0.0238 | +0.208 | +4.64 | 0.61 | 498 | +1 | yes |
| np_rev | fwd_ret_20d | +0.0322 | +0.270 | +6.02 | 0.62 | 498 | +1 | yes |
| eps_rev | fwd_ret_5d | +0.0210 | +0.195 | +4.35 | 0.59 | 498 | +1 | yes |
| eps_rev | fwd_ret_10d | +0.0240 | +0.218 | +4.86 | 0.61 | 498 | +1 | yes |
| eps_rev | fwd_ret_20d | +0.0328 | +0.286 | +6.39 | 0.62 | 498 | +1 | yes |
| rev_diff | fwd_ret_5d | +0.0216 | +0.152 | +3.34 | 0.56 | 480 | +1 | yes |
| rev_diff | fwd_ret_10d | +0.0290 | +0.211 | +4.62 | 0.59 | 480 | +1 | yes |
| rev_diff | fwd_ret_20d | +0.0371 | +0.269 | +5.89 | 0.63 | 480 | +1 | yes |
| rating_chg | fwd_ret_5d | +0.0033 | +0.044 | +0.97 | 0.51 | 498 | +1 | yes |
| rating_chg | fwd_ret_10d | +0.0020 | +0.026 | +0.59 | 0.51 | 498 | +1 | yes |
| rating_chg | fwd_ret_20d | +0.0023 | +0.031 | +0.70 | 0.52 | 498 | +1 | yes |
| tp_impl | fwd_ret_5d | +0.0318 | +0.202 | +4.50 | 0.57 | 498 | +1 | yes |
| tp_impl | fwd_ret_10d | +0.0366 | +0.238 | +5.32 | 0.58 | 498 | +1 | yes |
| tp_impl | fwd_ret_20d | +0.0411 | +0.277 | +6.18 | 0.60 | 498 | +1 | yes |
| disp | fwd_ret_5d | -0.0203 | -0.151 | -3.38 | 0.56 | 498 | -1 | yes |
| disp | fwd_ret_10d | -0.0232 | -0.172 | -3.83 | 0.57 | 498 | -1 | yes |
| disp | fwd_ret_20d | -0.0330 | -0.244 | -5.44 | 0.60 | 498 | -1 | yes |
| cover_chg | fwd_ret_5d | +0.0056 | +0.072 | +1.62 | 0.54 | 498 | +1 | yes |
| cover_chg | fwd_ret_10d | +0.0080 | +0.104 | +2.32 | 0.54 | 498 | +1 | yes |
| cover_chg | fwd_ret_20d | +0.0136 | +0.185 | +4.12 | 0.59 | 498 | +1 | yes |
| np_rev_neut | fwd_ret_5d | +0.0119 | +0.151 | +3.36 | 0.58 | 498 | +1 | yes |
| np_rev_neut | fwd_ret_10d | +0.0170 | +0.211 | +4.70 | 0.58 | 498 | +1 | yes |
| np_rev_neut | fwd_ret_20d | +0.0211 | +0.253 | +5.64 | 0.61 | 498 | +1 | yes |
| eps_rev_neut | fwd_ret_5d | +0.0110 | +0.146 | +3.25 | 0.57 | 498 | +1 | yes |
| eps_rev_neut | fwd_ret_10d | +0.0159 | +0.206 | +4.60 | 0.56 | 498 | +1 | yes |
| eps_rev_neut | fwd_ret_20d | +0.0194 | +0.245 | +5.47 | 0.63 | 498 | +1 | yes |
| rev_diff_neut | fwd_ret_5d | +0.0156 | +0.129 | +2.76 | 0.58 | 457 | +1 | yes |
| rev_diff_neut | fwd_ret_10d | +0.0209 | +0.188 | +4.02 | 0.60 | 457 | +1 | yes |
| rev_diff_neut | fwd_ret_20d | +0.0260 | +0.237 | +5.06 | 0.62 | 457 | +1 | yes |
| rating_chg_neut | fwd_ret_5d | +0.0008 | +0.008 | +0.18 | 0.52 | 498 | +1 | yes |
| rating_chg_neut | fwd_ret_10d | +0.0050 | +0.049 | +1.10 | 0.53 | 498 | +1 | yes |
| rating_chg_neut | fwd_ret_20d | +0.0054 | +0.055 | +1.22 | 0.52 | 498 | +1 | yes |
| tp_impl_neut | fwd_ret_5d | +0.0220 | +0.235 | +5.25 | 0.57 | 498 | +1 | yes |
| tp_impl_neut | fwd_ret_10d | +0.0245 | +0.265 | +5.90 | 0.59 | 498 | +1 | yes |
| tp_impl_neut | fwd_ret_20d | +0.0292 | +0.304 | +6.77 | 0.62 | 498 | +1 | yes |
| disp_neut | fwd_ret_5d | -0.0031 | -0.043 | -0.96 | 0.49 | 498 | -1 | yes |
| disp_neut | fwd_ret_10d | -0.0036 | -0.048 | -1.07 | 0.52 | 498 | -1 | yes |
| disp_neut | fwd_ret_20d | -0.0058 | -0.076 | -1.69 | 0.54 | 498 | -1 | yes |
| cover_chg_neut | fwd_ret_5d | +0.0067 | +0.103 | +2.29 | 0.55 | 498 | +1 | yes |
| cover_chg_neut | fwd_ret_10d | +0.0093 | +0.141 | +3.15 | 0.55 | 498 | +1 | yes |
| cover_chg_neut | fwd_ret_20d | +0.0153 | +0.229 | +5.11 | 0.61 | 498 | +1 | yes |

## 6. Carry decision

- **Survivors (neut |t| ≥ 3 + aligned + |corr| ≤ 0.7)**: `np_rev, rev_diff, tp_impl, cover_chg`.
- **Dropped — no signal (neut |t| < 3 or misaligned)**: `rating_chg, disp`.
- **Dropped — redundant with carry cluster (|corr| > 0.7)**: `(none)`.
- **Dropped — redundant with a stronger new factor**: `eps_rev`.
- **R4_CARRY = R3_CARRY (12) ∪ survivors** = `ret_5d, ret_20d, vol_20d, max_20d, ep_ttm, turn_20d, amihud_20d, roe, gpm, np_yoy, rev_yoy, accr, np_rev, rev_diff, tp_impl, cover_chg`.
- **Thin-collinearity-support survivors (redundancy screen unreliable, < 60 dates → carried but flagged)**: `(none)`.
- Weak / redundant factors are dropped honestly (as R2-2 dropped momentum, R3-3 dropped SUE). If the survivor set is empty, R4-5 adds no new alpha source and the round likely still FAILs — reported, not papered over.


## 7. Honest read (development evidence ≠ verdict)

- **Strong neutralized IC is NECESSARY, NOT SUFFICIENT.** Rounds 1/2/3 all had strong train_val IC yet FAILed the locked test; the DSR / PBO / SPA gates correctly warned each time. A high |t| here does not pre-judge R4-6.
- **The screen's t-stat is OPTIMISTIC** (§ header): overlapping 10/20-day forward windows make the per-date IC series autocorrelated, so the effective N is well below n_dates, and `verdicts` takes the best of 3 horizons — both inflate |t|. The |t| ≥ 3 floor is therefore a generous screen, NOT the Harvey-Liu-Zhu guarantee on a clean t. The honest multiple-testing control is R4-5's DSR/PBO/SPA (deflated by the cumulative trial count across all 4 rounds), not this gate.
- **What is genuinely different this round**: 4 analyst factors survive AND are orthogonal to the existing 12-factor carry cluster — the strongest pair is tp_impl ↔ ret_20d at |corr| 0.38 (below the 0.7 ceiling but NOT negligible — target-price implied return carries a reversal flavour). Still, this is the first round whose new material is both strong and a largely NEW axis (R2 quality survived but was insufficient; R3 accruals earned only a 0.006 weight). Analyst revision is information-flow, not a financial-report derivative — the orthogonal source the three FAILs lacked.
- **The np_rev vs eps_rev pick is a near-tie** (neut |t| 5.64 vs 5.47, |corr| 0.90): np_rev is kept on a t-margin smaller than the screen's own inflation, so the specific survivor identity is not robustly preferred — the two are economically interchangeable (magnitude of the same revision) and only one belongs in the composite.
- **Collinearity fail-open**: a pair with thin 2-way support scores 0.00 (treated as 'not redundant'), so a low-coverage factor is more likely to clear the redundancy gate than a well-covered one — §4 shows each survivor's support and ⚠️-flags thin estimates (none thin this run).
- **Coverage caveats (§1)**: tp_impl has the lowest coverage (~30%); sell-side coverage skews large-cap, so the carry set tilts toward the covered universe; A-share analysts are systematically optimistic, so only the *change* (used here) is clean.
- **Verdict path**: R4-5 (DSR≥0.95 / PBO≤0.5 / SPA-vs-passive + sentinel + CPCV, cumulative-N deflation across 4 rounds) → R4-6 (existing locked test, 4th evaluation, four gates NOT relaxed). If the analyst alpha does not produce a positive index excess net of cost, the round is reported FAIL — no data-snooping to clear the bar.

