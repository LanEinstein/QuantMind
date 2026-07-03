# 买卖点 / 仓位管理 harness —— 调查研究综合(2026-07-03)

> **状态**:调查研究 doc(配套计划书 = `buy-sell-timing-harness-implementation-plan-2026-07-03.md`)。**作者**:Claude(Opus 4.8)· owner:dr.zhang
> **触发**:owner 2026-07-03 handoff(`docs/handoff/buy-sell-timing-harness-fable5-kickoff-prompt-2026-07-03.md`)—— 在选股成功前提下,为「建仓/加仓/减仓/清仓时机 + 仓位 sizing」做调研 + 计划书,使选股+买卖点整合后整体稳定盈利。
> **⏳ 时机门(硬)**:本层输入 = 选股线最终锁定的 alpha。选股线当前在 `DS 防御选股`(D1→D4 未开跑,alpha **未锁定**)。故本 session **只产调研 + 计划书草案**;整合进 `docs/plan.html` 作为新 phase 并实施的时机 = **选股 alpha 敲定后的 owner gate**。不写实现代码。
> **红线**:永禁真实下单 · 飞书人工执行 · 127.0.0.1 · **研究/评测零 LLM**(LLM 永不写决策) · RiskEngine 纯函数 · InstructionPlan 单一构造点 · PIT 字节存档 · 反过拟合四门不放宽 · 非清零账本不清零 · **FAIL 报 FAIL** · push/摄取/live 激活 owner-gated · 报告中文/代码 commit 英文。

---

## 0. 一句话结论(整份调研收敛到这一句)

**在 ≤5 集中 + T+1 + 人工执行的 A 股约束下,回撤控制与稳定盈利的载重杠杆是「持多少」(结构性 sizing:硬现金 buffer + 置信集中权重),不是「何时持」(择时 overlay)。** 本项目四刀负结果(C1a/B1/B2/QGR-4)与四域外部文献**独立收敛到同一结论**:凡权重或现金水平随「市场近期波动/趋势」变动的规则 = 伪装的择时,已被我们自己的真数据 + Cederburg(2020)/Zakamulin(2017)/Kaminski-Lo(2014)三篇主文献分别证伪。因此 harness 架构的**默认立场**必须是:sizing 层承重且优先;一切 timing 层(建仓确认门、止损、做T、避顶部)**默认高风险、默认 OFF,每一个都须在冻结引擎上独立跑赢零信息 placebo,否则丢弃**。

---

## 1. 负结果对账(先看这个;架构须内化,绝不重蹈)

四刀失败**不是运气,是定理在真数据上的兑现**——外部文献给出了各自的理论解释:

| 我们的负结果 | 数值 | 机制 | 对应文献定理 |
|---|---|---|---|
| **C1a 避顶部 EXIT-on-held** | net **−663k** vs baseline **+459k**;MDD 54%→**82%**;输两 placebo;②错失588k>①避损485k | EXIT 砍掉刚买入的超卖名=反弹前砍在地板 | Kaminski-Lo:均值回复书上止损**严格净有害**(退出恰在反弹兑现前) |
| **机械 −12% 固定止损** | stop_only **−431k** | 固定阈值卖在反弹前,与反转 alpha 正面冲突 | Kaminski-Lo 定理(随机游走止损降期望收益;负自相关更甚) |
| **B1 regime de-risk** | derisk MDD **60.4%**>baseline 54.2%;两 placebo 双双击败(择时负技艺) | −10% 检测器晚触发 + 持现金穿过 V 型反弹;满仓引擎去不掉暴露 | Zakamulin:去 look-ahead 后 MA 择时仅边际优于 buy&hold |
| **B2 永久防御 sleeve** | 旧 MDD≤8% gate FAIL on every arm;唯一 ≤8%=全仓国债(非选股闸) | 8% 在 ≤5 槽股票书结构不可达 | —(触发 owner 判据重定:弃 8% 硬门) |
| **QGR-4 exit-veto** | MDD 54.2%→**58.3%**(veto 不控回撤反升) | 同因子买入 veto 亦不转化净值 | —(与 C1a 一致:GROSS 尾部边两形态都不转化) |

