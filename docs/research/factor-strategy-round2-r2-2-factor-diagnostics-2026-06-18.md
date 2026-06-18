# Round-2 factor diagnostics (R2-2, train_val only)

> Panel: 326854 rows / 3003 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, winsor=0.01, min_obs=20.
> PIT industry coverage (rows with an SW L1): **66.28%** (long-delisted names absent from the current SW table → neutralized factor None for those, raw factor retained).

## 1. Fundamentals vintage audit (PIT contamination)

- codes with fundamentals: **6682**
- (code, report-period) cells: **216452**
- restated (≥2 distinct ann_date): **214** (**0.10%**)
- median announcement lag (ann_date − end_date): **54d**
- median restatement gap (latest − first ann): **267d**


## 2. New-factor honest verdict (raw)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| mom_12_1 | fwd_ret_5d | +0.0093 | +1.10 | yes | no |
| dist_high | fwd_ret_20d | +0.0070 | +0.77 | yes | no |
| trend_slope | fwd_ret_20d | -0.0603 | -7.51 | **NO** | yes |
| roe | fwd_ret_20d | +0.0519 | +6.94 | yes | yes |
| gpm | fwd_ret_20d | +0.0203 | +3.75 | yes | yes |
| np_yoy | fwd_ret_20d | +0.0135 | +2.35 | yes | no |
| rev_yoy | fwd_ret_20d | +0.0166 | +2.88 | yes | no |

## 3. New-factor honest verdict (industry+size neutralized)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| mom_12_1_neut | fwd_ret_5d | +0.0052 | +0.99 | yes | no |
| dist_high_neut | fwd_ret_5d | -0.0067 | -1.11 | **NO** | no |
| trend_slope_neut | fwd_ret_20d | -0.0379 | -7.00 | **NO** | yes |
| roe_neut | fwd_ret_20d | +0.0241 | +5.92 | yes | yes |
| gpm_neut | fwd_ret_20d | +0.0156 | +4.40 | yes | yes |
| np_yoy_neut | fwd_ret_20d | +0.0164 | +4.59 | yes | yes |
| rev_yoy_neut | fwd_ret_20d | +0.0224 | +5.58 | yes | yes |

