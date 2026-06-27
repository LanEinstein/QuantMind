# 批 B1 · regime-gated 暴露 de-risk overlay 事件循环消融(train_val only)

> **状态**:真 train_val 跑结果(loop-engineering 自驱动第 1 刀:把 owner「控回撤」从 QGR-4「买入集 veto 不控回撤」推进到「regime-gated 真去暴露」)· **未改 governance / 未碰 live / 引擎字节一行未动(cash 经包装器叠加)/ sim 暂停** · **日期**:2026-06-26 · **作者**:Claude(Opus 4.8,自驱动 loop)
> **上位**:`mfi-batch-b1-regime-derisk-spec-2026-06-26.md`(pre-committed spec + §6 证伪台账「预承诺报 FAIL」)+ `qgr-2-eval-arena-freeze-spec-2026-06-22.md`(净 P&L + MDD≤8%)+ `qgr-4-exit-veto-ablation-results-2026-06-26.md`(起点)+ `main-force-intent-research-program-macro-direction-2026-06-26.md` §2.1/§2.3。
> **代码**:`scripts/factor_research/regime_detector.py` + `derisk_overlay_panel.py` + `derisk_regime_ablation.py` + tests(18 单测)。**codex 前置门** = `/code-review high`(40 agent,1 CONFIRMED + 多 PLAUSIBLE 全修;详 §6)。

## 0. 一句话裁决

**FAIL on every gate,如实报 —— 且比预承诺预期更糟。** regime-gated 轮动到现金 overlay 在 ≤5 槽满仓轮动竞技场里**不仅没控住回撤,反而让 MDD 略升**(baseline 54.2% → derisk_regime **60.4%**,owner 8% cap 远不可达),净 P&L 也降(+459k→+347k)。更关键:**regime 择时是负技艺** —— 两个「同量恒定/随机持现金」placebo **双双击败** regime 臂(配对 t = −0.39 / −0.36),即「盲目铺开现金」严格优于「在检测到的崩盘里集中持现金」。诚实机理:**(a)满仓 ≤1-轮换/日的引擎结构上去不掉暴露**(derisk 臂均暴露 0.60 ≥ baseline 0.58,104 个治理日只触发 18 次现金轮动,主要是空转加换手 0.33 vs 0.23);**(b)−10% 回撤检测器触发太晚 + 持现金穿过 A 股 V 型反弹** → derisk 臂在「震荡」regime 最差(−0.298 vs baseline +0.061)。这**双重印证纲领 §2.1/§2.3**(拥挤/regime 预测崩盘概率而非精确择时;「连顶级量化都无法精确测拥挤及时撤」)**并坐实 spec §1 结构墙**:控回撤的边**不在 rotation-only 竞技场能表达的任何 overlay 里**。

## 1. 方法(复用 QGR-2 冻结竞技场 + 合成 cash 叠加,引擎字节不动)

