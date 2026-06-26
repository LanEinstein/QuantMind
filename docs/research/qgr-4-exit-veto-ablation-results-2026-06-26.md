# QGR-4 第一刀 · EXIT-veto 事件循环三臂消融(train_val only)

> **状态**:真 train_val 跑结果(本刀 = batch-A next-pointer ③:把 `ideal_amplitude_20d`〔A2 PASS 的正交 size-neutral 拥挤 EXIT 赢家〕从 IC/尾部层兑现成 owner 判据**绝对净 P&L + MDD≤8%**)· **未改 governance / 未碰 live 路径 / sim 暂停** · **日期**:2026-06-26 · **作者**:Claude(Opus 4.8)
> **上位文档**:`main-force-intent-lowbase-transition-system-design-2026-06-26.md` §6.6(三臂消融 + placebo 纪律)+ `qgr-2-eval-arena-freeze-spec-2026-06-22.md`(评测口径冻结:净 P&L + MDD≤8%,去 CSI300 超额硬门)+ `mfi-batch-a-crowding-results-2026-06-26.md`(A1/A2/A3)。
> **代码**:`scripts/factor_research/exit_veto_panel.py`(纯变换)+ `scripts/factor_research/exit_veto_ablation.py`(编排驱动)+ `tests/factor_research/test_exit_veto_panel.py`(15 单测)。

## 0. 一句话裁决

**FAIL,如实报。** 拥挤 EXIT 轴在因子/尾部层是真的(batch-A A1/A2 PASS),但**作 long-only 买入集 veto 在 ≤5 槽真系统里不产生任何可部署边**:它过不了**任何**一条 pre-committed 门(MDD≤8% / MDD 改善 / 严格 beats-placebo / DSR≥0.95)。更重的诚实发现:**整个 ≤5 槽反转排序器策略类(baseline 及其 veto/placebo 变体)2015–2025 全窗口 MDD ~54–58%,超 owner 8% cap 约 7 倍** —— 买入集 veto 不仅没修回撤,反而让 MDD 略升(54.2%→58.3%)。这强力印证纲领 §2.1:**控回撤的可交易边在『真·去暴露/避险/减持』侧,不在『买入集拥挤过滤』侧** —— 指向下一刀(避险轮动 + 拥挤触发持仓 REDUCE/EXIT)。

## 1. 方法(复用 QGR-2 冻结竞技场,不动引擎字节)

