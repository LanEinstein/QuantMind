# 量化选股策略研究 R5 — 前向 OOS 启动 + train_val 稳健体检(2026-06-21)

> round-4 = 四轮首个四门全过,但 **provisional**(dev DSR 0.007/哨兵不过未背书 + 第 4 次评测)。
> owner「开:摄取前向窗口数据 + 跑冻结策略处子 OOS + 并行稳健体检」。本文件 = 两件事的诚实结果:
> ① 前向 OOS 管线已搭好 + 启动时钟(但**数据不足,出不了 verdict,需累积数月**);
> ② train_val 稳健体检 = **provisional PASS 既非纯真也非纯假**:有真分析师边际(集中在 rev_diff),
> 但大块表观超额来自构造/size tilt(噪声地板高)。冻结策略未动,判定仍在真前向窗口。

---

## 1. 前向窗口 OOS — 状态 = ACCRUING(累积中,非 verdict)

**硬约束(authoritative Tushare 交易日历)**:test_end=2026-06-12 后只有 **4 个交易日**(06-15/16/17/18),然后 **端午节+周末封市**(06-19/20/21)。下一个交易日 = 06-22。冻结策略 horizon=5,首个前向调仓的 5 日 fwd 收益需要 5 个后续交易日 → **0 个可评分期**。

```
R5 FORWARD-WINDOW VIRGIN OOS (postdate test_end 2026-06-12)
forward_td=4 (20260615..20260618)  scoreable_periods=0
STATUS: ACCRUING  (need > HORIZON=5 forward td for one complete 5d label)
```

**已做(启动时钟)**:① 前向 `daily`/`daily_basic`/`adj_factor` 摄取 06-15..18(16 快照,字节+checksum,`ingest_historical_pit --start 20260613`)；② 前向 `report_rc` June "20260618" 快照(1 新,幂等,`ingest_round2_data --phase round4`,supersede 旧 06-12 cap)。**管线端到端验证通过**(build_r4_inputs 载 150 report_rc 月含新 June + build_forward_panel_r4 建 06-15 cohort 1394 行,分析师列在,0 可评分=正确)。

**管线已就位(re-run 一行)**:`$PY -m scripts.factor_research.round4_forward_test`。随数据累积重跑;**≥MIN_FORWARD_PERIODS(8)才出四门读数**,且 8-20 仍属 tentative,**~20-40 期(~5-10 月)才够信**。**前置:跑真前向 verdict 前须 refresh `csi300_daily.csv`(append 前向 index_daily 000300.SH 收盘)+ index_weight**(runner 有 fail-closed 闸:csi300 不覆盖可评分前向日即拒,防静默少计)。

> **诚实**:前向窗口是化解 round-4 两条 caveat(低 DSR/第 4 次评测)的**唯一干净办法**,但它需要**日历时间**累积。今天只能启动时钟,不能出判定。

---

## 2. train_val 稳健体检 — provisional PASS 的成色(dev 证据,未碰 test)

冻结的 16 权重 composite(constituent_only/k0.2/a_max0.04)在 **train_val(498 日 2015-2025)**上的三组探针(firewall <test_start)。

### 2.1 倾斜强度扫描(positive excess 是否只靠激进 tilt?)
| k | 超额(train_val) | IR | TE | size_active |
|---|---|---|---|---|
| 0.05 | +54.10% | +1.48 | 3.31% | +0.080 |
| 0.10 | +54.26% | +1.55 | 3.18% | +0.070 |
| **0.20(冻结)** | +52.16% | +1.53 | 3.10% | +0.064 |

→ **倾斜-稳健 ✓**:超额/IR 在 k=0.05/0.10/0.20 **几乎一致**(IR ~1.5)。冻结的激进 k=0.20 **不特殊**,弱 tilt 也成立 → **不是「激进 tilt 放大噪声」的脆弱产物**(部分缓解 R4-5 哨兵不过的担忧)。

### 2.2 哨兵 by k(真组合 IR vs 洗牌噪声 IR;real>noise=过)
| k | real_IR | noise_IR(洗牌) | 过? |
|---|---|---|---|
| 0.05 | +1.48 | +1.46 | True(薄) |
| 0.10 | +1.55 | +1.46 | True |
| 0.20 | +1.53 | +1.46 | True(薄,差 0.07) |

→ **关键 caveat ⚠️**:full train_val 上真组合**全 k 都险胜**噪声,但 **margin 极薄**(1.53 vs 1.46)。**噪声地板 IR ~1.46 很高** —— 即便**等权随机(洗牌)因子**经 constituent_only 构造也产 IR 1.46。说明 **train_val IR 一大块来自构造本身**(很可能 long-only floor 不对称 → 小盘 tilt,印证 size_active +0.06-0.08),**而非因子信号**;真因子边际 OVER 构造仅 ~0.07 IR。**这解释了 R4-5 inner-val 哨兵失败**(更难的 108 日窗噪声 1.34 > 真 0.71)**+ 低 DSR**。(注:哨兵=冻结权重真 vs 等权洗牌,沿用 round2_search 约定。)