- **竞技场**:`backend.backtest` 真事件循环(≤5 槽 / 5td 轮动 / T+1 / 分板块滑点 / 涨停不可成交),经 `gate_backtest` + `PitBarSource`(K-002 PIT 字节,qfq as-of pin,真 stk_limit)。窗口:**497 rebalance / 2484 daily / universe 1823 + CSI300 ETF;20150209–20250425**。
- **结构约束(决定机制,spec §1)**:冻结引擎**只建模 ≤5 槽轮动**,SELL 只来自轮动(需挑战者,每日 ≤1)且 `protective_stop`/`hard_exit` 使在位者**被保护不被卖** → 满仓 by construction。live 真 EXIT 路径(Line-2/RiskEngine)是 proxy 边界,不在竞技场。→ 唯一忠实每日暴露杠杆 = **轮动到一个赢得轮动的合成 CASH sleeve**(flat ¥100、board=etf、宽 limit、巨 ADV;经新 `CashAugmentedBarSource` 叠加,引擎/bar source 字节不动)。
- **regime 检测器(PIT-clean,无前视,预承诺阈值)**:CSI300 ETF `510300.SH`(`fund_daily`)trailing 60d 峰值回撤 ≤ **−10%** → high_risk;causal/trailing-only(单测断言)。**market coverage 2484/2484 = 100%**(fail-closed 守门通过);**high_risk = 104/497 rebalance 日(~21%)**。
- **三臂 + 1 placebo(同一 ranker / 同一 cash 机制 / 同一撮合 / 同一持有窗;唯一差异 = cash 处理的日期集)**:
  ① **baseline** = QGR-3 幸存 ranker `{rev_1d,max_5d,turn_spike}` 单独,满仓无 cash。
  ② **derisk_regime** = 在 104 高危 regime 日注入 cash 处理(cash 强挑战者 + 该日股票在位强制 de-risk 资格〔independently_weak〕→ 轮动把弱股槽换入 cash)。
  ③ **placebo_constant** / ④ **placebo_random** = 在 regime-**盲**的等距/随机 104 日注入同样处理(匹配治理日数)。
  > **关键**:cash 机制对 placebo 同样施加,**唯一差异是治理日期集** → regime 臂任何边只能来自 TIMING,非机制。机制真穿透事件循环(强制 cash 探针证 day-1 买满 5 cash sleeve、MDD→0.01%)。
- **部署 baseline(beta 守门)**:random_top5 / etf_only_510300 / csi300_etf_hold。
- **统计**:SPA(Hansen)+ Romano-Wolf vs csi300;DSR-HAC(非清零 legacy floor N=2384);regime 分层(竞技场自身 beta 序列 trailing-4 cumret);regime-vs-placebo 配对 t。
- **判据(pre-committed spec §4)**:deployable 须**全部** ① MDD≤8% ② 净 P&L>0 ③ **严格**击败两 placebo(配对 t≥2)④ DSR≥0.95。

## 2. 结果(真 train_val 跑)

### 2.1 各臂绝对净盈 + 控回撤(初始 ¥1,000,000)

| 臂 | 净 P&L (¥) | **MDD** | MDD≤8% | 均暴露 | 月换手 | cash 轮动(intents) | 治理日 |
|---|---|---|---|---|---|---|---|
| baseline | +459,000 | 54.23% | ❌ | 0.58 | 0.23 | 0 | 0 |
| **derisk_regime** | +346,556 | **60.41%** | ❌ | 0.60 | 0.33 | 18 | 104 |
| placebo_constant | +543,738 | **49.72%** | ❌ | 0.59 | 0.30 | 39 | 104 |
| placebo_random | +533,927 | 55.44% | ❌ | 0.54 | 0.29 | 26 | 104 |
| random_top5 | +119,085 | 56.92% | ❌ | 0.74 | 0.01 | — | — |
| etf_only_510300 | +23,827 | 10.61% | ❌ | 0.17 | 0.00 | — | — |
| csi300_etf_hold | +176,112 | 46.33% | ❌ | 1.00 | 0.00 | — | — |

- **derisk_regime MDD 60.4% > baseline 54.2%**:regime 去暴露**反而让回撤更糟**(迟滞 + 穿过反弹的空转换手所致),且净 P&L 更低。owner 8% cap 远不可达。
- **均暴露 0.60 ≥ baseline 0.58 + cash 轮动仅 18/104**:≤1-轮换/日 + 龄门(condition 3,engine-tracked,新轮入仓须 aging 才可再卖)使 rotate-to-cash **几乎咬不动满仓股票书**;治理主要表现为换手上升(0.33 vs 0.23)= 空转,而非真去暴露。

### 2.2 regime 择时是负技艺(配对 t,核心科学问题)

