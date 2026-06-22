# QGR-3 short-term factor diagnostics ⑦ (train_val only)

> Panel: 326854 rows / 3003 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Panel params: rebalance=5td / fast leg t1(reversal+lottery)+t2(1d-mom+limit-board).
> Family (full fast leg): tranche-1 reversal (rev_1d/rev_3d) + lottery removal (max_5d/turn_spike/n_limit_up_5d); tranche-2 1-day momentum (intraday_ret_1d/overnight_gap_1d) + limit-board structure (limit_streak_prev/broke_board_prev, `<d`, limit_list_d 2020+). Carry cluster = the round-1 cross-sectional factors. Horizons 1/5/10/20d (fwd_ret_1d = fast-leg T+1).
> Neutralization: industry SW-L1 dummies + log(circ_mv), per-date OLS, winsor=0.01, min_obs=20. Collinearity: PAIRWISE 2-way common support on the *_neut columns.
> Inclusion gate: neutralized |t| ≥ 3 + aligned sign + |corr| ≤ 0.7 vs the carry cluster AND vs a stronger QGR factor. **The IC t-stat is an OPTIMISTIC SCREEN, not the verdict** (overlapping forward windows → autocorrelated IC, effective N < n_dates; best-of-3-horizons) — the honest control is the QGR-2 arena's DSR/SPA/Romano-Wolf with cumulative-N deflation (QGR-4).

## 1. Coverage (defined-rate of cohort rows)

| factor | defined-rate (of cohort rows) | mean (defined) |
|---|---|---|
| rev_1d | 100.00% | -0.0002 |
| rev_3d | 100.00% | +0.0033 |
| max_5d | 100.00% | +0.0383 |
| turn_spike | 99.84% | +0.0741 |
| n_limit_up_5d | 99.91% | +0.1177 |
| intraday_ret_1d | 100.00% | +0.0008 |
| overnight_gap_1d | 100.00% | -0.0010 |
| limit_streak_prev | 61.00% | +0.0267 |
| broke_board_prev | 61.00% | +0.0115 |

## 2. QGR-factor honest verdict (raw)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| rev_1d | fwd_ret_1d | -0.0192 | -2.54 | yes | no |
| rev_3d | fwd_ret_5d | -0.0282 | -3.87 | yes | yes |
| max_5d | fwd_ret_20d | -0.0669 | -9.64 | yes | yes |
| turn_spike | fwd_ret_20d | -0.0293 | -5.00 | yes | yes |
| n_limit_up_5d | fwd_ret_20d | -0.0581 | -13.10 | yes | yes |
| intraday_ret_1d | fwd_ret_1d | -0.0320 | -4.30 | **NO** | yes |
| overnight_gap_1d | fwd_ret_20d | +0.0347 | +7.36 | **NO** | yes |
| limit_streak_prev | fwd_ret_20d | -0.0325 | -7.85 | yes | yes |
| broke_board_prev | fwd_ret_20d | -0.0264 | -7.19 | yes | yes |

## 3. QGR-factor honest verdict (industry+size neutralized)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| rev_1d_neut | fwd_ret_1d | -0.0207 | -4.37 | yes | yes |
| rev_3d_neut | fwd_ret_5d | -0.0297 | -6.84 | yes | yes |
| max_5d_neut | fwd_ret_20d | -0.0435 | -11.34 | yes | yes |
| turn_spike_neut | fwd_ret_1d | -0.0205 | -5.34 | yes | yes |
| n_limit_up_5d_neut | fwd_ret_20d | +0.0161 | +2.72 | **NO** | no |
| intraday_ret_1d_neut | fwd_ret_1d | -0.0328 | -7.40 | **NO** | yes |
| overnight_gap_1d_neut | fwd_ret_10d | +0.0248 | +8.13 | **NO** | yes |
| limit_streak_prev_neut | fwd_ret_10d | +0.0257 | +2.20 | **NO** | no |
| broke_board_prev_neut | fwd_ret_5d | -0.0122 | -0.83 | yes | no |

## 4. Collinearity vs the round-1 carry cluster + mutual

| QGR factor | most-collinear carry | |corr| | support (dates) | redundant >0.7? |
|---|---|---|---|---|
| rev_1d | ret_5d | 0.39 | 498 | no |
| rev_3d | ret_5d | 0.72 | 498 | **YES** |
| max_5d | ret_5d | 0.62 | 498 | no |
| turn_spike | ret_20d | 0.31 | 497 | no |
| n_limit_up_5d | ret_5d | 0.28 | 490 | no |
| intraday_ret_1d | ret_5d | 0.36 | 498 | no |
| overnight_gap_1d | turn_20d | 0.09 | 498 | no |
| limit_streak_prev | ret_5d | 0.17 | 157 | no |
| broke_board_prev | ret_5d | 0.14 | 89 | no |

