# QuantMind 量化研究现状 + 下一步对策(战略态势报告,2026-07-04)

> **用途**:owner 请 **Fable 5** 干净上下文思考下一步对策。本文 = 战略态势快照(不是实施指南)—— 把「已排除什么、什么有效、跨所有尝试的元规律、当前候选池、开放的战略问题」摊清楚,供 Fable 5 提对策。**作者**:Claude(Opus 4.8)· owner:dr.zhang
> **读法**:先读本文拿全局 → 要细节再钻 §7 指针的权威文档。**别急着实施**;owner 要的是「下一步做什么方向」的判断。

---

## 0. 一句话现状

**量化选股线走到一个关键判断点:反过拟合主门(DSR≥0.95)在 round-1..4 + alpha-pivot + DS-D1 的每一次横截面排名尝试上从未被满足过(DSR 实测 0.001–0.05),而唯一被真数据证明有效的东西是「防御宇宙过滤 + 现金 buffer 控回撤」——它不是排名 alpha,是一个近乎被动的低回撤 sleeve。** 战略问题因此从「哪个因子」上升为「在 ≤5 集中 A 股书上,能过四门的选股排名 alpha 到底存不存在;若不存在,产品该重新定义成什么」。

---

## 1. 唯一活跃约束 = 选股 alpha 质量(整条线的 through-line）

owner 2026-06-21 重定框(QGR):量化 = 面对 ~5000 票的**第一道选股闸门**,判据 = **绝对净盈 + 控回撤**(去掉 CSI300 超额硬门,仅披露)。此后每一刀都把绑定约束推回到**同一处 = 选股 alpha 本身的质量**:

- **风险容器已答死**:`slot_frontier`(2026-06-27)扫 slot×sizing frontier 证 **全部容器配置 DSR ~0.003–0.006、与容器无关(DSR-invariant)**;绑定约束是 ranker alpha 太弱,不是容器。现金 buffer 是**唯一**控回撤杠杆(buf40_5:MDD 减半、净盈翻倍)但**不加 Sharpe**。
- **≤5 集中已答死**:分散(槽 5→50)伤害 MDD(56%→66%);集中 + 现金 buffer 是对的方向(= owner P-E)。
- 所以：**要整体稳定盈利,缺口在「选哪些票」的 alpha,不在「怎么持/持多少/何时买卖」**(后者已分别被证有害或无增量,见 §2)。

---

## 2. 已排除地图(真数据证伪,别重蹈；带机制）

| 方向 | 结果 | 机制 / 定理 |
|---|---|---|
| **择时/退出 overlay**(C1a 避顶部 / B1 regime de-risk / QGR-4 exit-veto / 机械 −12% 止损) | 全 **净有害**(C1a −663k vs +459k baseline;止损 −431k)| **Kaminski-Lo(2014)**:均值回复书上止损严格净有害(反弹前砍仓)。永不再建。 |
| **MDD≤8% 硬门** | 结构不可达(唯一达标 = 全仓国债 = 非选股闸)| ≤5 A 股股票书 2015/2018 个股回撤 40–55% → owner 弃 8% 硬门 |
| **容器 / sizing 作绑定约束** | DSR-invariant(~0.005)| 现金 buffer 控回撤但不加边;分散伤害 |
| **round-1..4 挖矿**(反转/SUE/应计/质量/价值 zero-cost 因子 + 复合)| 三轮 FAIL + 一轮 provisional(增强指数,≤5 装不下)| 越做越小盘;反过拟合门三次精准预测 OOS 失败 |
| **alpha-pivot 复合**(反转 0.5 + 分析师 0.25 + 质量 0.25)| **AP-0.5 收益盲 power precheck 判 NO-GO**(SR_req 年化 2.67 vs K·SR_ref 0.62,gap 8.6×)| 复合的先验 Sharpe 达不到过门所需,评测前就否 |
| **DS-D1 红利低波防御排名**(本次)| **FAIL**:输同宇宙随机 placebo(t −0.98/−0.85)+ DSR 0.002–0.004 + 2018/2022 股灾负 | 防御是**宇宙过滤器**不是**排名器**(见 §3)|