## 4. Full IC table — raw factors (round-1 + round-2)

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| ret_5d | fwd_ret_5d | -0.0413 | -0.257 | -5.74 | 0.60 | 498 | -1 | yes |
| ret_5d | fwd_ret_10d | -0.0368 | -0.233 | -5.19 | 0.57 | 498 | -1 | yes |
| ret_5d | fwd_ret_20d | -0.0342 | -0.216 | -4.83 | 0.58 | 498 | -1 | yes |
| ret_20d | fwd_ret_5d | -0.0500 | -0.276 | -6.17 | 0.61 | 498 | -1 | yes |
| ret_20d | fwd_ret_10d | -0.0589 | -0.333 | -7.43 | 0.62 | 498 | -1 | yes |
| ret_20d | fwd_ret_20d | -0.0697 | -0.419 | -9.35 | 0.66 | 498 | -1 | yes |
| vol_20d | fwd_ret_5d | -0.0654 | -0.298 | -6.65 | 0.62 | 498 | -1 | yes |
| vol_20d | fwd_ret_10d | -0.0780 | -0.353 | -7.88 | 0.66 | 498 | -1 | yes |
| vol_20d | fwd_ret_20d | -0.0968 | -0.444 | -9.91 | 0.68 | 498 | -1 | yes |
| max_20d | fwd_ret_5d | -0.0621 | -0.369 | -8.24 | 0.65 | 498 | -1 | yes |
| max_20d | fwd_ret_10d | -0.0737 | -0.431 | -9.62 | 0.65 | 498 | -1 | yes |
| max_20d | fwd_ret_20d | -0.0908 | -0.539 | -12.03 | 0.72 | 498 | -1 | yes |
| ep_ttm | fwd_ret_5d | +0.0410 | +0.192 | +4.27 | 0.56 | 498 | +1 | yes |
| ep_ttm | fwd_ret_10d | +0.0513 | +0.229 | +5.12 | 0.57 | 498 | +1 | yes |
| ep_ttm | fwd_ret_20d | +0.0651 | +0.281 | +6.27 | 0.62 | 498 | +1 | yes |
| turn_20d | fwd_ret_5d | -0.0772 | -0.335 | -7.47 | 0.61 | 498 | -1 | yes |
| turn_20d | fwd_ret_10d | -0.0961 | -0.415 | -9.26 | 0.65 | 498 | -1 | yes |
| turn_20d | fwd_ret_20d | -0.1199 | -0.516 | -11.52 | 0.70 | 498 | -1 | yes |
| amihud_20d | fwd_ret_5d | -0.0208 | -0.143 | -3.20 | 0.58 | 498 | -1 | yes |
| amihud_20d | fwd_ret_10d | -0.0217 | -0.148 | -3.31 | 0.56 | 498 | -1 | yes |
| amihud_20d | fwd_ret_20d | -0.0221 | -0.155 | -3.47 | 0.58 | 498 | -1 | yes |
| mom_12_1 | fwd_ret_5d | +0.0093 | +0.052 | +1.10 | 0.52 | 451 | +1 | yes |
| mom_12_1 | fwd_ret_10d | +0.0078 | +0.043 | +0.91 | 0.54 | 451 | +1 | yes |
| mom_12_1 | fwd_ret_20d | +0.0035 | +0.019 | +0.40 | 0.52 | 451 | +1 | yes |
| dist_high | fwd_ret_5d | +0.0016 | +0.008 | +0.17 | 0.52 | 452 | +1 | yes |
| dist_high | fwd_ret_10d | +0.0026 | +0.013 | +0.28 | 0.52 | 452 | +1 | yes |
| dist_high | fwd_ret_20d | +0.0070 | +0.036 | +0.77 | 0.52 | 452 | +1 | yes |
| trend_slope | fwd_ret_5d | -0.0441 | -0.234 | -5.18 | 0.59 | 490 | +1 | **NO** |
| trend_slope | fwd_ret_10d | -0.0562 | -0.306 | -6.77 | 0.62 | 490 | +1 | **NO** |
| trend_slope | fwd_ret_20d | -0.0603 | -0.339 | -7.51 | 0.64 | 490 | +1 | **NO** |
| roe | fwd_ret_5d | +0.0351 | +0.224 | +4.95 | 0.58 | 488 | +1 | yes |
| roe | fwd_ret_10d | +0.0430 | +0.262 | +5.79 | 0.60 | 488 | +1 | yes |
| roe | fwd_ret_20d | +0.0519 | +0.314 | +6.94 | 0.61 | 488 | +1 | yes |
| gpm | fwd_ret_5d | +0.0154 | +0.125 | +2.76 | 0.55 | 488 | +1 | yes |
| gpm | fwd_ret_10d | +0.0185 | +0.150 | +3.31 | 0.55 | 488 | +1 | yes |
| gpm | fwd_ret_20d | +0.0203 | +0.170 | +3.75 | 0.58 | 488 | +1 | yes |
| np_yoy | fwd_ret_5d | +0.0117 | +0.100 | +2.22 | 0.56 | 488 | +1 | yes |
| np_yoy | fwd_ret_10d | +0.0118 | +0.097 | +2.14 | 0.56 | 488 | +1 | yes |
| np_yoy | fwd_ret_20d | +0.0135 | +0.106 | +2.35 | 0.56 | 488 | +1 | yes |
| rev_yoy | fwd_ret_5d | +0.0123 | +0.105 | +2.32 | 0.55 | 488 | +1 | yes |
| rev_yoy | fwd_ret_10d | +0.0140 | +0.113 | +2.49 | 0.57 | 488 | +1 | yes |
| rev_yoy | fwd_ret_20d | +0.0166 | +0.131 | +2.88 | 0.54 | 488 | +1 | yes |