| 对比 | mean_diff | **paired t** | 解读 |
|---|---|---|---|
| derisk_regime − baseline | −0.00030 | **−0.34** | regime 去暴露**劣于**不去暴露 |
| derisk_regime − placebo_constant | −0.00034 | **−0.39** | 盲目恒定持现金**严格优于** regime 择时 |
| derisk_regime − placebo_random | −0.00033 | **−0.36** | 盲目随机持现金**亦优于** regime 择时 |

- **两 placebo 净 P&L 双双高于 regime 臂**(constant +544k / random +534k vs regime +347k),placebo_constant MDD(49.7%)亦低于 regime(60.4%)→ **regime TIMING 加负值**(配对 t 全为负,远低于严格 t≥2)。即:在「检测到的崩盘」里集中持现金,**不如**把同量现金均匀铺开。

### 2.3 DSR / SPA / Romano-Wolf

- **DSR(N=2384)**:baseline 0.0051 / **derisk_regime 0.0033** / placebo_constant 0.0064 / placebo_random 0.0066 → **无臂存活风险调整边**(门 0.95);regime 臂最低。
- **SPA p = 0.592;Romano-Wolf 全 rejected=False**;各臂 vs CSI300 t = baseline 0.27 / **regime 0.006** / placebo 0.28–0.30 → **无臂击败 beta**,regime 臂几乎零超额。

### 2.4 regime 分层(揭示机理 —— 为何 regime 择时失败)

竞技场自身 beta trailing-4 cumret 分 {牛 138 / 熊 128 / 震荡 230};表 = sum_period_return / worst_period:

| 臂 | 牛 | 熊 | 震荡 |
|---|---|---|---|
| baseline | +0.774 / −0.079 | **−0.221** / −0.181 | +0.061 / −0.080 |
| derisk_regime | +0.555 / −0.074 | **+0.207** / −0.114 | **−0.298** / −0.058 |
| placebo_constant | +0.760 / −0.082 | +0.113 / −0.124 | −0.241 / −0.084 |
| placebo_random | +0.722 / −0.077 | −0.173 / −0.147 | +0.077 / −0.077 |

- **机理诚实点**:derisk_regime 在**深熊**确实最好(+0.207 vs baseline −0.221)—— 持现金在真崩盘段有效。**但它在「震荡」灾难性最差**(−0.298 vs baseline +0.061):**−10% 回撤检测器触发晚**(崩盘已发生才 ≤−10%)**且持现金穿过 A 股 V 型反弹**(回撤要时间才爬回 −10% 上方,期间被判 high_risk 持现金 → 错过violent 反弹)。熊市那点收益被震荡段的反弹缺席**远远抹平** → 总体劣于 baseline 和两 placebo。这正是 §2.1「regime 预测崩盘概率而非精确择时」+ §2.3「无法及时撤/及时回」的实证。

## 3. 判据裁决(pre-committed spec §4 — FAIL 报 FAIL)

| 门 | 结果 | 值 |
|---|---|---|
| ① MDD≤8% | ❌ FAIL | regime 60.4% ≫ 8%(且 > baseline 54.2%) |
| ② 净 P&L>0 | ✅ pass | +346,556(但 < baseline +459,000) |
| ③ 严格击败两 placebo(t≥2) | ❌ FAIL | t = −0.39 / −0.36(负技艺) |
| ④ DSR≥0.95 | ❌ FAIL | regime DSR 0.0033 |
| **deployable_edge** | **❌ FALSE** | — |

**证伪台账(spec §6)逐条兑现**:B1-a FAIL(MDD 不仅没到 8%,反升;rotation-only 竞技场结构上去暴露太慢)/ B1-b FAIL(regime 择时**负技艺**,不胜恒定/随机持现金)/ B1-c FAIL(DSR 0.0033 无边)。**全部按预承诺报 FAIL,未洗白。**

## 4. 结论与下一刀指针