### 2.3 因子消融(at 冻结 k=0.2;分析师块的边际贡献 + 集中度)
| 变体 | 超额 | IR |
|---|---|---|
| frozen_full(16 因子) | +52.16% | +1.53 |
| drop_rev_diff(去主导分析师) | +25.40% | +0.77 |
| no_analyst_block(去全 4 分析师) | +20.38% | +0.62 |

→ ① **分析师块真贡献 ✓**:去全 4 分析师 → 超额 52%→20%(IR 1.53→0.62)= 分析师扛了 **>半数** train_val 超额,非装饰;② **但重度 rev_diff-集中 ⚠️**:单去 rev_diff → 52%→25%(一半超额来自一个因子)= **单因子集中**(rev_diff OOS 衰减则边际坍塌的脆弱)。

---

## 3. 综合研读(provisional PASS 的成色)

**round-4 的 +2.68% 测试超额 = 三股力的混合,诚实拆解:**
1. **真分析师边际**(消融证:>半数 train_val 超额来自分析师块)—— 但**重度集中在 rev_diff**(单因子),非分散的 4-因子信息流。
2. **倾斜-稳健**(扫描证:弱 tilt 也成立)—— 这一条是**加分**,削弱「靠激进 tilt」的担忧。
3. **构造/size tilt**(哨兵证:噪声地板 IR 1.46 高;size_active 全程 +0.06~0.09)—— **一大块表观超额是 constituent_only 构造的内建小盘倾斜**(2025H2 小盘强→帮了 test 的 +2.68%);regime 反转(大盘领涨)则会**反咬**。

**∴ provisional PASS 既非纯真也非纯假**:有一个**薄的、真实的、集中于 rev_diff 的分析师边际**,骑在一个**构造/size tilt** 上。低 DSR + 哨兵薄胜 = 「因子 OVER 构造的边际薄 + rev_diff 集中」的诚实信号。**这不推翻 round-4 PASS(四门客观全过、无偷看),但精确了它的成色 → 更须真前向窗口确认**:① rev_diff 前向是否续работа;② size tilt 前向 regime 是帮是咬。

---

## 4. 下一步(owner 拍板)

1. **(主)前向窗口累积 → 真处子 OOS**:管线已就位(`round4_forward_test`)。随日历时间重跑(建议每月,数据 ≥20-40 期才出可信前向 verdict)。**verdict 前 refresh csi300/index_weight**(runner 已 fail-closed 防 stale)。过 → 升「确认稳健」可议上线 gate;不过/边际 → 如实记,rev_diff 衰减或 size tilt 反咬即印证 provisional 的脆弱。
2. **(可选)缓解 rev_diff 集中**:研究分散分析师信号(np_rev/tp_impl/cover_chg 各自前向贡献)或加非-size-tilt 约束(组合级 size 中性,把构造地板的小盘 bet 剥掉,看纯因子 alpha 还剩多少)—— **但任何改动 = 新策略,须重新冻结 + 新前向窗口**,不可在已 4 评的 test 上再迭代。
3. **(备选)新正交料**:资金流/事件/筹码(¥0 已解锁)叠加 —— 同样**测试集已 4 次评测,慎第 5 次**,优先前向。

---

## 5. 诚实保障(全程未破)
- **冻结策略未动**(`ffc1db3`);前向 = 同一冻结策略的处子 OOS;robustness = train_val only(firewall <test_start,`assert_all_not_test`)。
- **PIT/无前视**:前向 label 仅用前向 bar(>test_end);buffer(≤test_end)仅作 feature;`report_date<d`。
- **fail-closed**:前向窗口太短 → ACCRUING(绝不在噪声上出 verdict);csi300 不覆盖 → 拒(防静默少计)。
- **门禁**:390 测试绿(+7)+ ruff + mypy --strict(32)+ redline;`/code-review high` 2 finder 0 PIT-leakage/correctness,2 finding(P2 benchmark 闸 + P3 消融测试非退化)全修。
- 数据源仅 Tushare 官方 SDK;离线;governance enum 未动;永禁真实下单。

### artifact
- 前向状态:`data/factor_research/round4_forward_status.json`(ACCRUING)
- 稳健体检:`data/factor_research/round4_robustness_study.json`
- 代码:`scripts/factor_research/{round4_forward_test,r4_robustness_study}.py` + `build_factor_panel.build_forward_panel_r4/forward_trade_dates`(commit `c2a8c6e`)