## 5. Full IC table — neutralized factors

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| ret_5d_neut | fwd_ret_5d | -0.0401 | -0.398 | -8.88 | 0.62 | 498 | -1 | yes |
| ret_5d_neut | fwd_ret_10d | -0.0349 | -0.358 | -7.98 | 0.62 | 498 | -1 | yes |
| ret_5d_neut | fwd_ret_20d | -0.0321 | -0.339 | -7.58 | 0.61 | 498 | -1 | yes |
| ret_20d_neut | fwd_ret_5d | -0.0386 | -0.340 | -7.59 | 0.64 | 498 | -1 | yes |
| ret_20d_neut | fwd_ret_10d | -0.0434 | -0.401 | -8.95 | 0.65 | 498 | -1 | yes |
| ret_20d_neut | fwd_ret_20d | -0.0483 | -0.458 | -10.21 | 0.65 | 498 | -1 | yes |
| vol_20d_neut | fwd_ret_5d | -0.0417 | -0.362 | -8.09 | 0.65 | 498 | -1 | yes |
| vol_20d_neut | fwd_ret_10d | -0.0499 | -0.438 | -9.78 | 0.67 | 498 | -1 | yes |
| vol_20d_neut | fwd_ret_20d | -0.0628 | -0.545 | -12.15 | 0.71 | 498 | -1 | yes |
| max_20d_neut | fwd_ret_5d | -0.0403 | -0.438 | -9.77 | 0.66 | 498 | -1 | yes |
| max_20d_neut | fwd_ret_10d | -0.0477 | -0.513 | -11.45 | 0.69 | 498 | -1 | yes |
| max_20d_neut | fwd_ret_20d | -0.0567 | -0.644 | -14.38 | 0.76 | 498 | -1 | yes |
| ep_ttm_neut | fwd_ret_5d | +0.0295 | +0.289 | +6.45 | 0.63 | 498 | +1 | yes |
| ep_ttm_neut | fwd_ret_10d | +0.0349 | +0.329 | +7.34 | 0.60 | 498 | +1 | yes |
| ep_ttm_neut | fwd_ret_20d | +0.0441 | +0.392 | +8.75 | 0.62 | 498 | +1 | yes |
| turn_20d_neut | fwd_ret_5d | -0.0401 | -0.453 | -10.10 | 0.69 | 498 | -1 | yes |
| turn_20d_neut | fwd_ret_10d | -0.0499 | -0.580 | -12.94 | 0.72 | 498 | -1 | yes |
| turn_20d_neut | fwd_ret_20d | -0.0626 | -0.710 | -15.84 | 0.76 | 498 | -1 | yes |
| amihud_20d_neut | fwd_ret_5d | +0.0116 | +0.146 | +3.25 | 0.57 | 498 | -1 | **NO** |
| amihud_20d_neut | fwd_ret_10d | +0.0177 | +0.230 | +5.13 | 0.60 | 498 | -1 | **NO** |
| amihud_20d_neut | fwd_ret_20d | +0.0290 | +0.387 | +8.64 | 0.68 | 498 | -1 | **NO** |
| mom_12_1_neut | fwd_ret_5d | +0.0052 | +0.047 | +0.99 | 0.52 | 451 | +1 | yes |
| mom_12_1_neut | fwd_ret_10d | +0.0035 | +0.030 | +0.65 | 0.51 | 451 | +1 | yes |
| mom_12_1_neut | fwd_ret_20d | -0.0001 | -0.001 | -0.01 | 0.51 | 451 | +1 | **NO** |
| dist_high_neut | fwd_ret_5d | -0.0067 | -0.052 | -1.11 | 0.50 | 452 | +1 | **NO** |
| dist_high_neut | fwd_ret_10d | -0.0066 | -0.051 | -1.08 | 0.54 | 452 | +1 | **NO** |
| dist_high_neut | fwd_ret_20d | -0.0056 | -0.043 | -0.92 | 0.51 | 452 | +1 | **NO** |
| trend_slope_neut | fwd_ret_5d | -0.0274 | -0.235 | -5.21 | 0.58 | 490 | +1 | **NO** |
| trend_slope_neut | fwd_ret_10d | -0.0334 | -0.289 | -6.39 | 0.62 | 490 | +1 | **NO** |
| trend_slope_neut | fwd_ret_20d | -0.0379 | -0.316 | -7.00 | 0.66 | 490 | +1 | **NO** |
| roe_neut | fwd_ret_5d | +0.0167 | +0.197 | +4.36 | 0.59 | 488 | +1 | yes |
| roe_neut | fwd_ret_10d | +0.0187 | +0.213 | +4.71 | 0.58 | 488 | +1 | yes |
| roe_neut | fwd_ret_20d | +0.0241 | +0.268 | +5.92 | 0.59 | 488 | +1 | yes |
| gpm_neut | fwd_ret_5d | +0.0125 | +0.165 | +3.64 | 0.55 | 488 | +1 | yes |
| gpm_neut | fwd_ret_10d | +0.0145 | +0.191 | +4.22 | 0.58 | 488 | +1 | yes |
| gpm_neut | fwd_ret_20d | +0.0156 | +0.199 | +4.40 | 0.58 | 488 | +1 | yes |
| np_yoy_neut | fwd_ret_5d | +0.0098 | +0.131 | +2.89 | 0.56 | 488 | +1 | yes |
| np_yoy_neut | fwd_ret_10d | +0.0118 | +0.151 | +3.35 | 0.57 | 488 | +1 | yes |
| np_yoy_neut | fwd_ret_20d | +0.0164 | +0.208 | +4.59 | 0.59 | 488 | +1 | yes |
| rev_yoy_neut | fwd_ret_5d | +0.0120 | +0.160 | +3.53 | 0.58 | 488 | +1 | yes |
| rev_yoy_neut | fwd_ret_10d | +0.0147 | +0.177 | +3.91 | 0.58 | 488 | +1 | yes |
| rev_yoy_neut | fwd_ret_20d | +0.0224 | +0.253 | +5.58 | 0.57 | 488 | +1 | yes |