**唯一正面 partial(全项目仅此一个 timing/结构改善生还)= B2 的永久现金 buffer**:`perm_cash2`(永久 2 槽现金)净盈 **+459k→+966k**(翻倍)+ MDD **54%→34%**(减半)+ 熊市 −0.22→−0.04、震荡 +0.06→+0.22(逆境双改善)。**但 DSR 0.044 仍不过反过拟合门 → provisional**。这与 owner P-E(≥40% 现金 buffer)**完全对齐**,并直接成为本 harness 的载重设计元素。

**四条内化教训(架构级)**:
1. 失败都在**均值回复(反转)书**上;新选股线是**防御性**(D1-D4,慢因子月级),自相关符号**未知** → **绝不假设 timing 在新书上有效,须先测持仓的短期自相关符号,再上任何价格止损**。
2. **执行机制已冻结为契约**(C0b `ExitExecutionContract`:close-T→T+1 / 不可成交排队被套 / 硬止损不等确认 / 做T profit-gate / 再入锁)。本 harness 设计的是 **timing 决策**,不是执行机制;契约字节不动。
3. **结构墙**:冻结引擎 rotation-only(≤1 SELL/日、需挑战者、被保护名不可卖)= **满仓 by construction**(B1 坐实)。组合层控回撤 overlay 表达不了;真控回撤杠杆 = **现金 buffer**(经 `frozen_cash_cents` / 永久现金 sleeve 表达,B2 approach 有效)+ **置信集中 sizing**(须新 provider,见 §3.1)。
4. **FAIL 报 FAIL**;绝不移球门 / 样本内调符号权重(round-1..4 死法)。

---

## 2. 文献综合(四域;provenance 定死于评测前,绝不样本内挖)

四路并行研究 agent(gh search + 一手文献)独立收敛。**核心分野**:每一域文献都把技术分成「结构性/状态无关(稳健)」vs「条件性/动态(伪装择时,OOS 崩)」两族,且分野线与我们的负结果**逐条对齐**。

### 2.1 仓位 sizing(载重;最强正向证据)

**主文献锚**:**Cederburg, O'Doherty, Wang & Yan (2020), "On the performance of volatility-managed portfolios," _JFE_ 138(1):95-117** —— 跨 103 个策略,vol-managed alpha 在**样本内 spanning 回归**为真,但**"reasonable out-of-sample versions generally earn lower certainty-equivalent returns and Sharpe ratios than simple investments in the original unmanaged portfolios"**,因 spanning 回归结构不稳定。DeMiguel-Martín-Utrera-Nogales(2024, _JF_)+ Barroso-Detzel(扣成本后消失)佐证。

> **关键区分(必须保留)**:「波动率目标」是两个不同东西 —— (a) **截面 inverse-vol 加权**(每个名 ∝ 1/σ_i,风险贡献可比)= 结构性、无择时、**保留**;(b) **时序 gross-exposure 随市场波动缩放** = 伪装择时的那一版、OOS 崩、**等同于我们已杀的 overlay**。

| # | 技术 | committed 方向 | 证据 | 映射到 ≤5+≥40%现金 |
|---|---|---|---|---|
| 1a | 截面 inverse-vol 加权(∝1/σ_i) | **USE**(结构性无择时) | MED-HIGH;Moreira-Muir/AQR | 仅在已投 sleeve 内做;N=5 时是温和 tilt。可被置信 override(~60% 名) |
| 1b | 时序 gross-exposure vol 缩放 | **AVOID**(伪装择时) | HIGH against, OOS;Cederburg 2020 | = 我们已证净有害的 overlay。硬 ≥40% 现金是「定比」替代,拿到控回撤好处而无择时赌注 |
| 2 | 分数 Kelly(半/四分之一) | **USE 作上限/收缩,永不全 Kelly** | MED;MacLean-Thorp-Ziemba | 全 Kelly 在集中书上"uninvestable"(中途回撤先终结);10% edge 高估≈翻倍下注;对期望收益误差敏感度≈协方差的 20× → edge 保守标定 |
| 3 | 置信/信号强度加权(score/rank→权重) | **USE**(这是 ~60% 置信名的正当性来源) | MED;GSAM signals | ≤5 名 softmax(温度 τ 卡顶名≈60% of invested);直接实现 P-E 置信集中 |
| 4 | ERC / 风险平价 | **N=5 边际,优先简单** | LOW-MED;Maillard-Roncalli-Teïletche | ERC 为多资产设计;N=5(同防御簇高相关)退化向等权且"高度依赖宇宙结构",且与 P-E 60% 集中**对着干** → 仅作 tie-breaker |
| 5 | 永久现金 buffer / 结构性 dry powder | **USE**(我们已证的杠杆) | MED-HIGH(与我们 B2 对齐) | **恒定**持有(非恐惧时才加)= 定比去暴露,vol-scaling 的稳健表亲。caveat:择时 dry powder("等回调")通常输给 in-invested |
| 6 | 加仓 / pyramiding(加码赢家) | **AVOID**(部分伪装择时 + T+1 摩擦) | LOW/MIXED;Concretum | pyramiding = sizing 里嵌的趋势赌注(12.8%→20% IRR 但 vol 翻倍、MDD→48.7%),与防御书 + 控回撤 + T+1 人工冲突 |
| 7 | 定比 sizing 控回撤(非 vol-scaling) | **USE 定比;AVOID 动态** | HIGH(定比 by construction 稳健) | 长多书尾部风险由「投多少」控远比「何时投」可靠;定比不会被 vol spike 打错方向,动态会(且已被打错) |