1. **regime-gated rotate-to-cash 不产生可部署边,且 regime 择时是负技艺**:① 满仓 ≤1-轮换/日 + 龄门使去暴露几乎咬不动(均暴露反升,18/104 轮动);② −10% 检测器晚触发 + 穿过 V 型反弹 → 持现金错过反弹,总体劣于不去暴露。
2. **结构墙坐实(横跨 QGR-4 + B1)**:rotation-only 冻结竞技场**结构上无法表达控回撤的 overlay** —— 买入集 veto(QGR-4)和 regime rotate-to-cash(B1)都把 MDD 卡在 50–60%,离 8% 差 ~7 倍。**owner MDD≤8% 在 ≤5 槽集中股票书 + A 股(2015/2018 个股与指数回撤 40–55%)下结构上near-impossible**:要么(a)**引擎级真 EXIT 路径**(快速 de-risk SELL,绕过 ≤1/日轮动 + 挑战者要求 —— **须 amendment + 碰冻结引擎,owner 决策**);要么(b)**重度永久防御配置**(book 大部分轮入低波资产 = 不再是选股闸而是防御基金);要么(c)换评测 harness。
3. **⏭️ 下一刀(loop 续)**:① **批 B2 = 避险目的地(红利低波)**:把 B1 的 cash destination 换成真红利低波 ETF —— 测**目的地是否重要**(红利低波作 parking 优于 cash/优于满仓股票?)+ 试**永久防御 sleeve 变体**(规避 B1 已证的 timing 负技艺,直接常驻 1–2 槽防御)。诚实预期:同 ≤1/日轮动墙 → MDD 仍难到 8%,但目的地/永久 sleeve 是不同假说。② **批 B3(拥挤触发持仓 REDUCE/EXIT)在本竞技场结构上已被 B1 subsume**:其拟用的 `hard_exit`/`protective_stop` 在 arena 里**保护**在位者(不卖),要跑须借 B1 的 cash-destination trick → ~同结果。spec 留锚,优先级降。**B1+QGR-4 已强烈指向:控回撤的真问题是引擎/构造级,非 overlay 级 → 跑完 B2 后若仍无门,应向 owner 升级『引擎 amendment vs 重定问题框定』决策。**

## 5. 诚实 caveat(scope)

- **train_val only**:test 封存未读(防火墙断言**实际 bar-read 窗口 ⊆ train_val**,coverage 守门 2484/2484);真 OOS / 前向 = 后续 B 层 gate,本刀未做。
- **quant-mechanism proxy(§4.4/§7)**:事件循环不含 LLM 辩论 / 全 RiskEngine / Line-2 盘中风控 —— **而 Line-2 盘中风控正是 live 系统的真 EXIT 路径**;本刀 FAIL 的根因(rotation-only 去暴露太慢)本质是 proxy 不含那条路径。go-live 仍须真管线 shadow replay。这是本刀最重要的 caveat:**arena 可能是评测 MDD-control overlay 的错工具**(结构墙 = proxy 边界)。
- **合成 cash sleeve**:flat 零收益零回撤,付真实 ETF 往返 friction(印花 0.1%/卖,**偏保守不利于发现 de-risk 边**);忠实代表「该槽持现金」,documented proxy 边界。
- **regime 检测器**:raw(未复权)ETF 收盘 —— 分红 ex-date ~1–2% 跌幅远小于 −10% headline 门(robust,disclosed);PIT 库无复权 ETF 序列(ETF 无 adj_factor)。vol 变体 disclosure-only,本刀用 headline。
- **强制 de-risk 资格 health**:arena 无直接 SELL 路径,本刀以「在治理日把股票在位置为 independently_weak」作 live EXIT 的最忠实 proxy;**对 placebo 同样施加**(同机制错时)→ 不为 regime 臂造 spurious edge。
- **DSR 用非清零 legacy floor N=2384**(round-1..4 + qgr.exit_veto mining 债不因改框架清零);append `qgr.derisk_regime` family(kind=ablation,effective_n=1,window 20150209–20250425)。
