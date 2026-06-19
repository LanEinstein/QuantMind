# Round-3 factor diagnostics (R3-3, train_val only)

> Panel: 325718 rows / 3001 codes / 498 rebalance dates (train_val; the sealed test window is never read).
> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, winsor=0.01, min_obs=20.
> PIT industry coverage (rows with an SW L1): **66.33%**.
> Inclusion gate: neutralized |t| ≥ 3 + aligned sign + low collinearity (≤ 0.7).

## 1. New-factor honest verdict (raw)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| sue | fwd_ret_20d | +0.0311 | +4.44 | yes | yes |
| accr | fwd_ret_20d | -0.0205 | -4.48 | yes | yes |
| asset_growth | fwd_ret_20d | +0.0159 | +2.60 | **NO** | no |

## 2. New-factor honest verdict (industry+size neutralized)

| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |
|---|---|---|---|---|---|
| sue_neut | fwd_ret_20d | +0.0129 | +2.74 | yes | no |
| accr_neut | fwd_ret_20d | -0.0132 | -4.35 | yes | yes |
| asset_growth_neut | fwd_ret_20d | +0.0101 | +2.68 | **NO** | no |

## 3. Collinearity vs the round-2 carry cluster

| new factor | most-collinear carry | |corr| | redundant (>0.7)? |
|---|---|---|---|
| sue | np_yoy | 0.60 | no |
| accr | np_yoy | 0.13 | no |
| asset_growth | rev_yoy | 0.29 | no |

accr ↔ asset_growth mutual |corr| = **0.24** (distinct axes).

## 4. IC tables — round-3 factors (raw + neutralized)

| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |
|---|---|---|---|---|---|---|---|---|
| sue | fwd_ret_5d | +0.0228 | +0.187 | +3.63 | 0.59 | 376 | +1 | yes |
| sue | fwd_ret_10d | +0.0261 | +0.202 | +3.91 | 0.60 | 376 | +1 | yes |
| sue | fwd_ret_20d | +0.0311 | +0.229 | +4.44 | 0.63 | 376 | +1 | yes |
| accr | fwd_ret_5d | -0.0125 | -0.136 | -2.70 | 0.58 | 396 | -1 | yes |
| accr | fwd_ret_10d | -0.0154 | -0.169 | -3.36 | 0.60 | 396 | -1 | yes |
| accr | fwd_ret_20d | -0.0205 | -0.225 | -4.48 | 0.62 | 396 | -1 | yes |
| asset_growth | fwd_ret_5d | +0.0123 | +0.107 | +2.14 | 0.57 | 396 | -1 | **NO** |
| asset_growth | fwd_ret_10d | +0.0145 | +0.121 | +2.41 | 0.55 | 396 | -1 | **NO** |
| asset_growth | fwd_ret_20d | +0.0159 | +0.131 | +2.60 | 0.57 | 396 | -1 | **NO** |
| sue_neut | fwd_ret_5d | +0.0080 | +0.102 | +1.98 | 0.55 | 375 | +1 | yes |
| sue_neut | fwd_ret_10d | +0.0089 | +0.106 | +2.05 | 0.58 | 375 | +1 | yes |
| sue_neut | fwd_ret_20d | +0.0129 | +0.142 | +2.74 | 0.57 | 375 | +1 | yes |
| accr_neut | fwd_ret_5d | -0.0054 | -0.092 | -1.83 | 0.55 | 395 | -1 | yes |
| accr_neut | fwd_ret_10d | -0.0077 | -0.128 | -2.55 | 0.55 | 395 | -1 | yes |
| accr_neut | fwd_ret_20d | -0.0132 | -0.219 | -4.35 | 0.59 | 395 | -1 | yes |
| asset_growth_neut | fwd_ret_5d | +0.0071 | +0.095 | +1.89 | 0.51 | 395 | -1 | **NO** |
| asset_growth_neut | fwd_ret_10d | +0.0077 | +0.101 | +2.01 | 0.52 | 395 | -1 | **NO** |
| asset_growth_neut | fwd_ret_20d | +0.0101 | +0.135 | +2.68 | 0.54 | 395 | -1 | **NO** |

## 5. Statement vintage audits (PIT restatement contamination)


**fina profit_dedt (SUE)**
- codes with fundamentals: **6682**
- (code, report-period) cells: **216452**
- restated (≥2 distinct ann_date): **214** (**0.10%**)
- median announcement lag (ann_date − end_date): **54d**
- median restatement gap (latest − first ann): **267d**

**income n_income (accruals)**
- codes with fundamentals: **6330**
- (code, report-period) cells: **197127**
- restated (≥2 distinct ann_date): **385** (**0.20%**)
- median announcement lag (ann_date − end_date): **51d**
- median restatement gap (latest − first ann): **68d**

**cashflow n_cashflow_act (accruals)**
- codes with fundamentals: **6318**
- (code, report-period) cells: **196411**
- restated (≥2 distinct ann_date): **338** (**0.17%**)
- median announcement lag (ann_date − end_date): **51d**
- median restatement gap (latest − first ann): **66d**

**balancesheet total_assets (accr/AG)**
- codes with fundamentals: **6344**
- (code, report-period) cells: **192653**
- restated (≥2 distinct ann_date): **544** (**0.28%**)
- median announcement lag (ann_date − end_date): **49d**
- median restatement gap (latest − first ann): **84d**


## 6. Carry decision

- **Survivors (neut |t| ≥ 3 + aligned + |corr| ≤ 0.7)**: `accr`.
- **Dropped as redundant (|corr| > 0.7)**: `(none)`.
- **R3_CARRY = round-2 eleven ∪ survivors** = `ret_5d, ret_20d, vol_20d, max_20d, ep_ttm, turn_20d, amihud_20d, roe, gpm, np_yoy, rev_yoy, accr`.
- Weak/redundant factors are dropped honestly (as R2-2 dropped momentum/trend). If the carry increment is empty, R3-4 cannot add a new alpha source and the round likely still FAILs — reported, not papered over.