**sizing 候选(结构性、无择时、离散、人工可执行;各保 ≥40% 现金 + ≤5 名 by construction)**:
- **S1 定比 + 置信 softmax(推荐基线)**:已投 sleeve 固定 60%(现金 40% 恒定),60% 按防御分 softmax(温度 τ 卡顶名≈60% of invested≈36% of total)。最干净的 P-E 编码。
- **S2 = S1 + 截面 inverse-vol 归一**:softmax 前先 ∝1/σ_i 归一,测结构风险归一是否改善实现回撤。
- **S3 分数 Kelly 置信分数 + 硬现金地板**:edge→半/四分之一 Kelly of sleeve,顶名 clip 60%,40% 现金不可破。
- **S4 少于 5 名集中测**(「非必占满」子句):仅注资过置信阈的名,少名时现金**浮于 40% 之上**——须由**截面信号广度**驱动,**非**市场择时。
- **S5 分批建仓 2-3 个 T+1 日(执行平滑,NOT pyramiding)**:目标权重**预先定死**,只平滑成交,**不加码赢家**;回测须区分,防漂移成伪装动量择时。

### 2.2 建仓 timing(P-A 确认门;窄条件正向,防御书上先验低)

**主锚**:**Zakamulin (2017), _Market Timing with Moving Averages_**(去 look-ahead 后 MA 择时仅边际优于 buy&hold);**Han-Yang-Zhou (2013, _JFQA_)**:MA 择时 alpha **集中在高波十分位(3.1%–8.0%/yr),低波名可忽略** → **对防御(低波)书,历史截面择时 alpha 近零,C-E1/C-E2 产生真 alpha 的先验概率 LOW**;**A 股 T+1 反转(_IREF_ 2024)**:A 股日/周/月频显著反转、无动量、T+1 是因 → **确认延迟在均值回复 tape 上机械买更高 = 退出 overlay 失败的入场侧镜像**;**T+1 开盘折价(_JIFM_ 2020,~14bp)**:T+1 开盘买捕捉隔夜折价 = 次日买的小结构顺风(但 placebo 同得,故不会误认作确认 alpha)。

**建仓候选**(close-T 决策,T+1 执行;vs 立即入 baseline **且** 同延迟随机入 placebo,须同时胜两者):
- **C-E1 区间收复确认**(首次收复参照位后入)= P-A 最字面编码;**HIGH 伪装择时风险**(反转 tape 上收复日常是局部顶)。
- **C-E2 趋势门入场**(仅在名/指数 > 60/120/200 日 SMA 时建仓,"不接飞刀")= **最可能"work"因而最可能是纯暴露削减**;须分解(暴露效应 vs 条件选择效应)+ 给闲置资本计现金收益;bear/sideways 帮、V 型底伤(须 regime 分层报,绝不 pool)。
- **C-E3 分批建仓**(thirds;闲置 tranche 现金须计 T-bill 收益)= 理论上 lump-sum 胜期望收益,分批仅赢方差/后悔 → risk-management 非 alpha,被现金 buffer 支配。
- **C-E4 量能确认入场**(换手 ≥k×20 日均)= **A 股可能符号反转**(高换手本身是反转/彩票信号 → 可能选到超买将回复的名);provenance 最弱(vendor blog);须显式测符号。

### 2.3 退出 / 止损 / 做T(P-B/P-C;文献只背书两种形态)