**Mutually collinear QGR pairs (|corr| > 0.7):**
- `intraday_ret_1d` ↔ `rev_1d` = **0.88**
- `broke_board_prev` ↔ `limit_streak_prev` = **0.79**

## 5. §3.1 limit-loser disclosure (reversal IC with vs without at-limit)

| reversal factor | filter | IC_mean (fwd_ret_5d) | t | n_dates |
|---|---|---|---|---|
| rev_1d | all rows | -0.0141 | -1.88 | 498 |
| rev_1d | exclude at-down-limit | -0.0204 | -2.72 | 498 |
| rev_1d | exclude any at-limit | -0.0216 | -2.84 | 498 |
| rev_3d | all rows | -0.0282 | -3.87 | 498 |
| rev_3d | exclude at-down-limit | -0.0319 | -4.36 | 498 |
| rev_3d | exclude any at-limit | -0.0326 | -4.40 | 498 |

> Cohort rows at the down-limit on d: **1.21%**; at the up-limit on d: **2.16%**. The strategy (QGR-4) filters un-buyable at-limit names; here they are KEPT so the IC measurement is unbiased and the loser-leg effect is disclosed, not hidden.

## 6. IC tables — QGR factors (raw + neutralized)

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| rev_1d | fwd_ret_1d | -0.0192 | -0.114 | -2.54 | 0.57 | 498 | -1 | yes |
| rev_1d | fwd_ret_5d | -0.0141 | -0.084 | -1.88 | 0.53 | 498 | -1 | yes |
| rev_1d | fwd_ret_10d | +0.0008 | +0.005 | +0.11 | 0.52 | 498 | -1 | **NO** |
| rev_1d | fwd_ret_20d | +0.0099 | +0.064 | +1.44 | 0.52 | 498 | -1 | **NO** |
| rev_3d | fwd_ret_1d | -0.0238 | -0.134 | -2.98 | 0.55 | 498 | -1 | yes |
| rev_3d | fwd_ret_5d | -0.0282 | -0.173 | -3.87 | 0.58 | 498 | -1 | yes |
| rev_3d | fwd_ret_10d | -0.0167 | -0.107 | -2.40 | 0.53 | 498 | -1 | yes |
| rev_3d | fwd_ret_20d | -0.0173 | -0.115 | -2.56 | 0.53 | 498 | -1 | yes |
| max_5d | fwd_ret_1d | -0.0311 | -0.200 | -4.47 | 0.59 | 498 | -1 | yes |
| max_5d | fwd_ret_5d | -0.0550 | -0.384 | -8.56 | 0.67 | 498 | -1 | yes |
| max_5d | fwd_ret_10d | -0.0594 | -0.399 | -8.91 | 0.67 | 498 | -1 | yes |
| max_5d | fwd_ret_20d | -0.0669 | -0.432 | -9.64 | 0.70 | 498 | -1 | yes |
| turn_spike | fwd_ret_1d | -0.0247 | -0.175 | -3.91 | 0.57 | 497 | -1 | yes |
| turn_spike | fwd_ret_5d | -0.0246 | -0.192 | -4.29 | 0.56 | 497 | -1 | yes |
| turn_spike | fwd_ret_10d | -0.0236 | -0.177 | -3.95 | 0.57 | 497 | -1 | yes |
| turn_spike | fwd_ret_20d | -0.0293 | -0.224 | -5.00 | 0.61 | 497 | -1 | yes |
| n_limit_up_5d | fwd_ret_1d | -0.0047 | -0.046 | -1.02 | 0.52 | 498 | -1 | yes |
| n_limit_up_5d | fwd_ret_5d | -0.0356 | -0.367 | -8.19 | 0.67 | 498 | -1 | yes |
| n_limit_up_5d | fwd_ret_10d | -0.0466 | -0.470 | -10.48 | 0.68 | 498 | -1 | yes |
| n_limit_up_5d | fwd_ret_20d | -0.0581 | -0.587 | -13.10 | 0.74 | 498 | -1 | yes |
| intraday_ret_1d | fwd_ret_1d | -0.0320 | -0.193 | -4.30 | 0.59 | 498 | +1 | **NO** |
| intraday_ret_1d | fwd_ret_5d | -0.0247 | -0.151 | -3.38 | 0.56 | 498 | +1 | **NO** |
| intraday_ret_1d | fwd_ret_10d | -0.0085 | -0.055 | -1.22 | 0.53 | 498 | +1 | **NO** |
| intraday_ret_1d | fwd_ret_20d | -0.0022 | -0.014 | -0.32 | 0.51 | 498 | +1 | **NO** |
| overnight_gap_1d | fwd_ret_1d | +0.0205 | +0.176 | +3.93 | 0.57 | 498 | -1 | **NO** |
| overnight_gap_1d | fwd_ret_5d | +0.0284 | +0.248 | +5.54 | 0.62 | 498 | -1 | **NO** |
| overnight_gap_1d | fwd_ret_10d | +0.0276 | +0.261 | +5.82 | 0.63 | 498 | -1 | **NO** |
| overnight_gap_1d | fwd_ret_20d | +0.0347 | +0.330 | +7.36 | 0.64 | 498 | -1 | **NO** |
| limit_streak_prev | fwd_ret_1d | -0.0024 | -0.036 | -0.57 | 0.53 | 252 | -1 | yes |
| limit_streak_prev | fwd_ret_5d | -0.0233 | -0.362 | -5.75 | 0.65 | 252 | -1 | yes |
| limit_streak_prev | fwd_ret_10d | -0.0266 | -0.401 | -6.37 | 0.65 | 252 | -1 | yes |
| limit_streak_prev | fwd_ret_20d | -0.0325 | -0.495 | -7.85 | 0.67 | 252 | -1 | yes |
| broke_board_prev | fwd_ret_1d | -0.0056 | -0.095 | -1.48 | 0.51 | 245 | -1 | yes |
| broke_board_prev | fwd_ret_5d | -0.0202 | -0.360 | -5.63 | 0.64 | 245 | -1 | yes |
| broke_board_prev | fwd_ret_10d | -0.0240 | -0.408 | -6.38 | 0.66 | 245 | -1 | yes |
| broke_board_prev | fwd_ret_20d | -0.0264 | -0.459 | -7.19 | 0.67 | 245 | -1 | yes |
| rev_1d_neut | fwd_ret_1d | -0.0207 | -0.196 | -4.37 | 0.58 | 498 | -1 | yes |
| rev_1d_neut | fwd_ret_5d | -0.0127 | -0.136 | -3.02 | 0.55 | 498 | -1 | yes |
| rev_1d_neut | fwd_ret_10d | -0.0052 | -0.058 | -1.29 | 0.54 | 498 | -1 | yes |
| rev_1d_neut | fwd_ret_20d | -0.0006 | -0.007 | -0.15 | 0.50 | 498 | -1 | yes |
| rev_3d_neut | fwd_ret_1d | -0.0283 | -0.257 | -5.73 | 0.61 | 498 | -1 | yes |
| rev_3d_neut | fwd_ret_5d | -0.0297 | -0.306 | -6.84 | 0.59 | 498 | -1 | yes |
| rev_3d_neut | fwd_ret_10d | -0.0224 | -0.244 | -5.45 | 0.60 | 498 | -1 | yes |
| rev_3d_neut | fwd_ret_20d | -0.0222 | -0.259 | -5.78 | 0.60 | 498 | -1 | yes |
| max_5d_neut | fwd_ret_1d | -0.0236 | -0.244 | -5.46 | 0.62 | 498 | -1 | yes |
| max_5d_neut | fwd_ret_5d | -0.0387 | -0.441 | -9.85 | 0.68 | 498 | -1 | yes |
| max_5d_neut | fwd_ret_10d | -0.0383 | -0.431 | -9.62 | 0.67 | 498 | -1 | yes |
| max_5d_neut | fwd_ret_20d | -0.0435 | -0.508 | -11.34 | 0.69 | 498 | -1 | yes |
| turn_spike_neut | fwd_ret_1d | -0.0205 | -0.240 | -5.34 | 0.62 | 497 | -1 | yes |
| turn_spike_neut | fwd_ret_5d | -0.0173 | -0.207 | -4.62 | 0.58 | 497 | -1 | yes |
| turn_spike_neut | fwd_ret_10d | -0.0138 | -0.172 | -3.83 | 0.55 | 497 | -1 | yes |
| turn_spike_neut | fwd_ret_20d | -0.0137 | -0.174 | -3.88 | 0.54 | 497 | -1 | yes |
| n_limit_up_5d_neut | fwd_ret_1d | +0.0143 | +0.112 | +2.48 | 0.55 | 490 | -1 | **NO** |
| n_limit_up_5d_neut | fwd_ret_5d | +0.0136 | +0.104 | +2.31 | 0.55 | 490 | -1 | **NO** |
| n_limit_up_5d_neut | fwd_ret_10d | +0.0155 | +0.120 | +2.65 | 0.57 | 490 | -1 | **NO** |
| n_limit_up_5d_neut | fwd_ret_20d | +0.0161 | +0.123 | +2.72 | 0.55 | 490 | -1 | **NO** |
| intraday_ret_1d_neut | fwd_ret_1d | -0.0328 | -0.332 | -7.40 | 0.65 | 498 | +1 | **NO** |
| intraday_ret_1d_neut | fwd_ret_5d | -0.0224 | -0.250 | -5.57 | 0.60 | 498 | +1 | **NO** |
| intraday_ret_1d_neut | fwd_ret_10d | -0.0148 | -0.174 | -3.89 | 0.58 | 498 | +1 | **NO** |
| intraday_ret_1d_neut | fwd_ret_20d | -0.0102 | -0.121 | -2.70 | 0.55 | 498 | +1 | **NO** |
| overnight_gap_1d_neut | fwd_ret_1d | +0.0208 | +0.276 | +6.16 | 0.62 | 498 | -1 | **NO** |
| overnight_gap_1d_neut | fwd_ret_5d | +0.0230 | +0.314 | +7.02 | 0.63 | 498 | -1 | **NO** |
| overnight_gap_1d_neut | fwd_ret_10d | +0.0248 | +0.364 | +8.13 | 0.65 | 498 | -1 | **NO** |
| overnight_gap_1d_neut | fwd_ret_20d | +0.0240 | +0.350 | +7.81 | 0.66 | 498 | -1 | **NO** |
| limit_streak_prev_neut | fwd_ret_1d | +0.0151 | +0.115 | +1.44 | 0.56 | 157 | -1 | **NO** |
| limit_streak_prev_neut | fwd_ret_5d | +0.0174 | +0.126 | +1.58 | 0.54 | 157 | -1 | **NO** |
| limit_streak_prev_neut | fwd_ret_10d | +0.0257 | +0.176 | +2.20 | 0.58 | 157 | -1 | **NO** |
| limit_streak_prev_neut | fwd_ret_20d | +0.0193 | +0.139 | +1.74 | 0.54 | 157 | -1 | **NO** |
| broke_board_prev_neut | fwd_ret_1d | -0.0119 | -0.087 | -0.82 | 0.53 | 89 | -1 | yes |
| broke_board_prev_neut | fwd_ret_5d | -0.0122 | -0.088 | -0.83 | 0.55 | 89 | -1 | yes |
| broke_board_prev_neut | fwd_ret_10d | +0.0079 | +0.051 | +0.48 | 0.54 | 89 | -1 | **NO** |
| broke_board_prev_neut | fwd_ret_20d | +0.0054 | +0.035 | +0.33 | 0.49 | 89 | -1 | **NO** |

