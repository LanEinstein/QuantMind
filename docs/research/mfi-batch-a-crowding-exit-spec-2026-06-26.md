# 主力意图纲领 · 批 A = RISK/EXIT 拥挤/blow-off 闸 — 设计 spec + 诊断协议

> **状态**:设计 spec(实施锚点)· **未改 governance / 未碰 live 路径 / sim 暂停** · **日期**:2026-06-26 · **作者**:Claude(Opus 4.8)接续 Fable-5 接手包
> **上位文档**:`main-force-intent-research-program-macro-direction-2026-06-26.md`(纲领,§2.1/§2.2/§2.10/§6 批 A)+ `main-force-intent-lowbase-transition-system-design-2026-06-26.md`(窄战术)+ `quant-first-gate-rearch-plan-2026-06-21.md`(QGR 竞技场)。
> **一句话**:把纲领 §2.1 的**头号载重发现(可交易边在 RISK/EXIT 侧 ≫ ENTRY 择时)**落成第一批可证伪研究 —— 拥挤/过度延展/blow-off 作 **REDUCE/EXIT/veto 闸**(**预测尾部/崩盘概率,不预测均值收益**),用既有 CPCV 竞技场 + size 中性化 + 删最小 30% + 非清零账本严格证伪,**FAIL 报 FAIL**。

---

## 0. 为什么先做批 A(不是进场内核)

纲领 §2.1 的非对称是本纲领最可操作的结论:**主力足迹的可交易边,RISK/EXIT/避险闸 远强于 ENTRY 择时 alpha**(多 agent 独立汇聚 + A 股本土同行评审锚点)。这恰好契合 owner 判据 **绝对净盈 + MDD≤8%(控回撤)** —— 能稳定赢的正是给暴露/回撤择时(不追拥挤、blow-off 时减仓)。所以**先做风控/退出闸,后做进场内核**。批 A = 这条非对称的第一刀。