**主锚(THE key paper)**:**Kaminski, K. & Lo, A.W. (2014), "When Do Stop-Loss Rules Stop Losses?", _Journal of Financial Markets_ 18:234-254** —— 随机游走下止损**总是**降期望收益;**均值回复(负自相关)下止损严格净有害**(恰在反弹兑现前退出);止损**仅在损失持续时加值**(动量 / 正自相关 / regime-switching 进持续坏态)。**Han-Zhou-Zhu (2016, _JFQA_)**:动量书上 10% 止损把最大月损 −49.79%→−11.36%、Sharpe 翻倍(0.165→0.399)—— 印证「止损因动量损失持续而有效」。**Dai-Marshall-Nguyen-Visaltanachoti (2021, _IRF_)**:trailing 止损降下行风险(尤其跌市)但**不加均值收益**,仅宽阈值扛得住成本。**López de Prado(triple-barrier)**:profit-take + stop + **time(vertical)barrier**,「哪个 barrier 先触」是分析单位。**Odean(1998)/Shefrin-Statman(1985)disposition effect**:卖赢家太早/持亏家太久是最有据的投资者错误 → **做T 卖赢家是行为偏差穿策略外衣,须最狠 placebo**。**A 股 T+1(_JBF_ 2020)**:隔夜收益显著负,做T「今卖次买」扛负隔夜漂移逆风。