> **🔴 元规律(最重要的一条,请 Fable 5 正视）**:**横截面排名 alpha 从未过 DSR≥0.95**。round-1、2、3、4、alpha-pivot、DS-D1——每一个候选的 DSR 都在 0.001–0.05。这道反过拟合主门(它在历史上三次精准预测了 OOS 失败,是可信的预言机)**从未被满足过一次**。这不是「又一个因子不行」,是**一个跨越两年、十几个候选的系统性信号**。

---

## 3. 唯一有效的东西(正面 partial,值得当地基）

**防御宇宙过滤(排除门)+ 永久现金 buffer = 控回撤有效,但无排名 alpha。**

- DS-D1 证据:`buf40_5`(防御宇宙 + 40% gross/60% 现金)MDD **14.78%**（vs CSI300 45% / 反转 54%），熊市累计正(+0.08），2018/2020/2022/2024 逆境不崩(个别切片小负）。
- B2 证据:永久 2 槽现金 sleeve 净盈翻倍 + MDD 减半 + 熊市/震荡双改善(但 DSR 0.044 不过门)。
- **但**:D1 防御宇宙**内部**,块加权排名**输给随机选股**(t<0）→「选哪只」没边;有边的只是「选防御宇宙 + 留现金」。

**读法**:这实际是一个**规则驱动的低回撤近被动 sleeve**(smart-beta 味道),不是主动选股 alpha。它满足 owner 判据的「熊市不亏 + 回撤可控 + 净盈>0」三条,**唯独过不了「胜 placebo + DSR」两条**(因为它本质不是 alpha,是 beta 削减 + 尾部裁剪)。

---

## 4. 当前候选池(DS 线剩余 + 各自到底在测什么）

owner 已令测试序 **D1→D2→D3→D4**(present 结果 owner 判,不替选）。D1 done=FAIL。剩:

| 候选 | 假设 | 与 D1 洞察的关系 | 先验 |
|---|---|---|---|
| **D2 防御宇宙上的反转** | 保留已验证 5 日反转 ranker,但宇宙限到防御过滤集;对照 A0 全宇宙反转,隔离「宇宙过滤」对熊市亏损的因果 | **直接检验 D1 洞察**:若防御=宇宙过滤器 + 反转=排名 alpha,则「防御宇宙 + 反转排名」= 两者结合可能是出路 | 中(唯一把「有边的反转排名」放进「有效的防御宇宙」的组合）|
| **D3 彩票排除低波** | 排除高 MAX/彩票名 + 低波倾斜(A 股最特异防御边）| 更纯的宇宙裁剪,可能同样是「过滤器非排名器」 | 中低(与 D1 同族,恐同命）|
| **D4 质量安全防御价值** | 便宜 ∧ 高质量(value ∧ quality,F-score 门）| 慢因子排名,round-1..4 已证 A 股弱独立 | 低 |

**注**:D3/D4 与 D1 同属「防御选股排名」族,若 D1 的「过滤器有效、排名器无效」是结构性的,D3/D4 大概率同命。**D2 不同** —— 它把反转(唯一从零验证过的排名 alpha)放进防御宇宙,是候选池里唯一「过滤器 × 排名器」的组合,先验最高。

---

## 5. 战略层开放问题(Fable 5 该权衡的真问题,不是实施细节）

1. **DSR≥0.95 从未被满足 —— 目标是否可达?** 在 ≤5 集中 A 股书上「找一个过四门的横截面排名 alpha」是否根本不现实?若门是对的(它三次预言准确),那答案可能是「这类 alpha 在此约束下不存在」。若如此,继续 D2/D3/D4 找排名 alpha 是否在重复一个已被元规律否定的搜索?
2. **产品是否该重定义?** 若排名 alpha 不可得,而「防御宇宙 + 现金 buffer」控回撤有效——**产品是不是就该是这个近被动低回撤 sleeve**(judged on 绝对净盈 + 回撤,forward-test,而非强求胜 placebo/DSR)?即把「主动选股 alpha」目标降级为「规则驱动防御 beta + 尾部裁剪」目标。这需要 owner 拍判据(现判据要求胜 placebo + DSR,这个 sleeve 过不了)。
3. **D2 = 唯一「过滤器 × 排名器」组合,是否该先做?** 它是候选池里唯一可能同时拿到「防御宇宙控回撤 + 反转排名 alpha」的构型,且直接检验 D1 的核心洞察。先验最高,值得优先。
4. **是否该转去别的价值创造路径?**
   - **买卖点/仓位 harness**(已有计划书,gated 等 alpha 锁定):但它的结论也是「sizing 承重、timing 陷阱」,且置信集中 conf60 冻结引擎表达不了(结构墙)——它不产生选股 alpha,只在给定 alpha 上管理买卖点。**没有选股 alpha,它无米下锅**。
   - **FW look-once 前向裁决**(ffc1db3 round-4 反转候选):post-2026-06-12 处子 OOS,稀缺一枪。是否值得为一个 DSR 0.007 的 provisional 候选烧这枪?
   - **长线价值投资线**(¥5万触发,已建休眠 AF-001..007):完全不同的时间尺度 + 判据,不受横截面排名元规律约束。
5. **分析师修正动量**(round-4 唯一正交正超额 +2.68% provisional):它是**benchmark-relative** 框定下的边,在**绝对净盈 + ≤5**框定下未被独立验证。是否值得在新框定下重验它作为排名 alpha?(它是文献先验、非挖矿,provenance 干净。)

---

## 6. 硬约束 / 红线(任何对策都须遵守）

永禁真实下单 · 飞书人工执行 · 127.0.0.1 · **研究/评测零 LLM**(LLM 只用于文献,不写决策)· RiskEngine 纯函数 · InstructionPlan 单一构造点 · PIT 字节存档可复现(`data/marketdata_pit/` ~29GB 禁重下,只增量)· **反过拟合四门不放宽**(放宽的只有 MDD)· **非清零账本不清零**(现 effective floor 2418)· **committed spec 评测前 hash、评测后绝不改**(反 p-hacking)· size/行业中性化删最小 30% · train_val only(sealed test = owner-gated look-once,已烧 4 次第 5 次极慎)· **FAIL 报 FAIL**(不移球门/不样本内调参)· push/摄取/live 激活/look-once = owner-gated · codex 前置门(代码任务)· 报告中文 / 代码 commit 英文。

**数据可用性**:全市场 PIT 字节存档在库(daily/daily_basic〔含 dv_ratio〕/adj/fund_daily/*_vip 财报/report_rc 分析师/namechange/suspend/index_member 等,23 端点);**统计报表存档始于 2015 → 依赖两期年报的因子(如 accr）可排名窗从 ~2017 起,2015/2016 股灾切片无法覆盖**(D1 已撞此限）。

---

## 7. 权威文档指针(要细节钻这里）

| 主题 | 文档 |
|---|---|
| **SSoT / 进度 / Session Log** | `docs/plan.html`(#current 现状 + #legacy 负结果地图 + SESSION_LOG 权威下一步）|
| **框定(量化=第一闸门 + 两层评测)** | `docs/research/quant-first-gate-rearch-plan-2026-06-21.md` |
| **判据重定(弃 8% + P-A..P-E)** | `docs/decisions/qgr-criterion-rebar-amendment-2026-06-27-*.md` + `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md` |
| **DS 防御选股综合 + 4 候选设计** | `docs/research/defensive-selection-research-synthesis-2026-07-03.md` + `defensive-candidate-D{1,2,3,4}-*.md` |
| **DS-D1 结果(本次 FAIL)** | `docs/research/defensive-d1-results-2026-07-04.md` |
| **容器 frontier(答容器非约束）** | `docs/research/slot-frontier-results-2026-06-27.md` |
| **负结果三刀** | `docs/research/{c1-avoid-top-exit,b1-regime-derisk,b2-defensive-sleeve}-results-2026-06-*.md` |
| **买卖点/仓位 harness 计划书(gated）** | `docs/research/buy-sell-timing-harness-implementation-plan-2026-07-03.md` + `-position-management-research-2026-07-03.md` |
| **alpha-pivot spec + power NO-GO** | `docs/research/alpha-pivot-composite-spec-outline-2026-06-27.md`;power 见 SESSION_LOG #3 |
| **红线总纲** | `docs/decisions/R0-two-line-rearch-...-2026-05-24.md` + `CLAUDE.md` §2 |
| **复用件**(建候选用）| `scripts/factor_research/`:`gate_backtest`(冻结竞技场）/ `slot_frontier`(容器）/ `arena_ablation`(firewall/ledger/placebo）/ `neutralize` / `honest_gates`(DSR）/ `trial_ledger`(非清零）/ `regime_detector` / `defensive_d1_*`(D1 全套,可仿建 D2）/ `beta_factor`(新因子）|

---

## 8. 可选下一步(供 Fable 5 提对策；每条附权衡，非我的推荐——owner 判）

- **(A) 做 D2(防御宇宙 × 反转排名)**:先验最高、直接检验 D1 洞察、复用 D1 全套件最省力。风险:若「过滤器有效排名器无效」是结构性,反转排名进防御宇宙仍可能不过 placebo/DSR。**但它是候选池里唯一有机会同时拿到控回撤 + 排名 alpha 的构型**。
- **(B) 重定义产品 = 规则驱动防御 sleeve**(承认无排名 alpha,把「防御宇宙 + 现金 buffer」作为低回撤近被动产品,请 owner 改判据:不强求胜 placebo/DSR,judged on 绝对净盈 + 回撤 + forward-test)。风险:这是 beta 削减非 alpha,收益天花板低;需 owner 明确接受判据降级。
- **(C) 元层面停手搜索**:接受 DSR 元规律 = 「此约束下横截面排名 alpha 不存在」,转向 FW look-once(ffc1db3)/ 长线价值线 / 或直接上「防御 sleeve + 买卖点 harness」的组合实盘 shadow。风险:放弃可能存在但没找到的 alpha。
- **(D) 新框定重验分析师修正动量**(round-4 唯一正超额,文献先验干净)作为绝对净盈框定下的排名 alpha 源。风险:它原本是 benchmark-relative 的边,≤5 绝对框定下未知;且 report_rc 2016+ 覆盖。

**给 Fable 5 的一句话**:别默认「继续找下一个因子」——先判断 §5 的问题 1(DSR 从未过 = 目标是否可达)。若判定可达 → (A) D2 最优先;若判定不可达 → (B)/(C) 的产品重定义/判据降级才是真对策。**owner 要的是这个层面的判断,不是又一个候选的实施。**