- **竞技场**:`backend.backtest` 真事件循环(≤5 槽 / 5td 轮动 / T+1 / 分板块滑点 / 涨停不可成交),经 `gate_backtest.run_gate_backtest` + `gate_bar_source.PitBarSource`(K-002 PIT 字节存档,qfq as-of pin,真 `stk_limit`)。**本刀是该竞技场首次在真 train_val 全窗口驱动完整事件循环**(此前只在 test 用合成 bar 跑过)。窗口:497 rebalance 日 / 2484 daily 日 / universe 1823 + CSI300 ETF;20150209–20250425。
- **选股**:中性化(申万 L1 + log size)survivors + `ideal_amplitude_20d`;ranker buy-score = 三幸存因子 **registry-signed** z 等权合成(`attractive_high=False` → 符号 −1,从 factor_lib registry 取符号,非硬编码)。
- **轮动驱动**:panel 派生 `health_overrides` 喂真黏滞 **≤1-轮换/日** 弱势门;`entry_percentile`/`score_*_20d` = 前 4 rebalance 步 trailing 代理(documented proxy boundary)。
- **三臂(+1 placebo,同一排序器/health/撮合/持有窗)**:① **baseline** = 排序器单独 ② **exit_veto** = 剔除顶 10% 拥挤分位(`ideal_amplitude_20d_neut crowd_pct ≥ 0.90`)出**买入集** = long-only veto ③ **placebo_random** = 同每日剔除数量、随机抽 ④ **placebo_sizematched** = 同数量、最近 log_size 配对抽(控 size 通道,R5 陷阱)。
- **部署 baseline(beta 守门)**:random_top5 / etf_only_510300 / csi300_etf_hold(全额买入持有 = 最硬 beta 门)。
- **统计**:SPA(Hansen)+ Romano-Wolf StepM vs csi300_etf_hold;DSR-HAC(**非清零 legacy floor**,deflation N=2383);veto-vs-对照配对 t;regime 分层(从竞技场自身 beta 序列 trailing-4-period cumret 派生,与各臂 period 对齐 by construction)。
- **判据(pre-committed §6.6,FAIL 报 FAIL)**:可部署边须**全部**满足 ① 净 P&L 不材料受损(对初始资本 sign-safe 容差 ≤2%)② MDD 改善且 ≤8% ③ **严格** beats placebo(veto−placebo 配对 t ≥ 2.0,两 placebo 都过)④ veto 臂过 **DSR≥0.95 主门**(CLAUDE.md 原则 #1)。

## 2. 结果(真 train_val 跑)

### 2.1 各臂绝对净盈 + 控回撤(初始 ¥1,000,000)

| 臂 | 总收益 | 净 P&L (¥) | **MDD** | MDD≤8% | 月换手 | fills | avg_exp | DSR(N=2383) |
|---|---|---|---|---|---|---|---|---|
| baseline | +45.9% | +459,000 | **54.2%** | ❌ | 0.23 | 179 | 0.58 | 0.005 |
| **exit_veto** | +78.1% | +781,288 | **58.3%** | ❌ | 0.22 | 173 | 0.56 | 0.011 |
| placebo_random | +70.3% | +703,348 | 57.3% | ❌ | 0.34 | 261 | 0.57 | 0.009 |
| placebo_sizematched | +60.8% | +608,031 | 56.1% | ❌ | 0.25 | 177 | 0.57 | 0.007 |
| random_top5 | +122.3% | +1,222,670 | 52.5% | ❌ | 0.00 | 5 | 0.85 | — |
| etf_only_510300 | +2.4% | +23,827 | 10.6% | ❌ | 0.00 | 1 | 0.17 | — |
| csi300_etf_hold | +17.6% | +176,112 | 46.3% | ❌ | 0.00 | 1 | 1.00 | — |

- **全部臂 MDD 远超 8% cap**(46–58%):≤5 槽集中长多在 2015 股灾 / 2018 熊市的回撤无法低于 8%;**买入集 veto 不控回撤**(MDD 54.2%→58.3%,反升)。
- **DSR ~0.005–0.011**:对 N=2383 累计 trial 去通胀后,**无任何臂存活风险调整后边**(对照 batch-A 因子 DSR 门 0.95)。
- **random_top5(+122%)是净 P&L 最高臂** → 该窗口 luck/beta 主导;排序器"技艺"未在 ≤5 槽净 P&L 兑现。

### 2.2 veto bite(机制层诚实指标)

- 全窗口 **总剔除 21,897 名**(均 44.1/日,顶 10%)。
- 但**实际改变买入决策仅 29/496 rebalance 日**;baseline 92 笔买入中**只 19 笔**被 veto 改掉。
- → **veto bite ≈ 3%**:反转/反彩票/低换手排序器**本就避开高拥挤名**,买入集 veto 几乎无咬合(印证 §0 诚实预期)。

### 2.3 veto vs 对照(配对 t)+ SPA / Romano-Wolf

| 对比 | mean_diff | **paired t** | 解读 |
|---|---|---|---|
| exit_veto − baseline | +0.000 | +0.95 | 远低于严格 t≥2;且 bite 仅 3% |
| exit_veto − placebo_random | −0.000 | **−0.06** | veto ≈ 随机同数量剔除 |
| exit_veto − placebo_sizematched | +0.000 | +0.31 | veto ≈ size-匹配同数量剔除 |

- **SPA p = 0.362;Romano-Wolf 全 rejected=False**;各臂 vs CSI300 买入持有 t = baseline 0.27 / veto 0.62 / placebo 0.43–0.59 → **无臂显著击败 beta**。
- exit_veto 比 baseline 净 P&L 高(+78% vs +46%),但**这个差落在 placebo 噪声内**(vs placebo t≈0)→ 是"少买 19 个名"的选择噪声,不是拥挤特异 alpha(同数量随机/size-匹配剔除拿到相近抬升)。

### 2.4 regime 分层(逆境制度不毁灭检验;sum period-return / worst period)

| 臂 | 牛(n=138) | 熊(n=128) | 震荡(n=230) |
|---|---|---|---|
| baseline | +0.774 / −0.079 | **−0.221** / −0.181 | +0.061 / −0.080 |
| exit_veto | +0.747 / −0.079 | −0.030 / −0.191 | +0.089 / −0.082 |
| placebo_random | +0.824 / −0.092 | **+0.093** / −0.223 | −0.091 / −0.085 |
| placebo_sizematched | +0.746 / −0.088 | −0.033 / −0.189 | −0.003 / −0.076 |
| csi300_etf_hold | +0.429 / −0.092 | −0.035 / −0.217 | +0.066 / −0.083 |

- exit_veto 在**熊市**累计(−0.03)看似比 baseline(−0.22)好,**但 placebo_random(+0.09)/ placebo_sizematched(−0.03)同样或更好** → 该"熊市改善"是**少买/选择伪影,非拥挤特异**(placebo 揭穿,§6.6 纪律生效)。worst-period 各臂 ~−0.18~−0.22,veto 未改善单期最坏。

## 3. 判据裁决(pre-committed §6.6 — FAIL 报 FAIL)

| 门 | 结果 | 值 |
|---|---|---|
| ① 净 P&L 不受损 | ✅ pass | veto +781k ≥ baseline +459k − ¥20k |
| ② MDD 改善 | ❌ FAIL | veto 58.3% > baseline 54.2%(反升) |
| ② MDD≤8% cap | ❌ FAIL | 58.3% ≫ 8% |
| ③ 严格 beats placebo(t≥2) | ❌ FAIL | vs placebo t = −0.06 / +0.31 |
| ④ DSR≥0.95 主门 | ❌ FAIL | exit_veto DSR 0.011 |
| **deployable_edge** | **❌ FALSE** | — |

**裁决**:EXIT-veto 作 long-only 买入集 veto **不产生独立可部署边**。除"净 P&L 不受损"(本身落在 placebo 噪声内)外**全门皆 FAIL**。

## 4. 结论与下一刀指针

1. **拥挤 EXIT 轴是真的(batch-A),但买入集 veto 不是它的正确兑现机制**:① 反转排序器本就避拥挤(bite 3%)② 买入集过滤**结构上无法控回撤**(它只决定买谁,不减已持仓暴露)。
2. **owner MDD≤8% 是硬约束,长多 ≤5 槽股票书过不了**(2015/2018 股灾 → ~50% MDD)。控回撤必须来自**真·去暴露**:股灾 regime 减仓 / 轮动到防御资产(红利低波)/ 对**持仓**的拥挤触发 REDUCE-EXIT —— **不是买入集过滤**。这正是纲领 §2.1 非对称(可交易边在 RISK/EXIT/避险侧)在系统层的实证。
3. **⏭️ 下一刀**:① **批 B 避险轮动**(crash/拥挤 regime → 红利低波 destination,直接攻 MDD)② **拥挤触发持仓 REDUCE/EXIT**(对已持仓拥挤名减仓,非买入集 veto;更强 EXIT 机制,spec 留锚)③ **stk_holdertrade 减持硬排除**(批 D,owner-gated 摄取)。本刀已证:net-P&L 排序优化 + 买入集过滤这条路在 owner MDD 判据下走不通,把火力转向真避险/减仓。

## 5. 诚实 caveat(scope)

- **train_val only**:test 窗口封存未读(防火墙断言**实际 bar-read 窗口 ⊆ train_val**,HORIZON 延伸不读封存字节);真 OOS / 前向确认 = 后续 B 层 gate,**本刀未做**。
- **quant-mechanism proxy(§4.4)**:事件循环不含 LLM 辩论 / 全 RiskEngine / Line-2 盘中风控;go-live 仍须真管线 shadow replay。
- **黏滞 ≤1-轮换/日**:真 live 机制(engine 字节);它本质上限制了买入集 veto 能咬多深(bite 3%)——是 proxy 边界,如实披露。
- **entry_percentile = trailing-max 代理**:事件循环不回传入场状态;无状态近似,documented。
- **MDD 从起始资本起算**(qgr-2 freeze §1.2);**DSR 用非清零 legacy floor N=2383**(round-1..4 mining 债不因改框架清零)。
- **buy-set veto ≠ 持仓 EXIT**:本刀只测 long-only 买入集 veto;拥挤触发的持仓 REDUCE/EXIT 是下一刀。
- **非清零账本**:append `qgr.exit_veto` family(`data/factor_research/mfi_trial_ledger.jsonl`,kind=ablation,effective_n=1,window 20150209–20250425)。