> **拯救 P-B 的推论**:P-B 要抓的事件——**造假、审计非标、ST/*ST 标记、退市风险、强制停牌跳空**——是均值回复的**反面**:**永久非回复的资本减值**(_ScienceDirect_ 2023 财务困境跳跃尾部风险),正是 Kaminski-Lo 说止损**加值最大**的持续坏态,**且无反弹可错失**。**真·异质安全止损与市场噪声止损是两种动物**:我们的数据杀死了噪声止损,**对安全止损一言未证**(造假/ST 名无反弹可错)。

**退出候选(按结构上豁免 Kaminski-Lo 均值回复陷阱排序)**:
- **★E1 异质灾难/资格退出(事件驱动,非价格%)= 真 P-B 安全底线**:触发于 ST/*ST 标记、退市风险、审计非标/业绩爆雷预警(`forecast_vip`/`express_vip`)、造假停牌、离开合格宇宙。**最不可能重蹈失败**(响应非回复状态变化,无反弹可错)。**无任何固定百分比**。数据今天可建(`namechange`/`suspend_d`/`stock_basic_delisted`/`forecast_vip`/`express_vip` 全在库,§4)。
- **★E2 宽 vol-scaled profit-ratchet,仅对已盈利仓(Chandelier 2.5-3×ATR)**:自适应(坐在噪声带外)+ 宽(扛成本)+ **仅对已盈利仓**(绑 P-C,永不碰新建超卖名)。give-back 限制器,非入场止损 → 绕过确切失败模式。
- **E3 时间 / 因子秩衰减退出(价格盲)**:名**离开**防御合格集时(月度)退出 / 最大持有期(vertical barrier)。价格盲 → **不可能砍进反弹** → 结构上豁免;契合月级慢因子设计;最便宜验证。
- **E4(条件)profit-gated 部分减仓 = P-C 做T**:仅正浮盈仓;守 T+1;**默认 OFF**,须跑赢 rate-matched 随机部分卖 placebo 才纳入(Odean 警告 disposition 偏差 + T+1 隔夜税)。
- **明确不推荐**:入场仓固定百分比硬止损(Kaminski-Lo 定理 + 我们 −431k)+ 任何避顶部价格退出(−663k)。**绝不换名重建**。

**方法学硬要求(退出侧)**:① 先测防御持仓的短期自相关符号,别假设「防御=趋势」;② 止损须与再入规则**联合建模**(P8:单独评止损=作弊半实验;我们 −431k 正是「booked loss, missed rebound」);③ 每个价格类候选须同时胜 calendar-random-sell + rate-matched-random-sell 两 placebo + P&L 四分解(避损 vs 错失)+ **在防御书上胜**(非动量/泛化书,那里文献正面结果所在);④ **防御书 placebo 门槛易 FAIL**(低波缩小 whipsaw 罚也缩小收益 → 效应小、噪声主导)。

### 2.4 可复用件(gh search;设计模式非框架采纳;许可门 MIT/BSD/Apache OK,GPL 仅 learn-from)

| 复用 | 许可 | 借什么抽象 | port/learn |
|---|---|---|---|
| **qlib `TopkDropoutStrategy`** | MIT | topk + `n_drop` 换手 cap + **`risk_degree` 现金旋钮**(=0.60→≥40% buffer)+ **`hold_thresh`**(=T+1);scoring→sell→buy→order 分离 | **PORT 算法**(~150 行纯 overlay);A 股最相关。**注**:我们已有冻结 rotation 引擎功能类似,作设计参照 |
| **PyPortfolioOpt `DiscreteAllocation.greedy_portfolio`** | MIT | 连续目标权重 + 最新价 + 组合值 → **整手 + 剩余现金**(置信权重→A 股 100 股整手的执行步)~80 行 | **PORT**(小、自足) |
| **Riskfolio-Lib** | BSD-3 | 分数 Kelly + ERC/HERC 闭式 + CVaR/CDaR(回撤感知) | **LEARN**(cvxpy 重依赖不引;port 闭式) |
| **vectorbt `from_signals`** | Apache* | `SizeType`(Amount/Value/Percent/**TargetPercent**)+ `accumulate`(scale-in)+ `sl_trail` 状态分离 | **LEARN** API 形状(词汇非 Numba 引擎) |
| **backtrader `Sizer._getsizing`** | GPL-3 ✗ | 可插拔 `Sizer(cash,price,is_buy)→size` seam(sizing 与信号解耦) | **仅 LEARN**(GPL 不可拷;自写 MIT-clean) |
| **rqalpha** | Apache* | **A 股原生 T+1**:`sellable` vs `quantity` 分离 + 涨跌停订单拒绝 + 整手 | **LEARN**(交叉核对我们契约) |
| ayondey47/kelly-criterion | MIT | 无依赖分数 Kelly + expected-log-growth,带 pytest/CI | **PORT** 作分数 Kelly 起点(低星,验数学) |

> **护栏**:无一件作冻结引擎运行时依赖;每件都是 <200 行可重实现自足算法。GPL(backtrader/cvxportfolio)+ LGPL(nautilus)仅设计参照。vectorbt/rqalpha 许可 API 显 `other` → 拷代码前核 LICENSE。

---

## 3. 引擎复用面 + 关键架构缺口

### 3.1 已知缺口:置信集中 sizing(P-E ~60% 单名)冻结引擎表达不了
`backend/backtest/strategy.py` = **等权分槽**(`equal_weight = total_equity // max_total_positions`)+ **15% 单股 cap**(`single_stock_cap_percent=15`);现金 buffer 靠 `frozen_cash_cents`。`slot_frontier.py:20` 明确记为 **"which needs a separate confidence-weighted sizing layer — a documented gap"**。→ **计划书须解决**:P-E 置信集中在冻结引擎上如何表达(见计划书 §2.1 + owner 待决 Q1)。

### 3.2 已就位的复用 seam
- **`e2e_simulator.ExitOverlay`**(close-T→T+1 契约,SELL/做T);`avoid_top_overlay._ReentryLockExitOverlay` = overlay + placebo 公平对照基类模板。
- **`PanelScoreProvider(scores_by_day, health_overrides)`** = 吃 DS 选股分数的插入点。
- **`gate_backtest.run_gate_backtest(*, horizon=…)`** = QGR-2 冻结竞技场(≤5 槽/T+1/分板块滑点/涨停不可成交)。
- **`slot_frontier.run_frontier`** = slot×sizing frontier 容器(eq_5 科学门 + buf40_5 部署门=40% gross/60% 现金,已满足 P-E ≥40% 地板;`alpha_pivot_spec.CONTAINERS` byte-anchor)。
- **反过拟合工具链**:`honest_gates.deflated_sharpe_hac` + `trial_ledger.TrialLedger`(非清零 legacy 债 N≈2417,新族 kind=ablation append)+ `stats_disclosure`(SPA/Romano-Wolf)+ `cpcv` + `regime_detector.classify_regimes`/`high_risk_dates`(牛/熊/震荡 + 6 股灾切片)+ `arena_ablation`(PIT 防火墙/baseline/`strong_protected_health`/placebo 模板)。
- **live 参照(不动)**:`backend/value_swing/swing_overlay.py`(确定性做T,base_floor 0.60 + 守 T+1 + ≤1 round-trip/日,env-OFF)= P-C live 对应。

### 3.3 数据可用性(今天可建,无需摄取)
- sizing:`daily`(σ_i/beta)+ 已有 `vol_20d`/`max_20d`/`amihud_20d`(`factor_lib.py`);**唯一新因子 = beta/tail-beta 滚动 OLS**(DS synthesis 已定)+ 可选 ATR(`stk_factor_pro.atr_qfq` 已摄)。
- 事件安全退出(★E1):`namechange`(PIT ST)/`suspend_d`(停牌)/`stock_basic_delisted`(退市)/`forecast_vip`/`express_vip`(预警)**全在 `data/marketdata_pit/`**。
- 建仓门:趋势因子已有(`momentum_skip`/`distance_from_high`/`trend_slope`);量能(`daily.vol`/换手)已有。

---

## 4. 诚实预期分级(FAIL 报 FAIL 前置心理账户)

- ✅ **有据、先验高**:结构性 sizing(硬现金 buffer + 置信 softmax + 截面 inverse-vol)—— B2 perm_cash2 + Cederburg 分野 + P-E 三重支持。**这是最可能兑现稳定盈利+控回撤的层**。
- ✅ **有据、结构豁免**:★E1 事件驱动异质安全退出(Kaminski-Lo 推论;无反弹可错)。**预期低频、但每次都对**;控回撤贡献来自砍永久减值尾。
- 🟡 **谨慎、先验低(付账本债、须胜 placebo)**:E2 宽 ATR profit-ratchet(仅盈利仓)/ E3 因子秩衰减退出 / C-E2 趋势门建仓(bear/sideways 可能帮,V 底伤,且大概率纯暴露削减)。
- 🔴 **弱/大概率 FAIL(测但预承诺报 FAIL)**:C-E1 区间收复 / C-E4 量能确认(A 股可能符号反转)/ E4 做T(disposition 偏差 + T+1 隔夜税);固定百分比价格止损 = **确定 FAIL,不建**。

---

## 5. 来源(provenance;评测前 committed)

**Sizing**:Cederburg-O'Doherty-Wang-Yan (2020, _JFE_ 138:95-117) · DeMiguel-Martín-Utrera-Nogales (2024, _JF_) · Moreira-Muir (2017, _JF_) · MacLean-Thorp-Ziemba(Good and bad properties of the Kelly criterion)· Maillard-Roncalli-Teïletche(ERC)· GSAM(Combining Investment Signals)· Concretum(VT vs VP vs Pyramiding)· AQR(Chasing Your Own Tail Risk)。
**Entry**:Zakamulin (2017, _Market Timing with Moving Averages_)· Han-Yang-Zhou (2013, _JFQA_ 48:5)· Faber (2006/2013)· Sullivan-Timmermann-White (1999, _JF_)· Moskowitz-Ooi-Pedersen (2012, _JFE_)· Hurst-Ooi-Pedersen (AQR 2017)· "Only strong short-term contrarian effect exists in Chinese stock market" (_IREF_ 2024)· T+1 overnight (_JIFM_ 2020)· Kitces/Constantinides(DCA)。
**Exit**:Kaminski-Lo (2014, _J. Fin. Markets_ 18:234-254 + 2017)· Han-Zhou-Zhu (2016, _JFQA_)· Dai-Marshall-Nguyen-Visaltanachoti (2021, _IRF_ 21:1334)· López de Prado (2018, _AFML_ triple-barrier)· Odean (1998, _JF_)/Shefrin-Statman (1985)· T+1 (_JBF_ 2020)· financial distress jump tail risk (_ScienceDirect_ 2023)。
**Reuse**:microsoft/qlib(MIT)· PyPortfolioOpt(MIT)· Riskfolio-Lib(BSD-3)· vectorbt(Apache*)· rqalpha(Apache*)· backtrader(GPL,learn-only)· ayondey47/kelly-criterion(MIT)。
**内部**:`c1-avoid-top-exit-results-2026-06-27.md` · `b1-regime-derisk-results-2026-06-26.md` · `b2-defensive-sleeve-results-2026-06-27.md` · `slot-frontier-results-2026-06-27.md` · `defensive-selection-research-synthesis-2026-07-03.md` · `qgr-criterion-rebar-amendment-2026-06-27-*.md` · `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md` · `feedback-owner-trading-principles-2026-06-27`。