## 6. Collinearity (mean cross-sectional rank corr, raw)

```
             ret_5d  ret_20d  vol_20d  max_20d  ep_ttm  turn_20d  amihud_20d  mom_12_1  dist_high  trend_slope   roe   gpm  np_yoy  rev_yoy
ret_5d         1.00     0.42     0.03     0.14   -0.03     -0.01        0.03     -0.01       0.28         0.03  0.01  0.01    0.01     0.01
ret_20d        0.42     1.00     0.25     0.48   -0.08      0.13        0.09     -0.06       0.46         0.36 -0.01 -0.00    0.01    -0.00
vol_20d        0.03     0.25     1.00     0.82   -0.33      0.68        0.32      0.22       0.04         0.33 -0.14  0.01    0.05     0.05
max_20d        0.14     0.48     0.82     1.00   -0.26      0.53        0.26      0.11       0.14         0.34 -0.11  0.00    0.03     0.03
ep_ttm        -0.03    -0.08    -0.33    -0.26    1.00     -0.32       -0.12     -0.14       0.00        -0.13  0.46 -0.05    0.08    -0.03
turn_20d      -0.01     0.13     0.68     0.53   -0.32      1.00        0.17      0.17      -0.08         0.23 -0.20 -0.03    0.10     0.06
amihud_20d     0.03     0.09     0.32     0.26   -0.12      0.17        1.00     -0.09      -0.06         0.03 -0.18 -0.01   -0.06    -0.07
mom_12_1      -0.01    -0.06     0.22     0.11   -0.14      0.17       -0.09      1.00       0.38         0.24  0.15  0.05    0.31     0.21
dist_high      0.28     0.46     0.04     0.14    0.00     -0.08       -0.06      0.38       1.00         0.55  0.12  0.01    0.14     0.06
trend_slope    0.03     0.36     0.33     0.34   -0.13      0.23        0.03      0.24       0.55         1.00 -0.02 -0.01    0.05     0.01
roe            0.01    -0.01    -0.14    -0.11    0.46     -0.20       -0.18      0.15       0.12        -0.02  1.00  0.33    0.35     0.27
gpm            0.01    -0.00     0.01     0.00   -0.05     -0.03       -0.01      0.05       0.01        -0.01  0.33  1.00    0.06     0.08
np_yoy         0.01     0.01     0.05     0.03    0.08      0.10       -0.06      0.31       0.14         0.05  0.35  0.06    1.00     0.52
rev_yoy        0.01    -0.00     0.05     0.03   -0.03      0.06       -0.07      0.21       0.06         0.01  0.27  0.08    0.52     1.00
```

## 7. Honest read (what R2-3 should carry, and what it should NOT)

> Tables 1–6 are auto-generated; this section is the human interpretation. All
> reads are **train_val in-sample** — IC ≠ a tradable edge, and the verdict
> still awaits the frozen-then-forward test (R2-6). The |t|≥3 bar is the
> Harvey-Liu-Zhu multiple-testing floor; the overlapping-window t-stat is
> optimistic, so treat |t| in (3,5) as "promising, not proven".