## 7. Carry decision

- **Survivors (neut |t| ≥ 3 + aligned + |corr| ≤ 0.7)**: `rev_1d, max_5d, turn_spike`.
- **Dropped — no signal (neut |t| < 3 or misaligned)**: `n_limit_up_5d, intraday_ret_1d, overnight_gap_1d, limit_streak_prev, broke_board_prev`.
- **Dropped — redundant with the round-1 carry cluster (|corr| > 0.7)**: `rev_3d`.
- **Dropped — redundant with a stronger QGR factor**: `(none)`.
- **Thin-collinearity-support survivors (< 60 dates, carried but flagged)**: `(none)`.
- Weak / redundant factors are dropped honestly (as R2-2 dropped momentum, R3-3 dropped SUE). The survivor set is the QGR-4 short-leg candidate axis; if empty, the fast leg adds no orthogonal signal — reported, not papered over.


## 8. Honest read (development evidence ≠ verdict)
- **Strong neutralized IC is NECESSARY, NOT SUFFICIENT.** Rounds 1-4 all had strong train_val IC yet the locked test FAILed three times; the honest gates correctly warned each time. A high |t| here does not pre-judge anything — QGR-3 runs NO search and makes NO promotion.
- **Sign verified from zero**: the A-share daily/short reversal and anti-lottery priors are CONFIRMED or REFUTED on real data here, never assumed; a refuted-sign factor is dropped.
- **Limit-loser caveat (§5)**: the reversal loser leg is polluted by at-down-limit falling knives; the strategy filters them, the diagnostic discloses the effect.
- **Limit-board factors are post-2020-only**: `limit_list_d` starts 2020, so `limit_streak_prev` / `broke_board_prev` are None for every pre-2020 rebalance date (§1 defined-rate ≈ the post-2020 fraction, NOT data loss). Their IC is measured on the post-2020 sub-regime — read alongside the full-window 1/5/10/20d cousins, not as comparable coverage.