**本 spec 的诚实预期(写在前面,防自欺)**:文献(arXiv 2512.11913 / SSRN 3803954 / PMO AEL2024)说拥挤 **预测崩盘概率 ~1.7–1.8×,但不预测均值收益**(「observable but efficiently priced」);Feb-2024 微盘崩盘事后复盘连顶级量化都承认「无法精确测量拥挤度以及时撤出」。**所以本批 A 的均值-收益 IC 大概率弱/不显著 —— 这不是失败,而是印证「拥挤是尾部/风险信号、非收益信号」**;真正承重的检验是**崩盘概率条件**(高拥挤分位的前向左尾是否显著更肥)。若连尾部都证不出稳健区分,**就报 FAIL**(不为拯救先验事后翻供;QGR 原则 #1「反过拟合门是真预言,信它」)。

---

## 1. 因子族:拥挤/过度延展/blow-off(全 attractive-LOW,EXIT 方向)

> 全部从**已摄 `daily`**(qfq 复权收益 + RAW high/low/amount/turnover)派生,**零新摄取**;PIT 纪律 = 特征只用 `≤d` 的 bar;每因子符号 **R 阶段从零验**(A 股本土文献先验**永不假设**)。`expected_ic_sign=-1`、`attractive_high=False`(高 = 该躲的名,EXIT 用)。

| 因子 | 公式(as-of d,oldest→newest 序列) | 机理锚 | 先验符号(从零验) |
|---|---|---|---|
| **`bias_20d`** | `close_d / mean(close[d-19..d]) − 1`(qfq) | 收盘价乖离(§2.2 拥挤三件套之一);价格远离自身 20d 均值 = 过度延展 | −1(高乖离 → 低前向) |
| **`ideal_amplitude_20d`** | 20d 内按 `close_t ≥ median(close,20d)` 分高/低价态;`amp_t=(high_t−low_t)/pre_close_t`;因子 = `mean(amp\|高态) − mean(amp\|低态)` | 理想振幅(开源证券,§2.10③ + §8 深挖):高价态比低价态更剧烈振幅 = 派发/不稳定;券商报 IC −0.067/ICIR −2.97 为**负向/退出因子**(与"暴力洗盘后拉升"看涨叙事相反) | −1(高 → 低前向);**且本批做独立 size-中性化复测**(券商 size-neutral 声明未经复现,§8) |
| **`blowoff_20d`** | `max(ret_20d, 0) × max(turn_spike_5_15, 0)`,其中 `ret_20d=close_d/close_{d-20}−1`,`turn_spike_5_15=mean(turn,5d)/mean(turn,prior15d)−1` | 反转 blow-off(kickoff 点名):大幅近端涨幅 **叠加** 换手放量 = 拥挤式高位放量见顶(只对"涨+放量"打分,clip 负部) | −1(blow-off 越强 → 越该退) |

**有意 DEFER(诚实标注,不硬塞)**:
- **成交集中度 / 内部关联度(§2.2 拥挤三件套另两件)**= **cohort/factor-level** 概念(整个交易多拥挤),per-name PIT-clean 化困难且坍缩成 size。批 A 只做 per-name EXIT 代理;cohort 拥挤度作为后续(批 A.2)单独设计。
- **OBV / 放量滞涨派发(`vol_price_div`)**:OBV 需带符号成交量,日频 turnover 不干净给出方向;`blowoff_20d` 已覆盖"放量+涨"一支,派发"放量+不涨/跌"一支留待加签名成交量后做。
- 理由:**精度优先**,宁可 3 个 defensible 因子,不要 6 个噪声。

**collinearity 自检(必做)**:批 A 因子最大风险 = **只是反转/换手/size 换皮**(§2.10② 印证:筹码/吸筹因子多是 CGO/反转/size)。诊断对 round-1 carry cluster(`ret_5d/ret_20d/vol_20d/max_20d/turn_20d/amihud_20d`)+ QGR 快腿(`rev_1d/rev_3d/max_5d/turn_spike/...`)做**中性化后**两两 `|corr|`;>0.7 = 冗余,如实记 → 若批 A 因子全冗余,**这本身就是结论**(印证 §2.10②),报告不洗白。

---

## 2. EXIT-gate 框架(用法 = REDUCE/veto,非排序 alpha)

- **不当截面排序买入因子**(那是 round-1..4 死法的诱惑)。拥挤/blow-off 因子的用法 = **退出/减仓/否决**:命中高拥挤分位 → REDUCE/EXIT/veto,**控回撤**。
- **承重检验 = 崩盘概率条件**(非均值 IC):对每因子,按 d 截面分位(top 5–10% = 高拥挤 vs 其余),比较**前向收益左尾**:① `P(fwd_5d < −5%)`(崩盘概率)② 最差十分位前向均值 / CVaR@5% ③ 左尾差是否跨 regime(牛/熊/震荡 × 大/小盘领涨)稳健。**高拥挤分位左尾显著更肥 = 闸有效**。
- **均值 IC = 诊断(预期弱)**,披露不晋级;若均值 IC 反而显著为**正**(高拥挤 → 高前向),那是反信号,如实报。

---

## 3. 严格性管线(不可旁路;复用既有竞技场)

| 阶段 | 做法 | 复用件 |
|---|---|---|
| **宇宙 + 删最小 30%** | 投资集(主板+创业板+ETF,排除四件套/ST/科创/北交/可转债)+ LSY CH-3 删最小 30% 市值(治壳/size 污染) | `build_factor_panel._cohort` + `SIZE_EXCLUDE_QUANTILE=0.30`(经 `build_crowding_panel` 复用) |
| **size + 行业中性化** | 每 d 截面 `factor ~ 1 + 申万L1 dummies + log(circ_mv)` OLS 残差;**所有 IC/分位/尾部都在残差上算** | `neutralize.neutralize_panel`(winsor 0.01) |
| **IC 从零验符号** | rank-IC(raw + neut),1/5/10/20d;`|t|≥T_BAR`(Harvey-Liu-Zhu)为**乐观筛非判据** | `factor_ic_study` + `r2_factor_diagnostics.verdicts` |
| **崩盘概率条件** | 高拥挤分位 vs 其余的前向左尾(P(<−5%)/CVaR/最差十分位),regime 分层 | 新建(诊断内) |
| **CPCV IC 稳定性** | per-date neut-IC 序列 → `run_cpcv_fixed_series`(N=6,k=2,embargo≥horizon)→ combo OOS 分布;固定序列零路径离散是 by-construction(QGR-4 才驱动选择过程) | `cpcv.run_cpcv_fixed_series` |
| **DSR/PBO(去通胀)** | IC-加权多空净收益序列喂 DSR-HAC(overlapping 持仓自相关)+ 非清零账本 `deflation_n` | `honest_gates.deflated_sharpe_hac` + `trial_ledger` |
| **非清零账本** | 批 A 每因子/每诊断 append `TrialRecord`(family=`mfi.batch_a.*`);DSR 的 N0 = `max(legacy 2348+, 新 ONC)` —— **改判据/换框架不清零 mining 债**(codex P0) | `trial_ledger.TrialLedger.with_legacy` |
| **泄漏门** | future-NaN 投毒:对 crowding 面板跑 `leak_probe.assert_no_future_leak`(投毒 trade_date>cutoff 的市场端点 → 重算 → cutoff 前 rebalance 日特征列须逐字节相同) | 新建 `leak_probe.py`(本 session P0) |

**铁律**:诚实门**永不**指导搜索(Goodhart);test 窗口封存(批 A 只在 train_val 跑诊断,不烧 test;真前向确认是后续 B 层);FAIL 报 FAIL。

---

## 4. P0 基础设施:future-NaN 投毒泄漏门(`leak_probe.py`)

纲领 §4「会静默毁掉研究的 top-3 基建错误」之首 = **泄漏**(smoothed-state 当特征 / 概念成分回填 / 复权未 as-of pin / 转换全样本拟合 / 前向 bar 漏进特征)。投毒门 = **经验**证伪泄漏:

- `PoisonedStore`(鸭子类型包 `StoreLike`):对 `latest(vendor,endpoint,trade_date)`,若 `endpoint ∈ poisoned_endpoints`(按 trade_date 键的市场端点)且 `trade_date > cutoff` → 返回 `None`(未来 bar 不可见)。catalog(asof)/period 端点透传(其 PIT 由 ann_date/asof 逻辑各自守,不在本门范围)。
- `assert_no_future_leak(build_fn, store, *, cutoff, feature_cols, poisoned_endpoints, ...)`:`build_fn(store)`(全量)vs `build_fn(PoisonedStore(...))`(投毒);取两边 `date ≤ cutoff` 的行,按 `(date,code)` 对齐,**断言每个 `feature_col` 逐值相同(NaN==NaN)**;不同 = 有人读了未来 → 返回 `LeakReport(leaked=True, mismatched_cols=...)` / `assert` 变体抛错。**标签列(fwd_ret)允许变 NaN**(投毒后前向 bar 消失是预期)。
- **纯 + 确定性**:鸭子类型 Protocol,零 `backend` 写,零 IO/RNG/wall-clock;测试用合成 store(无泄漏 build 通过 + 注入读未来的 build 被抓)。

---

## 5. 红线合规(全留)

永禁真实下单(只研究/sim,sim 暂停)· 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · LLM 永不进 PIT/评测路径 · governance enum 不动(批 A 因子机理全用既有 `EconomicMechanism` 值)· `scripts/factor_research/` 既冻结字节/locked split 不动(批 A 新建 `build_crowding_panel.py` + `crowding_factor_diagnostics.py` + `leak_probe.py`,round-1..4/QGR 面板字节不变)· **绝不碰 `backend/` value-sleeve 域(AF-*)** · **绝不接入 `moneyflow` 主路径** · **不把「主力意图」当产品宣称**(可交易内核诚实命名「拥挤/blow-off EXIT 闸」)· 改决策边界(真改 live 选股/持仓)须 amendment + codex + 重启(批 A 是离线研究,**不改 live 一行**)。

---

## 6. 交付件清单(本批)

- `scripts/factor_research/leak_probe.py`(+ test)— P0 投毒泄漏门(本 session)。
- `scripts/factor_research/factor_lib.py` 加 `CROWDING_FACTORS` registry + `compute_crowding_factors`(本 session)。
- `scripts/factor_research/build_crowding_panel.py`(读 high/low,镜像 build_qgr_panel,复用 _cohort+删30%+neut 输入)(本 session)。
- `scripts/factor_research/crowding_factor_diagnostics.py`(IC 从零 + 崩盘概率条件 + collinearity + CPCV + DSR-HAC + 账本)(本 session)。
- `docs/research/mfi-batch-a-crowding-results-2026-06-26.md`(真 train_val 跑结果 + 诚实判据,FAIL报FAIL)(本 session)。

**后续(spec 留锚,非本 session)**:批 A.2 cohort-level 拥挤度(成交集中度/内部关联度);批 B 避险轮动(红利低波 destination + 波动/拥挤 regime);EXIT-gate 进事件循环 → 绝对净 P&L+MDD(QGR-4 规模);`stk_holdertrade` 减持硬排除(批 D,owner-gated 摄取)。

---

## 7. 证伪台账登记(预承诺报 FAIL)

| ID | 假说 | 门 | 预承诺 |
|---|---|---|---|
| **A1** | 高拥挤分位(bias/amplitude/blowoff,size-neut)前向**左尾显著更肥**(崩盘概率) | 左尾差跨 regime 稳健 + 非清零账本去通胀后仍在 | 过不了 → FAIL 报告:拥挤连尾部都测不准(印证 Feb-2024 复盘) |
| **A2** | 理想振幅独立 size-中性化后仍是显著**负**因子(§8 复测) | neut-IC `|t|≥T_BAR` 且符号 −1 + 非冗余 carry | 翻号/不显著/冗余 → 报 FAIL,券商 size-neutral 声明不复现 |
| **A3** | blowoff/bias **不只是反转换皮** | 中性化后 `|corr|` vs carry+QGR ≤0.7 且 neut-IC 独立 | 冗余 → 报「= 反转换皮」(印证 §2.10②),不洗白为新 alpha |

---

## 8. 关键出处(provenance-gated;承本纲领 §11)
拥挤=崩盘概率非择时:arXiv 2512.11913 / SSRN 3803954(Kang-Rouwenhorst-Tang)/ PMO AEL2024 / Feb-2024 微盘崩盘复盘(中国基金报)。理想振幅:开源证券《振幅因子隐藏结构》(BigQuant w5WH1P01Bl,§2.10③)。乖离/拥挤三件套:华泰金工「崎岖之路」+ 国君 6 维拥挤(§2.2,**作风控不作收益引擎**)。size 中性化删最小 30%:Liu-Stambaugh-Yuan CH-3(JFE 2019)。反过拟合:López de Prado AFML(CPCV/DSR/PBO);Bailey-LdP DSR;Harvey-Liu-Zhu t>3。
</content>
</invoke>