**1. Quality is a genuine NEW orthogonal signal — CARRY.** `roe` (t=+6.94) and
`gpm` (t=+3.75) are the only new raw factors that clear |t|≥3 with the right
sign, and both SURVIVE industry+size neutralization (`roe_neut` t=+5.92,
`gpm_neut` t=+4.40). Collinearity is low against the round-1 reversal/vol/turn
cluster (roe↔ep_ttm 0.46 is the only notable overlap, the expected value-quality
link; gpm is near-orthogonal to everything). Quality fills a real round-1 gap.

**2. Growth is weak raw but STRENGTHENS after neutralization — CARRY (neutralized).**
`np_yoy` (raw t=+2.35) and `rev_yoy` (raw t=+2.88) are sub-threshold raw, but
industry+size neutralization roughly doubles their signal (`np_yoy_neut` t=+4.59,
`rev_yoy_neut` t=+5.58). Mechanism: raw growth is partly a sector/size artefact;
the residual is a cleaner cross-sectional growth premium. Carry the **neutralized**
form; they are mutually correlated (0.52) so count as ~one growth axis, not two.

**3. Momentum / 52-week-high carry NO signal — DROP.** `mom_12_1` (t=+1.10) and
`dist_high` (t=+0.77) are flat raw AND neutralized — a direct empirical
confirmation of the Phase-1 survey's "A-share momentum is weak/absent". They were
added as a possible regime/bull-tracking sleeve; on this train_val they do not
even track cross-sectionally. **Do not assume alpha; drop from the R2-3 composite.**

**4. `trend_slope` is a REVERSAL in disguise — do not treat as momentum.** Its IC
is strongly negative (t=−7.51, opposite the momentum prior) and it is correlated
with the round-1 reversal/vol cluster (↔ret_20d 0.36, ↔vol_20d 0.33). It is
re-discovering short-term over-extension → mean reversion, already covered by
ret_5d/ret_20d/vol_20d. Mechanistically redundant; **do not add it as a trend leg.**

**5. The "can't track a bull" problem is NOT solved by a trend factor.** The
round-1 FAIL root cause (a defensive book lagging a cap-weighted bull) has no
cross-sectional-factor fix here — momentum is absent and trend is reversal. The
fix must come from the **benchmark-relative construction itself** (R2-3: active
weights off the CSI300 weights, beta≈1, industry/size-neutral tilt), not from a
new return factor. R2-2 supplies cleaner orthogonal *alpha* (quality/growth);
tracking the index is a *construction* job.

**6. `amihud` neutralized sign-flips — caution against double-counting size.**
Raw `amihud_20d` (t=−3.47, illiquid→low return after the size cut) flips to
POSITIVE once size is neutralized (`amihud_20d_neut` t=+8.64). Raw amihud is
largely a size proxy; its size-orthogonal residual is the classic illiquidity
*premium* (illiquid→higher return). In a size-neutral benchmark-relative book the
neutralized sign is the relevant one — do not carry the raw-amihud orientation
blindly alongside an explicit size control.

**7. PIT integrity is strong; the industry gap is the real caveat.**
- Vintage contamination is low: only **0.10%** of (code, report-period) cells
  carry a genuine restatement (≥2 distinct ann_date), median announcement lag 54d.
  The ann_date<d gating is robust; quality/growth can claim PIT (caveat: Tushare
  may collapse some pre-2017 vintages, so 0.10% is a floor, not a guarantee).
- **Industry coverage is only 66.3%** of code-dates. The `index_member_all`
  snapshot is the *current* SW roster, so long-delisted / reclassified names have
  no PIT L1 and their neutralized factor is `None` (raw retained). R2-3's
  benchmark-relative arm leans on industry neutralization, so this 34% gap is a
  real limitation: either source a historical-complete SW membership table, or
  bound/disclose the no-industry sleeve. **Flagged, not hidden.**

**Carry-forward set for R2-3** = round-1 seven (reversal `ret_5d`/`ret_20d`, low-vol
`vol_20d`, anti-lottery `max_20d`, value `ep_ttm`, turnover `turn_20d`, liquidity
`amihud_20d`) **＋ quality `roe`/`gpm` ＋ growth `np_yoy`/`rev_yoy` (neutralized)**;
**drop** `mom_12_1`, `dist_high`, `trend_slope`. Neutralized inputs (`*_neut`) are
the construction-ready columns; the index-tracking fix is the benchmark-relative
builder, not a factor.
